"""Pure, bounded representative selection of candidate file paths.

No I/O, no content reads: only deterministic ordering of given path strings.
"""

ENTRYPOINT_STEMS: frozenset[str] = frozenset({"main", "app", "server", "index", "cli", "__main__"})
CONFIG_NAMES: frozenset[str] = frozenset(
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

ROLE_ORDER: tuple[str, ...] = ("entrypoint", "module", "test", "config", "docs")
EARLY_ROLES: tuple[str, ...] = ("config", "test", "module", "entrypoint")


def file_role(path: str) -> str:
    """Classify a relative path into a coarse role (pure, no I/O)."""
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


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _depth(path: str) -> int:
    return path.count("/")


def _folder_key(path: str) -> tuple[int, int, str, str]:
    name = path.rsplit("/", 1)[-1]
    init_rank = 1 if name == "__init__.py" else 0
    return (init_rank, _depth(path), path.casefold(), path)


def choose_candidates(paths: list[str], limit: int) -> list[str]:
    """Deterministic representative subset of at most ``limit`` paths.

    Deduplicates case-insensitively, groups by role, cycles roles so that
    configs/tests/code enter the first 80, and round-robins sorted parent
    directories so a single folder cannot monopolize the result.
    """
    if limit <= 0:
        return []
    unique: dict[str, str] = {}
    for path in paths:
        if type(path) is not str or not path:
            continue
        key = path.casefold()
        current = unique.get(key)
        if current is None or path < current:
            unique[key] = path
    if not unique:
        return []
    by_role: dict[str, dict[str, list[str]]] = {role: {} for role in ROLE_ORDER}
    for path in unique.values():
        role = file_role(path)
        if role not in by_role:
            role = "module"
        folder = _parent(path)
        by_role[role].setdefault(folder, []).append(path)
    for role in ROLE_ORDER:
        for folder in by_role[role]:
            by_role[role][folder].sort(key=_folder_key)
    folders: dict[str, list[str]] = {
        role: sorted(by_role[role].keys(), key=lambda name: (name.casefold(), name))
        for role in ROLE_ORDER
    }
    cursors: dict[tuple[str, str], int] = {}
    folder_index: dict[str, int] = {role: 0 for role in ROLE_ORDER}
    chosen: list[str] = []
    seen: set[str] = set()

    def take(role: str) -> bool:
        names = folders[role]
        if not names:
            return False
        start = folder_index[role]
        for offset in range(len(names)):
            index = (start + offset) % len(names)
            folder = names[index]
            bucket = by_role[role][folder]
            cursor_key = (role, folder)
            cursor = cursors.get(cursor_key, 0)
            if cursor < len(bucket):
                path = bucket[cursor]
                cursors[cursor_key] = cursor + 1
                folder_index[role] = (index + 1) % len(names)
                key = path.casefold()
                if key in seen:
                    return True
                seen.add(key)
                chosen.append(path)
                return True
        return False

    early = [role for role in EARLY_ROLES if folders[role]]
    if early:
        while len(chosen) < min(limit, 80):
            progressed = False
            for role in early:
                if len(chosen) >= min(limit, 80):
                    break
                if take(role):
                    progressed = True
            if not progressed:
                break
    while len(chosen) < limit:
        progressed = False
        for role in ROLE_ORDER:
            if len(chosen) >= limit:
                break
            if take(role):
                progressed = True
        if not progressed:
            break
    return chosen
