"""SQLAlchemy declarative models for AegisOps S0-01.

Audit runs and human feedback are append-only under the application role.
Schema creation is handled by migrations, never by create_all.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
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


class RecommendationFeedback(Base):
    """One immutable assessment or explicit correction of a previous assessment."""

    __tablename__ = "recommendation_feedback"

    feedback_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audit_runs.run_id"), nullable=False
    )
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    recommendation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recommendation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(64), nullable=False)
    assessment: Mapped[str] = mapped_column(String(16), nullable=False)
    comment: Mapped[str] = mapped_column(String(2000), nullable=False, server_default="")
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_cause: Mapped[str | None] = mapped_column(String(32), nullable=True)
    incident_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint("schema_version = 1", name="ck_feedback_schema_version"),
        CheckConstraint("origin IN ('synthetic_fixture', 'local_kind')", name="ck_feedback_origin"),
        CheckConstraint(
            "assessment IN ('useful', 'incorrect', 'unsafe', 'uncertain')",
            name="ck_feedback_assessment",
        ),
        CheckConstraint(
            "reviewer ~ '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$'", name="ck_feedback_reviewer"
        ),
        CheckConstraint("recommendation_hash ~ '^[0-9a-f]{64}$'", name="ck_feedback_hash"),
        CheckConstraint("supersedes_id <> feedback_id", name="ck_feedback_not_self"),
        CheckConstraint(
            "confirmed_cause IN ('application_exit_nonzero', 'missing_runtime_config', "
            "'invalid_command', 'oom_killed')",
            name="ck_feedback_confirmed_cause",
        ),
        CheckConstraint(
            "incident_outcome IN ('resolved', 'unresolved', 'unknown')",
            name="ck_feedback_incident_outcome",
        ),
        CheckConstraint(
            "reference ~ '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$'", name="ck_feedback_reference"
        ),
        CheckConstraint(
            "(confirmed_cause IS NULL AND incident_outcome IS NULL) OR reference IS NOT NULL",
            name="ck_feedback_label_reference",
        ),
        UniqueConstraint("feedback_id", "run_id", "reviewer", name="uq_feedback_identity"),
        UniqueConstraint("supersedes_id", name="uq_feedback_successor"),
        ForeignKeyConstraint(
            ["supersedes_id", "run_id", "reviewer"],
            [
                "recommendation_feedback.feedback_id",
                "recommendation_feedback.run_id",
                "recommendation_feedback.reviewer",
            ],
            name="fk_feedback_parent_identity",
        ),
        Index(
            "uq_feedback_root",
            "run_id",
            "reviewer",
            unique=True,
            postgresql_where=text("supersedes_id IS NULL"),
        ),
        Index("ix_feedback_created_at", "created_at"),
        Index("ix_feedback_run_id", "run_id"),
    )
