"""Rules-driven review reminders for open positions (Calendar page)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "position_review_reminders",
        sa.Column("reminder_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "position_id",
            UUID(as_uuid=True),
            sa.ForeignKey("portfolio_positions.position_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("google_event_id", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("position_id", "kind", "due_date", name="uq_review_reminder_position_kind_date"),
    )
    op.create_index("idx_review_reminders_user_due", "position_review_reminders", ["user_id", "due_date"])


def downgrade() -> None:
    op.drop_index("idx_review_reminders_user_due", table_name="position_review_reminders")
    op.drop_table("position_review_reminders")
