"""Bounded local data reads; reject duplicate keys, YAML aliases and path escapes."""

import json
from pathlib import Path
from typing import Any

import yaml
from yaml.nodes import MappingNode
from yaml.tokens import AliasToken


class DataFileError(ValueError):
    """Safe diagnostic: never include raw contents or filesystem paths."""


def read_text(path: Path, max_bytes: int = 131072) -> str:
    try:
        with path.open("rb") as stream:
            contents = stream.read(max_bytes + 1)
        if len(contents) > max_bytes:
            raise DataFileError("data file exceeds size limit")
        return contents.decode("utf-8")
    except (OSError, UnicodeError):
        raise DataFileError("data file cannot be read") from None


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise DataFileError("duplicate object key")
        result[key] = value
    return result


def read_json(path: Path) -> object:
    try:
        return json.loads(read_text(path), object_pairs_hook=unique_object)
    except (ValueError, RecursionError):
        raise DataFileError("invalid JSON data") from None


class UniqueSafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Any, Any]:
        keys: set[str] = set()
        for key_node, _ in node.value:
            if not isinstance(key_node.value, str) or key_node.value in keys:
                raise DataFileError("duplicate or invalid YAML key")
            keys.add(key_node.value)
        return super().construct_mapping(node, deep=deep)


def read_yaml(path: Path, max_bytes: int = 131072) -> object:
    value = read_text(path, max_bytes)
    try:
        for index, token in enumerate(yaml.scan(value)):
            if isinstance(token, AliasToken) or index > 10000:
                raise DataFileError("YAML aliases or excessive tokens are not permitted")
        return yaml.load(value, Loader=UniqueSafeLoader)  # noqa: S506 - subclasses SafeLoader
    except (yaml.YAMLError, ValueError, RecursionError):
        raise DataFileError("invalid YAML data") from None


def contained_file(root: Path, relative: str) -> Path:
    try:
        segment = Path(relative)
        if segment.is_absolute() or ".." in segment.parts:
            raise DataFileError("data path is outside allowed root")
        cursor = root
        if root.is_symlink():
            raise DataFileError("symbolic data root is not permitted")
        for part in segment.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise DataFileError("symbolic data path is not permitted")
        resolved = cursor.resolve(strict=True)
        if not resolved.is_relative_to(root.resolve(strict=True)):
            raise DataFileError("data path is outside allowed root")
        return resolved
    except OSError:
        raise DataFileError("data path cannot be resolved") from None
