"""Private bounded registry and append-only report files; never modify target projects."""

import json
import os
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from aegisops.domain.common import utc_now
from aegisops.io import read_text, unique_object
from aegisops.projects.models import Project, ProjectError, ProjectReport, ProjectState


def plain_path(path: Path) -> Path:
    if not path.is_absolute() or ".." in path.parts or str(path).startswith(("\\\\", "//")):
        raise ProjectError("Укажите абсолютный путь к локальной папке проекта")
    for component in (path, *path.parents):
        if component.is_symlink() or component.is_junction():
            raise ProjectError("Ссылки и перенаправления папок не поддерживаются")
    return path


def project_root(project: Project) -> Path:
    try:
        root = plain_path(Path(project.path))
        info = root.stat()
        if not root.is_dir() or (info.st_dev, info.st_ino) != (project.device, project.inode):
            raise ProjectError()
        return root
    except Exception:
        raise ProjectError("Папка недоступна или была заменена. Добавьте проект заново") from None


class ProjectRepository:
    def __init__(self, directory: Path):
        self.directory = directory.absolute()
        self.lock = RLock()

    def _read_json(self, path: Path, maximum: int) -> object:
        return json.loads(read_text(plain_path(path), maximum), object_pairs_hook=unique_object)

    def _read(self) -> ProjectState:
        path = plain_path(self.directory / "registry.json")
        if not path.exists():
            return ProjectState()
        return ProjectState.model_validate(self._read_json(path, 262144))

    def _new(self, path: Path, data: bytes) -> None:
        plain_path(path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())

    def _save(self, state: ProjectState) -> None:
        path = plain_path(self.directory / "registry.json")
        temporary = self.directory / f"registry-{uuid4()}.tmp"
        self._new(temporary, state.model_dump_json().encode())
        try:
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                plain_path(temporary).unlink()

    def list(self) -> tuple[Project, ...]:
        try:
            with self.lock:
                return self._read().projects
        except Exception:
            raise ProjectError() from None

    def get(self, identifier: UUID) -> Project:
        for project in self.list():
            if project.id == identifier:
                return project
        raise ProjectError("Проект не найден")

    def add(self, name: object, path: object) -> Project:
        if (
            type(name) is not str
            or not 1 <= len(name.strip()) <= 80
            or type(path) is not str
            or not 1 <= len(path) <= 1024
            or any(ord(char) < 32 for char in name + path)
        ):
            raise ProjectError("Укажите название и абсолютный путь к папке проекта")
        path = path.strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        try:
            root = plain_path(Path(path))
            if not root.is_dir():
                raise ProjectError("Папка не найдена")
            root = root.resolve(strict=True)
            # A drive/root or home is not a project; never sweep a user's whole profile.
            if root.parent == root or root == Path.home().resolve():
                raise ProjectError(
                    "Выберите папку проекта, а не весь диск или профиль пользователя"
                )
            info = root.stat()
            project = Project(
                id=uuid4(),
                name=name.strip(),
                path=str(root),
                device=info.st_dev,
                inode=info.st_ino,
                added_at=utc_now(),
            )
            with self.lock:
                state = self._read()
                if any(
                    os.path.normcase(item.path) == os.path.normcase(str(root))
                    for item in state.projects
                ):
                    raise ProjectError("Эта папка уже добавлена")
                self._save(ProjectState(projects=(*state.projects, project)))
            return project
        except ProjectError:
            raise
        except Exception:
            raise ProjectError(
                "Не удалось добавить проект. Проверьте папку и локальный реестр"
            ) from None

    def remove(self, identifier: UUID) -> None:
        try:
            with self.lock:
                state = self._read()
                self.get(identifier)
                self._save(
                    ProjectState(projects=tuple(p for p in state.projects if p.id != identifier))
                )
        except ProjectError:
            raise
        except Exception:
            raise ProjectError() from None

    def save_report(self, report: ProjectReport) -> None:
        try:
            report = ProjectReport.model_validate(report)
            with self.lock:
                state = self._read()
                project = self.get(report.project_id)
                path = self.directory / "reports" / f"{report.id}.json"
                self._new(path, report.model_dump_json().encode())
                if self.read_report(report.id) != report:
                    raise ProjectError()
                updated = project.model_copy(update={"reports": (report.id, *project.reports)[:20]})
                self._save(
                    ProjectState(
                        projects=tuple(
                            updated if item.id == project.id else item for item in state.projects
                        )
                    )
                )
        except Exception:
            raise ProjectError("Отчёт не подтверждён. Автоматического повтора нет") from None

    def read_report(self, identifier: UUID) -> ProjectReport:
        try:
            if type(identifier) is not UUID:
                raise ProjectError()
            path = self.directory / "reports" / f"{identifier}.json"
            report = ProjectReport.model_validate(self._read_json(path, 2097152))
            if report.id != identifier:
                raise ProjectError()
            return report
        except Exception:
            raise ProjectError("Отчёт недоступен") from None

    def history(self, identifier: UUID) -> tuple[ProjectReport, ...]:
        project = self.get(identifier)
        reports = tuple(self.read_report(item) for item in project.reports)
        if any(report.project_id != identifier for report in reports):
            raise ProjectError("История проекта не прошла проверку")
        return reports
