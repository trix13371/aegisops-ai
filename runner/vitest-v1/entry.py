"""Fixed Vitest checks inside the disposable container; never run npm scripts."""

import json
import re
import sys

sys.path.insert(0, "/opt/aegisops")
from python_runner import MAX_ARCHIVE, check, unpack  # noqa: E402

SUFFIXES = {".js", ".mjs", ".cjs", ".ts", ".mts", ".cts"}
ENVIRONMENT_DIRECTIVE = re.compile(rb"@(?:vitest|jest)-environment\b", re.IGNORECASE)
FIXED_TSCONFIG = json.dumps(
    {
        "compilerOptions": {
            "target": "ES2022",
            "module": "ESNext",
            "moduleResolution": "Bundler",
            "useDefineForClassFields": True,
        }
    }
)


def main():
    try:
        data = sys.stdin.buffer.read(MAX_ARCHIVE + 1)
        if len(data) > MAX_ARCHIVE:
            raise ValueError("archive limit")
        root = unpack(data)
        files = sorted(
            path for path in root.rglob("*") if path.is_file() and path.suffix in SUFFIXES
        )
        for path in files:
            if ENVIRONMENT_DIRECTIVE.search(path.read_bytes()):
                raise ValueError("custom test environments are not supported")
        tests = [
            path
            for path in files
            if not path.name.endswith((".d.ts", ".d.mts", ".d.cts"))
            and (path.stem.endswith((".test", ".spec")) or path.stem in {"test", "spec"})
        ]
        # Vite's internal TypeScript transform can discover tsconfig independently
        # of its public esbuild overrides. Replace configuration only in this guest
        # copy, including nested configs, so extends/references cannot load project
        # settings. The registered source folder is never modified or mounted.
        for path in root.rglob("*"):
            name = path.name.lower()
            if (
                path.is_file()
                and name.endswith(".json")
                and name.startswith(("tsconfig", "jsconfig"))
            ):
                path.write_text(FIXED_TSCONFIG, encoding="utf-8")
        (root / "tsconfig.json").write_text(FIXED_TSCONFIG, encoding="utf-8")
        # The link points only to immutable dependencies already in this guest image.
        # No source folder or host node_modules is mounted into the container.
        (root / "node_modules").symlink_to("/opt/aegisops/node_modules", target_is_directory=True)
        compiler = check(
            "typescript",
            ["/usr/local/bin/node", "--max-old-space-size=192", "/opt/aegisops/check.mjs"],
            20,
        )
        if tests:
            vitest = check(
                "vitest",
                ["/usr/local/bin/node", "--max-old-space-size=192", "/opt/aegisops/run.mjs"],
                40,
            )
        else:
            # Vitest exits 1 both for missing tests and genuine failures. Synthesize
            # no_tests only before invoking Vitest, never by interpreting its logs.
            vitest = {
                "tool": "vitest",
                "status": "no_tests",
                "exit_code": 5,
                "output": "No *.test.* / *.spec.* Vitest files found.",
                "output_truncated": False,
            }
        print(json.dumps({"checks": [compiler, vitest]}, ensure_ascii=True), flush=True)
        return 0
    except Exception:
        print('{"error":"runner_failed"}', flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
