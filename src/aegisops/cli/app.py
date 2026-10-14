"""Typer CLI for AegisOps S0-01.

Only the read-only ``doctor`` command is exposed. No apply, execute, fix,
or cluster options exist. No LLM or Kubernetes configuration is read.
"""

from __future__ import annotations

import json
import sys
from io import TextIOWrapper

import typer

from aegisops.config import ConfigurationError, load_database_url
from aegisops.db.health import DatabaseHealthError, check_database
from aegisops.sources.env_guard import EnvironmentGuardError, load_reader_identity
from aegisops.sources.safe_client import ReadOnlyClient, ReadOnlyError
from aegisops.sources.synthetic import ScenarioError, ScenarioRepository

app = typer.Typer(
    name="aegisops",
    help="AegisOps local L0 prototype (S0-01/S0-02).",
    no_args_is_help=True,
    add_completion=False,
)

_OK_MESSAGE = "doctor: OK (synthetic mode; database ready; no LLM or Kubernetes required)"
scenario_app = typer.Typer(help="List handcrafted scenarios; never execute actions.")
app.add_typer(scenario_app, name="scenario")


@app.callback()
def _root() -> None:
    """AegisOps local L0 prototype; stable UTF-8 for Windows pipes."""
    if isinstance(sys.stdout, TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")


@app.command()
def doctor(source: str = typer.Option("synthetic", "--source")) -> None:
    """Run read-only local database health checks."""

    if source not in {"synthetic", "local-kind"}:
        typer.echo("doctor: unsupported source")
        raise typer.Exit(code=1)
    try:
        url = load_database_url()
        check_database(url)
        if source == "local-kind":
            reader = ReadOnlyClient(load_reader_identity())
            try:
                reader.verify_environment()
            finally:
                reader.close()
    except ConfigurationError as exc:
        typer.echo(f"doctor: FAILED ({exc})")
        raise typer.Exit(code=1)
    except DatabaseHealthError as exc:
        typer.echo(f"doctor: FAILED ({exc})")
        raise typer.Exit(code=1)
    except (EnvironmentGuardError, ReadOnlyError):
        typer.echo("doctor: FAILED (local kind environment rejected)")
        raise typer.Exit(code=1) from None

    typer.echo(
        _OK_MESSAGE
        if source == "synthetic"
        else "doctor: OK (local-kind; marker verified; read-only identity)"
    )


@scenario_app.command("list")
def scenario_list() -> None:
    try:
        for identifier in ScenarioRepository().list_ids():
            typer.echo(identifier)
    except ScenarioError:
        typer.echo("scenario: invalid fixtures")
        raise typer.Exit(code=1) from None


@scenario_app.command("show")
def scenario_show(identifier: str) -> None:
    """Show metadata and statuses; raw log/event text is never displayed."""
    try:
        spec, snapshot = ScenarioRepository().load(identifier)
    except ScenarioError:
        typer.echo("scenario: invalid fixture or identifier")
        raise typer.Exit(code=1) from None
    projection = {
        "scenario": spec.model_dump(mode="json"),
        "origin": snapshot.origin.value,
        "observed_at": snapshot.observed_at.isoformat(),
        "containers": [
            {
                "name": item.name,
                "waiting_reason": item.waiting_reason,
                "restart_count": item.restart_count,
            }
            for item in snapshot.containers
        ],
        "execution_available": False,
    }
    typer.echo(json.dumps(projection, ensure_ascii=False, indent=2))
