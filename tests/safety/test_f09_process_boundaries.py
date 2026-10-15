"""Fresh process import graphs and environment stripping, without live services."""

import json
import os
import subprocess  # noqa: TID251 -- test-only child processes for entry-point isolation
import sys

import pytest

pytestmark = pytest.mark.safety


@pytest.mark.parametrize("role", ["collector", "controlplane"])
def test_standalone_process_has_only_its_role_imports_and_environment(role):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("AEGISOPS_", "OPENAI_", "OPENROUTER_", "PG"))
        and key.upper() != "KUBECONFIG"
    }
    for name in (
        "AEGISOPS_LLM_API_KEY",
        "AEGISOPS_RUNTIME_LLM_API_KEY",
        "AEGISOPS_MIGRATION_URL",
        "AEGISOPS_DATABASE_URL",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "PGPASSWORD",
        "KUBECONFIG",
    ):
        environment[name] = "ROLE_ENV_CANARY"
    code = f"""
import json, os, sys
from aegisops.{role} import cli
def inspect():
    print(json.dumps({{"kubernetes": "kubernetes" in sys.modules,
        "sqlalchemy": "sqlalchemy" in sys.modules,
        "llm": any(name.startswith("aegisops.llm") for name in sys.modules),
        "collector_signer": "aegisops.collector.signing" in sys.modules,
        "retained_sensitive_names": sorted(
            key for key, value in os.environ.items() if value == "ROLE_ENV_CANARY")}}))
cli.app = inspect
cli.main()
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )  # noqa: S603
    assert result.returncode == 0 and "ROLE_ENV_CANARY" not in result.stdout + result.stderr
    report = json.loads(result.stdout)
    if role == "collector":
        assert report["kubernetes"] is True
        assert report["sqlalchemy"] is report["llm"] is False
        assert report["retained_sensitive_names"] == []
    else:
        assert report["kubernetes"] is report["collector_signer"] is False
        assert report["sqlalchemy"] is True
        assert report["retained_sensitive_names"] == ["AEGISOPS_DATABASE_URL"]
