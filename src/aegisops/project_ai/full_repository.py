"""Single-attempt AI history for immutable full-check summaries."""

import json
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from aegisops.domain.common import DTO, Sha256, UTCDateTime, utc_now
from aegisops.io import read_text, unique_object
from aegisops.project_ai.full_models import FullSummary
from aegisops.project_ai.full_projection import full_summary
from aegisops.project_ai.repository import AnalysisRepository
from aegisops.project_ai.review_models import ReviewExplanation
from aegisops.project_checks.models import FullCheckResult, canonical_digest
from aegisops.project_checks.repository import FullCheckRepository
from aegisops.projects.models import ProjectError


class FullClaim(DTO):
    check_id: UUID
    check_sha256: Sha256
    payload: FullSummary
    created_at: UTCDateTime


class FullResult(DTO):
    check_id: UUID
    check_sha256: Sha256
    completed_at: UTCDateTime
    status: Literal["completed", "failed"]
    model: Literal["deepseek/deepseek-v4.1-flash"] = "deepseek/deepseek-v4.1-flash"
    response: ReviewExplanation | None = None
    input_tokens: Annotated[int, Field(strict=True, ge=0, le=100000)] = 0
    output_tokens: Annotated[int, Field(strict=True, ge=0, le=1600)] = 0
    execution_performed: Literal[False] = False

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.status == "completed") != (self.response is not None):
            raise ValueError("inconsistent full explanation")
        return self


class FullAnalysisRepository:
    def __init__(self, directory: Path, checks: FullCheckRepository) -> None:
        self.files = AnalysisRepository(directory)
        self.checks = checks

    def write(self, identifier: UUID, name: str, data: str) -> None:
        self.files.write(identifier, name, data)

    def evidence(self, identifier: UUID) -> tuple[FullCheckResult, FullSummary]:
        check = self.checks.result(identifier)
        if check is None:
            raise ProjectError("Полная проверка ещё не завершена")
        claim = self.checks.claim(identifier)
        run = self.checks.runs.result(identifier)
        if run is None:
            raise ProjectError("Результат проверки не подтверждён")
        report = None
        if check.static_report_id is not None:
            report = self.checks.projects.read_report(check.static_report_id)
        summary = full_summary(check, report, claim, run)
        return check, summary

    def read_claim(self, identifier: UUID) -> FullClaim | None:
        path = self.files.path(identifier, "claim")
        if not path.exists():
            return None
        claim = FullClaim.model_validate(
            json.loads(read_text(path, 16384), object_pairs_hook=unique_object)
        )
        if claim.check_id != identifier:
            raise ProjectError("Запрос разбора полной проверки повреждён")
        return claim

    def read_result(self, identifier: UUID) -> FullResult | None:
        path = self.files.path(identifier, "result")
        if not path.exists():
            return None
        result = FullResult.model_validate(
            json.loads(read_text(path, 131072), object_pairs_hook=unique_object)
        )
        claim = self.read_claim(identifier)
        if (
            claim is None
            or result.check_id != identifier
            or result.check_sha256 != claim.check_sha256
            or result.completed_at < claim.created_at
        ):
            raise ProjectError("Ответ разбора полной проверки повреждён")
        if result.response is not None:
            allowed = {item.code for item in claim.payload.categories}
            if any(item.code not in allowed for item in result.response.actions):
                raise ProjectError("Ответ ссылается на отсутствующие проблемы")
        return result

    def claim(self, identifier: UUID) -> FullClaim:
        check, summary = self.evidence(identifier)
        if self.read_claim(identifier) is None and (
            self.files.path(identifier, "result").exists()
            or self.files.path(identifier, "started").exists()
        ):
            raise ProjectError("История разбора полной проверки повреждена")
        claim = FullClaim(
            check_id=identifier,
            check_sha256=canonical_digest(check),
            payload=summary,
            created_at=utc_now(),
        )
        try:
            self.write(identifier, "claim", claim.model_dump_json())
        except FileExistsError:
            raise ProjectError(
                "Разбор этой полной проверки уже запрошен. Повтор не отправляется"
            ) from None
        return claim

    def state(self, identifier: UUID) -> dict[str, object]:
        check, summary = self.evidence(identifier)
        claim = self.read_claim(identifier)
        if claim is None:
            if (
                self.files.path(identifier, "result").exists()
                or self.files.path(identifier, "started").exists()
            ):
                raise ProjectError("История разбора полной проверки повреждена")
            return {"status": "not_requested"}
        if claim.check_sha256 != canonical_digest(check) or claim.payload != summary:
            raise ProjectError("Полная проверка изменилась после запроса разбора")
        result = self.read_result(identifier)
        return (
            result.model_dump(mode="json")
            if result
            else {"status": "pending", "created_at": claim.created_at.isoformat()}
        )

    def fail(self, identifier: UUID) -> None:
        claim = self.read_claim(identifier)
        if claim is None or self.read_result(identifier) is not None:
            return
        result = FullResult(
            check_id=identifier,
            check_sha256=claim.check_sha256,
            completed_at=utc_now(),
            status="failed",
        )
        try:
            self.write(identifier, "result", result.model_dump_json())
        except FileExistsError:
            return


__all__ = ("FullAnalysisRepository", "FullClaim", "FullResult")
