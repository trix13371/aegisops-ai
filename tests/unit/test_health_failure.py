"""No raw driver diagnostics may escape the public health error."""

import traceback
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from aegisops.db import health

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("during_creation", [True, False])
def test_database_exception_is_safe_and_engine_disposed(
    monkeypatch: pytest.MonkeyPatch, during_creation: bool
) -> None:
    canary = "synthetic_sensitive_connection_value"
    engine = MagicMock()
    if during_creation:

        def fail_engine(url: str) -> None:
            raise SQLAlchemyError(canary)

        monkeypatch.setattr(health, "_engine", fail_engine)
    else:
        engine.connect.side_effect = SQLAlchemyError(canary)
        monkeypatch.setattr(health, "_engine", lambda url: engine)
    with pytest.raises(health.DatabaseHealthError) as failure:
        health.check_database("unused-local-test-input")
    assert canary not in "".join(traceback.format_exception(failure.value))
    if not during_creation:
        engine.dispose.assert_called_once()
