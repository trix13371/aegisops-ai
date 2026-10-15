"""Append-only human assessments with explicit, concurrency-safe corrections."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_feedback"
down_revision = "0001_audit_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recommendation_feedback",
        sa.Column("feedback_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("recommendation_id", sa.String(128), nullable=False),
        sa.Column("recommendation_hash", sa.String(64), nullable=False),
        sa.Column("reviewer", sa.String(64), nullable=False),
        sa.Column("assessment", sa.String(16), nullable=False),
        sa.Column("comment", sa.String(2000), nullable=False, server_default=""),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["audit_runs.run_id"]),
        sa.CheckConstraint("schema_version = 1", name="ck_feedback_schema_version"),
        sa.CheckConstraint(
            "origin IN ('synthetic_fixture', 'local_kind')", name="ck_feedback_origin"
        ),
        sa.CheckConstraint(
            "assessment IN ('useful', 'incorrect', 'unsafe', 'uncertain')",
            name="ck_feedback_assessment",
        ),
        sa.CheckConstraint(
            "reviewer ~ '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$'", name="ck_feedback_reviewer"
        ),
        sa.CheckConstraint("recommendation_hash ~ '^[0-9a-f]{64}$'", name="ck_feedback_hash"),
        sa.CheckConstraint("supersedes_id <> feedback_id", name="ck_feedback_not_self"),
        sa.UniqueConstraint("feedback_id", "run_id", "reviewer", name="uq_feedback_identity"),
        sa.UniqueConstraint("supersedes_id", name="uq_feedback_successor"),
        sa.ForeignKeyConstraint(
            ["supersedes_id", "run_id", "reviewer"],
            [
                "recommendation_feedback.feedback_id",
                "recommendation_feedback.run_id",
                "recommendation_feedback.reviewer",
            ],
            name="fk_feedback_parent_identity",
        ),
    )
    op.create_index(
        "uq_feedback_root",
        "recommendation_feedback",
        ["run_id", "reviewer"],
        unique=True,
        postgresql_where=sa.text("supersedes_id IS NULL"),
    )
    op.create_index("ix_feedback_created_at", "recommendation_feedback", ["created_at"])
    op.create_index("ix_feedback_run_id", "recommendation_feedback", ["run_id"])
    op.execute("REVOKE ALL ON TABLE public.recommendation_feedback FROM PUBLIC, aegisops_app")
    op.execute("GRANT SELECT, INSERT ON TABLE public.recommendation_feedback TO aegisops_app")


def downgrade() -> None:
    op.drop_table("recommendation_feedback")
