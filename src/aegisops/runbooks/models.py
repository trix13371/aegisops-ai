"""Bounded immutable reference documents and canonical content hashes."""

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StrictStr, model_validator

from aegisops.context.sanitizer import sanitize_data
from aegisops.domain.common import DTO, Sha256

RunbookID = Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")]
Version = Annotated[
    StrictStr, Field(max_length=16, pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
]


class RunbookError(ValueError):
    def __init__(self) -> None:
        super().__init__("runbook data rejected")


class RunbookSignal(StrEnum):
    CRASHLOOP = "crashloop"
    OOM_KILLED = "oom_killed"
    EXIT_NONZERO = "exit_nonzero"


def canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class RunbookDocument(DTO):
    id: RunbookID
    version: Version
    data_class: Literal["public"]
    title: Annotated[StrictStr, Field(min_length=1, max_length=96)]
    signal: RunbookSignal
    steps: tuple[Annotated[StrictStr, Field(min_length=1, max_length=180)], ...] = Field(
        min_length=1, max_length=4
    )

    @model_validator(mode="after")
    def bounded_clean_data(self) -> "RunbookDocument":
        data = self.model_dump(mode="json")
        if len(canonical(data).encode("utf-8")) > 1024 or sanitize_data(data) != data:
            raise ValueError("runbook document exceeds bounds or contains credential-shaped text")
        if not self.title.strip() or any(not step.strip() for step in self.steps):
            raise ValueError("blank runbook content")
        return self


class RunbookCatalog(DTO):
    catalog_version: Version
    documents: tuple[RunbookDocument, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def unique_documents(self) -> "RunbookCatalog":
        if len({doc.id for doc in self.documents}) != len(self.documents):
            raise ValueError("duplicate runbook ID")
        signals = {doc.signal for doc in self.documents}
        if len(signals) != len(self.documents) or RunbookSignal.CRASHLOOP not in signals:
            raise ValueError("duplicate signals or missing general runbook")
        return self


def selection_hash(catalog_version: str, documents: tuple[RunbookDocument, ...]) -> str:
    return digest(
        {
            "catalog_version": catalog_version,
            "documents": [
                {
                    "id": doc.id,
                    "version": doc.version,
                    "sha256": digest(doc.model_dump(mode="json")),
                }
                for doc in documents
            ],
        }
    )


class RunbookSelection(DTO):
    catalog_version: Version
    documents: tuple[RunbookDocument, ...] = Field(max_length=2)
    selection_hash: Sha256

    @model_validator(mode="after")
    def bound_to_documents(self) -> "RunbookSelection":
        if len({doc.id for doc in self.documents}) != len(self.documents):
            raise ValueError("duplicate selection ID")
        signals = [doc.signal for doc in self.documents]
        if len(set(signals)) != len(signals) or signals != sorted(
            signals, key=lambda signal: signal.value
        ):
            raise ValueError("duplicate or unsorted selection signals")
        if self.selection_hash != selection_hash(self.catalog_version, self.documents):
            raise ValueError("runbook selection hash mismatch")
        if len(canonical(self.model_dump(mode="json")).encode("utf-8")) > 2048:
            raise ValueError("runbook selection exceeds byte limit")
        return self
