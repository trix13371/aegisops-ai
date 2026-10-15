"""Standalone consumer: no collector, Kubernetes SDK or signing-key reader imports."""

import os
from pathlib import Path
from uuid import UUID

import typer

from aegisops.audit.postgres import PostgresAuditStore
from aegisops.audit.reader import PostgresAuditReader
from aegisops.authorization.http import OpaClient
from aegisops.authorization.pep import DryRunPEP
from aegisops.controlplane.service import IngestionService
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import UnavailableLLMClient
from aegisops.llm.routing import DataClass
from aegisops.telemetry.files import load_trust, read_packet
from aegisops.telemetry.verification import verify_packet

app = typer.Typer(add_completion=False)


@app.callback()
def root_command() -> None:
    """Consume local signed observations; no Kubernetes reader or action executor."""


@app.command()
def consume(batch_id: UUID) -> None:
    """Verify a local signed batch and process it once through OPA and durable audit."""
    try:
        root = Path.cwd()
        packet = read_packet(root, batch_id)
        verify_packet(packet, load_trust(root))
        with PostgresAuditStore() as store, PostgresAuditReader() as reader:
            delivery = IngestionService(
                root,
                AnalysisEngine(UnavailableLLMClient(), data_class=DataClass.INTERNAL),
                store,
                reader,
                pep=DryRunPEP(OpaClient()),
            ).consume(packet)
        output = delivery.model_dump_json(indent=2)
    except Exception:
        typer.echo("control-plane: FAILED (packet, claim, policy or durable audit rejected)")
        raise typer.Exit(code=1) from None
    typer.echo(output)
    if delivery.receipt.status != "completed":
        raise typer.Exit(code=1)


def main() -> None:
    # Keep only the application DB URL; no migration/owner, cloud-model or kube credentials.
    for name in list(os.environ):
        upper = name.upper()
        if (
            upper.startswith(("AEGISOPS_", "PG", "OPENAI_", "OPENROUTER_"))
            and upper != "AEGISOPS_DATABASE_URL"
        ) or upper == "KUBECONFIG":
            os.environ.pop(name, None)
    app()


if __name__ == "__main__":
    main()
