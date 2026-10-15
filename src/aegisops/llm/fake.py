"""Deterministic offline LLM clients backed by packaged fixtures.

``FakeLLMClient`` returns pre-validated drafts keyed by prompt hash and never
performs network access.  ``UnavailableLLMClient`` always fails safely.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from aegisops.domain.models import Origin
from aegisops.io import contained_file, read_json
from aegisops.llm.base import (
    AnalysisDraft,
    LLMError,
    LLMLimits,
    LLMReply,
    LLMRequest,
)
from aegisops.llm.prompt import verify_request
from aegisops.llm.routing import decide_route

__all__ = ["FakeLLMClient", "UnavailableLLMClient"]

_FIXTURE_ASSET = ("aegisops", "assets", "fixtures", "llm", "analysis-v2.json")
_MAX_FIXTURE_BYTES = 128 * 1024
_MAX_RECORDS = 32
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_VALID_ORIGINS = frozenset({Origin.SYNTHETIC_FIXTURE, Origin.LOCAL_KIND})


def _load_default_fixtures() -> dict[str, object]:
    try:
        root = Path(str(resources.files("aegisops"))) / "assets"
        path = contained_file(root, "fixtures/llm/analysis-v2.json")
        data = read_json(path)
    except Exception:
        raise LLMError() from None
    if not isinstance(data, dict):
        raise LLMError()
    return data


def _validate_fixtures(raw: object) -> dict[str, AnalysisDraft]:
    if not isinstance(raw, dict):
        raise LLMError()
    if len(raw) > _MAX_RECORDS:
        raise LLMError()
    validated: dict[str, AnalysisDraft] = {}
    for key, value in raw.items():
        if type(key) is not str or _HASH_RE.fullmatch(key) is None:
            raise LLMError()
        try:
            draft = AnalysisDraft.model_validate(value)
        except Exception:
            raise LLMError() from None
        validated[key] = draft
    return validated


class FakeLLMClient:
    """Offline client returning fixture drafts keyed by prompt hash."""

    provider = "fake"
    model_name = "fixture-v2"

    def __init__(self, fixtures: dict[str, object] | None = None) -> None:
        source = _load_default_fixtures() if fixtures is None else fixtures
        self._fixtures = _validate_fixtures(source)
        self.calls = 0

    def generate_structured(self, request: LLMRequest, limits: LLMLimits) -> LLMReply:
        verify_request(request)
        if not decide_route(request.origin, self.provider, request.data_class).allowed:
            raise LLMError()
        if type(limits) is not LLMLimits:
            raise LLMError()
        try:
            limits.__post_init__()
        except LLMError:
            raise
        except Exception:
            raise LLMError() from None
        if request.input_bytes > limits.max_input_bytes:
            raise LLMError()
        self.calls += 1
        draft = self._fixtures.get(request.prompt_hash)
        if draft is None:
            raise LLMError()
        try:
            validated = AnalysisDraft.model_validate(draft)
        except Exception:
            raise LLMError() from None
        return LLMReply(data=validated, input_tokens=0, output_tokens=0)


class UnavailableLLMClient:
    """Client that always fails safely; used when no provider is configured."""

    provider = "rule_based"
    model_name = "unavailable"

    def generate_structured(self, request: LLMRequest, limits: LLMLimits) -> LLMReply:
        raise LLMError()
