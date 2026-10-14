"""Canaries must disappear before serialization and user-visible output."""

from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from aegisops.cli.app import app
from aegisops.context.builder import ContextBuilder, ContextError
from aegisops.detection.engine import DetectionEngine, DetectionError
from aegisops.domain.models import ContextLimits, EvidenceSource, Incident, PodSnapshot
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = pytest.mark.safety
CANARY = "S005_FAKE_CREDENTIAL_DO_NOT_USE"


def fixture(**changes):
    raw = ScenarioRepository().load("crashloop-exit-1")[1].model_dump()
    raw.update(changes)
    return PodSnapshot.model_validate(raw)


def test_canaries_in_names_status_events_and_log_are_removed(capsys, caplog):
    raw = fixture().model_dump()
    raw["cluster_id"] = f"api_key={CANARY}"
    raw["pod_name"] = f"password={CANARY}"
    raw["owner"]["name"] = f"token={CANARY}"
    raw["containers"][0]["name"] = f"secret={CANARY}"
    raw["containers"][0]["last_terminated_reason"] = f"password={CANARY}"
    raw["log_tail"] = f'{{"api_key": "{CANARY}"}}\nlast diagnostic'
    raw["events"] = [
        {
            "reason": f"token={CANARY}",
            "type": "Warning",
            "message": f"Bearer {CANARY}",
            "count": 2,
            "last_seen": datetime(2026, 10, 10, 11, 59, tzinfo=UTC),
        }
    ]
    incident = DetectionEngine().detect([PodSnapshot.model_validate(raw)])[0]
    context = ContextBuilder().build(incident)
    assert CANARY not in incident.model_dump_json()
    assert CANARY not in context.model_dump_json()
    assert "last diagnostic" in context.model_dump_json()
    assert {ev.evidence_id for ev in context.evidence} <= {
        ev.evidence_id for ev in incident.evidence
    }
    assert all(ev.attributes["untrusted"] is True for ev in context.evidence)
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    assert CANARY not in caplog.text


def test_cli_context_preserves_injection_as_data_and_hides_secret():
    runner = CliRunner()
    secret = runner.invoke(app, ["detect", "--scenario", "inj-secret-canary", "--show-context"])
    assert secret.exit_code == 0
    assert "DEMO_CANARY_NOT_A_REAL_CREDENTIAL" not in secret.output
    assert "[REDACTED]" in secret.output
    injection = runner.invoke(
        app, ["detect", "--scenario", "inj-log-instruction", "--show-context"]
    )
    assert injection.exit_code == 0
    assert "execute shell" in injection.output
    assert '"execution_available": false' in injection.output
    assert '"untrusted": true' in injection.output


def test_context_keeps_log_tail_and_ids_under_cumulative_quota():
    incident = DetectionEngine().detect(
        [fixture(log_tail="a" * 4090 + "\n" + "😀" * 2000 + "\nFINAL_DIAGNOSTIC")]
    )[0]
    original = incident.model_dump_json()
    assert sum(ev.source is EvidenceSource.CONTAINER_LOG for ev in incident.evidence) >= 2
    context = ContextBuilder(
        ContextLimits(max_log_bytes=101, max_log_lines=2, max_evidence=2)
    ).build(incident)
    logs = [ev for ev in context.evidence if ev.source is EvidenceSource.CONTAINER_LOG]
    assert len(logs) == 1
    assert logs[0].summary.endswith("FINAL_DIAGNOSTIC")
    assert sum(len(ev.summary.encode("utf-8")) for ev in logs) <= 101
    assert sum(len(ev.summary.splitlines()) for ev in logs) <= 2
    assert incident.model_dump_json() == original


def test_context_rejects_recognizable_secret_split_across_citations():
    incident = DetectionEngine().detect([fixture()])[0]
    raw = incident.model_dump()
    status = raw["evidence"][0]
    raw["evidence"] = [status] + [
        {
            "evidence_id": f"evidence-piece-{index}",
            "source": "container_log",
            "summary": text,
            "observed_at": incident.observed_at,
            "attributes": {"untrusted": True},
        }
        for index, text in enumerate(["password=", CANARY])
    ]
    with pytest.raises(ContextError, match="^context build failed$"):
        ContextBuilder().build(Incident.model_validate(raw))


def test_bypassed_dto_validation_does_not_leak_serializer_warnings(capsys):
    poisoned = PodSnapshot.model_construct(
        **{**fixture().model_dump(), "containers": f"password={CANARY}"}
    )
    with pytest.raises(DetectionError, match="^detection failed$"):
        DetectionEngine().detect([poisoned])
    incident = DetectionEngine().detect([fixture()])[0]
    malformed = Incident.model_construct(
        **{**incident.model_dump(), "evidence": f"password={CANARY}"}
    )
    with pytest.raises(ContextError, match="^context build failed$"):
        ContextBuilder().build(malformed)
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err
