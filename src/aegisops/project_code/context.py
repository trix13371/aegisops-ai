"""Local project map and related-file suggestions (UI17).

Read-only, bounded analysis of local sources. Nothing is executed, fetched or
sent anywhere; captured text is untrusted data and never leaves this module.
"""

import ast
import re
import time
from pathlib import Path
from typing import Final, TypedDict, cast

from aegisops.project_code.models import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
)
from aegisops.project_code.source import SUFFIX_LANGUAGE, capture, catalog
from aegisops.projects.models import Project, ProjectError

MAX_CANDIDATES: Final[int] = 80
MAX_SOURCE_BYTES: Final[int] = 262144
DEADLINE_SECONDS: Final[float] = 5.0
MAX_SYMBOLS: Final[int] = 12
MAX_IMPORTS: Final[int] = 20
MAX_RELATED: Final[int] = 20
MAX_SUGGESTIONS: Final[int] = 10
MAX_IMPORT_LEN: Final[int] = 128
RECOMMEND_PROFILE: Final[str] = "project-code-selection-v1"
RECOMMEND_LIMITATIONS: Final[tuple[str, ...]] = (
    "Эвристический подбор, а не полный аудит проекта.",
    "Файлы могут измениться до предпросмотра.",
)

ENTRYPOINT_STEMS: Final[frozenset[str]] = frozenset(
    {"main", "app", "server", "index", "cli", "__main__"}
)
CONFIG_NAMES: Final[frozenset[str]] = frozenset(
    {
        "package.json",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "tox.ini",
        "requirements.txt",
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
        "makefile",
        "tsconfig.json",
        "vite.config.ts",
        "vite.config.js",
    }
)
JS_SUFFIXES: Final[tuple[str, ...]] = (".js", ".mjs", ".cjs", ".ts", ".tsx")
JS_RESOLVE_SUFFIXES: Final[tuple[str, ...]] = (".js", ".mjs", ".cjs", ".ts")

_JS_IMPORT = re.compile(
    r"(?:import\s[^;\n]*?from\s*|export\s[^;\n]*?from\s*|require\s*\(\s*)"
    r"[\"']([^\"'\n]{1,200})[\"']"
)


def _language(path: str) -> str:
    return SUFFIX_LANGUAGE.get(Path(path).suffix.lower(), "")


