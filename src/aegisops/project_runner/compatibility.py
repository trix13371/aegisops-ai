"""Bounded advisory inspection of declarations, without resolving or executing them."""

import json
import math
import re
from typing import Annotated, Final, Literal, Never

from pydantic import Field, StrictStr, field_validator

from aegisops.domain.common import DTO

MAX_PACKAGE_BYTES: Final = 256 * 1024
PREPARED_VITEST_DEPENDENCIES: Final = frozenset({"vitest", "vite", "typescript", "@types/node"})
_PACKAGE_PATTERN = r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$"
_PACKAGE_NAME = re.compile(_PACKAGE_PATTERN, re.ASCII)
PackageName = Annotated[StrictStr, Field(max_length=214, pattern=_PACKAGE_PATTERN)]
CompatibilityReason = Literal[
    "package_missing",
    "package_invalid",
    "dependency_limit",
    "invalid_dependency",
    "workspace_project",
    "non_registry_dependency",
    "jest_unsupported",
    "mixed_frameworks",
    "additional_dependencies",
    "dependency_versions_unverified",
]


class PackageCompatibility(DTO):
    framework: Literal["node", "vitest", "jest", "mixed", "unknown"]
    suggested_environment: Literal["node-js-v1", "node-vitest-v1"] | None
    status: Literal["supported", "limited", "unsupported"]
    reasons: tuple[CompatibilityReason, ...] = Field(default=(), max_length=10)
    dependencies: tuple[PackageName, ...] = Field(default=(), max_length=100)

    @field_validator("dependencies")
    @classmethod
    def safe_names(cls, names: tuple[str, ...]) -> tuple[str, ...]:
        if names != tuple(sorted(set(names))) or any(
            _PACKAGE_NAME.fullmatch(name) is None for name in names
        ):
            raise ValueError("invalid dependency names")
        return names


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _constant(value: str) -> Never:
    raise ValueError("non-finite number")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite number")
    return number


def _invalid(reason: CompatibilityReason = "package_invalid") -> PackageCompatibility:
    return PackageCompatibility(
        framework="unknown", suggested_environment=None, status="unsupported", reasons=(reason,)
    )


def inspect_package(text: str | None) -> PackageCompatibility:
    """Inspect runtime, development, optional and peer direct dependency declarations.

    Scripts, configs and project modules are never read.
    A supported declaration does not assert dependency version compatibility. The operator's
    prepared environment, rather than project declarations, determines installed versions.
    """
    if text is None:
        return PackageCompatibility(
            framework="node",
            suggested_environment="node-js-v1",
            status="limited",
            reasons=("package_missing",),
        )
    if len(text) > MAX_PACKAGE_BYTES:
        return _invalid()
    try:
        if len(text.encode("utf-8")) > MAX_PACKAGE_BYTES:
            return _invalid()
        package = json.loads(
            text, object_pairs_hook=_object, parse_constant=_constant, parse_float=_float
        )
    except (ValueError, UnicodeError, RecursionError):
        return _invalid()
    if not isinstance(package, dict):
        return _invalid()
    dependencies: dict[str, str] = {}
    versions: list[str] = []
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        declared = package.get(section, {})
        if not isinstance(declared, dict):
            return _invalid()
        if len(declared) > 100:
            return _invalid("dependency_limit")
        for name, version in declared.items():
            if (
                not isinstance(name, str)
                or len(name) > 214
                or _PACKAGE_NAME.fullmatch(name) is None
                or not isinstance(version, str)
                or not 1 <= len(version) <= 256
                or not version.strip()
                or any(ord(character) < 32 for character in version)
            ):
                return _invalid("invalid_dependency")
            dependencies[name] = version
            versions.append(version)
    if len(dependencies) > 100:
        return _invalid("dependency_limit")
    names = tuple(sorted(dependencies))
    vitest = "vitest" in dependencies
    jest = "jest" in dependencies or "@jest/core" in dependencies
    framework: Literal["node", "vitest", "jest", "mixed", "unknown"] = (
        "mixed" if vitest and jest else "vitest" if vitest else "jest" if jest else "node"
    )
    reasons: list[CompatibilityReason] = []
    unsupported = False
    # Workspaces require dependency resolution across directories, which this preset cannot do.
    if "workspaces" in package:
        reasons.append("workspace_project")
        unsupported = True
    if any(
        value.startswith(("file:", "link:", "workspace:", "npm:", "git", ".", "/", "\\"))
        or ":" in value
        or "/" in value
        for value in versions
    ):
        reasons.append("non_registry_dependency")
        unsupported = True
    if framework == "mixed":
        reasons.append("mixed_frameworks")
        unsupported = True
    elif framework == "jest":
        reasons.append("jest_unsupported")
        unsupported = True
    if any(name not in PREPARED_VITEST_DEPENDENCIES for name in names):
        reasons.append("additional_dependencies")
    if vitest:
        reasons.append("dependency_versions_unverified")
    return PackageCompatibility(
        framework=framework,
        suggested_environment=(
            None if unsupported else "node-vitest-v1" if vitest else "node-js-v1"
        ),
        status="unsupported" if unsupported else "limited" if reasons else "supported",
        reasons=tuple(reasons),
        dependencies=names,
    )
