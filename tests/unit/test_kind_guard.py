from __future__ import annotations

import base64
import copy
import hashlib
import json
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from aegisops.sources.env_guard import EnvironmentGuardError, ReaderIdentity, load_reader_identity

CANARY = "topsecretcanarytokenvalue12345"
pytestmark = pytest.mark.unit


def b64url(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()


@pytest.fixture
def valid_env(tmp_path: Path) -> dict[str, Any]:
    now = datetime(2026, 4, 18, 12, 0, 0, tzinfo=UTC)
    kind_dir = tmp_path / ".local" / "kind"
    kind_dir.mkdir(parents=True)
    ca_bytes = b"fake-ca-cert-payload-for-testing"
    (kind_dir / "ca.crt").write_bytes(ca_bytes)
    ca_hash = hashlib.sha256(ca_bytes).hexdigest()
    ca_b64 = base64.b64encode(ca_bytes).decode()
    claims = {
        "sub": "system:serviceaccount:aegisops-demo:aegisops-reader",
        "aud": ["https://kubernetes.default.svc"],
        "iat": int(now.timestamp()) - 10,
        "exp": int(now.timestamp()) + 600,
        "namespace": "aegisops-demo",
        "kubernetes.io": {
            "namespace": "aegisops-demo",
            "serviceaccount": {"name": "aegisops-reader"},
        },
    }
    fake_jwt = f"{b64url({'alg': 'RS256'})}.{b64url(claims)}.fake_sig_{CANARY}"
    state = {
        "cluster_name": "aegisops-demo",
        "context": "kind-aegisops-demo",
        "namespace": "aegisops-demo",
        "instance_id": "12345678-1234-1234-1234-123456789abc",
        "server": "https://127.0.0.1:6443",
        "ca_sha256": ca_hash,
        "node_image": f"kindest/node:v1.36.4@sha256:{'a' * 64}",
    }
    (kind_dir / "state.json").write_text(json.dumps(state))
    kcfg = {
        "apiVersion": "v1",
        "kind": "Config",
        "current-context": "kind-aegisops-demo",
        "clusters": [
            {
                "name": "kind-aegisops-demo",
                "cluster": {
                    "server": "https://127.0.0.1:6443",
                    "certificate-authority-data": ca_b64,
                },
            }
        ],
        "contexts": [
            {
                "name": "kind-aegisops-demo",
                "context": {
                    "cluster": "kind-aegisops-demo",
                    "user": "aegisops-reader",
                    "namespace": "aegisops-demo",
                },
            }
        ],
        "users": [{"name": "aegisops-reader", "user": {"token": fake_jwt}}],
        "preferences": {},
    }
    (kind_dir / "reader-kubeconfig.yaml").write_text(yaml.safe_dump(kcfg))
    return {
        "root": tmp_path,
        "now": now,
        "claims": claims,
        "state": state,
        "kcfg": kcfg,
        "token": fake_jwt,
        "ca_bytes": ca_bytes,
    }


def write_files(env: dict[str, Any]) -> None:
    kd = env["root"] / ".local" / "kind"
    (kd / "state.json").write_text(json.dumps(env["state"]))
    (kd / "reader-kubeconfig.yaml").write_text(yaml.safe_dump(env["kcfg"]))


def set_jwt(env: dict[str, Any], claims: dict[str, Any]) -> None:
    env["kcfg"]["users"][0]["user"]["token"] = (
        f"{b64url({'alg': 'RS256'})}.{b64url(claims)}.fake_sig_{CANARY}"
    )
    write_files(env)


def test_positive_load_and_secret_mask(
    valid_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KUBECONFIG", "/etc/kubernetes/admin.conf")
    identity = load_reader_identity(valid_env["root"], valid_env["now"])
    assert isinstance(identity, ReaderIdentity)
    assert CANARY not in repr(identity)
    assert CANARY not in repr(identity.token)
    assert identity.token.get_secret_value() == valid_env["token"]


def test_traceback_safety(valid_env: dict[str, Any]) -> None:
    valid_env["state"]["context"] = "bad-context"
    write_files(valid_env)
    try:
        load_reader_identity(valid_env["root"], valid_env["now"])
    except EnvironmentGuardError:
        tb = traceback.format_exc()
        assert CANARY not in tb
    else:
        pytest.fail("Expected EnvironmentGuardError")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda e: e["kcfg"].__setitem__("current-context", "wrong-context"),
        lambda e: e["kcfg"]["contexts"].append(copy.deepcopy(e["kcfg"]["contexts"][0])),
        lambda e: e["kcfg"]["clusters"].append(copy.deepcopy(e["kcfg"]["clusters"][0])),
        lambda e: (
            e["state"].__setitem__("server", "https://example.com:6443"),
            e["kcfg"]["clusters"][0]["cluster"].__setitem__("server", "https://example.com:6443"),
        ),
        lambda e: (
            e["state"].__setitem__("server", "http://127.0.0.1:6443"),
            e["kcfg"]["clusters"][0]["cluster"].__setitem__("server", "http://127.0.0.1:6443"),
        ),
        lambda e: (
            e["state"].__setitem__("server", "https://127.0.0.1"),
            e["kcfg"]["clusters"][0]["cluster"].__setitem__("server", "https://127.0.0.1"),
        ),
        lambda e: (
            e["state"].__setitem__("server", "https://127.0.0.1:99999"),
            e["kcfg"]["clusters"][0]["cluster"].__setitem__("server", "https://127.0.0.1:99999"),
        ),
        lambda e: e["kcfg"]["clusters"][0]["cluster"].__setitem__("insecure-skip-tls-verify", True),
        lambda e: e["kcfg"]["clusters"][0]["cluster"].__setitem__(
            "proxy-url", "http://127.0.0.1:8080"
        ),
        lambda e: e["kcfg"]["users"][0]["user"].__setitem__("exec", {"apiVersion": "v1"}),
        lambda e: e["kcfg"]["users"][0]["user"].__setitem__("auth-provider", {"name": "oidc"}),
        lambda e: e["kcfg"]["users"][0]["user"].__setitem__("client-certificate-data", "dGVzdA=="),
        lambda e: e["kcfg"]["users"][0]["user"].__setitem__("tokenFile", "/var/run/token"),
        lambda e: e["state"].__setitem__("ca_sha256", "0" * 64),
        lambda e: (e["root"] / ".local" / "kind" / "ca.crt").write_bytes(b"mismatch"),
        lambda e: e["state"].__setitem__("extra_unknown_key", 123),
    ],
)
def test_state_and_kubeconfig_negatives(valid_env: dict[str, Any], mutation: Any) -> None:
    mutation(valid_env)
    write_files(valid_env)
    with pytest.raises(EnvironmentGuardError):
        load_reader_identity(valid_env["root"], valid_env["now"])


