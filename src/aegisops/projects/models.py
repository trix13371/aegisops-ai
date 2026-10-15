"""Local-only settings and reports, separate from Kubernetes incident audit."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, StrictBool, StrictStr, model_validator

from aegisops.domain.common import DTO, UTCDateTime

Name = Annotated[StrictStr, Field(min_length=1, max_length=80)]


class ProjectError(ValueError):
    def __init__(self, message: str = "Не удалось прочитать настройки или отчёт проекта"):
        super().__init__(message)


class Project(DTO):
    id: UUID
    name: Name
    path: Annotated[StrictStr, Field(min_length=1, max_length=1024)]
    device: int
    inode: int
    added_at: UTCDateTime
    reports: tuple[UUID, ...] = Field(default=(), max_length=20)


class ProjectState(DTO):
    format: Literal["aegisops-local-projects-v1"] = "aegisops-local-projects-v1"
    projects: tuple[Project, ...] = Field(default=(), max_length=50)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({project.id for project in self.projects}) != len(self.projects):
            raise ValueError("duplicate project identity")
        return self


FindingCode = Literal[
    "syntax",
    "encoding",
    "read_failed",
    "yaml_alias",
    "privileged",
    "host_namespace",
    "mutable_image",
    "missing_limits",
    "limit",
]


class Finding(DTO):
    severity: Literal["error", "warning"]
    code: FindingCode
    file: Annotated[StrictStr, Field(min_length=1, max_length=1024)]
    line: int | None = Field(default=None, strict=True, ge=1)
    message: Annotated[StrictStr, Field(min_length=1, max_length=300)]


class ProjectReport(DTO):
    format: Literal["aegisops-local-static-v1"] = "aegisops-local-static-v1"
    id: UUID
    project_id: UUID
    started_at: UTCDateTime
    completed_at: UTCDateTime
    status: Literal["passed", "issues", "partial"]
    files_seen: int = Field(strict=True, ge=0, le=4000)
    files_checked: int = Field(strict=True, ge=0, le=1000)
    skipped: int = Field(strict=True, ge=0, le=4000)
    stacks: tuple[StrictStr, ...] = Field(max_length=8)
    findings: tuple[Finding, ...] = Field(max_length=200)
    partial: StrictBool
    execution_performed: Literal[False] = False

    @model_validator(mode="after")
    def consistent(self) -> Self:
        expected = "partial" if self.partial else "issues" if self.findings else "passed"
        if (
            self.status != expected
            or self.files_checked > self.files_seen
            or (self.files_checked == 0 and not self.partial)
            or self.completed_at < self.started_at
        ):
            raise ValueError("inconsistent project report")
        return self
