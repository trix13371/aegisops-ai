"""Handcrafted synthetic scenario fixtures for AegisOps S0-02.

These are hand-created fixtures, NOT claimed real cluster captures.
"""

from __future__ import annotations

import importlib.resources
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from aegisops.domain.common import DTO, ShortText
from aegisops.domain.models import (
    ActionID,
    CauseCategory,
    Origin,
    PodSnapshot,
)
from aegisops.io import DataFileError, contained_file, read_json, read_yaml

__all__ = ["ScenarioRepository", "ScenarioSpec", "SyntheticSource"]

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_CLUSTER_ID = "kind-aegisops-demo"
_NAMESPACE = "aegisops-demo"

_FIXED = "synthetic scenario is invalid"


def _fail() -> ScenarioError:
    return ScenarioError(_FIXED)


class ScenarioError(ValueError):
    """Raised for any invalid scenario fixture. Message is fixed."""

    def __init__(self, message: str = _FIXED) -> None:
        super().__init__(message)


class ScenarioSpec(DTO):
    """Declarative description of a handcrafted synthetic scenario."""

    scenario_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    should_detect: StrictBool
    cause_category: CauseCategory
    allowed_actions: tuple[ActionID, ...] = Field(default=(), max_length=4)
    forbidden_actions: tuple[ActionID, ...] = Field(default=(), max_length=4)
    description: ShortText
    fixture_kind: Literal["handcrafted_synthetic"] = "handcrafted_synthetic"

    @field_validator("scenario_id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not isinstance(value, str) or len(value) > 64 or not _ID_RE.fullmatch(value):
            raise ValueError("bad scenario id")
        return value

    @field_validator("allowed_actions", "forbidden_actions")
    @classmethod
    def _check_actions(cls, value: tuple[ActionID, ...]) -> tuple[ActionID, ...]:
        if len(value) > 4:
            raise ValueError("too many actions")
        if len(set(value)) != len(value):
            raise ValueError("duplicate actions")
        return value

    @model_validator(mode="after")
    def _check_overlap(self) -> ScenarioSpec:
        if set(self.allowed_actions) & set(self.forbidden_actions):
            raise ValueError("overlapping actions")
        return self


def _default_root() -> Path:
    base = Path(str(importlib.resources.files("aegisops")))
    return base / "assets" / "scenarios" / "crashloop"


def _read_bounded(base: Path, relative: str) -> object:
    try:
        resolved = contained_file(base, relative)
    except DataFileError:
        raise _fail() from None
    except Exception:
        raise _fail() from None
    try:
        if resolved.suffix.lower() in (".yaml", ".yml"):
            return read_yaml(resolved)
        return read_json(resolved)
    except DataFileError:
        raise _fail() from None
    except Exception:
        raise _fail() from None


@dataclass(frozen=True)
class ScenarioRepository:
    """Read-only repository of handcrafted synthetic scenario fixtures."""

    root: Path = field(default_factory=_default_root)

    def _base(self) -> Path:
        if self.root.is_symlink():
            raise _fail()
        try:
            base = self.root.resolve()
        except Exception:
            raise _fail() from None
        if not base.is_dir():
            raise _fail()
        return base

    def list_ids(self) -> tuple[str, ...]:
        base = self._base()
        try:
            entries = sorted(base.iterdir(), key=lambda p: p.name)
        except Exception:
            raise _fail() from None
        ids: list[str] = []
        for entry in entries:
            if entry.is_symlink():
                raise _fail()
            if not entry.is_dir():
                continue
            name = entry.name
            if len(name) > 64 or not _ID_RE.fullmatch(name):
                raise _fail()
            try:
                contained_file(base, name)
            except DataFileError:
                raise _fail() from None
            except Exception:
                raise _fail() from None
            ids.append(name)
            if len(ids) > 32:
                raise _fail()
        return tuple(ids)

    def load(self, scenario_id: str) -> tuple[ScenarioSpec, PodSnapshot]:
        if (
            not isinstance(scenario_id, str)
            or len(scenario_id) > 64
            or not _ID_RE.fullmatch(scenario_id)
        ):
            raise _fail()
        base = self._base()
        try:
            directory = contained_file(base, scenario_id)
        except DataFileError:
            raise _fail() from None
        except Exception:
            raise _fail() from None
        if directory.is_symlink() or not directory.is_dir():
            raise _fail()

        spec_raw = _read_bounded(base, str(Path(scenario_id) / "expected.yaml"))
        snap_raw = _read_bounded(base, str(Path(scenario_id) / "snapshot.json"))
        try:
            spec = ScenarioSpec.model_validate(spec_raw)
            snapshot = PodSnapshot.model_validate(snap_raw)
        except Exception:
            raise _fail() from None

        if spec.scenario_id != scenario_id:
            raise _fail()
        if snapshot.origin is not Origin.SYNTHETIC_FIXTURE:
            raise _fail()
        if snapshot.cluster_id != _CLUSTER_ID:
            raise _fail()
        if snapshot.namespace != _NAMESPACE:
            raise _fail()
        if snapshot.snapshot_id != "synthetic-" + scenario_id:
            raise _fail()
        return spec, snapshot


@dataclass(frozen=True)
class SyntheticSource:
    """Source yielding one validated synthetic snapshot per collection."""

    repository: ScenarioRepository
    scenario_id: str

    def collect(self) -> list[PodSnapshot]:
        _spec, snapshot = self.repository.load(self.scenario_id)
        fresh = PodSnapshot.model_validate(snapshot.model_dump())
        return [fresh]
