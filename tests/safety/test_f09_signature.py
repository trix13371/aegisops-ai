"""Authentication and scope remain required even for structurally valid packets."""

import json
from datetime import timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aegisops.telemetry.contracts import MAX_PACKET_BYTES, SignedTelemetryBatch, TelemetryError
from aegisops.telemetry.files import packet_path, read_packet, write_new
from aegisops.telemetry.verification import require_fresh, signing_bytes, verify_packet

pytestmark = pytest.mark.safety


def test_public_verification_matches_signed_packet(telemetry_case):
    case = telemetry_case
    packet = case.make()
    reference = verify_packet(packet, case.trust)
    assert reference.batch_id == packet.body.batch_id and reference.key_id == case.trust.key_id
    assert reference.registration_id == case.trust.registration_id
    require_fresh(packet.body, case.now)


@pytest.mark.parametrize("fault", ["signature", "other_key", "registration", "key_id", "content"])
def test_bad_signature_or_scope_is_rejected(telemetry_case, fault):
    case = telemetry_case
    packet = case.make()
    if fault == "signature":
        packet = SignedTelemetryBatch(body=packet.body, signature="0" * 128)
    elif fault == "other_key":
        packet = SignedTelemetryBatch(
            body=packet.body,
            signature=Ed25519PrivateKey.generate().sign(signing_bytes(packet.body)).hex(),
        )
    elif fault == "registration":
        packet = case.make(registration_id=uuid4())
    elif fault == "key_id":
        packet = case.make(key_id="0" * 64)
    else:
        raw = packet.body.model_dump()
        raw["snapshots"] = ()
        packet = SignedTelemetryBatch(body=raw, signature=packet.signature)
    with pytest.raises(TelemetryError, match="^local telemetry handoff rejected$"):
        verify_packet(packet, case.trust)


@pytest.mark.parametrize(
    "offset", [timedelta(microseconds=-1), timedelta(minutes=5), timedelta(days=1)]
)
def test_freshness_has_no_grace_after_expiry(telemetry_case, offset):
    case = telemetry_case
    with pytest.raises(TelemetryError):
        require_fresh(case.make().body, case.now + offset)


def test_signed_unsanitized_log_is_still_rejected(telemetry_case):
    case = telemetry_case
    raw = case.snapshot.model_dump()
    raw["log_tail"] = "password=DEMO_CANARY_NOT_A_REAL_CREDENTIAL"
    packet = case.make(snapshots=(raw,))
    with pytest.raises(TelemetryError):
        verify_packet(packet, case.trust)


@pytest.mark.parametrize("fault", ["duplicate_keys", "oversize", "wrong_filename", "malformed"])
def test_file_boundary_rejects_before_processing(telemetry_case, fault):
    case = telemetry_case
    packet = case.make()
    text = packet.model_dump_json()
    identifier = packet.body.batch_id
    if fault == "duplicate_keys":
        text = '{"body":{},"body":{}}'
    elif fault == "oversize":
        text = " " * (MAX_PACKET_BYTES + 1)
    elif fault == "wrong_filename":
        identifier = uuid4()
    else:
        text = "not JSON"
    write_new(packet_path(case.root, identifier), text.encode())
    with pytest.raises(TelemetryError):
        read_packet(case.root, identifier)


def test_sample_contract_signature_is_consistent():
    from pathlib import Path

    from aegisops.telemetry.contracts import CollectorTrust

    root = Path(__file__).resolve().parents[2] / "examples/contracts"
    packet = SignedTelemetryBatch.model_validate(
        json.loads((root / "SignedTelemetryBatch.json").read_text())
    )
    trust = CollectorTrust.model_validate(json.loads((root / "CollectorTrust.json").read_text()))
    verify_packet(packet, trust)
