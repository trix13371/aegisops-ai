"""Private run history; no source, network, credentials or execution access."""

import json
import os
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from aegisops.domain.common import utc_now
from aegisops.io import read_text, unique_object
from aegisops.project_runner.diagnostics import missing_modules
from aegisops.project_runner.models import Environment, RunClaim, RunIndex, RunResult, profile_for
from aegisops.projects.models import ProjectError
from aegisops.projects.repository import plain_path


class RunRepository:
    def __init__(self, directory: Path):
        self.directory = directory.absolute()
        self.lock = RLock()

    def path(self, identifier: UUID, kind: str) -> Path:
        if type(identifier) is not UUID or kind not in {"claim", "started", "result", "index"}:
            raise ProjectError("Неверный идентификатор запуска")
        return plain_path(self.directory / str(identifier) / f"{kind}.json")

    def read(self, path: Path, maximum: int = 262144) -> object:
        return json.loads(read_text(plain_path(path), maximum), object_pairs_hook=unique_object)

    def write(self, identifier: UUID, kind: str, value: str) -> None:
        path = self.path(identifier, kind)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value.encode())
            stream.flush()
            os.fsync(stream.fileno())

    def index(self, project_id: UUID) -> RunIndex:
        path = self.path(project_id, "index")
        return RunIndex.model_validate(self.read(path, 4096)) if path.exists() else RunIndex()

    def create(self, project_id: UUID, environment: Environment = "minimal") -> RunClaim:
        try:
            with self.lock:
                previous = self.index(project_id)
                claim = RunClaim(
                    id=uuid4(),
                    project_id=project_id,
                    created_at=utc_now(),
                    environment=environment,
                    profile=profile_for(environment),
                )
                self.write(claim.id, "claim", claim.model_dump_json())
                index = RunIndex(runs=(claim.id, *previous.runs)[:20])
                temporary_id = uuid4()
                self.write(temporary_id, "index", index.model_dump_json())
                destination = self.path(project_id, "index")
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                os.replace(self.path(temporary_id, "index"), destination)
                return claim
        except Exception:
            raise ProjectError(
                "Не удалось сохранить запрос запуска; автоматического повтора нет"
            ) from None

    def claim(self, identifier: UUID) -> RunClaim:
        claim = RunClaim.model_validate(self.read(self.path(identifier, "claim"), 4096))
        if claim.id != identifier:
            raise ProjectError("Запрос запуска не прошёл проверку")
        return claim

    def result(self, identifier: UUID) -> RunResult | None:
        path = self.path(identifier, "result")
        if not path.exists():
            return None
        claim = self.claim(identifier)
        result = RunResult.model_validate(self.read(path))
        if (
            result.id != identifier
            or result.project_id != claim.project_id
            or result.environment != claim.environment
            or result.profile != claim.profile
            or result.completed_at < claim.created_at
        ):
            raise ProjectError("Результат запуска не прошёл проверку")
        return result

    def save(self, result: RunResult) -> None:
        result = RunResult.model_validate(result)
        claim = self.claim(result.id)
        if (
            result.project_id != claim.project_id
            or result.environment != claim.environment
            or result.profile != claim.profile
            or result.completed_at < claim.created_at
        ):
            raise ProjectError("Результат запуска не соответствует запросу")
        self.write(result.id, "result", result.model_dump_json())
        if self.result(result.id) != result:
            raise ProjectError("Результат запуска не подтверждён")

    def state(self, identifier: UUID) -> dict[str, object]:
        claim = self.claim(identifier)
        result = self.result(identifier)
        return {
            "claim": claim.model_dump(mode="json"),
            "result": result.model_dump(mode="json") if result else None,
            "missing_modules": list(missing_modules(result.checks[1]))
            if result and len(result.checks) == 2
            else [],
        }

    def history(self, project_id: UUID) -> list[dict[str, object]]:
        result = []
        for identifier in self.index(project_id).runs:
            if self.claim(identifier).project_id != project_id:
                raise ProjectError("История запусков повреждена")
            state = self.state(identifier)
            if isinstance(state["result"], dict):
                state["result"] = {
                    key: value for key, value in state["result"].items() if key != "checks"
                }
            result.append(state)
        return result
