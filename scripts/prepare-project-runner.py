"""Explicit operator setup. Never builds a user's Dockerfile or installs project deps."""

import argparse
import json

from project_runner_host import ROOT, context_hash, docker

from aegisops.project_runner.models import ENVIRONMENTS, profile_for


def main():
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--environment", choices=ENVIRONMENTS, default="minimal")
        parser.add_argument("--use-built", action="store_true")
        arguments = parser.parse_args()
        environment = arguments.environment
        tag = f"aegisops-python-checks:{'v1' if environment == 'minimal' else environment}"
        javascript = environment in {"node-js-v1", "node-vitest-v1"}
        directory_name = "vitest-v1" if environment == "node-vitest-v1" else "javascript-v1"
        if javascript:
            tag = (
                "aegisops-vitest-checks:v1"
                if environment == "node-vitest-v1"
                else "aegisops-javascript-checks:v1"
            )
        requirements = "requirements.txt" if environment == "minimal" else "data-requirements.txt"
        if not arguments.use_built:
            build = (
                [
                    "build",
                    "--pull=false",
                    "--tag",
                    tag,
                    "--file",
                    str(ROOT / "runner" / directory_name / "Dockerfile"),
                    str(ROOT / "runner"),
                ]
                if javascript
                else [
                    "build",
                    "--pull=false",
                    "--tag",
                    tag,
                    "--build-arg",
                    f"DEPENDENCIES={requirements}",
                    str(ROOT / "runner/python-v1"),
                ]
            )
            docker(build, timeout=300)
        inspected = json.loads(docker(["image", "inspect", tag]))[0]
        image_id = inspected["Id"]
        if inspected["Os"] != "linux" or inspected["Config"].get("Volumes"):
            raise ValueError("unsupported image")
        directory = ROOT / ".local/project-runner"
        directory.mkdir(parents=True, exist_ok=True)
        filename = "image.json" if environment == "minimal" else f"{environment}.json"
        temporary = directory / f"{filename}.tmp"
        temporary.write_text(
            json.dumps(
                {
                    "profile": profile_for(environment),
                    "environment": environment,
                    "image_id": image_id,
                    "context_sha256": context_hash(environment),
                }
            ),
            encoding="utf-8",
        )
        temporary.replace(directory / filename)
        print("Project checks runner ready; project code was not executed.")
        return 0
    except Exception:
        print("Runner setup failed. Check local Docker Desktop; no project was executed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
