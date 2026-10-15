"""Standalone collector entry point. Never imports the all-in-one CLI."""

import json
import os
from pathlib import Path

import typer

from aegisops.collector.service import collect_batch

app = typer.Typer(add_completion=False)


@app.callback()
def root_command() -> None:
    """Local read-only collector; no analysis or database access."""


@app.command()
def collect() -> None:
    """Collect one bounded local-kind batch; print counters, never raw observations."""
    try:
        packet = collect_batch(Path.cwd())
    except Exception:
        typer.echo("collector: FAILED (identity, read-only collection or signed handoff rejected)")
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {
                "batch_id": str(packet.body.batch_id),
                "snapshot_count": len(packet.body.snapshots),
                "data_class": "internal",
                "execution_performed": False,
            }
        )
    )


def main() -> None:
    # Child process only: no DB/LLM credentials or ambient kubeconfig are inherited onward.
    for name in list(os.environ):
        if (
            name.upper().startswith(("AEGISOPS_", "PG", "OPENAI_", "OPENROUTER_"))
            or name.upper() == "KUBECONFIG"
        ):
            os.environ.pop(name, None)
    app()


if __name__ == "__main__":
    main()
