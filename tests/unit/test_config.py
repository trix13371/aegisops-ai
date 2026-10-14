from __future__ import annotations

import traceback

import pytest

from aegisops.config import ENV_VAR_NAME, ConfigurationError, load_database_url

CANARY_PASS = "secret_canary_value"  # noqa: S105 - intentionally fake redaction fixture
pytestmark = pytest.mark.unit
BASE_URL = f"postgresql+psycopg://aegisops_app:{CANARY_PASS}@127.0.0.1:5432/aegisops_local"


def test_valid_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    for host in ("127.0.0.1", "[::1]"):
        for db in ("aegisops_local", "aegisops_test"):
            url = f"postgresql+psycopg://aegisops_app:{CANARY_PASS}@{host}:5432/{db}"
            monkeypatch.setenv(ENV_VAR_NAME, url)
            assert load_database_url() == url


def test_missing_or_blank_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_VAR_NAME, raising=False)
    with pytest.raises(ConfigurationError, match="local database not configured"):
        load_database_url()
    monkeypatch.setenv(ENV_VAR_NAME, "")
    with pytest.raises(ConfigurationError, match="local database not configured"):
        load_database_url()


@pytest.mark.parametrize(
    "invalid_url",
    [
        f"postgresql+psycopg://aegisops_app:{CANARY_PASS}@10.0.0.1:5432/aegisops_local",
        f"postgresql+asyncpg://aegisops_app:{CANARY_PASS}@127.0.0.1:5432/aegisops_local",
        f"postgresql+psycopg://wrong_user:{CANARY_PASS}@127.0.0.1:5432/aegisops_local",
        "postgresql+psycopg://aegisops_app:@127.0.0.1:5432/aegisops_local",
        f"postgresql+psycopg://aegisops_app:{CANARY_PASS}@127.0.0.1:5432/wrong_db",
        f"postgresql+psycopg://aegisops_app:{CANARY_PASS}@127.0.0.1/aegisops_local",
        f"postgresql+psycopg://aegisops_app:{CANARY_PASS}@127.0.0.1:99999/aegisops_local",
        f"{BASE_URL}?sslmode=require",
        f"{BASE_URL}\n",
        f"{BASE_URL}%0d",
        f"postgresql+psycopg://aegisops_app:{CANARY_PASS}@127.0.0.1:invalid/aegisops_local",
        f"malformed-url-{CANARY_PASS}",
        f"postgresql+psycopg://aegisops_app:{CANARY_PASS}@localhost:5432/aegisops_local",
    ],
)
def test_invalid_urls_and_redaction(monkeypatch: pytest.MonkeyPatch, invalid_url: str) -> None:
    monkeypatch.setenv(ENV_VAR_NAME, invalid_url)
    with pytest.raises(ConfigurationError) as exc_info:
        load_database_url()
    err_str = str(exc_info.value)
    assert CANARY_PASS not in err_str
    assert "10.0.0.1" not in err_str
    formatted = "".join(traceback.format_exception(exc_info.value))
    assert CANARY_PASS not in formatted
    assert invalid_url not in formatted
