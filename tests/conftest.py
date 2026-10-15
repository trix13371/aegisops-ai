"""Common minimal authorization inputs; no credentials or live clients."""

import importlib.util
from pathlib import Path

import pytest

from aegisops.context.builder import ContextBuilder
from aegisops.detection.engine import DetectionEngine
from aegisops.domain.authorization import AuthorizationControl
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.sources.synthetic import ScenarioRepository


@pytest.fixture
def tls_operator():
    spec = importlib.util.spec_from_file_location(
        "operator_tls", Path(__file__).resolve().parents[1] / "scripts/opa-tls-local.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def tls_root(tmp_path, tls_operator):
    root = tmp_path / "outputs/project"
    root.mkdir(parents=True)
    tls_operator.issue(root)
    return root


@pytest.fixture
def known_pki(tmp_path, tls_operator):
    from unittest.mock import patch

    from cryptography.hazmat.primitives.asymmetric import ec

    root = tmp_path / "identity with spaces/outputs/project"
    root.mkdir(parents=True)
    keys = [ec.generate_private_key(ec.SECP256R1()) for _ in range(3)]
    with patch.object(tls_operator.ec, "generate_private_key", side_effect=keys):
        tls_operator.issue(root)
    return root, keys[0]


@pytest.fixture
def authz_case():
    incident = DetectionEngine().detect([ScenarioRepository().load("crashloop-oom")[1]])[0]
    result = AnalysisEngine(FakeLLMClient()).analyze(incident, ContextBuilder().build(incident))
    return incident, result.recommendation


@pytest.fixture
def open_control():
    return AuthorizationControl(generation=1, global_blocked=False)


@pytest.fixture
def integrity_signer():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "test_local_signer", root / "scripts/audit-integrity-local.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def valid_policy_reader():
    """Ephemeral signed release for older PEP tests; never depends on operator files."""
    from datetime import timedelta
    from uuid import uuid4

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from aegisops.domain.policy_release import (
        PolicyReleaseBody,
        PolicyReleaseCheck,
        PolicyReleaseState,
        SignedPolicyRelease,
        release_bytes,
        release_hash,
        release_status,
    )
    from aegisops.integrity.chain import key_id
    from aegisops.integrity.models import TrustAnchor

    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw().hex()
    trust = TrustAnchor(public_key=public, key_id=key_id(public))
    cache = {}

    def reader(config, now):
        identity = (config.bundle_hash, config.catalog_hash)
        if identity not in cache:
            body = PolicyReleaseBody(
                release_id=uuid4(),
                revision=1,
                bundle_hash=config.bundle_hash,
                catalog_hash=config.catalog_hash,
                key_id=trust.key_id,
                issued_at=now - timedelta(minutes=1),
                expires_at=now + timedelta(hours=1),
            )
            signed = SignedPolicyRelease(body=body, signature=key.sign(release_bytes(body)).hex())
            state = PolicyReleaseState(
                generation=2,
                minimum_revision=1,
                active_release_hash=release_hash(signed),
                changed_at=body.issued_at,
            )
            cache[identity] = signed, state
        signed, state = cache[identity]
        status = release_status(
            signed,
            state,
            trust,
            now,
            bundle_hash=config.bundle_hash,
            catalog_hash=config.catalog_hash,
            policy_version=config.policy_version,
        )
        return PolicyReleaseCheck(
            checked_at=now, status=status, release=signed, state=state, trust=trust
        )

    return reader


@pytest.fixture
def policy_signer():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "test_policy_signer", root / "scripts/policy-release-local.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
