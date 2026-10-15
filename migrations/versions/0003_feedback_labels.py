"""Optional user-asserted cause/outcome labels; preserve existing feedback."""

import sqlalchemy as sa
from alembic import op

revision = "0003_feedback_labels"
down_revision = "0002_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("recommendation_feedback", sa.Column("confirmed_cause", sa.String(32)))
    op.add_column("recommendation_feedback", sa.Column("incident_outcome", sa.String(16)))
    op.add_column("recommendation_feedback", sa.Column("reference", sa.String(64)))
    for name, condition in (
        (
            "ck_feedback_confirmed_cause",
            "confirmed_cause IN ('application_exit_nonzero', "
            "'missing_runtime_config', 'invalid_command', 'oom_killed')",
        ),
        (
            "ck_feedback_incident_outcome",
            "incident_outcome IN ('resolved', 'unresolved', 'unknown')",
        ),
        ("ck_feedback_reference", "reference ~ '^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$'"),
        (
            "ck_feedback_label_reference",
            "(confirmed_cause IS NULL AND incident_outcome IS NULL) OR reference IS NOT NULL",
        ),
    ):
        op.create_check_constraint(name, "recommendation_feedback", condition)


def downgrade() -> None:
    for name in (
        "ck_feedback_label_reference",
        "ck_feedback_reference",
        "ck_feedback_incident_outcome",
        "ck_feedback_confirmed_cause",
    ):
        op.drop_constraint(name, "recommendation_feedback", type_="check")
    for name in ("reference", "incident_outcome", "confirmed_cause"):
        op.drop_column("recommendation_feedback", name)
