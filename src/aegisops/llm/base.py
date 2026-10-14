"""Small provider-independent contracts and conservative per-run reservations."""

from dataclasses import dataclass
from typing import Protocol

from aegisops.domain.common import DTO
from aegisops.domain.models import DiagnosisDraft, Origin, RecommendationDraft


class LLMError(ValueError):
    def __init__(self) -> None:
        super().__init__("model operation failed")


class AnalysisDraft(DTO):
    diagnosis: DiagnosisDraft
    recommendation: RecommendationDraft


@dataclass(frozen=True)
class LLMLimits:
    max_input_bytes: int = 32768
    max_output_tokens: int = 1600
    timeout_seconds: int = 30
    max_response_bytes: int = 65536
    max_total_tokens: int = 65536
    max_calls: int = 12

    def __post_init__(self) -> None:
        for value, low, high in (
            (self.max_input_bytes, 1024, 32768),
            (self.max_output_tokens, 256, 2048),
            (self.timeout_seconds, 1, 60),
            (self.max_response_bytes, 1024, 65536),
            (self.max_total_tokens, 256, 65536),
            (self.max_calls, 1, 12),
        ):
            if type(value) is not int or not low <= value <= high:
                raise LLMError()


@dataclass(frozen=True, repr=False)
class LLMRequest:
    prompt_id: str
    prompt_hash: str
    system: str
    user: str
    origin: Origin

    @property
    def input_bytes(self) -> int:
        return len(self.system.encode("utf-8")) + len(self.user.encode("utf-8"))


@dataclass(frozen=True, repr=False)
class LLMReply:
    data: AnalysisDraft
    input_tokens: int = 0
    output_tokens: int = 0
    response_model: str | None = None

    def __post_init__(self) -> None:
        for value in (self.input_tokens, self.output_tokens):
            if type(value) is not int or not 0 <= value <= 2**31 - 1:
                raise LLMError()
        object.__setattr__(self, "data", AnalysisDraft.model_validate(self.data))
        if self.response_model is not None and (
            type(self.response_model) is not str or not 1 <= len(self.response_model) <= 128
        ):
            raise LLMError()


class LLMClient(Protocol):
    provider: str
    model_name: str

    def generate_structured(self, request: LLMRequest, limits: LLMLimits) -> LLMReply: ...


class RunBudget:
    """No refunds: reserve a byte-based input upper bound plus maximum output."""

    def __init__(self, limits: LLMLimits) -> None:
        limits.__post_init__()
        self.limits = limits
        self.calls = 0
        self.reserved_tokens = 0

    def reserve(self, request: LLMRequest) -> None:
        estimate = request.input_bytes + self.limits.max_output_tokens + 128
        if (
            request.input_bytes > self.limits.max_input_bytes
            or self.calls >= self.limits.max_calls
            or self.reserved_tokens + estimate > self.limits.max_total_tokens
        ):
            raise LLMError()
        self.calls += 1
        self.reserved_tokens += estimate
