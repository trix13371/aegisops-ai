"""Real SQL sampling, revision heads and snapshot isolation for label comparisons."""

import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, insert
from sqlalchemy.exc import DBAPIError
from typer.testing import CliRunner

from aegisops.actions.catalog import load_catalog
from aegisops.audit.postgres import PostgresAuditStore
from aegisops.cli.app import app
from aegisops.context.builder import ContextBuilder
from aegisops.db.models import RecommendationFeedback
from aegisops.detection.engine import DetectionEngine
from aegisops.domain.models import CauseCategory, Origin, PodSnapshot
from aegisops.feedback.models import Assessment, IncidentOutcome
from aegisops.feedback.postgres import PostgresFeedbackStore
from aegisops.feedback.quality_models import QualityReport
from aegisops.llm.base import LLMLimits
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.llm.prompt import build_request
from aegisops.review.service import ReviewService
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = [pytest.mark.integration, pytest.mark.safety]


@pytest.fixture
def url():
    value = os.environ.get("AEGISOPS_DATABASE_URL")
    if not value:
        pytest.skip("local PostgreSQL not configured")
    return value


def unique_target(url, origin=Origin.SYNTHETIC_FIXTURE):
    snapshot = ScenarioRepository().load("crashloop-oom")[1]
    reference = DetectionEngine().detect([snapshot])[0]
    request = build_request(reference, ContextBuilder().build(reference), load_catalog())
    draft = FakeLLMClient().generate_structured(request, LLMLimits()).data.model_dump(mode="json")
    raw = snapshot.model_dump()
    raw["owner"]["name"] = "quality-" + uuid4().hex[:16]
    raw["origin"] = origin
    incident = DetectionEngine().detect([PodSnapshot.model_validate(raw)])[0]
    context = ContextBuilder().build(incident)
    draft["diagnosis"]["hypotheses"][0]["supporting_evidence_ids"] = [
        context.evidence[0].evidence_id
    ]
    current = build_request(incident, context, load_catalog())
    client = FakeLLMClient({current.prompt_hash: draft})
    with PostgresAuditStore(url) as store:
        return ReviewService(AnalysisEngine(client), store).review(incident)


def add(store, record, reviewer="alice", **changes):
    values = dict(
        reviewer=reviewer,
        assessment=Assessment.USEFUL,
        confirmed_cause=CauseCategory.OOM_KILLED,
        incident_outcome=IncidentOutcome.RESOLVED,
        reference="TEST-PM-42",
    )
    values.update(changes)
    return store.add(record.run_id, **values)


def test_incomplete_group_cannot_claim_match_then_complete_sample_counts_once(url):
    record = unique_target(url)
    with PostgresFeedbackStore(url) as store:
        add(store, record)
        add(store, record, "bob")
        partial = store.quality(origin=Origin.SYNTHETIC_FIXTURE, limit=1)
        assert partial.has_more and partial.incomplete == 1
        assert partial.matches == partial.resolved_incidents == 0
        complete = store.quality(origin=Origin.SYNTHETIC_FIXTURE, limit=2)
        assert complete.matches == 1 and complete.sample_size == 2
        assert len(complete.cases) == 1 and complete.resolved_incidents == 1
        add(store, record, "charlie")
        assert store.quality(origin=Origin.SYNTHETIC_FIXTURE, limit=2).incomplete == 1


def test_conflict_resolution_uses_only_current_correction_leaves(url):
    record = unique_target(url)
    with PostgresFeedbackStore(url) as store:
        add(store, record)
        wrong = add(store, record, "bob", confirmed_cause=CauseCategory.INVALID_COMMAND)
        assert store.quality(origin=Origin.SYNTHETIC_FIXTURE, limit=2).conflicts == 1
        correction = add(store, record, "bob", supersedes_id=wrong.feedback_id)
        report = store.quality(origin=Origin.SYNTHETIC_FIXTURE, limit=2)
        assert report.matches == 1 and report.conflicts == report.incomplete == 0
        assert correction.feedback_id in report.cases[0].feedback_ids
        assert wrong.feedback_id not in report.cases[0].feedback_ids


def test_origin_filter_does_not_mix_labelled_cases(url):
    synthetic, local = unique_target(url), unique_target(url, Origin.LOCAL_KIND)
    with PostgresFeedbackStore(url) as store:
        add(store, synthetic)
        add(store, local, confirmed_cause=CauseCategory.INVALID_COMMAND)
        assert store.quality(origin=Origin.SYNTHETIC_FIXTURE, limit=1).matches == 1
        local_report = store.quality(origin=Origin.LOCAL_KIND, limit=1)
        assert local_report.mismatches == 1 and local_report.origin is Origin.LOCAL_KIND


def test_quality_reads_one_read_only_snapshot_despite_concurrent_correction(url):
    record = unique_target(url)
    with PostgresFeedbackStore(url) as store:
        first = add(store, record)
        observed = []

        def concurrent_correction(connection, cursor, statement, parameters, context, executemany):
            if "stored_incident_id" not in statement or observed:
                return
            observed.append(connection.exec_driver_sql("SHOW transaction_read_only").scalar_one())
            with PostgresFeedbackStore(url) as writer:
                add(
                    store=writer,
                    record=record,
                    supersedes_id=first.feedback_id,
                    confirmed_cause=CauseCategory.INVALID_COMMAND,
                )

        event.listen(store._engine, "after_cursor_execute", concurrent_correction)
        try:
            snapshot = store.quality(origin=Origin.SYNTHETIC_FIXTURE, limit=1)
        finally:
            event.remove(store._engine, "after_cursor_execute", concurrent_correction)
        assert observed == ["on"]
        assert snapshot.matches == 1 and snapshot.incomplete == 0
        assert snapshot.cases[0].feedback_ids == (first.feedback_id,)
        assert store.quality(origin=Origin.SYNTHETIC_FIXTURE, limit=1).mismatches == 1


def test_cli_labelled_feedback_and_comparison(url):
    record = unique_target(url)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "feedback",
            "add",
            str(record.run_id),
            "--reviewer",
            "test-user",
            "--assessment",
            "useful",
            "--confirmed-cause",
            "oom_killed",
            "--outcome",
            "resolved",
            "--reference",
            "TEST-PM-CLI",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["confirmed_cause"] == "oom_killed"
    compared = runner.invoke(app, ["feedback", "quality", "--limit", "1"])
    assert compared.exit_code == 0, compared.output
    report = QualityReport.model_validate_json(compared.output)
    assert report.matches == 1 and report.cases[0].prediction_kind == "fixture"


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmed_cause": "unknown"},
        {"incident_outcome": "fixed_by_ai"},
        {"reference": None},
        {"reference": "invalid reference"},
    ],
)
def test_sql_constraints_reject_invalid_labels_without_application(url, changes):
    record = unique_target(url)
    with PostgresFeedbackStore(url) as store:
        feedback = add(store, record)
    values = feedback.model_dump(mode="python")
    values.update(feedback_id=uuid4(), reviewer="direct-sql-test")
    values.update(changes)
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            with pytest.raises(DBAPIError) as failure:
                connection.execute(insert(RecommendationFeedback).values(**values))
            assert failure.value.orig.sqlstate == "23514"
    finally:
        engine.dispose()
