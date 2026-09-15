"""Store "Analyze with AI" (Kamilo) requests/verdicts from the Insider page."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "insider_ai_analyses",
        sa.Column("analysis_id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_tab", sa.String(10), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=True),
        sa.Column("filters_json", JSONB, nullable=True),
        sa.Column("filings_considered", JSONB, nullable=True),
        sa.Column("portfolio_snapshot", JSONB, nullable=True),
        sa.Column("rating", sa.String(10), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_insider_ai_analyses_user_id", "insider_ai_analyses", ["user_id"])
    op.create_index("idx_insider_ai_analyses_ticker", "insider_ai_analyses", ["ticker"])


def downgrade() -> None:
    op.drop_index("idx_insider_ai_analyses_ticker", table_name="insider_ai_analyses")
    op.drop_index("idx_insider_ai_analyses_user_id", table_name="insider_ai_analyses")
    op.drop_table("insider_ai_analyses")
