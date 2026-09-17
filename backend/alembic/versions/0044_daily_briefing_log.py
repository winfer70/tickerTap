"""Dedup log for pre-market/post-market Telegram briefings."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_briefing_log",
        sa.Column("log_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("briefing_type", sa.String(20), nullable=False),
        sa.Column("sent_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "user_id", "briefing_type", "sent_date", name="uq_daily_briefing_log_user_type_date"
        ),
    )


def downgrade() -> None:
    op.drop_table("daily_briefing_log")
