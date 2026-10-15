"""One durable claim and at most one worker start per static report; no retries."""

import json
import os
from pathlib import Path
from uuid import UUID

from aegisops.domain.common import utc_now
from aegisops.io import read_text, unique_object
from aegisops.project_ai.models import Claim, Result
from aegisops.project_ai.projection import project_summary, report_digest
from aegisops.projects.models import ProjectError, ProjectReport
from aegisops.projects.repository import plain_path


class AnalysisRepository:
    def __init__(self, directory: Path):
        self.directory = directory.absolute()

    def path(self, identifier: UUID, name: str) -> Path:
        if type(identifier) is not UUID or name not in {"claim", "started", "result"}:
            raise ProjectError("Неверный идентификатор разбора")
        return plain_path(self.directory / str(identifier) / f"{name}.json")

    def write(self, identifier: UUID, name: str, data: str) -> None:
        path = self.path(identifier, name)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data.encode())
            stream.flush()
            os.fsync(stream.fileno())

    def claim(self, report: ProjectReport) -> Claim:
        claim = Claim(
            report_id=report.id,
            report_sha256=report_digest(report),
            payload=project_summary(report),
            created_at=utc_now(),
        )
        try:
            self.write(report.id, "claim", claim.model_dump_json())
        except FileExistsError:
            raise ProjectError(
                "Разбор этого отчёта уже запрошен. Автоматического повтора нет"
            ) from None
        return claim

    def read_claim(self, identifier: UUID) -> Claim | None:
        path = self.path(identifier, "claim")
        if not path.exists():
            return None
        claim = Claim.model_validate(
            json.loads(read_text(path, 16384), object_pairs_hook=unique_object)
        )
        if claim.report_id != identifier:
            raise ProjectError("Сохранённый разбор не прошёл проверку")
        return claim

    def read_result(self, identifier: UUID) -> Result | None:
        path = self.path(identifier, "result")
        if not path.exists():
            return None
        result = Result.model_validate(
            json.loads(read_text(path, 131072), object_pairs_hook=unique_object)
        )
        claim = self.read_claim(identifier)
        if (
            claim is None
            or result.report_id != identifier
            or result.report_sha256 != claim.report_sha256
        ):
            raise ProjectError("Сохранённый разбор не прошёл проверку")
        if result.response is not None:
            allowed = {item.code for item in claim.payload.categories}
            if any(item.code not in allowed for item in result.response.actions):
                raise ProjectError("Разбор ссылается на неизвестные замечания")
        return result

    def state(self, report: ProjectReport) -> dict[str, object]:
        claim = self.read_claim(report.id)
        if claim is None:
            return {"status": "not_requested"}
        if claim.report_sha256 != report_digest(report) or claim.payload != project_summary(report):
            raise ProjectError("Отчёт изменился после запроса разбора")
        result = self.read_result(report.id)
        if result is not None:
            return result.model_dump(mode="json")
        return {"status": "pending", "created_at": claim.created_at.isoformat()}

    def fail(self, identifier: UUID) -> None:
        claim = self.read_claim(identifier)
        if claim is None or self.read_result(identifier) is not None:
            return
        result = Result(
            report_id=identifier,
            report_sha256=claim.report_sha256,
            completed_at=utc_now(),
            status="failed",
        )
        try:
            self.write(identifier, "result", result.model_dump_json())
        except FileExistsError:
            return  # Never replace a result already saved by either worker or host.
