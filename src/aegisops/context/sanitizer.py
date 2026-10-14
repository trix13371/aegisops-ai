"""Bounded deterministic secret redaction for the AegisOps S0-05 local L0 prototype.

This module performs *heuristic* redaction of common credential shapes in text and
JSON-serializable structures. It is NOT a guarantee: arbitrary, encoded, split,
or unknown secret formats may survive. Prompt-injection text is treated purely as
data and is never interpreted or executed.

All operations are deterministic, idempotent, and bounded. No I/O, network,
subprocess, logging, or LLM calls are performed.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from typing import Final

from aegisops.domain.common import Scalar

__all__ = [
    "REDACTED",
    "SanitizationError",
    "bound_log",
    "sanitize_attributes",
    "sanitize_data",
    "sanitize_text",
]

REDACTED: Final[str] = "[REDACTED]"

_MAX_TEXT_CHARS: Final[int] = 65536
_MAX_SCALAR_CHARS: Final[int] = 512
_MAX_DEPTH: Final[int] = 16
_MAX_NODES: Final[int] = 10000
_MAX_BYTES: Final[int] = 16384
_MAX_LINES: Final[int] = 100

_ERR_TEXT: Final[str] = "sanitization failed"
_ERR_BOUND: Final[str] = "invalid bound"
_ERR_ATTRS: Final[str] = "invalid attributes"
_ERR_DATA: Final[str] = "invalid data"


class SanitizationError(ValueError):
    """A rejected input; the message contains no source data."""


_CRED_NAMES: Final[frozenset[str]] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "auth_token",
        "bearer_token",
        "token",
        "client_secret",
        "private_key",
        "database_url",
        "connection_string",
        "dsn",
    }
)

_CRED_SUFFIXES: Final[tuple[str, ...]] = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "auth_token",
    "bearer_token",
    "token",
    "client_secret",
    "private_key",
    "database_url",
    "connection_string",
    "dsn",
)

_CONNECTION_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?<![A-Za-z0-9_-])(?P<quote>["']?)"""
    r"(?P<key>(?:[A-Za-z0-9_-]+[_-])?(?:database[_-]url|connection[_-]string|dsn))"
    r"""(?P=quote)(?P<sep>\s*[:=]\s*)"""
    r"""(?P<val>"(?:[^"\\]|\\.)*(?:"|\Z)|'(?:[^'\\]|\\.)*(?:'|\Z)|[^\r\n]+)""",
    re.IGNORECASE,
)

_PEM_RE: Final[re.Pattern[str]] = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
    r"[\s\S]*?"
    r"(?:-----END (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----|\Z)",
)

_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]*://[^\s/?#@]+@[^\s]+",
)

_ASSIGN_RE: Final[re.Pattern[str]] = re.compile(
    r"""(?<![A-Za-z0-9_-])(?P<quote>["']?)(?P<key>[A-Za-z_][A-Za-z0-9_\-]*)(?P=quote)"""
    r"(?P<sep>\s*[:=]\s*)"
    r"""(?P<val>"(?:[^"\\]|\\.)*(?:"|\Z)|'(?:[^'\\]|\\.)*(?:'|\Z)|[^\s,;]+)""",
)

_BARE_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:sk-[A-Za-z0-9_\-]{8,}"
    r"|ghp_[A-Za-z0-9]{8,}"
    r"|github_pat_[A-Za-z0-9_]{8,}"
    r"|(?:AKIA|ASIA)[A-Z0-9]{12,}"
    r"|ya29\.[A-Za-z0-9_\-]{8,})\b",
)

_AUTH_RE: Final[re.Pattern[str]] = re.compile(
    r"""\b(?:Bearer|Basic)\s+[^\s,"'<>]+""",
    re.IGNORECASE,
)

_JWT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]*"
    r"(?![A-Za-z0-9_-])",
)


def _is_credential_name(name: str) -> bool:
    low = name.strip().lower().replace("-", "_")
    if low in _CRED_NAMES:
        return True
    return any(low.endswith(suffix) for suffix in _CRED_SUFFIXES)


def _decode_segment(segment: str) -> object:
    pad = "=" * (-len(segment) % 4)
    raw = base64.urlsafe_b64decode(segment + pad)
    return json.loads(raw.decode("utf-8"))


def _is_jwt(candidate: str) -> bool:
    parts = candidate.split(".")
    if len(parts) != 3:
        return False
    try:
        header = _decode_segment(parts[0])
        payload = _decode_segment(parts[1])
    except (ValueError, binascii.Error, UnicodeDecodeError):
        return False
    if not isinstance(header, dict) or not isinstance(payload, dict):
        return False
    return "alg" in header or "typ" in header


def _redact_jwt(match: re.Match[str]) -> str:
    return REDACTED if _is_jwt(match.group(0)) else match.group(0)


