"""Single-attempt storage for the synthetic project-code evaluation suites.

Benchmarks do not belong to a registered project: this repository uses
``CodeRepository`` purely for low-level path/read/write plumbing (claim,
started and result slots plus a bounded recent-suite ``index``) and never
calls its project-facing preview/claim APIs. Every read revalidates the
fixture digest so a mutated suite is rejected instead of scored.

Two closed profiles are supported: the legacy ``synthetic-line-overlap-v1``
(three cases) and ``synthetic-file-overlap-v2`` (nine cases). Claims without
an explicit ``suite_profile`` are read as v1. Each read validates the stored
profile against its own fixture digest and result class; unknown or
mismatched profiles are rejected.
"""

import os
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import UUID, uuid4

from aegisops.domain.common import utc_now
from aegisops.project_code import evaluation as _v1
from aegisops.project_code import evaluation_v2 as _v2
from aegisops.project_code.evaluation import EvalClaim
from aegisops.project_code.repository import CodeIndex, CodeRepository
from aegisops.projects.repository import ProjectRepository, plain_path

_INDEX_ID = UUID(int=0)

EvalProfile = Literal["synthetic-line-overlap-v1", "synthetic-file-overlap-v2"]

_V1_PROFILE: EvalProfile = "synthetic-line-overlap-v1"
_V2_PROFILE: EvalProfile = "synthetic-file-overlap-v2"

_V1_RESULT_LIMIT = 196608
_V2_RESULT_LIMIT = 524288


def _profile_of(claim: EvalClaim) -> EvalProfile:
    """Return the stored profile; absent profile reads as v1."""
    profile = claim.suite_profile
    if profile not in (_V1_PROFILE, _V2_PROFILE):
        raise ValueError("Неизвестный профиль оценки")
    return profile


def _fixture_digest(profile: EvalProfile) -> str:
    if profile == _V1_PROFILE:
        return _v1.fixture_digest()
    if profile == _V2_PROFILE:
        return _v2.fixture_digest()
    raise ValueError("Неизвестный профиль оценки")


def _result_class(
    profile: EvalProfile,
) -> type[_v1.EvalResult] | type[_v2.EvalResultV2]:
    if profile == _V1_PROFILE:
        return _v1.EvalResult
    if profile == _V2_PROFILE:
        return _v2.EvalResultV2
    raise ValueError("Неизвестный профиль оценки")


def _result_limit(profile: EvalProfile) -> int:
    if profile == _V1_PROFILE:
        return _V1_RESULT_LIMIT
    if profile == _V2_PROFILE:
        return _V2_RESULT_LIMIT
    raise ValueError("Неизвестный профиль оценки")


