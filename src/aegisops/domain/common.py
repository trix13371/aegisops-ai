"""Shared bounded types and immutable scalar objects, serializable as JSON."""

from datetime import UTC, datetime
from typing import Annotated, Any, Never

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
)


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("nonblank text required")
    return value


Identifier = Annotated[StrictStr, Field(min_length=1, max_length=128), AfterValidator(nonblank)]
ShortText = Annotated[StrictStr, Field(min_length=1, max_length=512), AfterValidator(nonblank)]
Text = Annotated[StrictStr, Field(min_length=1, max_length=4096), AfterValidator(nonblank)]
Sha256 = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
Scalar = (
    Annotated[StrictStr, Field(max_length=512)]
    | StrictBool
    | StrictInt
    | Annotated[StrictFloat, Field(allow_inf_nan=False)]
    | None
)
UTCDateTime = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(UTC))]
NonNegativeInt = Annotated[StrictInt, Field(ge=0, le=2**63 - 1)]


class FrozenScalarDict(dict[str, Scalar]):
    """Prevent ordinary mutation after validation; not a hostile-code sandbox."""

    def __setitem__(self, key: str, value: Scalar) -> None:
        raise TypeError("validated mapping is immutable")

    def __delitem__(self, key: str) -> None:
        raise TypeError("validated mapping is immutable")

    def clear(self) -> None:
        raise TypeError("validated mapping is immutable")

    def pop(self, *args: Any, **kwargs: Any) -> Never:
        raise TypeError("validated mapping is immutable")

    def popitem(self) -> Never:
        raise TypeError("validated mapping is immutable")

    def setdefault(self, *args: Any, **kwargs: Any) -> Never:
        raise TypeError("validated mapping is immutable")

    def update(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("validated mapping is immutable")

    def __ior__(self, other: Any) -> Never:  # type: ignore[misc]
        raise TypeError("validated mapping is immutable")


def freeze_scalars(value: dict[str, Scalar]) -> dict[str, Scalar]:
    if any(not key.strip() or len(key) > 64 for key in value):
        raise ValueError("invalid scalar object key")
    return FrozenScalarDict(value)


ScalarMap = Annotated[dict[StrictStr, Scalar], Field(max_length=16), AfterValidator(freeze_scalars)]


class DTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
        revalidate_instances="always",
    )

    @field_validator(
        "execution_performed",
        "simulated",
        "requires_human_approval",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def literal_boolean(cls, value: object) -> bool:
        if type(value) is not bool:
            raise ValueError("literal boolean required")
        return value


def utc_now() -> datetime:
    """Default clock; callers can inject a fixed clock in later pipeline stages."""
    return datetime.now(UTC)
