"""Bounded local static checks. Never import, build, install or run project code."""

import ast
import json
import os
import time
import tomllib
from pathlib import Path
from uuid import uuid4

import yaml
from yaml.tokens import AliasToken

from aegisops.domain.common import utc_now
from aegisops.io import UniqueSafeLoader, unique_object
from aegisops.projects.manifest import RECOMMENDATIONS, ManifestChecks
from aegisops.projects.models import Finding, FindingCode, Project, ProjectReport
from aegisops.projects.repository import plain_path, project_root

EXCLUDED = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "target",
        "vendor",
        "__pycache__",
        ".local",
        ".idea",
        ".vscode",
        ".next",
        ".terraform",
        ".cache",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".ssh",
        ".aws",
        ".kube",
        ".codex",
    }
)
PRIVATE_SUFFIXES = frozenset({".pem", ".key", ".pfx", ".p12", ".dpapi", ".log"})
FORMATS = frozenset({".py", ".json", ".yaml", ".yml", ".toml"})
LOCKS = frozenset({"package-lock.json", "composer.lock", "uv.lock", "yarn.lock", "pnpm-lock.yaml"})
MAX_FILE_BYTES = 262144
MAX_TOTAL_BYTES = 8 * 1024 * 1024


def reject_constant(value: str) -> None:
    raise ValueError("non-standard JSON constant")


def excluded_file(name: str) -> bool:
    lower = name.lower()
    return (
        lower.startswith((".env", "kubeconfig", "credentials", "secrets.", "secret.", "token."))
        or "kubeconfig" in lower
        or Path(name).suffix.lower() in PRIVATE_SUFFIXES
        or lower in LOCKS
    )


def scan_project(project: Project) -> ProjectReport:
    root = project_root(project)
    started, deadline = utc_now(), time.monotonic() + 20
    seen = checked = skipped = total = entries = 0
    partial = False
    findings: list[Finding] = []
    stacks: set[str] = set()

    def emit(item: Finding) -> None:
        nonlocal partial
        if len(findings) >= 199:
            partial = True
            return
        findings.append(item)

    manifests = ManifestChecks(emit, stacks)

    def finding(
        file: str, code: FindingCode, message: str, *, error: bool = False, line: int | None = None
    ) -> None:
        emit(
            Finding(
                severity="error" if error else "warning",
                code=code,
                file=file,
                line=line,
                message=message,
                recommendation=RECOMMENDATIONS[code],
            )
        )

    pending = [(root, 0)]
    while pending:
        if time.monotonic() > deadline or entries >= 4000 or seen >= 1000 or len(findings) >= 199:
            partial = True
            break
        directory, depth = pending.pop()
        try:
            plain_path(directory)
            with os.scandir(directory) as iterator:
                for item in iterator:
                    entries += 1
                    if (
                        entries > 4000
                        or seen >= 1000
                        or time.monotonic() > deadline
                        or len(findings) >= 199
                        or total >= MAX_TOTAL_BYTES
                    ):
                        partial = True
                        break
                    path = Path(item.path)
                    relative = path.relative_to(root).as_posix()
                    if len(relative) > 1024:
                        partial = True
                        skipped += 1
                        continue
                    if item.is_symlink() or path.is_junction():
                        skipped += 1
                        continue
                    if item.is_dir(follow_symlinks=False):
                        if item.name.lower() in EXCLUDED:
                            skipped += 1
                        elif depth >= 12:
                            partial = True
                        else:
                            pending.append((path, depth + 1))
                        continue
                    if not item.is_file(follow_symlinks=False):
                        skipped += 1
                        continue
                    seen += 1
                    suffix = path.suffix.lower()
                    if excluded_file(item.name):
                        skipped += 1
                        continue
                    if suffix == ".py" or item.name in {"pyproject.toml", "requirements.txt"}:
                        stacks.add("Python")
                    if suffix in {".js", ".jsx", ".ts", ".tsx"} or item.name == "package.json":
                        stacks.add("JavaScript / TypeScript")
                    if item.name.lower().startswith("dockerfile"):
                        stacks.add("Docker")
                    if suffix not in FORMATS:
                        skipped += 1
                        continue
                    try:
                        plain_path(path)
                        with path.open("rb") as source:
                            data = source.read(MAX_FILE_BYTES + 1)
                        if len(data) > MAX_FILE_BYTES:
                            partial = True
                            skipped += 1
                            continue
                        if total + len(data) > MAX_TOTAL_BYTES:
                            partial = True
                            skipped += 1
                            total = MAX_TOTAL_BYTES
                            break
                        total += len(data)
                        if suffix == ".py":
                            ast.parse(data, filename="project-file")
                        else:
                            text = data.decode("utf-8-sig")
                            if suffix == ".json":
                                document = json.loads(
                                    text,
                                    object_pairs_hook=unique_object,
                                    parse_constant=reject_constant,
                                )
                                manifests.check(document, relative, "document[1]")
                            elif suffix == ".toml":
                                tomllib.loads(text)
                            else:
                                aliases = False
                                for index, token in enumerate(yaml.scan(text)):
                                    if isinstance(token, AliasToken) or index >= 20000:
                                        aliases = True
                                        break
                                if aliases:
                                    partial = True
                                    skipped += 1
                                    finding(
                                        relative,
                                        "yaml_alias",
                                        "YAML-ссылки или сложный документ: проверка ограничена",
                                    )
                                    continue
                                for index, document in enumerate(
                                    yaml.load_all(text, Loader=UniqueSafeLoader)
                                ):
                                    if index >= 32:
                                        partial = True
                                        break
                                    manifests.check(document, relative, f"document[{index + 1}]")
                        checked += 1
                    except UnicodeDecodeError:
                        partial = True
                        skipped += 1
                        finding(
                            relative, "encoding", "Кодировка файла не поддержана; ожидается UTF-8"
                        )
                    except (SyntaxError, ValueError, yaml.YAMLError) as error:
                        checked += 1
                        line = getattr(error, "lineno", None)
                        finding(
                            relative,
                            "syntax",
                            "Некорректный синтаксис или дублирующиеся ключи",
                            error=True,
                            line=line if type(line) is int and line > 0 else None,
                        )
                    except (OSError, RecursionError, MemoryError):
                        partial = True
                        skipped += 1
                        finding(
                            relative,
                            "read_failed",
                            "Не удалось полностью прочитать или разобрать файл",
                        )
        except OSError:
            partial = True
            finding(
                directory.relative_to(root).as_posix(), "read_failed", "Папка недоступна для чтения"
            )
    partial = partial or manifests.partial
    if checked == 0:
        partial = True
        finding(".", "limit", "Не найдено файлов, которые удалось проверить этим профилем")
    if partial:
        findings.append(
            Finding(
                severity="warning",
                code="limit",
                file=".",
                message="Проверка частичная: достигнут лимит или часть файлов недоступна",
                recommendation=RECOMMENDATIONS["limit"],
            )
        )
    return ProjectReport(
        rules_version="local-static-v2",
        id=uuid4(),
        project_id=project.id,
        started_at=started,
        completed_at=utc_now(),
        status="partial" if partial else "issues" if findings else "passed",
        files_seen=seen,
        files_checked=checked,
        skipped=min(skipped, 4000),
        stacks=tuple(sorted(stacks)),
        findings=tuple(findings),
        partial=partial,
    )