class EvalRepository:
    """Filesystem-backed, single-attempt evaluation claim/result storage."""

    def __init__(self, directory: Path, projects: ProjectRepository) -> None:
        self.directory = directory.absolute()
        self.projects = projects
        # Reused only for path()/read()/write() low-level slots.
        self.store = CodeRepository(directory, projects)
        self.lock = RLock()

    def _index(self, identifier: UUID) -> CodeIndex:
        """Bounded recent-suite index stored in the ``index`` slot."""
        path = self.store.path(identifier, "index")
        if not path.exists():
            return CodeIndex()
        return CodeIndex.model_validate(self.store.read(path, 4096))

    def _write(self, identifier: UUID, kind: str, value: str) -> None:
        self.store.write(identifier, kind, value)

    def _read(self, identifier: UUID, kind: str, maximum: int = 4096) -> object:
        return self.store.read(self.store.path(identifier, kind), maximum)

    def _result_path(self, identifier: UUID) -> Path:
        return plain_path(self.store.path(identifier, "result"))

    def create(self, suite_profile: EvalProfile = _V1_PROFILE) -> EvalClaim:
        """Claim a fresh suite: O_EXCL claim plus atomically updated index.

        The default profile remains the legacy three-case v1 suite. Only the
        two closed profiles are accepted.
        """
        if suite_profile not in (_V1_PROFILE, _V2_PROFILE):
            raise ValueError("Неизвестный профиль оценки")
        identifier = uuid4()
        claim = EvalClaim(
            id=identifier,
            created_at=utc_now(),
            fixture_sha256=_fixture_digest(suite_profile),
            suite_profile=suite_profile,
        )
        with self.lock:
            path = self.store.path(identifier, "claim")
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(claim.model_dump_json().encode())
                stream.flush()
                os.fsync(stream.fileno())
            previous = self._index(_INDEX_ID)
            index = CodeIndex(previews=(identifier, *previous.previews)[:20])
            temporary_id = uuid4()
            self._write(temporary_id, "index", index.model_dump_json())
            destination = self.store.path(_INDEX_ID, "index")
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.replace(self.store.path(temporary_id, "index"), destination)
        return claim

    def read_claim(self, identifier: UUID) -> EvalClaim | None:
        """Load and revalidate a claim against its stored-profile digest."""
        path = self.store.path(identifier, "claim")
        if not path.exists():
            return None
        claim = EvalClaim.model_validate(self._read(identifier, "claim"))
        if claim.id != identifier:
            raise ValueError("Повреждён запрос оценки")
        profile = _profile_of(claim)
        if claim.fixture_sha256 != _fixture_digest(profile):
            raise ValueError("Набор оценки изменился после запроса")
        return claim

    def result(self, identifier: UUID) -> _v1.EvalResult | _v2.EvalResultV2 | None:
        """Load a result, validating hash, id, time and report via the DTO.

        The result class and read cap are chosen from the stored claim
        profile; unknown or mismatched result types are rejected.
        """
        if not self._result_path(identifier).exists():
            return None
        claim = self.read_claim(identifier)
        if claim is None:
            raise ValueError("Ответ оценки повреждён")
        profile = _profile_of(claim)
        result_class = _result_class(profile)
        result = result_class.model_validate(
            self._read(identifier, "result", _result_limit(profile))
        )
        if (
            result.id != identifier
            or result.fixture_sha256 != claim.fixture_sha256
            or result.completed_at < claim.created_at
        ):
            raise ValueError("Ответ оценки повреждён")
        return result

    def save(self, result: _v1.EvalResult | _v2.EvalResultV2) -> None:
        """Persist a result without overwriting an existing outcome.

        The typed object is revalidated and its type must match the stored
        claim profile before writing.
        """
        claim = self.read_claim(result.id)
        if claim is None:
            raise ValueError("Оценка не запрошена")
        profile = _profile_of(claim)
        result_class = _result_class(profile)
        if type(result) is not result_class:
            raise ValueError("Ответ оценки повреждён")
        checked = result_class.model_validate(result.model_dump())
        if checked.fixture_sha256 != claim.fixture_sha256:
            raise ValueError("Ответ оценки повреждён")
        if checked.completed_at < claim.created_at:
            raise ValueError("Ответ оценки повреждён")
        self._write(checked.id, "result", checked.model_dump_json())

    def state(self, identifier: UUID) -> dict[str, object]:
        """Return claim plus derived status; reject orphan artifacts."""
        claim = self.read_claim(identifier)
        if claim is None:
            if (
                self._result_path(identifier).exists()
                or self.store.path(identifier, "started").exists()
            ):
                raise ValueError("История оценки повреждена")
            return {"claim": None, "status": "pending", "result": None}
        result = self.result(identifier)
        status = result.status if result is not None else "pending"
        return {
            "claim": claim.model_dump(mode="json"),
            "status": status,
            "result": result.model_dump(mode="json") if result else None,
        }

    def history(self) -> list[dict[str, object]]:
        """List the last twenty suite states referenced by the index.

        Both profiles are supported without reinterpretation: each entry is
        dispatched by its own stored claim profile.
        """
        entries: list[dict[str, object]] = []
        for identifier in self._index(_INDEX_ID).previews:
            entries.append(self.state(identifier))
        return entries


__all__ = ("EvalRepository",)
