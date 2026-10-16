"""Trusted local Docker CLI boundary. No arbitrary commands, mounts or daemon endpoints."""

import hashlib
import json
import os
import re
import subprocess
import threading
from pathlib import Path
from uuid import UUID

from aegisops.io import read_text, unique_object
from aegisops.project_runner.models import ENVIRONMENTS
from aegisops.projects.repository import plain_path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "python-checks-v1"
LABEL = "org.aegisops.project-run"
ENGINE = (
    "npipe:////./pipe/dockerDesktopLinuxEngine"
    if os.name == "nt"
    else "unix:///var/run/docker.sock"
)


class RunnerFailure(ValueError):
    pass


def context_hash(environment="minimal"):
    if environment not in ENVIRONMENTS:
        raise RunnerFailure("runner_unavailable")
    digest = hashlib.sha256()
    digest.update(environment.encode())
    for name in ("Dockerfile", "requirements.txt", "data-requirements.txt", "entry.py"):
        digest.update(name.encode())
        digest.update((ROOT / "runner/python-v1" / name).read_bytes())
    return digest.hexdigest()


def read_image(environment="minimal"):
    if environment not in ENVIRONMENTS:
        raise RunnerFailure("runner_unavailable")
    filename = "image.json" if environment == "minimal" else "python-data-v1.json"
    value = json.loads(read_text(plain_path(ROOT / ".local/project-runner" / filename), 2048))
    if (
        not isinstance(value, dict)
        or value.get("profile") != PROFILE
        or value.get("environment", "minimal") != environment
        or value.get("context_sha256") != context_hash(environment)
        or not isinstance(value.get("image_id"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["image_id"])
    ):
        raise RunnerFailure("runner_unavailable")
    return value["image_id"]


def docker(arguments, *, data=None, timeout=15, maximum=131072):
    # Keep only OS/tool transport settings. Never forward provider, cloud, DB or kube keys.
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "COMSPEC",
        "HOME",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    process = subprocess.Popen(
        ["docker", "--host", ENGINE, *arguments],
        cwd=ROOT,
        env=environment,
        stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()

    def collect(stream, buffer):
        try:
            while chunk := stream.read(4096):
                buffer.extend(chunk[: max(0, maximum + 1 - len(buffer))])
                if len(buffer) > maximum:
                    overflow.set()
                    process.kill()
                    break
        except (OSError, ValueError):
            return

    readers = [
        threading.Thread(target=collect, args=(stream, buffer), daemon=True)
        for stream, buffer in zip((process.stdout, process.stderr), buffers, strict=True)
    ]
    for reader in readers:
        reader.start()

    def send():
        try:
            process.stdin.write(data)
            process.stdin.close()
        except (OSError, ValueError):
            return

    if data is not None:
        threading.Thread(target=send, daemon=True).start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait(timeout=3)
    finally:
        for reader in readers:
            reader.join(timeout=2)
        for stream in (process.stdout, process.stderr):
            stream.close()
    if timed_out:
        raise RunnerFailure("deadline")
    if overflow.is_set():
        raise RunnerFailure("sandbox_failed")
    if process.returncode != 0:
        raise RunnerFailure("sandbox_failed")
    return bytes(buffers[0])


def container_arguments(run_id: UUID, image_id: str):
    if type(run_id) is not UUID or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        raise RunnerFailure("sandbox_failed")
    return [
        "create",
        "--pull=never",
        "--interactive",
        "--name",
        f"aegisops-check-{run_id}",
        "--label",
        f"{LABEL}={run_id}",
        "--label",
        f"org.aegisops.profile={PROFILE}",
        "--network=none",
        "--read-only",
        "--user=65532:65532",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--cpus=1",
        "--memory=512m",
        "--memory-swap=512m",
        "--pids-limit=64",
        "--restart=no",
        "--no-healthcheck",
        "--log-driver=none",
        "--shm-size=8m",
        "--ulimit=nofile=256:256",
        "--ulimit=fsize=8388608:8388608",
        "--stop-timeout=1",
        "--tmpfs=/work:rw,noexec,nosuid,nodev,size=67108864,uid=65532,gid=65532,mode=0700",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16777216,uid=65532,gid=65532,mode=0700",
        image_id,
    ]


def inspect_owned(identifier, run_id):
    if re.fullmatch(r"[0-9a-f]{64}", identifier) is None:
        raise RunnerFailure("sandbox_failed")
    value = json.loads(docker(["inspect", identifier]), object_pairs_hook=unique_object)
    if (
        not isinstance(value, list)
        or len(value) != 1
        or value[0].get("Id") != identifier
        or value[0].get("Config", {}).get("Labels", {}).get(LABEL) != str(run_id)
        or value[0].get("Config", {}).get("Labels", {}).get("org.aegisops.profile") != PROFILE
        or value[0].get("Name") != f"/aegisops-check-{run_id}"
    ):
        raise RunnerFailure("sandbox_failed")
    return value[0]


def validate_container(value, image_id):
    config, host = value["Config"], value["HostConfig"]
    if (
        value["Image"] != image_id
        or config.get("User") != "65532:65532"
        or config.get("Entrypoint") != ["python", "-I", "/opt/aegisops/entry.py"]
        or config.get("Cmd")
        or config.get("Volumes")
        or host.get("Binds")
        or host.get("Mounts")
        or host.get("Privileged")
        or host.get("Devices")
        or host.get("NetworkMode") != "none"
        or not host.get("ReadonlyRootfs")
        or host.get("CapDrop") != ["ALL"]
        or host.get("CapAdd")
        or "no-new-privileges:true" not in host.get("SecurityOpt", [])
        or host.get("NanoCpus") != 1000000000
        or host.get("Memory") != 536870912
        or host.get("MemorySwap") != 536870912
        or host.get("PidsLimit") != 64
        or host.get("PortBindings")
        or host.get("PidMode") == "host"
        or host.get("IpcMode") == "host"
        or host.get("RestartPolicy", {}).get("Name") != "no"
        or set(host.get("Tmpfs", {})) != {"/work", "/tmp"}  # noqa: S108 -- container tmpfs
        or host.get("LogConfig", {}).get("Type") != "none"
    ):
        raise RunnerFailure("sandbox_failed")


def remove_owned(identifier, run_id):
    inspect_owned(identifier, run_id)
    docker(["rm", "--force", identifier])
    return True
