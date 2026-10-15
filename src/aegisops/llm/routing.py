"""Conservative local-development model routing policy.

This module is a narrowly scoped, pure-Python foundation for local
development. It is NOT production DLP, governance, or zero-retention
enforcement. It performs no IO and no network access.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aegisops.domain.models import Origin

ROUTING_POLICY_VERSION = "local-data-v1"

_ROUTING_ERROR_MESSAGE = "model routing rejected"


class RoutingError(ValueError):
    """Raised when routing inputs are malformed or unsupported."""

    def __init__(self) -> None:
        super().__init__(_ROUTING_ERROR_MESSAGE)


class DataClass(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


_MINIMUM_BY_ORIGIN: dict[Origin, DataClass] = {
    Origin.SYNTHETIC_FIXTURE: DataClass.PUBLIC,
    Origin.LOCAL_KIND: DataClass.INTERNAL,
}

_ORDER: dict[DataClass, int] = {
    DataClass.PUBLIC: 0,
    DataClass.INTERNAL: 1,
    DataClass.CONFIDENTIAL: 2,
    DataClass.RESTRICTED: 3,
}

_OFFLINE_PROVIDERS: frozenset[str] = frozenset({"fake", "rule_based"})


def _minimum_for(origin: Origin) -> DataClass:
    if type(origin) is not Origin:
        raise RoutingError()
    try:
        return _MINIMUM_BY_ORIGIN[origin]
    except KeyError:
        raise RoutingError() from None


def resolve_data_class(
    origin: Origin,
    data_class: DataClass | None = None,
) -> DataClass:
    """Resolve the effective data class for an origin.

    The origin determines a minimum data class. An explicitly provided
    class must be at least that minimum; a lower class is rejected.
    """
    minimum = _minimum_for(origin)

    if data_class is None:
        return minimum

    if type(data_class) is not DataClass:
        raise RoutingError()

    if _ORDER[data_class] < _ORDER[minimum]:
        raise RoutingError()

    return data_class


@dataclass(frozen=True)
class RouteDecision:
    data_class: DataClass
    provider: str
    allowed: bool
    reason: str
    policy_version: str = ROUTING_POLICY_VERSION


def decide_route(
    origin: Origin,
    provider: str,
    data_class: DataClass | None = None,
) -> RouteDecision:
    """Decide whether a provider may serve a request.

    No retries, fallbacks, or provider selection are performed here.
    """
    resolved = resolve_data_class(origin, data_class)

    if type(provider) is not str:
        raise RoutingError()

    if resolved is DataClass.RESTRICTED:
        return RouteDecision(
            data_class=resolved,
            provider=provider,
            allowed=False,
            reason="restricted_data",
        )

    if provider in _OFFLINE_PROVIDERS:
        return RouteDecision(
            data_class=resolved,
            provider=provider,
            allowed=True,
            reason="offline_client",
        )

    if provider == "local":
        return RouteDecision(
            data_class=resolved,
            provider=provider,
            allowed=True,
            reason="local_model",
        )

    if provider == "openrouter":
        if resolved is DataClass.PUBLIC:
            return RouteDecision(
                data_class=resolved,
                provider=provider,
                allowed=True,
                reason="public_external",
            )
        return RouteDecision(
            data_class=resolved,
            provider=provider,
            allowed=False,
            reason="external_data_denied",
        )

    return RouteDecision(
        data_class=resolved,
        provider=provider,
        allowed=False,
        reason="unknown_provider",
    )
