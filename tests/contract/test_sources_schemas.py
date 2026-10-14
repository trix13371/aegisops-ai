"""Independent checks of packaged fixtures, schemas and file boundaries."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from typer.testing import CliRunner

from aegisops.domain.models import AuditRecord, PodSnapshot
from aegisops.domain.schemas import CONTRACTS, contract_schemas
from aegisops.io import DataFileError, contained_file, read_json, read_yaml
from aegisops.sources.synthetic import ScenarioError, ScenarioRepository, SyntheticSource

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]


def test_all_scenarios_and_schema_exports() -> None:
    repository = ScenarioRepository()
    schemas = contract_schemas()
    assert len(repository.list_ids()) == 10
    for name, schema in schemas.items():
        Draft202012Validator.check_schema(schema)
        assert json.loads((ROOT / "schemas" / f"{name}.json").read_text()) == schema
    for scenario in repository.list_ids():
        spec, snapshot = repository.load(scenario)
        for name, model in (("ScenarioSpec", spec), ("PodSnapshot", snapshot)):
            Draft202012Validator(schemas[name]).validate(model.model_dump(mode="json"))
        source = SyntheticSource(repository, scenario)
        first, second = source.collect()[0], source.collect()[0]
        assert first == second and first is not second
        assert first.observed_at.tzinfo is not None


def test_complete_record_json_schema(audit_data: dict[str, Any]) -> None:
    record = AuditRecord.model_validate(audit_data)
    payload = record.model_dump(mode="json")
    Draft202012Validator(contract_schemas()["AuditRecord"]).validate(payload)
    assert AuditRecord.model_validate_json(record.model_dump_json()) == record


def test_every_contract_matches_golden_json() -> None:
    schemas = contract_schemas()
    for model in CONTRACTS:
        path = ROOT / "examples" / "contracts" / f"{model.__name__}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        instance = model.model_validate(payload)
        assert instance.model_dump(mode="json") == payload
        Draft202012Validator(schemas[model.__name__]).validate(payload)
        with pytest.raises(ValidationError):
            model.model_validate(dict(payload, unexpected_field="forbidden"))


def test_intermediate_symbolic_component_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "alias"
    directory.mkdir()
    (directory / "data.json").write_text("{}")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == directory or original(path))
    with pytest.raises(DataFileError):
        contained_file(tmp_path, "alias/data.json")


@pytest.mark.parametrize(
    "identifier",
    ["../secret", "..", "/absolute/a", "a/b", "a\\b", "crashloop-exit-1\n", "A", "a" * 65],
)
def test_scenario_path_rejected_before_read(identifier: str) -> None:
    with pytest.raises(ScenarioError):
        ScenarioRepository().load(identifier)


def test_path_escape_and_symlink(tmp_path: Path) -> None:
    with pytest.raises(DataFileError):
        contained_file(tmp_path, "../escape.json")
    target = tmp_path / "target.json"
    target.write_text("{}")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Windows symbolic link privilege not available")
    with pytest.raises(DataFileError):
        contained_file(tmp_path, "link.json")


@pytest.mark.parametrize(
    "contents,kind",
    [
        ('{"key": 1, "key": 2}', "json"),
        ('{"key":', "json"),
        ("key: 1\nkey: 2\n", "yaml"),
        ("x: &x [1]\ny: *x\n", "yaml"),
        ("!!python/object/apply:os.system ['echo forbidden']", "yaml"),
    ],
)
def test_unsafe_data_files_rejected(tmp_path: Path, contents: str, kind: str) -> None:
    path = tmp_path / f"data.{kind}"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(DataFileError):
        (read_json if kind == "json" else read_yaml)(path)


def test_file_size_and_malformed_fixture(tmp_path: Path) -> None:
    oversized = tmp_path / "data.json"
    oversized.write_text('"' + "x" * 131072 + '"')
    with pytest.raises(DataFileError):
        read_json(oversized)
    spec, snapshot = ScenarioRepository().load("crashloop-exit-1")
    directory = tmp_path / spec.scenario_id
    directory.mkdir()
    (directory / "expected.yaml").write_text(json.dumps(spec.model_dump(mode="json")))
    value = snapshot.model_dump(mode="json")
    value["origin"] = "local_kind"
    (directory / "snapshot.json").write_text(json.dumps(value))
    with pytest.raises(ScenarioError):
        ScenarioRepository(tmp_path).load(spec.scenario_id)


def test_nested_nonallowlisted_data_rejected() -> None:
    value = ScenarioRepository().load("crashloop-exit-1")[1].model_dump(mode="json")
    value["containers"][0]["env"] = {"real": "forbidden"}
    with pytest.raises(ValidationError):
        PodSnapshot.model_validate(value)


def test_packaged_assets_match_canonical_inputs() -> None:
    assets = ROOT / "src" / "aegisops" / "assets"
    for name in ("actions", "scenarios"):
        for source in (ROOT / name).rglob("*"):
            if source.is_file():
                assert (
                    source.read_bytes()
                    == (assets / name / source.relative_to(ROOT / name)).read_bytes()
                )


def test_time_normalizes_utc_and_rejects_naive() -> None:
    value = ScenarioRepository().load("crashloop-exit-1")[1].model_dump(mode="json")
    value["observed_at"] = "2026-10-10T15:00:00+03:00"
    snapshot = PodSnapshot.model_validate(value)
    assert snapshot.observed_at.isoformat() == "2026-10-10T12:00:00+00:00"
    value["observed_at"] = "2026-10-10T12:00:00"
    with pytest.raises(ValidationError):
        PodSnapshot.model_validate(value)


def test_scenario_cli_does_not_display_untrusted_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    from aegisops.cli.app import app

    monkeypatch.delenv("AEGISOPS_DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("KUBECONFIG", "invalid-context")
    runner = CliRunner()
    result = runner.invoke(app, ["scenario", "list"])
    assert result.exit_code == 0 and "crashloop-exit-1" in result.output
    for scenario in ("inj-secret-canary", "inj-log-instruction"):
        result = runner.invoke(app, ["scenario", "show", scenario])
        assert result.exit_code == 0
        assert "DEMO_CANARY_NOT_A_REAL_CREDENTIAL" not in result.output
        assert "ignore instructions" not in result.output
    assert runner.invoke(app, ["scenario", "show", "../secret"]).exit_code == 1
