"""Fixed JavaScript/TypeScript checks inside the disposable container only."""

import json
import sys

# Python -I excludes the script directory; add only our immutable trusted location.
sys.path.insert(0, "/opt/aegisops")
from python_runner import MAX_ARCHIVE, check, unpack  # noqa: E402

SUFFIXES = {".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"}


def main():
    try:
        data = sys.stdin.buffer.read(MAX_ARCHIVE + 1)
        if len(data) > MAX_ARCHIVE:
            raise ValueError("archive limit")
        root = unpack(data)
        compiler = check("typescript", ["/usr/local/bin/node", "/opt/aegisops/check.mjs"], 20)
        # Explicit bounded file list avoids package scripts, config hooks and shell globs.
        tests = sorted(
            str(path)
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix in SUFFIXES
            and (path.stem.endswith((".test", ".spec")) or path.stem in {"test", "spec"})
        )
        if tests:
            node = check(
                "node-test",
                [
                    "/usr/local/bin/node",
                    "--test",
                    "--test-concurrency=1",
                    "--test-reporter=tap",
                    "--",
                    *tests,
                ],
                40,
            )
        else:
            node = {
                "tool": "node-test",
                "status": "no_tests",
                "exit_code": 5,
                "output": "No *.test.* / *.spec.* Node test files found.",
                "output_truncated": False,
            }
        print(json.dumps({"checks": [compiler, node]}, ensure_ascii=True), flush=True)
        return 0
    except Exception:
        print('{"error":"runner_failed"}', flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
