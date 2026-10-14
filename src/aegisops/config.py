"""Local configuration loading for AegisOps S0-01.

This module reads a single environment variable and validates that it points
at a strictly local PostgreSQL endpoint. It performs no network access and
never logs or prints configuration values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import unquote

from sqlalchemy.engine import URL, make_url

ENV_VAR_NAME = "AEGISOPS_DATABASE_URL"

_ALLOWED_DRIVER = "postgresql+psycopg"
_ALLOWED_HOSTS = frozenset({"127.0.0.1", "::1"})
_ALLOWED_DATABASES = frozenset({"aegisops_local", "aegisops_test"})
_ALLOWED_USERNAME = "aegisops_app"


def _has_control(value: str) -> bool:
    """Return True when the value contains CR, LF, or other control chars."""

    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


class ConfigurationError(ValueError):
    """Raised when local database configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Immutable settings for the local synthetic prototype."""

    database_url: str


def _fail(reason: str) -> ConfigurationError:
    """Build a safe error that never echoes configuration contents."""

    return ConfigurationError(f"invalid local database configuration: {reason}")


def _validate_url(raw: str) -> URL:
    """Validate the raw URL string and return the parsed SQLAlchemy URL."""

    if _has_control(raw) or _has_control(unquote(raw)):
        raise _fail("control characters are not permitted")

    try:
        url = make_url(raw)
    except Exception:
        raise _fail("could not be parsed") from None

    if url.drivername != _ALLOWED_DRIVER:
        raise _fail("driver must be postgresql+psycopg")

    if url.host is None or url.host not in _ALLOWED_HOSTS:
        raise _fail("host must be a loopback IP literal")

    if url.database is None or url.database not in _ALLOWED_DATABASES:
        raise _fail("database name is not in the local allowlist")

    if url.username != _ALLOWED_USERNAME:
        raise _fail("username is not the application role")

    if url.password is None or url.password == "":
        raise _fail("password is required")

    try:
        port = url.port
    except Exception:
        raise _fail("port must be present and within range") from None

    if port is None or not (1 <= port <= 65535):
        raise _fail("port must be present and within range")

    if url.query:
        raise _fail("query parameters are not permitted")

    return url


def load_database_url() -> str:
    """Load and validate the local database URL from the environment.

    Returns the original, unmodified URL string when it is valid. Raises
    :class:`ConfigurationError` when the variable is absent or invalid.
    """

    raw = os.environ.get(ENV_VAR_NAME)
    if raw is None or raw == "":
        raise ConfigurationError("local database not configured")

    return validate_database_url(raw)


def validate_database_url(raw: str) -> str:
    """Validate an explicitly supplied application URL without logging it."""
    if type(raw) is not str:
        raise _fail("URL must be text")
    _validate_url(raw)
    return raw


def load_settings() -> Settings:
    """Return validated settings for the local prototype."""

    return Settings(database_url=load_database_url())
