"""Observation source protocol for the AegisOps local L0 prototype."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from aegisops.domain.models import PodSnapshot

__all__ = ["ObservationSource"]


@runtime_checkable
class ObservationSource(Protocol):
    """A read-only source of pod snapshots."""

    def collect(self) -> list[PodSnapshot]:
        """Return validated pod snapshots or raise on any failure."""
        ...
