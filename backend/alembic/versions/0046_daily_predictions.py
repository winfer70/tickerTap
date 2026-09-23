"""Daily pre-market predictions, post-close grading, and distilled lessons."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "daily_predictions",
        sa.Column("prediction_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("confidence", sa.SmallInteger(), nullable=False),
        sa.Column("expected_move_pct", sa.Numeric(8, 3), nullable=True),
        sa.Column("action", sa.String(120), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("reference_close", sa.Numeric(18, 4), nullable=True),
        sa.Column("close_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("actual_change_pct", sa.Numeric(8, 3), nullable=True),
        sa.Column("market_change_pct", sa.Numeric(8, 3), nullable=True),
        sa.Column("outcome", sa.String(12), nullable=True),
        sa.Column("reflection", sa.Text(), nullable=True),
        sa.Column("model", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("graded_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "trade_date", "ticker", name="uq_daily_predictions_user_date_ticker"),
    )
    op.create_index("idx_daily_predictions_user_date", "daily_predictions", ["user_id", "trade_date"])
    op.create_table(
        "prediction_lessons",
        sa.Column("lesson_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=True),
        sa.Column("lesson", sa.Text(), nullable=False),
        sa.Column("source_date", sa.Date(), nullable=False),
        sa.Column(
            "prediction_id",
            UUID(as_uuid=True),
            sa.ForeignKey("daily_predictions.prediction_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_prediction_lessons_user_active", "prediction_lessons", ["user_id", "active"])


def downgrade() -> None:
    op.drop_index("idx_prediction_lessons_user_active", table_name="prediction_lessons")
    op.drop_table("prediction_lessons")
    op.drop_index("idx_daily_predictions_user_date", table_name="daily_predictions")
    op.drop_table("daily_predictions")
