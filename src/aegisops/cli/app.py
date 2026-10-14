"""Typer CLI for AegisOps S0-01.

Only the read-only ``doctor`` command is exposed. No apply, execute, fix,
or cluster options exist. No LLM or Kubernetes configuration is read.
"""

from __future__ import annotations

import typer

from aegisops.config import ConfigurationError, load_database_url
from aegisops.db.health import DatabaseHealthError, check_database

app = typer.Typer(
    name="aegisops",
    help="AegisOps local synthetic prototype (S0-01).",
    no_args_is_help=True,
    add_completion=False,
)

_OK_MESSAGE = "doctor: OK (synthetic mode; database ready; no LLM or Kubernetes required)"


@app.callback()
def _root() -> None:
    """AegisOps local synthetic prototype (S0-01)."""


@app.command()
def doctor() -> None:
    """Run read-only local database health checks."""

    try:
        url = load_database_url()
        check_database(url)
    except ConfigurationError as exc:
        typer.echo(f"doctor: FAILED ({exc})")
        raise typer.Exit(code=1)
    except DatabaseHealthError as exc:
        typer.echo(f"doctor: FAILED ({exc})")
        raise typer.Exit(code=1)

    typer.echo(_OK_MESSAGE)
