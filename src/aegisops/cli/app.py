"""Local L0 checks, fixture metadata and bounded read-only observation."""

from __future__ import annotations

import json
import os
import sys
from io import TextIOWrapper
from uuid import UUID

import typer
from pydantic import SecretStr

from aegisops.audit.base import AuditStoreError
from aegisops.audit.postgres import PostgresAuditStore
from aegisops.audit.reader import PostgresAuditReader
from aegisops.config import ConfigurationError, load_database_url
from aegisops.context.builder import ContextBuilder, ContextError
from aegisops.context.sanitizer import SanitizationError, sanitize_data
from aegisops.db.health import DatabaseHealthError, check_database
from aegisops.detection.engine import DetectionEngine, DetectionError
from aegisops.domain.models import CauseCategory, Origin, PodSnapshot
from aegisops.evaluation.suite import EvaluationError, evaluate_suite
from aegisops.feedback.models import Assessment, FeedbackError, IncidentOutcome
from aegisops.feedback.postgres import PostgresFeedbackStore
from aegisops.llm.base import LLMClient, LLMError
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient, UnavailableLLMClient
from aegisops.llm.routing import DataClass
from aegisops.llm.transport import DEFAULT_MODEL, OpenAICompatibleClient
from aegisops.pipeline.engine import Pipeline, PipelineError
from aegisops.review.service import ReviewError, ReviewService
from aegisops.sources.env_guard import EnvironmentGuardError, load_reader_identity
from aegisops.sources.kubernetes_readonly import CollectionError, KubernetesReadOnlySource
from aegisops.sources.pipeline import LocalKindSource, SyntheticBatchSource
from aegisops.sources.safe_client import ReadOnlyClient, ReadOnlyError
from aegisops.sources.synthetic import ScenarioError, ScenarioRepository, SyntheticSource

app = typer.Typer(
    name="aegisops",
    help="AegisOps local L0 prototype.",
    no_args_is_help=True,
    add_completion=False,
)

_OK_MESSAGE = "doctor: OK (synthetic mode; database ready; no LLM or Kubernetes required)"
scenario_app = typer.Typer(help="List handcrafted scenarios; never execute actions.")
app.add_typer(scenario_app, name="scenario")
audit_app = typer.Typer(help="Read bounded local audit records; no mutation commands.")
app.add_typer(audit_app, name="audit")
feedback_app = typer.Typer(help="Record local human assessments; never approve or execute actions.")
app.add_typer(feedback_app, name="feedback")


@feedback_app.command("add")
def feedback_add(
    run_id: str,
    reviewer: str = typer.Option(..., "--reviewer"),
    assessment: Assessment = typer.Option(..., "--assessment"),
    comment: str = typer.Option("", "--comment"),
    supersedes: str | None = typer.Option(None, "--supersedes"),
    confirmed_cause: CauseCategory | None = typer.Option(None, "--confirmed-cause"),
    incident_outcome: IncidentOutcome | None = typer.Option(None, "--outcome"),
    reference: str | None = typer.Option(None, "--reference"),
) -> None:
    """Append an assessment, or explicitly correct its current predecessor."""
    try:
        identifier = UUID(run_id)
        predecessor = UUID(supersedes) if supersedes is not None else None
        with PostgresFeedbackStore() as store:
            record = store.add(
                identifier,
                reviewer=reviewer,
                assessment=assessment,
                comment=comment,
                supersedes_id=predecessor,
                confirmed_cause=confirmed_cause,
                incident_outcome=incident_outcome,
                reference=reference,
            )
        output = record.model_dump_json(indent=2)
    except (ValueError, FeedbackError):
        typer.echo("feedback: FAILED (input, target, correction or durable storage rejected)")
        raise typer.Exit(code=1) from None
    typer.echo(output)


