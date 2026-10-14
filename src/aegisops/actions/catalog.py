"""Action catalog loading and proposal validation for AegisOps S0-02.

Data files are untrusted: parsed via shared aegisops.io helpers.
"""

from __future__ import annotations

import importlib.resources
import re
from pathlib import Path
from typing import Any, Literal, Never

from pydantic import (
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from aegisops.domain.common import DTO, Identifier, ShortText
from aegisops.domain.models import (
    ActionID,
    ActionProposal,
    ReversibilityClass,
)
from aegisops.io import DataFileError, contained_file, read_json, read_yaml

__all__ = ["ActionCatalog", "ActionDefinition", "CatalogError", "ParameterRule", "load_catalog"]

_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")

_ESCALATE = ActionID.ESCALATE_TO_HUMAN
_COLLECT = ActionID.COLLECT_MORE_EVIDENCE
_RESTART = ActionID.RESTART_WORKLOAD
_ROLLBACK = ActionID.ROLLBACK_DEPLOYMENT

_NAMESPACE = "aegisops-demo"

_FIXED = "action catalog is invalid"


def _fail() -> CatalogError:
    return CatalogError(_FIXED)


class CatalogError(ValueError):
    """Raised for any invalid catalog or proposal. Message is fixed."""

    def __init__(self, message: str = _FIXED) -> None:
        super().__init__(message)


class FrozenParameterMap(dict[str, "ParameterRule"]):
    """Read-only mapping of parameter name to ParameterRule."""

    def __setitem__(self, key: str, value: ParameterRule) -> Never:
        raise TypeError("parameter map is immutable")

    def __delitem__(self, key: str) -> Never:
        raise TypeError("parameter map is immutable")

    def clear(self) -> Never:
        raise TypeError("parameter map is immutable")

    def pop(self, key: str, default: object = ...) -> Never:
        raise TypeError("parameter map is immutable")

    def popitem(self) -> Never:
        raise TypeError("parameter map is immutable")

    def setdefault(self, key: str, default: object = None) -> Never:
        raise TypeError("parameter map is immutable")

    def update(self, *args: object, **kwargs: object) -> Never:
        raise TypeError("parameter map is immutable")

    def __ior__(self, other: object) -> Never:  # type: ignore[misc]
        raise TypeError("parameter map is immutable")


def _bounded_text(value: str) -> str:
    if not value.strip():
        raise ValueError("blank text")
    if len(value) > 512:
        raise ValueError("text too long")
    return value


class ParameterRule(DTO):
    """Rule constraining a single action parameter value."""

    type: Literal["string"] = "string"
    enum: tuple[Identifier, ...] | None = Field(default=None, max_length=8)
    const: Identifier | None = Field(default=None, max_length=63)
    max_length: StrictInt = Field(default=63, ge=1, le=63)

    @field_validator("enum")
    @classmethod
    def _check_enum(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        if not value:
            raise ValueError("empty enum")
        if len(value) > 8:
            raise ValueError("enum too large")
        if len(set(value)) != len(value):
            raise ValueError("duplicate enum")
        for item in value:
            if not isinstance(item, str) or not item.strip() or len(item) > 63:
                raise ValueError("bad enum value")
        return value

    @field_validator("const")
    @classmethod
    def _check_const(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or len(value) > 63:
            raise ValueError("bad const")
        return value

    @model_validator(mode="after")
    def _check_exclusive(self) -> ParameterRule:
        if self.const is not None and self.enum is not None:
            raise ValueError("const and enum are mutually exclusive")
        return self


class ActionDefinition(DTO):
    """A single catalogued action."""

    id: ActionID
    reversibility: ReversibilityClass
    params: dict[str, ParameterRule] = Field(default_factory=dict, max_length=8)
    preconditions: tuple[ShortText, ...] = Field(default=(), max_length=8)
    postconditions: tuple[ShortText, ...] = Field(default=(), max_length=8)
    rollback: str | None = Field(default=None, max_length=512)
    rate_limit: str | None = Field(default=None, max_length=128)

    @field_validator("params")
    @classmethod
    def _check_params(cls, value: dict[str, ParameterRule]) -> dict[str, ParameterRule]:
        if len(value) > 8:
            raise ValueError("too many params")
        for key in value:
            if not isinstance(key, str) or not key.strip() or len(key) > 128:
                raise ValueError("bad param name")
        return value

    @field_validator("preconditions", "postconditions")
    @classmethod
    def _check_conditions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 8:
            raise ValueError("too many conditions")
        for item in value:
            _bounded_text(item)
        return value

    @field_validator("rollback")
    @classmethod
    def _check_rollback(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(value)

    @field_validator("rate_limit")
    @classmethod
    def _check_rate_limit(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or len(value) > 128:
            raise ValueError("bad rate limit")
        return value

    @model_validator(mode="after")
    def _freeze_params(self) -> ActionDefinition:
        frozen = FrozenParameterMap(self.params)
        object.__setattr__(self, "params", frozen)
        return self


class ActionCatalog(DTO):
    """Validated catalog of exactly four actions."""

    catalog_version: str = Field(
        max_length=32, pattern=r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
    )
    actions: tuple[ActionDefinition, ...] = Field(min_length=4, max_length=4)

    @field_validator("catalog_version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if not isinstance(value, str) or not _SEMVER_RE.fullmatch(value):
            raise ValueError("bad version")
        return value

    @field_validator("actions")
    @classmethod
    def _check_actions(cls, value: tuple[ActionDefinition, ...]) -> tuple[ActionDefinition, ...]:
        if len(value) != 4:
            raise ValueError("expected four actions")
        ids = [a.id for a in value]
        if len(set(ids)) != 4:
            raise ValueError("duplicate actions")
        return value

    @model_validator(mode="after")
    def _check_baseline(self) -> ActionCatalog:
        by_id = {a.id: a for a in self.actions}
        if set(by_id) != {_ESCALATE, _COLLECT, _RESTART, _ROLLBACK}:
            raise ValueError("unexpected action set")
        if by_id[_ESCALATE].reversibility is not ReversibilityClass.R1:
            raise ValueError("escalate must be R1")
        if by_id[_COLLECT].reversibility is not ReversibilityClass.R1:
            raise ValueError("collect must be R1")
        if by_id[_RESTART].reversibility is not ReversibilityClass.R2:
            raise ValueError("restart must be R2")
        if by_id[_ROLLBACK].reversibility is not ReversibilityClass.R1:
            raise ValueError("rollback must be R1")

        if by_id[_ESCALATE].params:
            raise ValueError("escalate takes no params")

        collect = by_id[_COLLECT].params
        if set(collect) != {"scope"}:
            raise ValueError("collect params")
        scope = collect["scope"]
        if scope.enum is None or set(scope.enum) != {"events", "previous_logs", "pod_spec_summary"}:
            raise ValueError("collect scope enum")
        if scope.const is not None:
            raise ValueError("collect scope const")

        restart = by_id[_RESTART].params
        if set(restart) != {"namespace", "workload"}:
            raise ValueError("restart params")
        rollback = by_id[_ROLLBACK].params
        if set(rollback) != {"namespace", "deployment"}:
            raise ValueError("rollback params")

        for params in (restart, rollback):
            ns = params["namespace"]
            if ns.const != _NAMESPACE or ns.enum is not None:
                raise ValueError("namespace const")
            if ns.max_length > 63:
                raise ValueError("namespace bound")

        for name in ("workload", "deployment"):
            rule = restart.get(name) or rollback.get(name)
            if rule is None:
                raise ValueError("missing target rule")
            if rule.const is not None or rule.enum is not None:
                raise ValueError("target rule shape")
            if rule.max_length > 63:
                raise ValueError("target bound")
        return self

    def validate_proposal(self, proposal: ActionProposal) -> ActionProposal:
        """Validate a proposal against this catalog, returning a revalidated copy."""
        try:
            if not isinstance(proposal, ActionProposal):
                raise _fail()
            by_id = {a.id: a for a in self.actions}
            definition = by_id.get(proposal.action_id)
            if definition is None:
                raise _fail()
            params = proposal.params
            if not isinstance(params, dict):
                raise _fail()
            expected = set(definition.params)
            if set(params) != expected:
                raise _fail()
            for name, rule in definition.params.items():
                value = params[name]
                if not isinstance(value, str) or isinstance(value, bool):
                    raise _fail()
                if not value.strip():
                    raise _fail()
                if len(value) > rule.max_length:
                    raise _fail()
                if rule.const is not None and value != rule.const:
                    raise _fail()
                if rule.enum is not None and value not in rule.enum:
                    raise _fail()
                if name in ("workload", "deployment"):
                    if not _DNS_LABEL_RE.fullmatch(value):
                        raise _fail()
            return ActionProposal.model_validate(proposal.model_dump())
        except CatalogError:
            raise
        except Exception:
            raise _fail() from None


def _load_raw(path: Path) -> Any:
    try:
        resolved = contained_file(path.parent, path.name)
    except DataFileError:
        raise _fail() from None
    except Exception:
        raise _fail() from None
    suffix = resolved.suffix.lower()
    try:
        if suffix in (".yaml", ".yml"):
            return read_yaml(resolved)
        return read_json(resolved)
    except DataFileError:
        raise _fail() from None
    except Exception:
        raise _fail() from None


def _default_path() -> Path:
    base = Path(str(importlib.resources.files("aegisops")))
    return base / "assets" / "actions" / "catalog.yaml"


def load_catalog(path: Path | None = None) -> ActionCatalog:
    """Load and validate the action catalog from a data file."""
    target = path if path is not None else _default_path()
    raw = _load_raw(target)
    try:
        return ActionCatalog.model_validate(raw)
    except Exception:
        raise _fail() from None
