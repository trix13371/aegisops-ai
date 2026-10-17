"""Local operator UI host. Fixed child commands only; never part of model capabilities.

Loopback-only, per-launch capability, HttpOnly/SameSite cookie, strict Host/Origin.
The web API cannot change controls, issue identities or choose commands. Explicit
project-summary consent permits one bounded paid request per saved static report.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import UTC, datetime
from http.client import HTTPConnection
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4

from aegisops.audit.reader import PostgresAuditReader
from aegisops.authorization.bundle import load_config
from aegisops.authorization.control import load_control
from aegisops.authorization.releases import load_release_check
from aegisops.dashboard.view import present_record
from aegisops.io import read_text, unique_object
from aegisops.pipeline.models import PipelineResult
from aegisops.project_ai.full_models import FULL_PROFILE
from aegisops.project_ai.full_repository import FullAnalysisRepository
from aegisops.project_ai.models import MODEL, PROFILE
from aegisops.project_ai.projection import project_summary
from aegisops.project_ai.repository import AnalysisRepository
from aegisops.project_ai.review_models import REVIEW_PROFILE
from aegisops.project_ai.review_projection import review_summary
from aegisops.project_ai.review_repository import ReviewAnalysisRepository
from aegisops.project_checks.comparison import compare_saved_checks
from aegisops.project_checks.repository import FullCheckRepository
from aegisops.project_reviews.repository import ReviewRepository
from aegisops.project_runner.comparison import compare_runs
from aegisops.project_runner.inspection import inspect_project_package
from aegisops.project_runner.models import ENVIRONMENTS, profile_for
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.comparison import compare_reports
from aegisops.projects.models import ProjectError
from aegisops.projects.repository import ProjectRepository
from aegisops.projects.scanner import scan_project
from aegisops.telemetry.contracts import DeliveryResult
from aegisops.telemetry.files import load_trust, read_object, safe_path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src/aegisops/dashboard/static"
STATE = ROOT / ".local/dashboard/session.json"
HOST, PORT = "127.0.0.1", 8765
ORIGIN = f"http://{HOST}:{PORT}"
SCENARIOS = frozenset({"crashloop-oom", "crashloop-exit-1", "neg-healthy"})
FULL_CHECK_CONSENT = "project-full-check-v1"


def role_environment(database=False):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("AEGISOPS_", "OPENAI_", "OPENROUTER_", "PG"))
        and key.upper() != "KUBECONFIG"
    }
    if database:
        # Select only the app role, never copy the entire .env into any child.
        for line in read_text(safe_path(ROOT / ".env"), 16384).splitlines():
            if line.startswith("AEGISOPS_DATABASE_URL="):
                env["AEGISOPS_DATABASE_URL"] = line.split("=", 1)[1]
                break
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class Dashboard:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.health_lock = threading.Lock()
        self.job = None
        self.stopping = False
        self.health_at = 0
        self.health = None
        self.projects = ProjectRepository(ROOT / ".local/projects")
        self.analyses = AnalysisRepository(ROOT / ".local/project-ai")
        self.runs = RunRepository(ROOT / ".local/project-runs")
        self.checks = FullCheckRepository(ROOT / ".local/project-checks", self.projects, self.runs)
        self.reviews = ReviewRepository(ROOT / ".local/project-reviews", self.projects, self.runs)
        self.review_analyses = ReviewAnalysisRepository(ROOT / ".local/project-review-ai")
        self.full_analyses = FullAnalysisRepository(ROOT / ".local/project-check-ai", self.checks)
        self.python = str(ROOT / ".venv/Scripts/python.exe")
        if os.name != "nt":
            self.python = sys.executable

    def child(self, arguments, *, database=False, timeout=60, json_output=True):
        result = subprocess.run(
            [self.python, *arguments],
            cwd=ROOT,
            env=role_environment(database),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            check=False,
        )
        if json_output:
            if result.returncode not in {0, 1}:
                raise ValueError("child rejected")
            return json.loads(result.stdout, object_pairs_hook=unique_object)
        if result.returncode != 0:
            raise ValueError("child rejected")
        return None

    def services(self):
        with self.health_lock:
            if self.health is not None and time.monotonic() - self.health_at < 15:
                return self.health
            services = {}
            try:
                with PostgresAuditReader() as reader:
                    reader.list(1)
                services["database"] = "ready"
            except Exception:
                services["database"] = "unavailable"
            try:
                # Readiness is a fixed synthetic read-only decision, never a cluster action.
                self.child(["scripts/wait-opa.py"], json_output=False, timeout=10)
                services["opa"] = "ready"
            except Exception:
                services["opa"] = "unavailable"
            try:
                trust = load_trust(ROOT)
                state = read_object(ROOT / ".local/kind/state.json", 4096)
                services["collector"] = (
                    "registered"
                    if isinstance(state, dict)
                    and state.get("instance_id") == str(trust.registration_id)
                    else "unavailable"
                )
            except Exception:
                services["collector"] = "unavailable"
            try:
                control = load_control()
                services["control"] = "blocked" if control.global_blocked else "ready"
            except Exception:
                services["control"] = "unavailable"
            try:
                release = load_release_check(load_config(), datetime.now(UTC))
                services["policy"] = "ready" if release.status == "valid" else "unavailable"
            except Exception:
                services["policy"] = "unavailable"
            self.health, self.health_at = services, time.monotonic()
            return services

    def project_ai_child(self, identifier):
        return self._project_ai_child(identifier, review=False)

    def project_review_ai_child(self, identifier):
        return self._project_ai_child(identifier, review=True)

    def project_check_ai_child(self, identifier):
        return self._project_ai_child(identifier, review=False, full=True)

    def _project_ai_child(self, identifier, *, review, full=False):
        identifier = str(UUID(str(identifier)))
        if os.name != "nt":
            raise ProjectError("Защищённое подключение OpenRouter настроено для Windows")
        # PowerShell 7's inherited module path breaks Windows PowerShell 5.1's
        # Security module autoload in a Python background process. Rebuild its defaults.
        runtime_environment = {
            key: value for key, value in role_environment().items() if key.upper() != "PSMODULEPATH"
        }
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(ROOT / "scripts/openrouter-local.ps1"),
                "-Mode",
                "project-check" if full else ("project-review" if review else "project-report"),
                "-CheckId" if full else ("-ReviewId" if review else "-ReportId"),
                identifier,
            ],
            cwd=ROOT,
            env=runtime_environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            timeout=80,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        if result.returncode != 0 or json.loads(result.stdout) != {"ok": True}:
            raise ProjectError("Не удалось получить подтверждённый ответ модели")

    def overview(self):
        records, omitted, database_error = [], 0, False
        try:
            with PostgresAuditReader() as reader:
                for entry in reader.list(25):
                    try:
                        records.append(present_record(reader.show(entry.run_id)))
                    except Exception:
                        omitted += 1
        except Exception:
            database_error = True
        return {
            "records": records,
            "omitted_records": omitted,
            "database_error": database_error,
            "services": self.services(),
            "job": self.current_job(),
            "limit": 25,
            "checked_at": datetime.now(UTC).isoformat(),
        }

    def current_job(self):
        with self.lock:
            return dict(self.job) if self.job else None

    def list_projects(self):
        items = []
        for project in self.projects.list():
            item = project.model_dump(mode="json", exclude={"device", "inode", "reports"})
            item["last_report"] = None
            item["report_error"] = False
            if project.reports:
                try:
                    item["last_report"] = self.projects.read_report(project.reports[0]).model_dump(
                        mode="json", exclude={"findings"}
                    )
                except ProjectError:
                    item["report_error"] = True
            items.append(item)
        return {"projects": items}

    def start(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("invalid request")
        mode = payload.get("mode")
        if mode == "local_kind" and set(payload) == {"mode"}:
            scenario = None
        elif (
            mode == "synthetic"
            and set(payload) == {"mode", "scenario"}
            and isinstance(payload.get("scenario"), str)
            and payload["scenario"] in SCENARIOS
        ):
            scenario = payload["scenario"]
        elif mode == "project" and set(payload) == {"mode", "project_id"}:
            scenario = str(UUID(payload["project_id"]))
            self.projects.get(UUID(scenario))
        elif (
            mode == "project_runner"
            and set(payload)
            in (
                {"mode", "project_id", "consent"},
                {"mode", "project_id", "consent", "environment"},
            )
            and payload.get("environment", "minimal") in ENVIRONMENTS
            and payload.get("consent") == profile_for(payload.get("environment", "minimal"))
        ):
            scenario = str(UUID(payload["project_id"]))
            self.projects.get(UUID(scenario))
        elif (
            mode == "project_full_check"
            and set(payload)
            in (
                {"mode", "project_id", "consent"},
                {"mode", "project_id", "consent", "environment"},
            )
            and payload.get("environment", "minimal") in ENVIRONMENTS
            and payload.get("consent") == FULL_CHECK_CONSENT
        ):
            scenario = str(UUID(payload["project_id"]))
            self.projects.get(UUID(scenario))
        elif (
            mode == "project_review_ai"
            and set(payload) == {"mode", "review_id", "consent"}
            and payload.get("consent") == REVIEW_PROFILE
        ):
            scenario = str(UUID(payload["review_id"]))
            review = self.reviews.get(UUID(scenario))
        elif (
            mode == "project_check_ai"
            and set(payload) == {"mode", "check_id", "consent"}
            and payload.get("consent") == FULL_PROFILE
        ):
            scenario = str(UUID(payload["check_id"]))
            self.full_analyses.evidence(UUID(scenario))
        elif (
            mode == "project_ai"
            and set(payload) == {"mode", "report_id", "consent"}
            and payload.get("consent") == PROFILE
        ):
            scenario = str(UUID(payload["report_id"]))
            report = self.projects.read_report(UUID(scenario))
            self.projects.get(report.project_id)
        else:
            raise ValueError("invalid request")
        with self.lock:
            if self.stopping:
                raise ValueError("dashboard is closing")
            if self.job and self.job["state"] == "running":
                return dict(self.job), False
            if mode == "project_ai":
                self.analyses.claim(report)
            if mode == "project_review_ai":
                self.review_analyses.claim(review)
            if mode == "project_check_ai":
                self.full_analyses.claim(UUID(scenario))
            if mode == "project_runner":
                scenario = str(
                    self.runs.create(UUID(scenario), payload.get("environment", "minimal")).id
                )
            if mode == "project_full_check":
                scenario = str(
                    self.checks.create(UUID(scenario), payload.get("environment", "minimal")).id
                )
            self.job = {
                "id": str(uuid4()),
                "mode": mode,
                "state": "running",
                "stage": "collecting",
                "started_at": datetime.now(UTC).isoformat(),
                "message": "Собираем наблюдения…",
            }
            if mode == "project":
                self.job.update(project_id=scenario, message="Читаем структуру проекта…")
            if mode == "project_runner":
                self.job.update(run_id=scenario, message="Готовим изолированную проверку кода…")
            if mode == "project_full_check":
                self.job.update(
                    check_id=scenario, message="Запускаем совместную проверку снимка проекта…"
                )
            if mode == "project_ai":
                self.job.update(
                    report_id=scenario, message="Запрошен разбор сводки через OpenRouter…"
                )
            if mode == "project_review_ai":
                self.job.update(
                    review_id=scenario, message="Запрошен разбор сводного отчёта через OpenRouter…"
                )
            if mode == "project_check_ai":
                self.job.update(
                    check_id=scenario,
                    message="Запрошен разбор совместной проверки через OpenRouter…",
                )
            job = dict(self.job)
        threading.Thread(target=self.run_job, args=(mode, scenario), daemon=True).start()
        return job, True

    def update(self, **fields):
        with self.lock:
            self.job.update(fields)

    def run_job(self, mode, scenario):
        try:
            if mode == "project_check_ai":
                identifier = UUID(scenario)
                try:
                    self.project_check_ai_child(identifier)
                except Exception:  # noqa: S110 -- never expose credential or provider details
                    pass
                self.full_analyses.fail(identifier)
                completed = self.full_analyses.state(identifier)["status"] == "completed"
                self.update(
                    state="completed" if completed else "failed",
                    stage="done",
                    check_id=scenario,
                    message="Нейросетевой разбор совместной проверки сохранён. Откройте отчёт."
                    if completed
                    else "Разбор совместной проверки не подтверждён. Откройте отчёт; повтора нет.",
                )
                return
            if mode == "project_review_ai":
                identifier = UUID(scenario)
                try:
                    self.project_review_ai_child(identifier)
                except Exception:  # noqa: S110 -- do not expose provider or credential details
                    pass
                self.review_analyses.fail(identifier)
                review = self.reviews.get(identifier)
                completed = self.review_analyses.state(review)["status"] == "completed"
                self.update(
                    state="completed" if completed else "failed",
                    stage="done",
                    review_id=scenario,
                    message="Нейросетевой разбор сохранён. Откройте сводный отчёт."
                    if completed
                    else "Разбор сводного отчёта не подтверждён. Откройте сводку; повтора нет.",
                )
                return
            if mode == "project_runner":
                self.child(["scripts/project-runner-local.py", scenario], timeout=220)
                result = self.runs.result(UUID(scenario))
                if result is None:
                    self.update(
                        state="failed",
                        stage="done",
                        run_id=scenario,
                        message="Результат запуска неизвестен. Откройте историю; повтора нет.",
                    )
                else:
                    self.update(
                        state="completed" if result.status == "passed" else "failed",
                        stage="done",
                        run_id=scenario,
                        message="Проверка контейнера завершена. Откройте результаты инструментов.",
                    )
                return
            if mode == "project_full_check":
                self.child(["scripts/project-check-local.py", scenario], timeout=250)
                result = self.checks.result(UUID(scenario))
                if result is None:
                    self.update(
                        state="failed",
                        stage="done",
                        check_id=scenario,
                        message="Результат совместной проверки неизвестен. Откройте историю; "
                        "повтора нет.",
                    )
                elif result.status == "passed":
                    self.update(
                        state="completed",
                        stage="done",
                        check_id=scenario,
                        message="Совместная проверка завершена успешно. Откройте отчёт.",
                    )
                elif result.status == "issues":
                    self.update(
                        state="failed",
                        stage="done",
                        check_id=scenario,
                        message="В проекте найдены замечания. Подробности в сохранённом отчёте.",
                    )
                else:
                    self.update(
                        state="failed",
                        stage="done",
                        check_id=scenario,
                        message="Проверка завершена частично. Откройте сохранённый отчёт.",
                    )
                return
            if mode == "project_ai":
                identifier = UUID(scenario)
                try:
                    self.project_ai_child(identifier)
                except Exception:  # noqa: S110 -- never expose credential-bearing provider errors
                    pass  # Fixed failure record below, never raw PowerShell/provider details.
                self.analyses.fail(identifier)
                report = self.projects.read_report(identifier)
                analysis = self.analyses.state(report)
                completed = analysis["status"] == "completed"
                self.update(
                    state="completed" if completed else "failed",
                    stage="done",
                    report_id=scenario,
                    message="Нейросетевой разбор сохранён. Откройте отчёт."
                    if completed
                    else "Нейросетевой разбор не подтверждён. Подробности в отчёте; повтора нет.",
                )
                return
            if mode == "project":
                project = self.projects.get(UUID(scenario))
                self.update(stage="processing", message="Проверяем форматы и конфигурацию проекта…")
                report = scan_project(project)
                self.projects.save_report(report)
                self.update(
                    state=report.status,
                    stage="done",
                    report_id=str(report.id),
                    files_checked=report.files_checked,
                    findings_count=len(report.findings),
                    message={
                        "passed": "Проверка форматов завершена. Отчёт сохранён.",
                        "issues": "В проекте найдены замечания. Отчёт сохранён.",
                        "partial": "Проверка проекта выполнена частично. Подробности в отчёте.",
                    }[report.status],
                )
                return
            if mode == "local_kind":
                self.child(
                    ["scripts/cluster-local.py", "refresh-reader"], json_output=False, timeout=30
                )
                collected = self.child(["-m", "aegisops.collector.cli", "collect"])
                identifier = str(UUID(collected["batch_id"]))
                self.update(stage="processing", message="Проверяем пакет и анализируем данные…")
                output = self.child(
                    ["-m", "aegisops.controlplane.cli", "consume", identifier], database=True
                )
                delivery = DeliveryResult.model_validate(output)
                receipt = delivery.receipt
                status, snapshots, incidents, ids = (
                    receipt.status.value,
                    receipt.snapshot_count,
                    receipt.incident_count,
                    [str(item) for item in receipt.audit_run_ids],
                )
            else:
                self.update(stage="processing", message="Разбираем учебный сценарий…")
                output = self.child(
                    [
                        "-c",
                        "from aegisops.cli.app import app; app()",
                        "run",
                        "--scenario",
                        scenario,
                        "--llm",
                        "none",
                        "--authorization",
                        "opa",
                    ],
                    database=True,
                )
                result = PipelineResult.model_validate(output)
                status, snapshots, incidents, ids = (
                    result.status.value,
                    result.snapshot_count,
                    result.incident_count,
                    [str(item.run_id) for item in result.records],
                )
            self.update(
                state=status,
                stage="done",
                snapshot_count=snapshots,
                incident_count=incidents,
                audit_ids=ids,
                message={
                    "completed": "Проверка завершена. Результаты сохранены в аудите.",
                    "blocked": "Анализ сохранён. Политика заблокировала дальнейший dry-run.",
                    "failed": "Проверка завершилась с ошибкой. Подробности — в аудите.",
                }[status],
            )
        except ProjectError as error:
            self.update(state="failed", stage="done", message=str(error))
        except Exception:
            # May be an uncertain claim: no retry, cleanup or controls mutation.
            self.update(
                state="failed",
                stage="done",
                message="Запуск прерван. Проверьте историю запусков и Docker; повтора нет."
                if mode in {"project_runner", "project_full_check"}
                else "Проверка остановлена. Проверьте среду и историю аудита. "
                "Незавершённый пакет автоматически не повторяется.",
            )
        finally:
            self.health_at = 0


class Handler(BaseHTTPRequestHandler):
    server_version = "AegisOpsLocal"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # No URLs, cookies, raw subprocess output or exception messages in logs.

    @property
    def dashboard(self):
        return self.server.dashboard

    def response(
        self, code, body=b"", content_type="application/json; charset=utf-8", headers=None
    ):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'none'",
        )
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, code, data):
        self.response(code, json.dumps(data, ensure_ascii=False).encode())

    def host_valid(self):
        return self.headers.get("Host") == f"{HOST}:{self.server.server_port}"

    def authorized(self):
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            token = cookie["aegisops_session"].value if "aegisops_session" in cookie else ""
            operator = self.headers.get("X-AegisOps-Session", "")
            return hmac.compare_digest(token, self.dashboard.token) or hmac.compare_digest(
                operator, self.dashboard.token
            )
        except Exception:
            return False

    def do_GET(self):
        if not self.host_valid():
            return self.send_json(403, {"error": "Недопустимый адрес"})
        if self.path.startswith("/session/"):
            if not hmac.compare_digest(self.path[9:], self.dashboard.token):
                return self.send_json(403, {"error": "Сессия недоступна"})
            return self.response(
                303,
                headers={
                    "Location": "/",
                    "Set-Cookie": (
                        f"aegisops_session={self.dashboard.token}; "
                        "HttpOnly; SameSite=Strict; Path=/"
                    ),
                },
            )
        if self.path.startswith("/api/"):
            if not self.authorized():
                return self.send_json(401, {"error": "Откройте интерфейс через Start-AegisOps.cmd"})
            try:
                if self.path == "/api/ping":
                    return self.send_json(200, {"application": "aegisops-local-ui-v1"})
                if self.path == "/api/overview":
                    return self.send_json(200, self.dashboard.overview())
                if self.path == "/api/job":
                    return self.send_json(200, {"job": self.dashboard.current_job()})
                if self.path == "/api/projects":
                    return self.send_json(200, self.dashboard.list_projects())
                if self.path.startswith("/api/project-reviews/"):
                    identifier = UUID(self.path[len("/api/project-reviews/") :])
                    reviews = self.dashboard.reviews.history(identifier)
                    return self.send_json(
                        200, {"reviews": [item.model_dump(mode="json") for item in reviews]}
                    )
                if self.path.startswith("/api/project-check-ai/"):
                    identifier = UUID(self.path[len("/api/project-check-ai/") :])
                    check, summary = self.dashboard.full_analyses.evidence(identifier)
                    return self.send_json(
                        200,
                        {
                            "check_id": str(check.id),
                            "analysis": self.dashboard.full_analyses.state(identifier),
                            "profile": FULL_PROFILE,
                            "model": MODEL,
                            "payload": summary.model_dump(mode="json"),
                        },
                    )
                if self.path.startswith("/api/project-review/"):
                    identifier = UUID(self.path[len("/api/project-review/") :])
                    review = self.dashboard.reviews.get(identifier)
                    return self.send_json(
                        200,
                        {
                            "review": review.model_dump(mode="json"),
                            "analysis": self.dashboard.review_analyses.state(review),
                            "ai_profile": REVIEW_PROFILE,
                            "model": MODEL,
                            "payload": review_summary(review).model_dump(mode="json"),
                        },
                    )
                if self.path.startswith("/api/projects/"):
                    identifier = UUID(self.path[len("/api/projects/") :])
                    reports = self.dashboard.projects.history(identifier)
                    return self.send_json(
                        200,
                        {
                            "reports": [
                                item.model_dump(mode="json", exclude={"findings"})
                                for item in reports
                            ]
                        },
                    )
                if self.path.startswith("/api/project-reports/"):
                    identifier = UUID(self.path[len("/api/project-reports/") :])
                    report = self.dashboard.projects.read_report(identifier)
                    self.dashboard.projects.get(report.project_id)
                    return self.send_json(200, report.model_dump(mode="json"))
                if self.path.startswith("/api/project-ai/"):
                    identifier = UUID(self.path[len("/api/project-ai/") :])
                    report = self.dashboard.projects.read_report(identifier)
                    self.dashboard.projects.get(report.project_id)
                    return self.send_json(
                        200,
                        {
                            "profile": PROFILE,
                            "model": MODEL,
                            "payload": project_summary(report).model_dump(mode="json"),
                            "analysis": self.dashboard.analyses.state(report),
                        },
                    )
                if self.path.startswith("/api/project-comparison/"):
                    identifiers = self.path[len("/api/project-comparison/") :].split("/")
                    if len(identifiers) != 2:
                        raise ValueError("invalid comparison")
                    base, target = (
                        self.dashboard.projects.read_report(UUID(value)) for value in identifiers
                    )
                    self.dashboard.projects.get(base.project_id)
                    self.dashboard.projects.get(target.project_id)
                    return self.send_json(
                        200, compare_reports(base, target).model_dump(mode="json")
                    )
                if self.path.startswith("/api/run-comparison/"):
                    identifiers = self.path[len("/api/run-comparison/") :].split("/")
                    if len(identifiers) != 2:
                        raise ValueError("invalid comparison")
                    base, target = (self.dashboard.runs.claim(UUID(value)) for value in identifiers)
                    self.dashboard.projects.get(base.project_id)
                    self.dashboard.projects.get(target.project_id)
                    comparison = compare_runs(
                        base,
                        target,
                        self.dashboard.runs.result(base.id),
                        self.dashboard.runs.result(target.id),
                    )
                    return self.send_json(200, comparison.model_dump(mode="json"))
                if self.path.startswith("/api/project-runs/"):
                    identifier = UUID(self.path[len("/api/project-runs/") :])
                    self.dashboard.projects.get(identifier)
                    return self.send_json(200, {"runs": self.dashboard.runs.history(identifier)})
                if self.path.startswith("/api/project-checks/"):
                    identifier = UUID(self.path[len("/api/project-checks/") :])
                    return self.send_json(
                        200, {"checks": self.dashboard.checks.history(identifier)}
                    )
                if self.path.startswith("/api/project-check-comparison/"):
                    identifiers = self.path[len("/api/project-check-comparison/") :].split("/")
                    if len(identifiers) != 2:
                        raise ValueError("invalid comparison")
                    comparison = compare_saved_checks(
                        self.dashboard.checks, UUID(identifiers[0]), UUID(identifiers[1])
                    )
                    return self.send_json(200, comparison.model_dump(mode="json"))
                if self.path.startswith("/api/project-check/"):
                    identifier = UUID(self.path[len("/api/project-check/") :])
                    return self.send_json(200, self.dashboard.checks.state(identifier))
                if self.path.startswith("/api/project-compatibility/"):
                    identifier = UUID(self.path[len("/api/project-compatibility/") :])
                    package = inspect_project_package(self.dashboard.projects.get(identifier))
                    return self.send_json(200, package.model_dump(mode="json"))
                if self.path.startswith("/api/project-run/"):
                    identifier = UUID(self.path[len("/api/project-run/") :])
                    claim = self.dashboard.runs.claim(identifier)
                    self.dashboard.projects.get(claim.project_id)
                    return self.send_json(200, self.dashboard.runs.state(identifier))
                if self.path.startswith("/api/audit/"):
                    identifier = UUID(self.path[11:])
                    with PostgresAuditReader() as reader:
                        record = present_record(reader.show(identifier), detail=True)
                    return self.send_json(200, record)
            except Exception:
                return self.send_json(503, {"error": "Данные временно недоступны"})
            return self.send_json(404, {"error": "Страница не найдена"})
        assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
            "/favicon.svg": ("favicon.svg", "image/svg+xml"),
        }
        if self.path not in assets:
            return self.send_json(404, {"error": "Страница не найдена"})
        filename, content_type = assets[self.path]
        return self.response(200, (STATIC / filename).read_bytes(), content_type)

    def do_POST(self):
        if (
            not self.host_valid()
            or self.headers.get("Origin") != f"http://{HOST}:{self.server.server_port}"
        ):
            return self.send_json(403, {"error": "Запрос отклонён"})
        if not self.authorized():
            return self.send_json(401, {"error": "Сессия недоступна"})
        if self.path not in {
            "/api/jobs",
            "/api/stop",
            "/api/projects",
            "/api/projects/remove",
            "/api/project-reviews",
        }:
            return self.send_json(404, {"error": "Команда недоступна"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if (
                not 0 < length <= (4096 if self.path == "/api/projects" else 1024)
                or self.headers.get("Content-Type") != "application/json"
                or self.headers.get("Transfer-Encoding") is not None
            ):
                raise ValueError("invalid body")
            payload = json.loads(self.rfile.read(length), object_pairs_hook=unique_object)
            if self.path == "/api/project-reviews":
                if not isinstance(payload, dict) or set(payload) != {
                    "project_id",
                    "static_report_id",
                    "run_id",
                }:
                    raise ValueError("invalid review request")
                project_id = UUID(payload["project_id"])
                static_report_id = (
                    UUID(payload["static_report_id"])
                    if payload["static_report_id"] is not None
                    else None
                )
                run_id = UUID(payload["run_id"]) if payload["run_id"] is not None else None
                with self.dashboard.lock:
                    if self.dashboard.stopping:
                        raise ValueError("dashboard is closing")
                    review = self.dashboard.reviews.create(project_id, static_report_id, run_id)
                return self.send_json(201, {"review": review.model_dump(mode="json")})
            if self.path == "/api/projects":
                if not isinstance(payload, dict) or set(payload) != {"name", "path"}:
                    raise ValueError("invalid project request")
                project = self.dashboard.projects.add(payload["name"], payload["path"])
                return self.send_json(201, {"project_id": str(project.id)})
            if self.path == "/api/projects/remove":
                if not isinstance(payload, dict) or set(payload) != {"project_id"}:
                    raise ValueError("invalid project request")
                identifier = UUID(payload["project_id"])
                with self.dashboard.lock:
                    if self.dashboard.job and self.dashboard.job["state"] == "running":
                        raise ProjectError("Дождитесь завершения проверки")
                    self.dashboard.projects.remove(identifier)
                return self.send_json(200, {"removed": True})
            if self.path == "/api/stop":
                if payload != {}:
                    raise ValueError("invalid stop request")
                # Keep the job lock through the response: never interrupt accepted work.
                with self.dashboard.lock:
                    if self.dashboard.job and self.dashboard.job["state"] == "running":
                        return self.send_json(409, {"error": "Дождитесь завершения проверки"})
                    self.dashboard.stopping = True
                self.send_json(202, {"stopped": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            job, created = self.dashboard.start(payload)
        except ProjectError as error:
            return self.send_json(400, {"error": str(error)})
        except Exception:
            return self.send_json(400, {"error": "Недопустимые параметры проверки"})
        self.send_json(202 if created else 409, {"job": job})


def existing_session():
    connection = None
    try:
        state = read_object(STATE, 1024)
        token = state["token"]
        if not isinstance(token, str) or len(token) != 43:
            return None
        connection = HTTPConnection(HOST, PORT, timeout=1)
        connection.request("GET", "/api/ping", headers={"X-AegisOps-Session": token})
        response = connection.getresponse()
        data = json.loads(response.read(1024))
        if response.status == 200 and data == {"application": "aegisops-local-ui-v1"}:
            return token
    except Exception:
        return None
    finally:
        if connection is not None:
            connection.close()
    return None


def serve():
    os.chdir(ROOT)
    environment = role_environment(database=True)
    os.environ.clear()
    os.environ.update(environment)
    dashboard = Dashboard()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    server.dashboard = dashboard
    safe_path(STATE).parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump({"token": dashboard.token}, output)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        if read_object(STATE, 1024) == {"token": dashboard.token}:
            safe_path(STATE).unlink()


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--serve", action="store_true")
    mode.add_argument("--stop", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    arguments = parser.parse_args()
    if arguments.serve:
        serve()
        return
    token = existing_session()
    if arguments.stop:
        if token is None:
            return
        connection = HTTPConnection(HOST, PORT, timeout=3)
        try:
            connection.request(
                "POST",
                "/api/stop",
                body="{}",
                headers={
                    "X-AegisOps-Session": token,
                    "Origin": ORIGIN,
                    "Content-Type": "application/json",
                },
            )
            if connection.getresponse().status != 202:
                raise RuntimeError("Running check must finish before closing")
        finally:
            connection.close()
        return
    if token is None:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--serve"],
            cwd=ROOT,
            env=role_environment(database=True),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        for _ in range(40):
            time.sleep(0.25)
            token = existing_session()
            if token:
                break
    if token is None:
        raise RuntimeError("Local dashboard could not start; port 8765 may be occupied.")
    if not arguments.no_browser:
        webbrowser.open(ORIGIN + "/session/" + token)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        (STATE.parent / "startup-error.txt").write_text(
            "Local UI could not start. Check port 8765, private .env and uv sync --frozen.\n",
            encoding="utf-8",
        )
        if os.name == "nt" and sys.stdout is None and "--serve" not in sys.argv:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                "Не удалось открыть AegisOps. Проверьте установку проекта, "
                "локальный .env и доступность порта 8765. "
                "Если проверка ещё идёт, дождитесь её завершения.",
                "AegisOps — локальный интерфейс",
                0x10,
            )
        raise SystemExit(1) from None
