"""Explicit operator setup. Never builds a user's Dockerfile or installs project deps."""

import json
import sys

from project_runner_host import PROFILE, ROOT, context_hash, docker


def main():
    try:
        if sys.argv[1:] not in ([], ["--use-built"]):
            raise ValueError("unsupported arguments")
        if not sys.argv[1:]:
            docker(
                [
                    "build",
                    "--pull=false",
                    "--tag",
                    "aegisops-python-checks:v1",
                    str(ROOT / "runner/python-v1"),
                ],
                timeout=300,
            )
        inspected = json.loads(docker(["image", "inspect", "aegisops-python-checks:v1"]))[0]
        image_id = inspected["Id"]
        if inspected["Os"] != "linux" or inspected["Config"].get("Volumes"):
            raise ValueError("unsupported image")
        directory = ROOT / ".local/project-runner"
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / "image.tmp"
        temporary.write_text(
            json.dumps(
                {"profile": PROFILE, "image_id": image_id, "context_sha256": context_hash()}
            ),
            encoding="utf-8",
        )
        temporary.replace(directory / "image.json")
        print("Python checks runner ready; project code was not executed.")
        return 0
    except Exception:
        print("Runner setup failed. Check local Docker Desktop; no project was executed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