def _redact_assignment(match: re.Match[str]) -> str:
    key = match.group("key")
    if not _is_credential_name(key):
        return match.group(0)
    quote = match.group("quote")
    val = match.group("val")
    value_quote = val[0] if val[0] in "\"'" else ""
    return f"{quote}{key}{quote}{match.group('sep')}{value_quote}{REDACTED}{value_quote}"


def _redact_text(text: str) -> str:
    out = _PEM_RE.sub(REDACTED, text)
    out = _CONNECTION_RE.sub(_redact_assignment, out)
    out = _URL_RE.sub(REDACTED, out)
    out = _ASSIGN_RE.sub(_redact_assignment, out)
    out = _AUTH_RE.sub(REDACTED, out)
    out = _BARE_RE.sub(REDACTED, out)
    out = _JWT_RE.sub(_redact_jwt, out)
    return out


def sanitize_text(text: str) -> str:
    """Redact heuristic credential shapes from ``text``.

    Accepts exact ``str`` only and rejects inputs longer than 65536 characters
    before any processing. Deterministic and idempotent.
    """
    if type(text) is not str:
        raise SanitizationError(_ERR_TEXT) from None
    if len(text) > _MAX_TEXT_CHARS:
        raise SanitizationError(_ERR_TEXT) from None
    try:
        text.encode("utf-8")
        return _redact_text(text)
    except SanitizationError:
        raise
    except Exception:
        raise SanitizationError(_ERR_TEXT) from None


def _bound_scalar(value: Scalar) -> Scalar:
    if isinstance(value, str):
        redacted = sanitize_text(value)
        if len(redacted) > _MAX_SCALAR_CHARS:
            return redacted[:_MAX_SCALAR_CHARS]
        return redacted
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise SanitizationError(_ERR_ATTRS) from None


def sanitize_attributes(values: dict[str, Scalar]) -> dict[str, Scalar]:
    """Sanitize attribute keys and values, masking sensitive credential values."""
    if not isinstance(values, dict) or len(values) > 16:
        raise SanitizationError(_ERR_ATTRS) from None
    try:
        out: dict[str, Scalar] = {}
        for key, value in values.items():
            if type(key) is not str:
                raise SanitizationError(_ERR_ATTRS) from None
            new_key = sanitize_text(key)
            if not new_key.strip() or len(new_key) > 64 or new_key in out:
                raise SanitizationError(_ERR_ATTRS) from None
            if _is_credential_name(key):
                out[new_key] = REDACTED
            else:
                out[new_key] = _bound_scalar(value)
        return out
    except SanitizationError:
        raise
    except Exception:
        raise SanitizationError(_ERR_ATTRS) from None


def _sanitize_node(value: object, depth: int, budget: list[int]) -> object:
    budget[0] -= 1
    if budget[0] < 0:
        raise SanitizationError(_ERR_DATA) from None
    if depth > _MAX_DEPTH:
        raise SanitizationError(_ERR_DATA) from None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise SanitizationError(_ERR_DATA) from None
        return value
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, item in value.items():
            budget[0] -= 1
            if budget[0] < 0:
                raise SanitizationError(_ERR_DATA) from None
            if type(key) is not str:
                raise SanitizationError(_ERR_DATA) from None
            new_key = sanitize_text(key)
            if new_key in out:
                raise SanitizationError(_ERR_DATA) from None
            if _is_credential_name(key):
                out[new_key] = REDACTED
            else:
                out[new_key] = _sanitize_node(item, depth + 1, budget)
        return out
    if isinstance(value, (list, tuple)):
        return [_sanitize_node(item, depth + 1, budget) for item in value]
    raise SanitizationError(_ERR_DATA) from None


def sanitize_data(value: object) -> object:
    """Recursively sanitize nested JSON-serializable data with bounded depth."""
    try:
        return _sanitize_node(value, 0, [_MAX_NODES])
    except SanitizationError:
        raise
    except Exception:
        raise SanitizationError(_ERR_DATA) from None


def bound_log(text: str, *, max_bytes: int, max_lines: int) -> str:
    """Redact the whole text, then tail-bound it by lines and UTF-8 bytes."""
    if type(text) is not str:
        raise SanitizationError(_ERR_BOUND) from None
    if type(max_bytes) is not int or type(max_lines) is not int:
        raise SanitizationError(_ERR_BOUND) from None
    if not 1 <= max_bytes <= _MAX_BYTES:
        raise SanitizationError(_ERR_BOUND) from None
    if not 1 <= max_lines <= _MAX_LINES:
        raise SanitizationError(_ERR_BOUND) from None
    try:
        redacted = sanitize_text(text)
        lines = redacted.splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        joined = "\n".join(lines)
        encoded = joined.encode("utf-8")
        if len(encoded) > max_bytes:
            encoded = encoded[-max_bytes:]
            while encoded:
                try:
                    return encoded.decode("utf-8")
                except UnicodeDecodeError:
                    encoded = encoded[1:]
            return ""
        return joined
    except SanitizationError:
        raise
    except Exception:
        raise SanitizationError(_ERR_BOUND) from None