@pytest.mark.parametrize("filename", ["state.json", "reader-kubeconfig.yaml", "ca.crt"])
def test_missing_files(valid_env: dict[str, Any], filename: str) -> None:
    (valid_env["root"] / ".local" / "kind" / filename).unlink()
    with pytest.raises(EnvironmentGuardError):
        load_reader_identity(valid_env["root"], valid_env["now"])


@pytest.mark.parametrize(
    "claim_mutation",
    [
        lambda c, n: c.__setitem__("exp", int(n.timestamp()) - 100),
        lambda c, n: c.__setitem__("exp", c["iat"] + 901),
        lambda c, n: c.__setitem__("sub", "system:serviceaccount:wrong:aegisops-reader"),
        lambda c, n: c.pop("aud"),
        lambda c, n: c.__setitem__("iat", True),
        lambda c, n: c.__setitem__("exp", False),
        lambda c, n: c["kubernetes.io"].__setitem__("namespace", "evil-namespace"),
        lambda c, n: c["kubernetes.io"]["serviceaccount"].__setitem__("name", "evil-sa"),
    ],
)
def test_token_claims_negatives(valid_env: dict[str, Any], claim_mutation: Any) -> None:
    c = copy.deepcopy(valid_env["claims"])
    claim_mutation(c, valid_env["now"])
    set_jwt(valid_env, c)
    with pytest.raises(EnvironmentGuardError):
        load_reader_identity(valid_env["root"], valid_env["now"])
