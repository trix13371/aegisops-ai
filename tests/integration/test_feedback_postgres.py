"""Real append-only feedback, corrections, races, grants and durable CLI output."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.exc import DBAPIError
from typer.testing import CliRunner

from aegisops.audit.postgres import PostgresAuditStore
from aegisops.audit.reader import PostgresAuditReader
from aegisops.cli.app import app
from aegisops.db.health import DatabaseHealthError, check_database
from aegisops.db.models import RecommendationFeedback
from aegisops.detection.engine import DetectionEngine
from aegisops.domain.models import Origin, PodSnapshot
from aegisops.feedback.models import Assessment, FeedbackError, FeedbackRecord
from aegisops.feedback.postgres import PostgresFeedbackStore
from aegisops.feedback.validation import recommendation_hash
from aegisops.llm.engine import AnalysisEngine
from aegisops.llm.fake import FakeLLMClient
from aegisops.review.service import ReviewService
from aegisops.sources.synthetic import ScenarioRepository

pytestmark = [pytest.mark.integration, pytest.mark.safety]


@pytest.fixture
def url():
    value = os.environ.get("AEGISOPS_DATABASE_URL")
    if not value:
        pytest.skip("local PostgreSQL not configured")
    return value


def target(url, origin=Origin.SYNTHETIC_FIXTURE):
    raw = ScenarioRepository().load("crashloop-oom")[1].model_dump()
    raw["origin"] = origin
    incident = DetectionEngine().detect([PodSnapshot.model_validate(raw)])[0]
    with PostgresAuditStore(url) as store:
        return ReviewService(AnalysisEngine(FakeLLMClient()), store).review(incident)


def test_roundtrip_corrections_and_no_changes_to_original_audit(url):
    record = target(url)
    with PostgresFeedbackStore(url) as store:
        first = store.add(
            record.run_id,
            reviewer="operator",
            assessment=Assessment.USEFUL,
            comment="password=F02_FAKE_CANARY",
        )
        assert first.recommendation_hash == recommendation_hash(record.recommendation)
        second = store.add(
            record.run_id,
            reviewer="operator",
            assessment=Assessment.INCORRECT,
            supersedes_id=first.feedback_id,
            comment="Недостаточно доказательств",
        )
        assert store.list(run_id=record.run_id) == (second, first)
        assert "F02_FAKE_CANARY" not in first.model_dump_json()
        for predecessor in (None, first.feedback_id):
            with pytest.raises(FeedbackError):
                store.add(
                    record.run_id,
                    reviewer="operator",
                    assessment=Assessment.UNSAFE,
                    supersedes_id=predecessor,
                )
        with pytest.raises(FeedbackError):
            store.add(
                record.run_id,
                reviewer="other-user",
                assessment=Assessment.USEFUL,
                supersedes_id=second.feedback_id,
            )
        third = store.add(
            record.run_id,
            reviewer="operator",
            assessment=Assessment.UNCERTAIN,
            supersedes_id=second.feedback_id,
        )
        assert len(store.list(run_id=record.run_id)) == 3
        assert third.supersedes_id == second.feedback_id
    with PostgresAuditReader(url) as reader:
        assert reader.show(record.run_id) == record
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            comments = (
                connection.execute(
                    select(RecommendationFeedback.comment).where(
                        RecommendationFeedback.run_id == record.run_id
                    )
                )
                .scalars()
                .all()
            )
        assert all("F02_FAKE_CANARY" not in comment for comment in comments)
    finally:
        engine.dispose()


def test_unknown_targets_no_recommendation_and_wrong_parent_are_rejected(url):
    cli = CliRunner()
    healthy = cli.invoke(app, ["run", "--scenario", "neg-healthy"])
    assert healthy.exit_code == 0
    healthy_id = json.loads(healthy.output)["records"][0]["run_id"]
    from uuid import UUID

    first_target, second_target = target(url), target(url)
    with PostgresFeedbackStore(url) as store:
        for identifier in (uuid4(), UUID(healthy_id)):
            with pytest.raises(FeedbackError):
                store.add(identifier, reviewer="operator", assessment=Assessment.USEFUL)
            assert store.list(run_id=identifier) == ()
        first = store.add(first_target.run_id, reviewer="operator", assessment=Assessment.USEFUL)
        for parent in (first.feedback_id, uuid4()):
            with pytest.raises(FeedbackError):
                store.add(
                    second_target.run_id,
                    reviewer="operator",
                    assessment=Assessment.USEFUL,
                    supersedes_id=parent,
                )
        assert store.list(run_id=second_target.run_id) == ()


def test_summary_counts_current_opinions_separates_origins_and_reports_truncation(url):
    synthetic, local = target(url), target(url, Origin.LOCAL_KIND)
    with PostgresFeedbackStore(url) as store:
        first = store.add(synthetic.run_id, reviewer="alice", assessment=Assessment.USEFUL)
        store.add(synthetic.run_id, reviewer="bob", assessment=Assessment.UNCERTAIN)
        store.add(
            synthetic.run_id,
            reviewer="alice",
            assessment=Assessment.UNSAFE,
            supersedes_id=first.feedback_id,
        )
        summary = store.summary(origin=Origin.SYNTHETIC_FIXTURE, limit=2)
        assert summary.sample_size == 2 and summary.unsafe == summary.uncertain == 1
        assert summary.useful == summary.incorrect == 0
        store.add(local.run_id, reviewer="alice", assessment=Assessment.INCORRECT)
        assert store.summary(origin=Origin.SYNTHETIC_FIXTURE, limit=2) == summary
        local_summary = store.summary(origin=Origin.LOCAL_KIND, limit=1)
        assert local_summary.incorrect == 1 and local_summary.origin == Origin.LOCAL_KIND
        store.add(synthetic.run_id, reviewer="charlie", assessment=Assessment.USEFUL)
        assert store.summary(origin=Origin.SYNTHETIC_FIXTURE, limit=2).has_more
        for limit in (0, 101, True):
            with pytest.raises(FeedbackError):
                store.summary(origin=Origin.SYNTHETIC_FIXTURE, limit=limit)
            with pytest.raises(FeedbackError):
                store.list(limit=limit)


@pytest.mark.parametrize("correction", [False, True])
def test_concurrent_root_or_correction_has_exactly_one_winner(url, correction):
    record = target(url)
    barrier = Barrier(2)
    with PostgresFeedbackStore(url) as store:
        parent = (
            store.add(record.run_id, reviewer="race", assessment=Assessment.UNCERTAIN)
            if correction
            else None
        )

        def write(assessment):
            barrier.wait(timeout=10)
            try:
                return store.add(
                    record.run_id,
                    reviewer="race",
                    assessment=assessment,
                    supersedes_id=parent.feedback_id if parent else None,
                )
            except FeedbackError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(write, [Assessment.USEFUL, Assessment.UNSAFE]))
        assert sum(result is not None for result in results) == 1
        assert len(store.list(run_id=record.run_id)) == (2 if correction else 1)


def test_cli_add_list_and_summary_only_print_committed_sanitized_data(url):
    record = target(url)
    cli = CliRunner()
    result = cli.invoke(
        app,
        [
            "feedback",
            "add",
            str(record.run_id),
            "--reviewer",
            "local-user",
            "--assessment",
            "unsafe",
            "--comment",
            "password=F02_FAKE_CLI_CANARY",
        ],
    )
    assert result.exit_code == 0, result.output
    feedback = FeedbackRecord.model_validate_json(result.output)
    assert "F02_FAKE_CLI_CANARY" not in result.output
    listed = cli.invoke(app, ["feedback", "list", "--run-id", str(record.run_id)])
    assert listed.exit_code == 0 and json.loads(listed.output) == [feedback.model_dump(mode="json")]
    summary = cli.invoke(app, ["feedback", "summary", "--limit", "1"])
    assert summary.exit_code == 0 and json.loads(summary.output)["unsafe"] == 1
    repeated = cli.invoke(
        app,
        [
            "feedback",
            "add",
            str(record.run_id),
            "--reviewer",
            "local-user",
            "--assessment",
            "useful",
        ],
    )
    assert repeated.exit_code == 1 and "feedback: FAILED" in repeated.output
    assert '"feedback_id"' not in repeated.output


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE recommendation_feedback SET assessment='useful' WHERE false",
        "DELETE FROM recommendation_feedback WHERE false",
        "TRUNCATE recommendation_feedback",
        "ALTER TABLE recommendation_feedback ADD COLUMN forbidden integer",
    ],
)
def test_feedback_mutations_denied_under_app_role(url, statement):
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            with pytest.raises(DBAPIError) as failure:
                connection.execute(text(statement))
            assert failure.value.orig.sqlstate == "42501"
    finally:
        engine.dispose()


def test_doctor_detects_excess_feedback_grants(url):
    owner_url = os.environ.get("AEGISOPS_MIGRATION_URL")
    if not owner_url:
        pytest.skip("local migration owner not configured")
    owner = create_engine(owner_url, hide_parameters=True)
    try:
        with owner.begin() as connection:
            connection.execute(text("GRANT DELETE ON recommendation_feedback TO aegisops_app"))
        try:
            with pytest.raises(DatabaseHealthError, match="privilege"):
                check_database(url)
        finally:
            with owner.begin() as connection:
                connection.execute(
                    text("REVOKE DELETE ON recommendation_feedback FROM aegisops_app")
                )
    finally:
        owner.dispose()


@pytest.mark.parametrize(
    "changes",
    [
        {"assessment": "approved"},
        {"schema_version": 0},
        {"origin": "production"},
        {"reviewer": "bad alias"},
        {"recommendation_hash": "0"},
        {"run_id": "missing"},
    ],
)
def test_database_constraints_reject_invalid_feedback_even_without_service(url, changes):
    record = target(url)
    with PostgresFeedbackStore(url) as store:
        first = store.add(record.run_id, reviewer="valid", assessment=Assessment.USEFUL)
    values = first.model_dump(mode="python")
    values.update(feedback_id=uuid4(), reviewer="another")
    values.update(changes)
    if changes.get("run_id") == "missing":
        values["run_id"] = uuid4()
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            with pytest.raises(DBAPIError) as failure:
                connection.execute(insert(RecommendationFeedback).values(**values))
            assert failure.value.orig.sqlstate in {"23514", "23503"}
    finally:
        engine.dispose()
