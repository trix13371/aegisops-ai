"""Read just a registered root package.json; never discover or execute project config."""

import os
import stat

from aegisops.project_runner.compatibility import (
    MAX_PACKAGE_BYTES,
    PackageCompatibility,
    inspect_package,
)
from aegisops.projects.models import Project
from aegisops.projects.repository import plain_path, project_root


def inspect_project_package(project: Project) -> PackageCompatibility:
    root = project_root(project)
    try:
        path = plain_path(root / "package.json")
        if not path.exists():
            project_root(project)
            return inspect_package(None)
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            return inspect_package("")
        with path.open("rb") as stream:
            data = stream.read(MAX_PACKAGE_BYTES + 1)
            after = os.fstat(stream.fileno())
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            return inspect_package("")
        project_root(project)
        return inspect_package(data.decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        return inspect_package("")