@feedback_app.command("list")
def feedback_list(
    run_id: str | None = typer.Option(None, "--run-id"),
    limit: int = typer.Option(25, "--limit", min=1, max=100),
) -> None:
    """List bounded assessment history, including superseded entries."""
    try:
        identifier = UUID(run_id) if run_id is not None else None
        with PostgresFeedbackStore() as store:
            records = store.list(run_id=identifier, limit=limit)
        output = json.dumps(
            [record.model_dump(mode="json") for record in records], ensure_ascii=False, indent=2
        )
    except (ValueError, FeedbackError):
        typer.echo("feedback: FAILED (input or local feedback read rejected)")
        raise typer.Exit(code=1) from None
    typer.echo(output)


@feedback_app.command("summary")
def feedback_summary(
    source: str = typer.Option("synthetic", "--source"),
    limit: int = typer.Option(100, "--limit", min=1, max=100),
) -> None:
    """Count latest current opinions by origin; this is not model accuracy."""
    try:
        if source not in {"synthetic", "local-kind"}:
            raise FeedbackError()
        origin = Origin.SYNTHETIC_FIXTURE if source == "synthetic" else Origin.LOCAL_KIND
        with PostgresFeedbackStore() as store:
            result = store.summary(origin=origin, limit=limit)
        output = result.model_dump_json(indent=2)
    except FeedbackError:
        typer.echo("feedback: FAILED (source or local summary read rejected)")
        raise typer.Exit(code=1) from None
    typer.echo(output)


@feedback_app.command("quality")
def feedback_quality(
    source: str = typer.Option("synthetic", "--source"),
    limit: int = typer.Option(100, "--limit", min=1, max=100),
) -> None:
    """Compare local labels with recorded diagnoses; no model quality or gate claim."""
    try:
        if source not in {"synthetic", "local-kind"}:
            raise FeedbackError()
        origin = Origin.SYNTHETIC_FIXTURE if source == "synthetic" else Origin.LOCAL_KIND
        with PostgresFeedbackStore() as store:
            result = store.quality(origin=origin, limit=limit)
        output = result.model_dump_json(indent=2)
    except FeedbackError:
        typer.echo("feedback: FAILED (source, labels or local comparison read rejected)")
        raise typer.Exit(code=1) from None
    typer.echo(output)


@app.command("run")
def run_pipeline(
    source: str = typer.Option("synthetic", "--source"),
    scenario: str | None = typer.Option(None, "--scenario"),
    llm: str = typer.Option("fake", "--llm"),
    model: str | None = typer.Option(None, "--model"),
    local_url: str | None = typer.Option(None, "--local-url"),
    data_class: DataClass | None = typer.Option(None, "--data-class"),
) -> None:
    """Run collection through durable audit; execution is unavailable."""
    try:
        if (
            source not in {"synthetic", "local-kind"}
            or source == "local-kind"
            and scenario is not None
        ):
            raise PipelineError()
        with PostgresAuditStore() as store:
            observation_source = (
                SyntheticBatchSource(scenario) if source == "synthetic" else LocalKindSource()
            )
            origin = Origin.SYNTHETIC_FIXTURE if source == "synthetic" else Origin.LOCAL_KIND
            pipeline = Pipeline(
                AnalysisEngine(_analysis_client(llm, model, local_url), data_class=data_class),
                store,
            )
            result = pipeline.run(observation_source, origin=origin)
        output = result.model_dump_json(indent=2)
    except (AuditStoreError, PipelineError, ReviewError, LLMError):
        typer.echo("run: FAILED (configuration, input or durable audit rejected)")
        raise typer.Exit(code=1) from None
    typer.echo(output)
    if result.status.value != "completed":
        raise typer.Exit(code=1)


@app.command("eval")
def evaluate() -> None:
    """Evaluate ten fixture runs and policy probes; fake model only."""
    try:
        with PostgresAuditStore() as store:
            result = evaluate_suite(store)
        output = result.model_dump_json(indent=2)
    except (AuditStoreError, EvaluationError, PipelineError, ReviewError, ScenarioError, LLMError):
        typer.echo("eval: FAILED (fixture or durable audit rejected)")
        raise typer.Exit(code=1) from None
    typer.echo(output)
    if not result.passed:
        raise typer.Exit(code=1)


