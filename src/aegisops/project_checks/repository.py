"""Private full-check history layered on top of the existing run repository.

``FullCheckRepository`` keeps its own ``.local/project-checks`` directory
for claims, results and a per-project index. It reuses ``RunRepository``
only as a private storage helper (``read``/``write``/``path``/``index``)
and never mutates the run repository's own files. Every read re-derives
the full-check result from freshly read sources and compares the entire
DTO, so a tampered saved source is rejected.
"""

import os
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from aegisops.project_checks.models import (
    FullCheckIndex,
    FullCheckResult,
    build_result,
)
from aegisops.project_runner.models import Environment, RunClaim
from aegisops.project_runner.repository import RunRepository
from aegisops.projects.models import ProjectError, ProjectReport
from aegisops.projects.repository import ProjectRepository, plain_path


class FullCheckRepository:
    def __init__(
        self,
        directory: Path,
        projects: ProjectRepository,
        runs: RunRepository,
    ) -> None:
        self.directory = directory.absolute()
        self.projects = projects
        self.runs = runs
        self.lock = RLock()

    def _path(self, identifier: UUID, kind: str) -> Path:
        if type(identifier) is not UUID or kind not in {"claim", "result", "index"}:
            raise ProjectError("Неверный идентификатор проверки")
        return plain_path(self.directory / str(identifier) / f"{kind}.json")

    def _read(self, path: Path, maximum: int = 262144) -> object:
        return self.runs.read(path, maximum)

    def _write(self, identifier: UUID, kind: str, value: str) -> None:
        path = self._path(identifier, kind)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value.encode())
            stream.flush()
            os.fsync(stream.fileno())

    def _index(self, project_id: UUID) -> FullCheckIndex:
        path = self._path(project_id, "index")
        if not path.exists():
            return FullCheckIndex()
        return FullCheckIndex.model_validate(self._read(path, 4096))

    def _save_index(self, project_id: UUID, index: FullCheckIndex) -> None:
        temporary_id = uuid4()
        self._write(temporary_id, "index", index.model_dump_json())
        destination = self._path(project_id, "index")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.replace(self._path(temporary_id, "index"), destination)

    def _claim(self, identifier: UUID) -> RunClaim:
        claim = RunClaim.model_validate(self._read(self._path(identifier, "claim"), 4096))
        if claim.id != identifier:
            raise ProjectError("Запрос проверки не прошёл проверку")
        self.projects.get(claim.project_id)
        return claim

    def _run_claim(self, identifier: UUID) -> RunClaim:
        try:
            return self.runs.claim(identifier)
        except ProjectError:
            raise ProjectError("Запрос проверки не прошёл проверку") from None

    def _static_report(self, claim: RunClaim, result: FullCheckResult) -> ProjectReport | None:
        if result.static_report_id is None:
            return None
        report = self.projects.read_report(result.static_report_id)
        if report.project_id != claim.project_id:
            raise ProjectError("Статический отчёт не соответствует проекту")
        return report

    def _rebuild(self, claim: RunClaim, result: FullCheckResult) -> FullCheckResult:
        run = self.runs.result(claim.id)
        if run is None:
            raise ProjectError("Результат проверки не подтверждён")
        report = self._static_report(claim, result)
        return build_result(claim, report, run)

    def create(self, project_id: UUID, environment: Environment = "minimal") -> RunClaim:
        try:
            with self.lock:
                self.projects.get(project_id)
                claim = self.runs.create(project_id, environment)
                self._write(claim.id, "claim", claim.model_dump_json())
                previous = self._index(project_id)
                index = FullCheckIndex(checks=(claim.id, *previous.checks)[:20])
                self._save_index(project_id, index)
                return claim
        except ProjectError:
            raise
        except Exception:
            raise ProjectError(
                "Не удалось сохранить запрос проверки; автоматического повтора нет"
            ) from None

    def claim(self, identifier: UUID) -> RunClaim:
        try:
            with self.lock:
                claim = self._claim(identifier)
                if self._run_claim(identifier) != claim:
                    raise ProjectError("Запрос проверки не прошёл проверку")
                return claim
        except ProjectError:
            raise
        except Exception:
            raise ProjectError("Запрос проверки не прошёл проверку") from None

    def save(self, result: FullCheckResult) -> None:
        try:
            result = FullCheckResult.model_validate(result)
            with self.lock:
                claim = self._claim(result.id)
                if self._run_claim(result.id) != claim:
                    raise ProjectError("Запрос проверки не прошёл проверку")
                expected = self._rebuild(claim, result)
                if expected != result:
                    raise ProjectError("Результат проверки не подтверждён")
                self._write(result.id, "result", result.model_dump_json())
                if self.result(result.id) != result:
                    raise ProjectError("Результат проверки не подтверждён")
        except ProjectError:
            raise
        except Exception:
            raise ProjectError("Результат проверки не подтверждён") from None

    def result(self, identifier: UUID) -> FullCheckResult | None:
        try:
            with self.lock:
                claim = self._claim(identifier)
                if self._run_claim(identifier) != claim:
                    raise ProjectError("Запрос проверки не прошёл проверку")
                path = self._path(identifier, "result")
                if not path.exists():
                    return None
                result = FullCheckResult.model_validate(self._read(path))
                if result.id != identifier or result.project_id != claim.project_id:
                    raise ProjectError("Результат проверки не прошёл проверку")
                expected = self._rebuild(claim, result)
                if expected != result:
                    raise ProjectError("Результат проверки не прошёл проверку")
                return result
        except ProjectError:
            raise
        except Exception:
            raise ProjectError("Результат проверки не прошёл проверку") from None

    def state(self, identifier: UUID) -> dict[str, object]:
        claim = self.claim(identifier)
        result = self.result(identifier)
        return {
            "claim": claim.model_dump(mode="json"),
            "result": result.model_dump(mode="json") if result else None,
        }

    def history(self, project_id: UUID) -> list[dict[str, object]]:
        self.projects.get(project_id)
        entries: list[dict[str, object]] = []
        for identifier in self._index(project_id).checks:
            claim = self._claim(identifier)
            if claim.project_id != project_id:
                raise ProjectError("История проверок повреждена")
            if self._run_claim(identifier) != claim:
                raise ProjectError("История проверок повреждена")
            result = self.result(identifier)
            entries.append(
                {
                    "claim": claim.model_dump(mode="json"),
                    "result": result.model_dump(mode="json") if result else None,
                }
            )
        return entries


__all__ = ("FullCheckRepository",)
