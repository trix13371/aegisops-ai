"""SQLAlchemy declarative models for AegisOps S0-01.

Only the audit_runs table is defined. No hash-chain columns exist in this
stage. Schema creation is handled by migrations, never by create_all.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for AegisOps models."""


class AuditRun(Base):
    """A single synthetic audit run record."""

    __tablename__ = "audit_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    incident_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    autonomy_level: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
        default="L0",
        server_default="L0",
    )
    origin: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
    )
    model_name: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    prompt_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    prompt_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    policy_version: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    context_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('completed', 'failed', 'blocked')",
            name="ck_audit_runs_status",
        ),
        CheckConstraint(
            "autonomy_level = 'L0'",
            name="ck_audit_runs_autonomy_level",
        ),
        CheckConstraint(
            "origin IN ('synthetic_fixture', 'local_kind')",
            name="ck_audit_runs_origin",
        ),
        CheckConstraint(
            "schema_version >= 1",
            name="ck_audit_runs_schema_version",
        ),
        Index("ix_audit_runs_incident_id", "incident_id"),
        Index("ix_audit_runs_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"AuditRun(run_id={self.run_id!r}, status={self.status!r})"
