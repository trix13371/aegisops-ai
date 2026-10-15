"""The reader closes before signing, and no raw canary reaches the spool."""

from types import SimpleNamespace

import pytest

import aegisops.collector.service as module
from aegisops.domain.models import PodSnapshot
from aegisops.telemetry.contracts import TelemetryError
from aegisops.telemetry.files import packet_path
from aegisops.telemetry.verification import verify_packet

pytestmark = [pytest.mark.unit, pytest.mark.safety]


@pytest.mark.parametrize("failure", [None, "source", "registration"])
def test_reader_lifecycle_and_redaction_before_persistence(telemetry_case, monkeypatch, failure):
    case = telemetry_case
    reader = SimpleNamespace(closed=False)
    reader.close = lambda: setattr(reader, "closed", True)
    registration = (
        "wrong-registration" if failure == "registration" else str(case.trust.registration_id)
    )
    monkeypatch.setattr(
        module, "load_reader_identity", lambda root: SimpleNamespace(instance_id=registration)
    )
    calls = []

    def open_reader(identity):
        calls.append(identity)
        return reader

    class Source:
        def __init__(self, client, *, include_logs):
            assert client is reader and include_logs

        def collect(self):
            if failure == "source":
                raise RuntimeError("password=DO_NOT_OUTPUT")
            raw = case.snapshot.model_dump()
            raw["log_tail"] = "password=DEMO_CANARY_NOT_A_REAL_CREDENTIAL"
            return [PodSnapshot.model_validate(raw)]

    monkeypatch.setattr(module, "ReadOnlyClient", open_reader)
    monkeypatch.setattr(module, "KubernetesReadOnlySource", Source)
    original = module.seal

    def seal(body, trust, root):
        assert reader.closed is True
        return original(body, trust, root)

    monkeypatch.setattr(module, "seal", seal)
    if failure:
        with pytest.raises(TelemetryError) as error:
            module.collect_batch(case.root)
        assert str(error.value) == "local telemetry handoff rejected"
        assert reader.closed is (failure == "source")
        assert len(calls) == int(failure == "source")
    else:
        packet = module.collect_batch(case.root)
        verify_packet(packet, case.trust)
        saved = packet_path(case.root, packet.body.batch_id).read_text(encoding="utf-8")
        assert "DEMO_CANARY_NOT_A_REAL_CREDENTIAL" not in saved
        assert "[REDACTED]" in saved and reader.closed is True


def test_unenrolled_collector_does_not_open_cluster(tmp_path, monkeypatch):
    def forbidden(*args):
        pytest.fail("Unenrolled collector tried reading credentials")

    monkeypatch.setattr(module, "load_reader_identity", forbidden)
    with pytest.raises(TelemetryError):
        module.collect_batch(tmp_path)
