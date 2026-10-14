"""Initial audit schema and restricted application grants."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_audit_runs"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_runs",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("incident_id", sa.String(128)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("autonomy_level", sa.String(2), nullable=False, server_default="L0"),
        sa.Column("origin", sa.String(32), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("model_name", sa.String(128)),
        sa.Column("prompt_id", sa.String(128)),
        sa.Column("prompt_hash", sa.String(64)),
        sa.Column("policy_version", sa.String(64)),
        sa.Column("context_hash", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'failed', 'blocked')", name="ck_audit_runs_status"
        ),
        sa.CheckConstraint("autonomy_level = 'L0'", name="ck_audit_runs_autonomy_level"),
        sa.CheckConstraint(
            "origin IN ('synthetic_fixture', 'local_kind')", name="ck_audit_runs_origin"
        ),
        sa.CheckConstraint("schema_version >= 1", name="ck_audit_runs_schema_version"),
    )
    op.create_index("ix_audit_runs_incident_id", "audit_runs", ["incident_id"])
    op.create_index("ix_audit_runs_created_at", "audit_runs", ["created_at"])
    op.execute("REVOKE ALL ON TABLE public.audit_runs FROM PUBLIC, aegisops_app")
    op.execute("GRANT SELECT, INSERT ON TABLE public.audit_runs TO aegisops_app")
    op.execute("GRANT SELECT ON TABLE public.alembic_version TO aegisops_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON TABLE public.alembic_version FROM aegisops_app")
    op.drop_table("audit_runs")