@audit_app.command("list")
def audit_list(limit: int = typer.Option(25, "--limit", min=1, max=100)) -> None:
    try:
        with PostgresAuditReader() as reader:
            entries = reader.list(limit)
        output = json.dumps(
            [entry.model_dump(mode="json") for entry in entries], ensure_ascii=False, indent=2
        )
    except AuditStoreError:
        typer.echo("audit: FAILED (local audit read rejected)")
        raise typer.Exit(code=1) from None
    typer.echo(output)


@audit_app.command("show")
def audit_show(run_id: str) -> None:
    try:
        identifier = UUID(run_id)
        with PostgresAuditReader() as reader:
            record = reader.show(identifier)
        output = record.model_dump_json(indent=2)
    except (ValueError, AuditStoreError):
        typer.echo("audit: FAILED (record missing, invalid or local audit read rejected)")
        raise typer.Exit(code=1) from None
    typer.echo(output)


def _analysis_client(mode: str, model: str | None, local_url: str | None) -> LLMClient:
    if local_url is not None and mode != "local":
        raise LLMError()
    if mode == "fake":
        return FakeLLMClient()
    if mode == "none":
        return UnavailableLLMClient()
    if mode == "openrouter":
        raw_key = os.environ.get("AEGISOPS_LLM_API_KEY")
        if not raw_key:
            return UnavailableLLMClient()
        return OpenAICompatibleClient(model_name=model or DEFAULT_MODEL, api_key=SecretStr(raw_key))
    if mode == "local" and model:
        local_key = os.environ.get("AEGISOPS_LOCAL_LLM_API_KEY")
        return OpenAICompatibleClient(
            provider="local",
            model_name=model,
            endpoint=local_url or "http://127.0.0.1:11434/v1/chat/completions",
            api_key=SecretStr(local_key) if local_key else None,
        )
    raise LLMError()


@app.command()
def review(
    scenario: str = typer.Option("crashloop-oom", "--scenario"),
    llm: str = typer.Option("fake", "--llm"),
    model: str | None = typer.Option(None, "--model"),
    local_url: str | None = typer.Option(None, "--local-url"),
    data_class: DataClass | None = typer.Option(None, "--data-class"),
) -> None:
    """Review one synthetic incident, simulate a description and commit its audit."""
    try:
        with PostgresAuditStore() as store:
            snapshots = _detection_snapshots("synthetic", scenario)
            incidents = DetectionEngine().detect(snapshots)
            if len(incidents) != 1:
                raise ReviewError()
            service = ReviewService(
                AnalysisEngine(_analysis_client(llm, model, local_url), data_class=data_class),
                store,
            )
            record = service.review(incidents[0])
        output = record.model_dump_json(indent=2)
    except (AuditStoreError, ReviewError, ScenarioError, DetectionError, LLMError):
        typer.echo("review: FAILED (input, configuration or durable audit rejected)")
        raise typer.Exit(code=1) from None
    typer.echo(output)
    if record.status.value != "completed":
        raise typer.Exit(code=1)


