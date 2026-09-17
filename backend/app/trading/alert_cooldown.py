"""alert_cooldown.py — per (user, ticker, event_type) Telegram-alert cooldown.

Multiple distinct insider filings on the same ticker in a short window used
to each become their own Telegram message — e.g. three different insiders
filing Form 144 notices on the same name in one day was three separate
pings, none of them individually actionable. This makes only the first one
in a rolling window actually send; the rest are recorded (not lost) so the
end-of-day digest can report "N more alerts on TICKER were suppressed
today" instead of the user hearing nothing about them at all.

Deliberately scoped by event_type, not just ticker: a Form 144 "planned
sell" notice and the later Form 4 "insider_sell" that actually executes it
are different event types, so one cooling down never suppresses the other —
the actual sale confirmation should always reach the user even right after
its own advance notice did.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import TickerAlertCooldown

DEFAULT_COOLDOWN_HOURS = 12
_MAX_SUPPRESSED_SUMMARIES = 10


async def should_send_alert(
    session: AsyncSession,
    user_id,
    ticker: str,
    event_type: str,
    summary: str,
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS,
) -> bool:
    """Returns True if this alert should actually be sent now. If a cooldown
    is active for this (user, ticker, event_type), records `summary` into
    the suppressed list instead and returns False.

    Caller is responsible for committing the session afterward (this just
    stages the row/update, matching how the monitor cycles already batch
    one commit per cycle).
    """
    result = await session.execute(
        select(TickerAlertCooldown).where(
            TickerAlertCooldown.user_id == user_id,
            TickerAlertCooldown.ticker == ticker,
            TickerAlertCooldown.event_type == event_type,
        )
    )
    row = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if row is not None and (now - row.last_alert_at) < timedelta(hours=cooldown_hours):
        row.suppressed_count = (row.suppressed_count or 0) + 1
        existing = list(row.suppressed_summaries or [])
        existing.append(summary[:200])
        row.suppressed_summaries = existing[-_MAX_SUPPRESSED_SUMMARIES:]
        return False

    if row is not None:
        row.last_alert_at = now
        row.suppressed_count = 0
        row.suppressed_summaries = []
    else:
        session.add(
            TickerAlertCooldown(
                cooldown_id=uuid4(),
                user_id=user_id,
                ticker=ticker,
                event_type=event_type,
                last_alert_at=now,
                suppressed_count=0,
                suppressed_summaries=[],
            )
        )
    return True


async def pop_suppressed_for_digest(session: AsyncSession, user_id) -> list[dict]:
    """Returns and clears every cooldown row with suppressed activity for
    this user, for the end-of-day digest to report. Does not touch rows
    with nothing suppressed (their cooldown timer keeps running normally)."""
    result = await session.execute(
        select(TickerAlertCooldown).where(
            TickerAlertCooldown.user_id == user_id,
            TickerAlertCooldown.suppressed_count > 0,
        )
    )
    rows = result.scalars().all()
    out = []
    for row in rows:
        out.append({
            "ticker": row.ticker,
            "event_type": row.event_type,
            "suppressed_count": row.suppressed_count,
            "summaries": list(row.suppressed_summaries or []),
        })
        row.suppressed_count = 0
        row.suppressed_summaries = []
    return out
