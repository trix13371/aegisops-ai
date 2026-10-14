"""Read-only loader for the local kind reader identity.

This module reads exactly three files from a fixed, contained directory
(``.local/kind``) beneath a trusted root.  It never consults environment
variables, ``KUBECONFIG``, or ``~/.kube``.  All failures surface as a single
fixed error message so that no credential material can leak through
chained exceptions or messages.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from pydantic import SecretStr

from aegisops.io import contained_file, read_json, read_yaml, unique_object

__all__ = [
    "EnvironmentGuardError",
    "ReaderIdentity",
    "load_reader_identity",
    "ensure_not_expired",
]

_ERROR: Final[str] = "local kind environment rejected"

_DEFAULT_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_KIND_DIR: Final[str] = ".local/kind"
_KUBECONFIG_NAME: Final[str] = "reader-kubeconfig.yaml"
_STATE_NAME: Final[str] = "state.json"
_CA_NAME: Final[str] = "ca.crt"

_CLUSTER_NAME: Final[str] = "aegisops-demo"
_CONTEXT_NAME: Final[str] = "kind-aegisops-demo"
_NAMESPACE: Final[str] = "aegisops-demo"
_USER_NAME: Final[str] = "aegisops-reader"
_SUBJECT: Final[str] = "system:serviceaccount:aegisops-demo:aegisops-reader"

_MAX_CA_BYTES: Final[int] = 32 * 1024
_MAX_TOKEN_LIFETIME: Final[int] = 900
_CLOCK_SKEW: Final[timedelta] = timedelta(seconds=5)

_STATE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "cluster_name",
        "context",
        "namespace",
        "instance_id",
        "server",
        "ca_sha256",
        "node_image",
    }
)
_KUBECONFIG_KEYS: Final[frozenset[str]] = frozenset(
    {
        "apiVersion",
        "kind",
        "clusters",
        "contexts",
        "current-context",
        "users",
        "preferences",
    }
)
_CLUSTER_KEYS: Final[frozenset[str]] = frozenset({"name", "cluster"})
_CLUSTER_INNER_KEYS: Final[frozenset[str]] = frozenset({"server", "certificate-authority-data"})
_CONTEXT_KEYS: Final[frozenset[str]] = frozenset({"name", "context"})
_CONTEXT_INNER_KEYS: Final[frozenset[str]] = frozenset({"cluster", "user", "namespace"})
_USER_KEYS: Final[frozenset[str]] = frozenset({"name", "user"})
_USER_INNER_KEYS: Final[frozenset[str]] = frozenset({"token"})

_UUID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_NODE_IMAGE_RE: Final[re.Pattern[str]] = re.compile(
    r"^kindest/node:v[0-9]+\.[0-9]+\.[0-9]+@sha256:[0-9a-f]{64}$"
)
_B64URL_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]+$")
_CONTROL_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x1f\x7f]")


def _reject() -> EnvironmentGuardError:
    return EnvironmentGuardError(_ERROR)


class EnvironmentGuardError(ValueError):
    """Raised for any rejected local kind environment."""


def _as_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _reject()
    for key in value:
        if not isinstance(key, str):
            raise _reject()
    return value


def _require_exact_keys(mapping: dict[str, Any], allowed: frozenset[str]) -> None:
    if set(mapping.keys()) != set(allowed):
        raise _reject()


def _require_subset_keys(mapping: dict[str, Any], allowed: frozenset[str]) -> None:
    if not set(mapping.keys()) <= set(allowed):
        raise _reject()


def _require_str(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise _reject()
    return value


def _require_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise _reject()
    return value


def _single_entry(entries: list[Any]) -> dict[str, Any]:
    if len(entries) != 1:
        raise _reject()
    return _as_mapping(entries[0])


def _decode_b64url(segment: str) -> bytes:
    if not _B64URL_RE.match(segment):
        raise _reject()
    padding = "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(segment + padding)
    except (binascii.Error, ValueError):
        raise _reject() from None


def _parse_claims(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        raise _reject()
    try:
        payload = _decode_b64url(parts[1])
        claims = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise _reject() from None
    return _as_mapping(claims)


def _require_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _reject()
    return int(value)


def _validate_audience(value: Any) -> None:
    if isinstance(value, str):
        if not value:
            raise _reject()
        return
    if isinstance(value, list):
        if not value:
            raise _reject()
        for item in value:
            if not isinstance(item, str) or not item:
                raise _reject()
        return
    raise _reject()


def _validate_token(token: str, now: datetime) -> datetime:
    claims = _parse_claims(token)
    if claims.get("sub") != _SUBJECT:
        raise _reject()
    if "namespace" in claims and claims["namespace"] != _NAMESPACE:
        raise _reject()
    _validate_audience(claims.get("aud"))
    kubernetes_claim = claims.get("kubernetes.io", {})
    if not isinstance(kubernetes_claim, dict):
        raise _reject()
    if kubernetes_claim.get("namespace", _NAMESPACE) != _NAMESPACE:
        raise _reject()
    account = kubernetes_claim.get("serviceaccount", {})
    if not isinstance(account, dict) or account.get("name", _USER_NAME) != _USER_NAME:
        raise _reject()
    iat = _require_int(claims.get("iat"))
    exp = _require_int(claims.get("exp"))
    if not 0 < exp - iat <= _MAX_TOKEN_LIFETIME:
        raise _reject()
    if iat > int(now.timestamp()) + int(_CLOCK_SKEW.total_seconds()):
        raise _reject()
    if exp <= int(now.timestamp()) + int(_CLOCK_SKEW.total_seconds()):
        raise _reject()
    return datetime.fromtimestamp(exp, tz=UTC)


def _validate_server(server: str) -> None:
    if _CONTROL_RE.search(server):
        raise _reject()
    try:
        parsed = urlsplit(server)
    except ValueError:
        raise _reject() from None
    if parsed.scheme != "https":
        raise _reject()
    if parsed.username is not None or parsed.password is not None:
        raise _reject()
    if parsed.query or parsed.fragment:
        raise _reject()
    if parsed.path not in ("", "/"):
        raise _reject()
    host = parsed.hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise _reject()
    try:
        port = parsed.port
    except ValueError:
        raise _reject() from None
    if port is None or not 1 <= port <= 65535:
        raise _reject()


def _validate_state(state: dict[str, Any]) -> tuple[str, str, str]:
    _require_exact_keys(state, _STATE_KEYS)
    if state["cluster_name"] != _CLUSTER_NAME:
        raise _reject()
    if state["context"] != _CONTEXT_NAME:
        raise _reject()
    if state["namespace"] != _NAMESPACE:
        raise _reject()
    instance_id = _require_str(state["instance_id"])
    if not _UUID_RE.match(instance_id):
        raise _reject()
    server = _require_str(state["server"])
    _validate_server(server)
    ca_sha256 = _require_str(state["ca_sha256"])
    if not _SHA256_RE.match(ca_sha256):
        raise _reject()
    node_image = _require_str(state["node_image"])
    if not _NODE_IMAGE_RE.match(node_image):
        raise _reject()
    return instance_id, server, ca_sha256


def _validate_kubeconfig(
    config: dict[str, Any], server: str, ca_sha256: str, ca_bytes: bytes
) -> str:
    _require_subset_keys(config, _KUBECONFIG_KEYS)
    _require_exact_keys(
        config, _KUBECONFIG_KEYS - {"preferences"}
    ) if "preferences" not in config else _require_exact_keys(config, _KUBECONFIG_KEYS)
    if config["apiVersion"] != "v1" or config["kind"] != "Config":
        raise _reject()
    if "preferences" in config:
        prefs = _as_mapping(config["preferences"])
        if prefs:
            raise _reject()
    if config["current-context"] != _CONTEXT_NAME:
        raise _reject()

    cluster = _single_entry(_require_list(config["clusters"]))
    _require_exact_keys(cluster, _CLUSTER_KEYS)
    if cluster["name"] != _CONTEXT_NAME:
        raise _reject()
    cluster_inner = _as_mapping(cluster["cluster"])
    _require_exact_keys(cluster_inner, _CLUSTER_INNER_KEYS)
    if cluster_inner["server"] != server:
        raise _reject()
    ca_data = _require_str(cluster_inner["certificate-authority-data"])
    try:
        decoded = base64.b64decode(ca_data, validate=True)
    except (binascii.Error, ValueError):
        raise _reject() from None
    if len(decoded) > _MAX_CA_BYTES:
        raise _reject()
    if hashlib.sha256(decoded).hexdigest() != ca_sha256:
        raise _reject()
    if decoded != ca_bytes:
        raise _reject()

    context = _single_entry(_require_list(config["contexts"]))
    _require_exact_keys(context, _CONTEXT_KEYS)
    if context["name"] != _CONTEXT_NAME:
        raise _reject()
    context_inner = _as_mapping(context["context"])
    _require_exact_keys(context_inner, _CONTEXT_INNER_KEYS)
    if context_inner["cluster"] != _CONTEXT_NAME:
        raise _reject()
    if context_inner["user"] != _USER_NAME:
        raise _reject()
    if context_inner["namespace"] != _NAMESPACE:
        raise _reject()

    user = _single_entry(_require_list(config["users"]))
    _require_exact_keys(user, _USER_KEYS)
    if user["name"] != _USER_NAME:
        raise _reject()
    user_inner = _as_mapping(user["user"])
    _require_exact_keys(user_inner, _USER_INNER_KEYS)
    return _require_str(user_inner["token"])


@dataclass(frozen=True)
class ReaderIdentity:
    """Validated, read-only identity for the local kind cluster."""

    server: str
    token: SecretStr
    ca_file: Path
    namespace: str
    instance_id: str
    expires_at: datetime


def _resolve_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(tz=UTC)
    if now.tzinfo is None:
        raise _reject()
    return now


def load_reader_identity(root: Path | None = None, now: datetime | None = None) -> ReaderIdentity:
    """Load and validate the local kind reader identity.

    ``root`` is a trusted developer/test override only; there is no CLI
    surface for it.  When omitted, the repository root is derived from this
    file's location.
    """
    try:
        base = _DEFAULT_ROOT if root is None else Path(root)
        kind_dir = base / _KIND_DIR
        kubeconfig_path = contained_file(kind_dir, _KUBECONFIG_NAME)
        state_path = contained_file(kind_dir, _STATE_NAME)
        ca_path = contained_file(kind_dir, _CA_NAME)

        state = _as_mapping(read_json(state_path))
        instance_id, server, ca_sha256 = _validate_state(state)

        with ca_path.open("rb") as stream:
            ca_bytes = stream.read(_MAX_CA_BYTES + 1)
        if len(ca_bytes) > _MAX_CA_BYTES:
            raise _reject()
        if hashlib.sha256(ca_bytes).hexdigest() != ca_sha256:
            raise _reject()

        config = _as_mapping(read_yaml(kubeconfig_path))
        token = _validate_kubeconfig(config, server, ca_sha256, ca_bytes)

        moment = _resolve_now(now)
        expires_at = _validate_token(token, moment)

        return ReaderIdentity(
            server=server.replace("https://localhost:", "https://127.0.0.1:"),
            token=SecretStr(token),
            ca_file=ca_path,
            namespace=_NAMESPACE,
            instance_id=instance_id,
            expires_at=expires_at,
        )
    except EnvironmentGuardError:
        raise _reject() from None
    except Exception:
        raise _reject() from None


def ensure_not_expired(identity: ReaderIdentity, now: datetime | None = None) -> None:
    """Raise if the identity token is expired or about to expire."""
    try:
        moment = _resolve_now(now)
        if identity.expires_at <= moment + _CLOCK_SKEW:
            raise _reject()
    except EnvironmentGuardError:
        raise _reject() from None
    except Exception:
        raise _reject() from None
