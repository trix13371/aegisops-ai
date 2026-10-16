"""Review workers bind saved evidence and permit only one durable inference attempt."""

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

from aegisops.project_ai.review_models import ReviewExplanation
from aegisops.project_ai.review_repository import ReviewAnalysisRepository
from aegisops.project_reviews.repository import ReviewRepository
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.models import ProjectReport
from aegisops.projects.repository import ProjectRepository
from aegisops.projects.scanner import scan_project

ROOT = Path(__file__).resolve().parents[2]
CANARY = "PRIVATE_SOURCE_CANARY"
pytestmark = [pytest.mark.unit, pytest.mark.safety]
ADVICE = {
    "summary": "Есть замечания конфигурации.",
    "actions": [
        {
            "code": "privileged",
            "priority": "high",
            "explanation": "Расширены полномочия.",
            "next_step": "Проверьте необходимость расширенных полномочий.",
        }
    ],
    "limitations": "Версия исходников не подтверждена, тесты не выбраны.",
}


@pytest.fixture
def case(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "compose.yaml").write_text("services:\n  app:\n    privileged: true\n")
    projects = ProjectRepository(tmp_path / ".local/projects")
    project = projects.add(CANARY, str(source))
    report = scan_project(project)
    projects.save_report(report)
    runs = RunRepository(tmp_path / ".local/project-runs")
    reviews = ReviewRepository(tmp_path / ".local/project-reviews", projects, runs)
    review = reviews.create(project.id, report.id, None)
    analyses = ReviewAnalysisRepository(tmp_path / ".local/project-review-ai")
    spec = importlib.util.spec_from_file_location(
        "project_review_ai_worker", ROOT / "scripts/project-review-ai-worker.py"
    )
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    monkeypatch.setattr(worker, "ROOT", tmp_path)
    monkeypatch.setattr(worker.sys, "argv", ["worker", str(review.id)])
    monkeypatch.setenv("AEGISOPS_LLM_API_KEY", "test-runtime-key")
    return projects, report, reviews, review, analyses, worker


def test_worker_once_only_and_no_project_code_imported_or_modified(case, monkeypatch, capsys):
    projects, report, _, review, analyses, worker = case
    source = Path(projects.get(report.project_id).path) / "compose.yaml"
    before = source.read_bytes()
    calls = []

    def provider(summary, key):
        assert key.get_secret_value() == "test-runtime-key"
        assert CANARY not in summary.model_dump_json()
        calls.append(summary)
        return ReviewExplanation.model_validate(ADVICE), 100, 200

    monkeypatch.setattr(worker, "explain_review", provider)
    analyses.claim(review)
    assert worker.main() == 0
    assert capsys.readouterr().out.strip() == '{"ok": true}'
    assert worker.main() == 1
    assert capsys.readouterr().out.strip() == '{"ok": false}'
    assert len(calls) == 1
    assert analyses.state(review)["status"] == "completed"
    assert source.read_bytes() == before


def test_uncertain_start_cannot_be_replayed_or_finalized(case, monkeypatch, capsys):
    _, _, _, review, analyses, worker = case
    analyses.claim(review)
    analyses.write(review.id, "started", "{}")

    def forbidden(*args):
        pytest.fail("uncertain attempt replayed")

    monkeypatch.setattr(worker, "explain_review", forbidden)
    assert worker.main() == 1
    assert capsys.readouterr().out.strip() == '{"ok": false}'
    assert analyses.state(review)["status"] == "pending"
    assert analyses.read_result(review.id) is None


def test_provider_failure_is_fixed_and_cannot_be_retried(case, monkeypatch, capsys):
    _, _, _, review, analyses, worker = case
    analyses.claim(review)
    calls = []

    def failure(*args):
        calls.append(True)
        raise ValueError(CANARY)

    monkeypatch.setattr(worker, "explain_review", failure)
    assert worker.main() == 1
    assert analyses.state(review)["status"] == "failed"
    assert worker.main() == 1
    assert len(calls) == 1
    output = capsys.readouterr()
    assert output.out == '{"ok": false}\n{"ok": false}\n'
    assert not output.err and CANARY not in output.out


@pytest.mark.parametrize("tamper", ["claim_digest", "claim_payload", "saved_reference", "review"])
def test_changed_evidence_or_claim_rejected_before_start(case, monkeypatch, capsys, tamper):
    projects, report, reviews, review, analyses, worker = case
    claim = analyses.claim(review)
    if tamper in {"claim_digest", "claim_payload"}:
        data = claim.model_dump(mode="json")
        if tamper == "claim_digest":
            data["review_sha256"] = "f" * 64
        else:
            data["payload"]["static"]["files_checked"] += 1
        analyses.files.path(review.id, "claim").write_text(json.dumps(data))
    elif tamper == "saved_reference":
        data = report.model_dump()
        data["findings"] = (report.findings[0].model_copy(update={"message": CANARY}),)
        changed = ProjectReport.model_validate(data)
        (projects.directory / "reports" / f"{report.id}.json").write_text(changed.model_dump_json())
    else:
        data = review.model_dump(mode="json")
        data["project_id"] = str(uuid4())
        reviews.path("reviews", review.id).write_text(json.dumps(data))

    def forbidden(*args):
        pytest.fail("changed evidence reached provider")

    monkeypatch.setattr(worker, "explain_review", forbidden)
    assert worker.main() == 1
    assert capsys.readouterr().out.strip() == '{"ok": false}'
    assert not analyses.files.path(review.id, "started").exists()
    assert analyses.read_result(review.id) is None


def test_evidence_changed_during_provider_never_saved_as_success(case, monkeypatch, capsys):
    projects, report, _, review, analyses, worker = case
    analyses.claim(review)

    def provider(*args):
        data = report.model_dump()
        data["findings"] = (report.findings[0].model_copy(update={"message": CANARY}),)
        (projects.directory / "reports" / f"{report.id}.json").write_text(
            ProjectReport.model_validate(data).model_dump_json()
        )
        return ReviewExplanation.model_validate(ADVICE), 100, 200

    monkeypatch.setattr(worker, "explain_review", provider)
    assert worker.main() == 1
    assert capsys.readouterr().out.strip() == '{"ok": false}'
    assert analyses.read_result(review.id).status == "failed"


def test_unclaimed_review_or_invalid_arguments_never_start(case, monkeypatch, capsys):
    _, _, _, review, analyses, worker = case

    def forbidden(*args):
        pytest.fail("unclaimed review reached provider")

    monkeypatch.setattr(worker, "explain_review", forbidden)
    assert worker.main() == 1
    for arguments in (["worker"], ["worker", CANARY], ["worker", str(review.id), "extra"]):
        monkeypatch.setattr(worker.sys, "argv", arguments)
        assert worker.main() == 1
    assert not analyses.files.path(review.id, "started").exists()
    assert analyses.read_result(review.id) is None
    assert CANARY not in capsys.readouterr().out
