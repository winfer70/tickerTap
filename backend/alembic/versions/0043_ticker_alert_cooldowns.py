"""Per-(user, ticker, event_type) alert cooldown to cut insider-alert spam."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticker_alert_cooldowns",
        sa.Column("cooldown_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("last_alert_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("suppressed_summaries", JSONB, nullable=True),
        sa.Column("suppressed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "user_id", "ticker", "event_type", name="uq_ticker_alert_cooldowns_user_ticker_event"
        ),
    )
    op.create_index("idx_ticker_alert_cooldowns_user_id", "ticker_alert_cooldowns", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_ticker_alert_cooldowns_user_id", table_name="ticker_alert_cooldowns")
    op.drop_table("ticker_alert_cooldowns")