def _role(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    lower = name.lower()
    stem = lower.rsplit(".", 1)[0]
    parts = path.lower().split("/")
    if lower in CONFIG_NAMES or stem in CONFIG_NAMES:
        return "config"
    if lower.endswith(".md"):
        return "docs"
    if "tests" in parts[:-1] or "test" in parts[:-1]:
        return "test"
    if lower.startswith("test_") or "_test." in lower:
        return "test"
    if ".test." in lower or ".spec." in lower:
        return "test"
    if stem in ENTRYPOINT_STEMS:
        return "entrypoint"
    return "module"


def _module_candidates(path: str) -> list[str]:
    """Local module path variants for a relative import target."""
    out: list[str] = []
    if not path:
        return out
    out.append(path + ".py")
    out.append(path + "/__init__.py")
    if not path.startswith("src/"):
        out.append("src/" + path + ".py")
        out.append("src/" + path + "/__init__.py")
    return out


def _resolve_relative(importer: str, module: str, level: int) -> list[str]:
    base = importer.split("/")[:-1]
    if level > 0:
        drop = level - 1
        if drop > len(base):
            return []
        base = base[: len(base) - drop] if drop else base
    parts = [p for p in module.split(".") if p]
    joined = "/".join(base + parts)
    return _module_candidates(joined)


def _resolve_absolute(module: str) -> list[str]:
    parts = [p for p in module.split(".") if p]
    if not parts:
        return []
    joined = "/".join(parts)
    return _module_candidates(joined)


def _resolve_js(importer: str, spec: str) -> list[str]:
    if not spec.startswith("."):
        return []
    base = importer.split("/")[:-1]
    segments = list(base)
    for part in spec.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not segments:
                return []
            segments.pop()
            continue
        segments.append(part)
    joined = "/".join(segments)
    out: list[str] = []
    if Path(joined).suffix.lower() in JS_RESOLVE_SUFFIXES:
        out.append(joined)
    for suffix in JS_RESOLVE_SUFFIXES:
        out.append(joined + suffix)
    for suffix in JS_RESOLVE_SUFFIXES:
        out.append(joined + "/index" + suffix)
    return out


def _python_meta(text: str, path: str) -> tuple[list[str], list[str], bool]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return [], [], False
    symbols: list[str] = []
    imports: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if len(symbols) < MAX_SYMBOLS and node.name not in symbols:
                symbols.append(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for candidate in _resolve_absolute(alias.name):
                    if len(imports) < MAX_IMPORTS:
                        imports.append(candidate[:MAX_IMPORT_LEN])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level or 0
            if level:
                for candidate in _resolve_relative(path, module, level):
                    if len(imports) < MAX_IMPORTS:
                        imports.append(candidate[:MAX_IMPORT_LEN])
            elif module:
                for candidate in _resolve_absolute(module):
                    if len(imports) < MAX_IMPORTS:
                        imports.append(candidate[:MAX_IMPORT_LEN])
    return symbols, imports, True


def _js_meta(text: str, path: str) -> tuple[list[str], list[str], bool]:
    imports: list[str] = []
    for match in _JS_IMPORT.finditer(text):
        spec = match.group(1)
        if len(imports) >= MAX_IMPORTS:
            break
        if spec.startswith("."):
            for candidate in _resolve_js(path, spec):
                if len(imports) < MAX_IMPORTS:
                    imports.append(candidate[:MAX_IMPORT_LEN])
        else:
            imports.append(spec[:MAX_IMPORT_LEN])
    return [], imports, True


def _analyze(path: str, text: str) -> tuple[list[str], list[str], bool]:
    language = _language(path)
    if language == "python":
        return _python_meta(text, path)
    if language in ("javascript", "typescript"):
        return _js_meta(text, path)
    return [], [], True


class _Node(TypedDict):
    path: str
    language: str
    role: str
    symbols: list[str]
    imports: list[str]
    related: list[str]
    analyzed: bool
    bytes: int
    sha256: str | None


def _node(path: str) -> _Node:
    return {
        "path": path,
        "language": _language(path),
        "role": _role(path),
        "symbols": [],
        "imports": [],
        "related": [],
        "analyzed": False,
        "bytes": 0,
        "sha256": None,
    }


def build_map(project: Project) -> dict[str, object]:
    """Bounded local map of candidate files; never sends or caches content."""
    listing = catalog(project)
    files = [str(item) for item in cast(list[str], listing["files"])]
    partial = bool(listing.get("partial", False))
    limitations: list[str] = []
    if partial:
        limitations.append("Список файлов неполный: достигнут лимит обхода.")
    candidates = files[:MAX_CANDIDATES]
    if len(files) > MAX_CANDIDATES:
        limitations.append("Проанализированы не все файлы: лимит кандидатов.")
    deadline = time.monotonic() + DEADLINE_SECONDS
    nodes: list[_Node] = []
    analyzed = 0
    budget = MAX_SOURCE_BYTES
    for path in candidates:
        if time.monotonic() > deadline:
            limitations.append("Анализ остановлен по времени.")
            break
        if budget <= 0:
            limitations.append("Анализ остановлен по лимиту объёма.")
            break
        node = _node(path)
        try:
            preview = capture(project, [path])
        except ProjectError:
            partial = True
            nodes.append(node)
            continue
        except Exception:
            partial = True
            nodes.append(node)
            continue
        if not preview.files:
            partial = True
            nodes.append(node)
            continue
        item = preview.files[0]
        size = len(item.content.encode("utf-8"))
        if size > budget:
            partial = True
            nodes.append(node)
            continue
        budget -= size
        node["bytes"] = size
        node["sha256"] = item.sha256
        symbols, imports, ok = _analyze(path, item.content)
        node["symbols"] = symbols[:MAX_SYMBOLS]
        node["imports"] = imports[:MAX_IMPORTS]
        node["analyzed"] = ok
        if not ok:
            partial = True
            limitations.append("Часть файлов не разобрана: ошибка парсера.")
            nodes.append(node)
            continue
        nodes.append(node)
        analyzed += 1
    known = {node["path"] for node in nodes}
    for node in nodes:
        node["imports"] = [target for target in node["imports"] if target in known][:MAX_IMPORTS]
        related: list[str] = []
        for target in node["imports"]:
            if len(related) >= MAX_RELATED:
                break
            if target != node["path"] and target not in related:
                related.append(target)
        node["related"] = related
    return {
        "files": nodes,
        "partial": partial or len(nodes) < len(files),
        "analyzed_files": analyzed,
        "candidate_files": len(files),
        "limitations": limitations,
    }


def _canonical(selected: object, files: list[str]) -> list[str]:
    if not isinstance(selected, (list, tuple)):
        raise ProjectError("Выберите от одного до пяти файлов")
    if not 1 <= len(selected) <= MAX_FILES:
        raise ProjectError("Выберите от одного до пяти файлов")
    lookup = {name.casefold(): name for name in files}
    out: list[str] = []
    seen: set[str] = set()
    for raw in selected:
        if type(raw) is not str:
            raise ProjectError("Недопустимый путь к файлу")
        canonical = lookup.get(raw.casefold())
        if canonical is None:
            raise ProjectError("Файл отсутствует в списке проекта")
        key = canonical.casefold()
        if key in seen:
            raise ProjectError("Дублирующиеся файлы не поддерживаются")
        seen.add(key)
        out.append(canonical)
    return out


def _test_related(path: str) -> list[str]:
    directory = path.rsplit("/", 1)[0] if "/" in path else ""
    name = path.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0]
    suffix = Path(name).suffix
    out: list[str] = []
    for candidate in (
        f"test_{stem}.py",
        f"{stem}_test.py",
        f"{stem}.test{suffix}",
        f"{stem}.spec{suffix}",
    ):
        out.append(f"{directory}/{candidate}" if directory else candidate)
    if directory:
        out.append(f"{directory}/tests/test_{stem}.py")
    else:
        out.append(f"tests/test_{stem}.py")
    return out


def suggest_context(project: Project, selected: object) -> dict[str, object]:
    """Suggest related files; the user decides what to add (max five)."""
    listing = catalog(project)
    files = [str(item) for item in cast(list[str], listing["files"])]
    chosen = _canonical(selected, files)
    project_map = build_map(project)
    raw_nodes = cast(list[_Node], project_map["files"])
    nodes = {node["path"]: node for node in raw_nodes}
    chosen_keys = {name.casefold() for name in chosen}
    suggestions: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(path: str, reason: str) -> None:
        if len(suggestions) >= MAX_SUGGESTIONS:
            return
        key = path.casefold()
        if key in chosen_keys or key in seen or path not in nodes:
            return
        seen.add(key)
        suggestions.append({"path": path, "reason": reason})

    for name in chosen:
        node = nodes.get(name)
        if node is None:
            continue
        for target in node["imports"]:
            add(target, "Импортируется выбранным файлом.")
    for name in chosen:
        for other, node in nodes.items():
            if name in node["imports"]:
                add(other, "Импортирует выбранный файл.")
    for name in chosen:
        for candidate in _test_related(name):
            add(candidate, "Связанный тестовый файл по имени.")
    return {"map": project_map, "suggestions": suggestions, "selected": chosen}


def _recommend_rank(node: _Node, inbound: dict[str, int]) -> tuple[int, int, str, str]:
    role = node["role"]
    if role == "entrypoint":
        order = 0
    elif role == "module":
        order = 1
    elif role == "test":
        order = 2
    elif role == "config":
        order = 3
    else:
        order = 4
    return (order, -inbound.get(node["path"], 0), node["path"].casefold(), node["path"])


def _recommend_eligible(node: _Node) -> bool:
    size = node["bytes"]
    if type(size) is not int or not 1 <= size <= MAX_FILE_BYTES:
        return False
    digest = node["sha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return False
    if node["role"] not in ("entrypoint", "module", "test", "config", "docs"):
        return False
    if node["language"] not in SUFFIX_LANGUAGE.values():
        return False
    return True


def recommend_review(project: Project) -> dict[str, object]:
    """Deterministic bounded starting-file plan; never returns content."""
    project_map = build_map(project)
    raw_nodes = cast(list[_Node], project_map["files"])
    partial = bool(project_map["partial"])
    limitations = [str(item) for item in cast(list[str], project_map["limitations"])]
    limitations.extend(RECOMMEND_LIMITATIONS)
    nodes = [node for node in raw_nodes if _recommend_eligible(node)]
    by_path = {node["path"]: node for node in nodes}
    inbound: dict[str, int] = {}
    for node in nodes:
        for target in node["imports"]:
            if target in by_path and target != node["path"]:
                inbound[target] = inbound.get(target, 0) + 1
    selected: list[dict[str, object]] = []
    chosen: set[str] = set()
    total = 0

    def add(node: _Node, reason: str) -> bool:
        nonlocal total
        if len(selected) >= MAX_FILES:
            return False
        key = node["path"].casefold()
        if key in chosen:
            return False
        size = node["bytes"]
        if total + size > MAX_TOTAL_BYTES:
            return False
        chosen.add(key)
        total += size
        selected.append(
            {
                "path": node["path"],
                "bytes": size,
                "sha256": node["sha256"],
                "reason": reason,
            }
        )
        return True

    ranked = sorted(nodes, key=lambda node: _recommend_rank(node, inbound))
    anchor: _Node | None = None
    for node in ranked:
        if node["role"] == "entrypoint":
            if add(node, "Точка входа проекта."):
                anchor = node
                break
    if anchor is None:
        for node in ranked:
            if node["role"] == "module":
                if add(node, "Центральный модуль проекта."):
                    anchor = node
                    break
    if anchor is not None:
        for target in anchor["related"]:
            candidate = by_path.get(target)
            if candidate is not None and candidate["role"] == "module":
                if add(candidate, "Импорт относительно опорного файла."):
                    break
        for target in anchor["related"]:
            candidate = by_path.get(target)
            if candidate is not None and candidate["role"] == "test":
                if add(candidate, "Связанный тест опорного файла."):
                    break
        if not any(item["reason"] == "Связанный тест опорного файла." for item in selected):
            for candidate in ranked:
                if candidate["role"] != "test":
                    continue
                if (
                    candidate["path"] in _test_related(anchor["path"])
                    or anchor["path"] in candidate["imports"]
                ):
                    if add(candidate, "Связанный тест опорного файла."):
                        break
        for candidate in ranked:
            if candidate["role"] == "config":
                if add(candidate, "Конфигурация проекта."):
                    break
    for node in ranked:
        if len(selected) >= MAX_FILES:
            break
        if node["role"] in ("entrypoint", "module"):
            add(node, "Центральный модуль проекта.")
    for node in ranked:
        if len(selected) >= MAX_FILES:
            break
        if node["role"] == "test":
            add(node, "Тестовый файл для ручной проверки.")
    for node in ranked:
        if len(selected) >= MAX_FILES:
            break
        if node["role"] == "config":
            add(node, "Конфигурация проекта.")
    for node in ranked:
        if len(selected) >= MAX_FILES:
            break
        if node["role"] == "docs":
            add(node, "Документация проекта.")
    return {
        "profile": RECOMMEND_PROFILE,
        "files": selected,
        "total_bytes": total,
        "max_files": MAX_FILES,
        "max_total_bytes": MAX_TOTAL_BYTES,
        "partial": partial,
        "limitations": limitations,
    }
