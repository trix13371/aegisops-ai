"""Explicit operator setup. Never builds a user's Dockerfile or installs project deps."""

import argparse
import json

from project_runner_host import PROFILE, ROOT, context_hash, docker

from aegisops.project_runner.models import ENVIRONMENTS


def main():
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--environment", choices=ENVIRONMENTS, default="minimal")
        parser.add_argument("--use-built", action="store_true")
        arguments = parser.parse_args()
        environment = arguments.environment
        tag = f"aegisops-python-checks:{'v1' if environment == 'minimal' else environment}"
        requirements = "requirements.txt" if environment == "minimal" else "data-requirements.txt"
        if not arguments.use_built:
            docker(
                [
                    "build",
                    "--pull=false",
                    "--tag",
                    tag,
                    "--build-arg",
                    f"DEPENDENCIES={requirements}",
                    str(ROOT / "runner/python-v1"),
                ],
                timeout=300,
            )
        inspected = json.loads(docker(["image", "inspect", tag]))[0]
        image_id = inspected["Id"]
        if inspected["Os"] != "linux" or inspected["Config"].get("Volumes"):
            raise ValueError("unsupported image")
        directory = ROOT / ".local/project-runner"
        directory.mkdir(parents=True, exist_ok=True)
        filename = "image.json" if environment == "minimal" else "python-data-v1.json"
        temporary = directory / f"{filename}.tmp"
        temporary.write_text(
            json.dumps(
                {
                    "profile": PROFILE,
                    "environment": environment,
                    "image_id": image_id,
                    "context_sha256": context_hash(environment),
                }
            ),
            encoding="utf-8",
        )
        temporary.replace(directory / filename)
        print("Python checks runner ready; project code was not executed.")
        return 0
    except Exception:
        print("Runner setup failed. Check local Docker Desktop; no project was executed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