@app.command()
def analyze(
    source: str = typer.Option("synthetic", "--source"),
    scenario: str | None = typer.Option(None, "--scenario"),
    llm: str = typer.Option("fake", "--llm"),
    model: str | None = typer.Option(None, "--model"),
    local_url: str | None = typer.Option(None, "--local-url"),
    data_class: DataClass | None = typer.Option(None, "--data-class"),
) -> None:
    """Produce advisory diagnoses and recommendations; fake replies are the default."""
    try:
        snapshots = _detection_snapshots(source, scenario)
        incidents = DetectionEngine().detect(snapshots)
        engine = AnalysisEngine(_analysis_client(llm, model, local_url), data_class=data_class)
        rows = []
        for incident in incidents:
            result = engine.analyze(incident, ContextBuilder().build(incident))
            rows.append(
                {
                    "incident_id": incident.incident_id,
                    "diagnosis": result.diagnosis.model_dump(mode="json"),
                    "recommendation": result.recommendation.model_dump(mode="json"),
                    "llm": result.metadata.model_dump(mode="json"),
                    "used_fallback": result.used_fallback,
                    "fallback_reason": result.fallback_reason,
                }
            )
        output = json.dumps(
            sanitize_data(
                {
                    "stage": "analysis",
                    "source": source,
                    "results": rows,
                    "execution_available": False,
                }
            ),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    except (
        LLMError,
        ScenarioError,
        EnvironmentGuardError,
        ReadOnlyError,
        CollectionError,
        DetectionError,
        ContextError,
        SanitizationError,
    ):
        typer.echo("analyze: FAILED (input or provider configuration rejected)")
        raise typer.Exit(code=1) from None
    typer.echo(output)


def _detection_snapshots(source: str, scenario: str | None) -> list[PodSnapshot]:
    if source == "synthetic":
        repository = ScenarioRepository()
        identifiers = (scenario,) if scenario is not None else repository.list_ids()
        return [
            snapshot
            for identifier in identifiers
            for snapshot in SyntheticSource(repository, identifier).collect()
        ]
    if source != "local-kind" or scenario is not None:
        raise DetectionError()
    reader = ReadOnlyClient(load_reader_identity())
    try:
        return KubernetesReadOnlySource(reader, include_logs=True).collect()
    finally:
        reader.close()


@app.command()
def detect(
    source: str = typer.Option("synthetic", "--source"),
    scenario: str | None = typer.Option(None, "--scenario"),
    show_context: bool = typer.Option(False, "--show-context"),
) -> None:
    """Detect CrashLoopBackOff and build sanitized evidence without a model call."""
    try:
        snapshots = _detection_snapshots(source, scenario)
        incidents = DetectionEngine().detect(snapshots)
        builder = ContextBuilder()
        summaries = []
        for incident in incidents:
            context = builder.build(incident)
            row: dict[str, object] = {
                "incident_id": incident.incident_id,
                "workload": incident.workload_name,
                "pod": incident.pod_name,
                "container": incident.container_name,
                "evidence_count": len(context.evidence),
                "context_hash": context.context_hash,
            }
            if show_context:
                row["context"] = context.model_dump(mode="json")
            summaries.append(row)
        projection = sanitize_data(
            {
                "stage": "detection",
                "source": source,
                "snapshots": len(snapshots),
                "incidents": summaries,
                "execution_available": False,
            }
        )
        output = json.dumps(projection, ensure_ascii=False, indent=2, allow_nan=False)
    except (
        ScenarioError,
        EnvironmentGuardError,
        ReadOnlyError,
        CollectionError,
        DetectionError,
        ContextError,
        SanitizationError,
    ):
        typer.echo("detect: FAILED (observation or context rejected)")
        raise typer.Exit(code=1) from None
    typer.echo(output)


@app.command()
def observe(include_logs: bool = typer.Option(False, "--include-logs")) -> None:
    """Collect from the fixed local kind; display aggregate counts only."""
    try:
        reader = ReadOnlyClient(load_reader_identity())
        try:
            snapshots = KubernetesReadOnlySource(reader, include_logs=include_logs).collect()
        finally:
            reader.close()
    except (EnvironmentGuardError, ReadOnlyError, CollectionError):
        typer.echo("observe: FAILED (local kind collection rejected)")
        raise typer.Exit(code=1) from None
    projection = {
        "origin": "local_kind",
        "snapshots": len(snapshots),
        "containers": sum(len(snapshot.containers) for snapshot in snapshots),
        "events": sum(len(snapshot.events) for snapshot in snapshots),
        "log_tails": sum(snapshot.log_tail is not None for snapshot in snapshots),
        "execution_available": False,
    }
    typer.echo(json.dumps(projection, indent=2))


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
    try:
        clean = sanitize_data(projection)
    except SanitizationError:
        typer.echo("scenario: invalid projection")
        raise typer.Exit(code=1) from None
    typer.echo(json.dumps(clean, ensure_ascii=False, indent=2))
