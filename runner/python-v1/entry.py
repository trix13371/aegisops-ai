"""Runs only inside the disposable container. Input is a bounded regular-file tar."""

import io
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath

MAX_ARCHIVE = 10 * 1024 * 1024
MAX_OUTPUT = 24000


def unpack(data):
    root = Path("/work/project")
    root.mkdir(mode=0o700)
    seen = set()
    total = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for index, member in enumerate(archive):
            path = PurePosixPath(member.name)
            if (
                index >= 1000
                or not member.isfile()
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in member.name
                or not path.parts
                or member.name in seen
                or len(member.name) > 1024
                or member.size > 262144
                or member.size < 0
            ):
                raise ValueError("invalid archive")
            seen.add(member.name)
            total += member.size
            if total > 8 * 1024 * 1024:
                raise ValueError("archive limit")
            destination = root.joinpath(*path.parts)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("invalid archive")
            with source, destination.open("xb") as output:
                output.write(source.read(262145))
            destination.chmod(0o600)
    return root


def check(name, command, timeout):
    process = subprocess.Popen(
        command,
        cwd="/work/project",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    data = bytearray()
    deadline = time.monotonic() + timeout
    timed_out = truncated = False
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                timed_out = True
                break
            for key, _ in selector.select(0.1):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                data.extend(chunk[: max(0, MAX_OUTPUT - len(data))])
                if len(data) >= MAX_OUTPUT:
                    truncated = True
                    break
            if truncated:
                break
        if timed_out or truncated:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=max(0.1, deadline - time.monotonic()) if not timed_out else 1)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=1)
    finally:
        selector.close()
        process.stdout.close()
        # Do not allow daemon children from one tool to survive into the next one.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    exit_code = process.returncode if 0 <= process.returncode <= 255 else None
    status = (
        "timeout"
        if timed_out
        else "error"
        if truncated
        else "passed"
        if exit_code == 0
        else "no_tests"
        if name == "pytest" and exit_code == 5
        else "failed"
        if exit_code == 1
        else "error"
    )
    output = data.decode("utf-8", errors="replace")
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", output)
    output = "".join(c for c in output if ord(c) >= 32 or c in "\n\t")
    return {
        "tool": name,
        "status": status,
        "exit_code": exit_code,
        "output": output,
        "output_truncated": truncated,
    }


def main():
    try:
        data = sys.stdin.buffer.read(MAX_ARCHIVE + 1)
        if len(data) > MAX_ARCHIVE:
            raise ValueError("archive limit")
        unpack(data)
        Path("/work/pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        # Ignore project tool config/addopts and third-party plugin auto-discovery.
        ruff = check(
            "ruff",
            [
                "/usr/local/bin/ruff",
                "check",
                "--isolated",
                "--no-cache",
                "--output-format=concise",
                "--select=E4,E7,E9,F",
                ".",
            ],
            15,
        )
        bridge = (
            "import sys,pytest; sys.path[:0]=['/work/project','/work/project/src'];"
            "raise SystemExit(pytest.main(['-c','/work/pytest.ini','--rootdir=/work/project',"
            "'-p','no:cacheprovider','-q','--tb=short','--color=no','--maxfail=10',"
            "'/work/project']))"
        )
        pytest = check("pytest", [sys.executable, "-I", "-c", bridge], 45)
        print(json.dumps({"checks": [ruff, pytest]}, ensure_ascii=True), flush=True)
        return 0
    except Exception:
        print('{"error":"runner_failed"}', flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
