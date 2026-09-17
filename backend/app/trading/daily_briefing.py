"""daily_briefing.py — pre-market and post-market portfolio digest.

Two arq cron entrypoints (send_premarket_briefings, send_postmarket_briefings)
that each run every few minutes on trading-worker and check "is it currently
within today's target ET window" from inside the job body — the same
DST-safe pattern alert_worker.py already uses for its EOD soft-stop check
(_is_after_rth_close), rather than trusting a single fixed-UTC cron minute
to stay aligned with 09:30/16:00 ET across DST transitions.

Because the window is a few minutes wide and the job can fire more than
once inside it, DailyBriefingLog (one row per user/briefing_type/date) is
the dedup: the first hit in the window sends and logs, every later hit in
the same window (or later that day) is a no-op.

Content deliberately reuses existing signals instead of building a new
scanner:
  - "Worth a look" (pre-market) is recent (default 7d) Form 4 open-market
    buys above a materiality floor on tickers already held or watchlisted —
    the same insider-buy signal insider_monitor.py already gates on, just
    surfaced here as a daily roundup instead of (or in addition to) a
    one-off alert.
  - Stop-level warnings (both briefings) reuse PortfolioPosition's existing
    hard_stop/soft_stop fields via book_loader's BookSnapshot.
  - The post-market recap's "suppressed today" section reuses
    alert_cooldown.pop_suppressed_for_digest() so cooldown-suppressed
    insider filings surface here instead of silently vanishing.
"""
from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from ..models import DailyBriefingLog, InsiderFiling
from .alert_cooldown import pop_suppressed_for_digest
from .book_loader import load_books
from .insider_gate import BookSnapshot
from .notifications import notify_soft_stop

logger = structlog.get_logger("daily_briefing")

_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@db:5432/tickerTap",
)
_engine = create_async_engine(_DATABASE_URL, echo=False, pool_size=2, max_overflow=1, pool_pre_ping=True)
_SessionLocal = sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

_NY_TZ = ZoneInfo("America/New_York")
# Windows are a few minutes wide, not a single instant, because the cron
# firing this job runs on the worker's own clock at a coarser granularity
# than a minute is guaranteed to land on exactly.
_PREMARKET_WINDOW = (9, 0, 9, 15)  # 09:00-09:15 ET, ahead of the 09:30 open
_POSTMARKET_WINDOW = (16, 30, 16, 45)  # 16:30-16:45 ET, after the 16:00 close
_BUY_SIGNAL_LOOKBACK_DAYS = 7
_BUY_SIGNAL_MIN_NOTIONAL = Decimal("25000")
_MAX_BUY_IDEAS = 5
_NEAR_STOP_PCT = Decimal("0.02")


def _ny_now() -> datetime:
    return datetime.now(_NY_TZ)


def _in_window(now: datetime, window: tuple) -> bool:
    h1, m1, h2, m2 = window
    start = now.replace(hour=h1, minute=m1, second=0, microsecond=0)
    end = now.replace(hour=h2, minute=m2, second=0, microsecond=0)
    return start <= now < end


async def _already_sent(session: AsyncSession, user_id, briefing_type: str, day: date) -> bool:
    res = await session.execute(
        select(DailyBriefingLog.log_id).where(
            DailyBriefingLog.user_id == user_id,
            DailyBriefingLog.briefing_type == briefing_type,
            DailyBriefingLog.sent_date == day,
        )
    )
    return res.scalar_one_or_none() is not None


def _mark_sent(session: AsyncSession, user_id, briefing_type: str, day: date) -> None:
    session.add(DailyBriefingLog(user_id=user_id, briefing_type=briefing_type, sent_date=day))


async def _fetch_quotes(tickers: list) -> dict:
    """Best-effort quote fetch for a list of tickers, run off the event loop
    since yfinance is synchronous. Missing/failed tickers are simply absent
    from the result rather than raising."""
    if not tickers:
        return {}
    from ..routes.market import _fetch_quote

    out: dict = {}

    async def _one(sym: str) -> None:
        try:
            out[sym] = await asyncio.to_thread(_fetch_quote, sym)
        except Exception:
            logger.warning("briefing_quote_failed", ticker=sym)

    await asyncio.gather(*(_one(t) for t in tickers))
    return out


async def _recent_buy_signals(session: AsyncSession, tickers: list) -> list:
    """Recent open-market insider buys (Form 4, code P) above the
    materiality floor on tickers the user already holds or watches, most
    recent/largest first, one entry per ticker."""
    if not tickers:
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=_BUY_SIGNAL_LOOKBACK_DAYS)).date()
    res = await session.execute(
        select(InsiderFiling)
        .where(
            InsiderFiling.ticker.in_(tickers),
            InsiderFiling.transaction_code == "P",
            InsiderFiling.transaction_date >= since,
            InsiderFiling.notional >= _BUY_SIGNAL_MIN_NOTIONAL,
        )
        .order_by(InsiderFiling.notional.desc())
    )
    seen: set = set()
    out = []
    for row in res.scalars().all():
        if row.ticker in seen:
            continue
        seen.add(row.ticker)
        role = "10% owner" if row.is_ten_percent else (row.officer_title or ("Director" if row.is_director else "Insider"))
        out.append(
            {
                "ticker": row.ticker,
                "summary": f"{role} bought ~${float(row.notional):,.0f} ({row.transaction_date})",
            }
        )
    return out


def _near_stop_note(ticker: str, pos: dict, price: Decimal) -> Optional[str]:
    hard = pos.get("hard_stop")
    soft = pos.get("soft_stop")
    if hard and price <= Decimal(str(hard)) * (1 + _NEAR_STOP_PCT):
        return f"⚠️ {ticker}: ${price:,.2f} approaching hard stop ${hard:,.2f}"
    if soft and price <= Decimal(str(soft)) * (1 + _NEAR_STOP_PCT):
        return f"⚠️ {ticker}: ${price:,.2f} approaching soft stop ${soft:,.2f}"
    return None


async def build_premarket_briefing(session: AsyncSession, user_id, book: BookSnapshot) -> Optional[str]:
    tickers = sorted(book.held_tickers | book.watchlist_tickers)
    if not tickers:
        return None
    quotes = await _fetch_quotes(tickers)

    lines: list = []
    if book.held_tickers:
        watch = []
        for t in sorted(book.held_tickers):
            q = quotes.get(t)
            pos = book.positions.get(t) or {}
            if not q:
                continue
            note = _near_stop_note(t, pos, Decimal(str(q.price)))
            if note:
                watch.append(note)
        if watch:
            lines.append("Watch today (near a stop level as of last close):")
            lines.extend(watch)
        else:
            lines.append(f"{len(book.held_tickers)} holding(s), none near a stop level as of last close.")

    ideas = await _recent_buy_signals(session, tickers)
    if ideas:
        if lines:
            lines.append("")
        lines.append("Worth a look (recent insider buying):")
        for idea in ideas[:_MAX_BUY_IDEAS]:
            lines.append(f"• {idea['ticker']}: {idea['summary']}")

    if not lines:
        return None
    return "\n".join(lines)[:3500]


async def build_postmarket_briefing(session: AsyncSession, user_id, book: BookSnapshot) -> Optional[str]:
    tickers = sorted(book.held_tickers)
    if not tickers:
        return None
    quotes = await _fetch_quotes(tickers)

    total_value = Decimal("0")
    total_day_pl = Decimal("0")
    movers = []
    near_stop = []
    for t in tickers:
        q = quotes.get(t)
        if not q:
            continue
        pos = book.positions.get(t) or {}
        qty = Decimal(str(pos.get("quantity") or 0))
        price = Decimal(str(q.price))
        total_value += qty * price
        total_day_pl += qty * Decimal(str(q.change))
        movers.append((t, q.change_pct))
        note = _near_stop_note(t, pos, price)
        if note:
            near_stop.append(note)

    lines = []
    sign = "+" if total_day_pl >= 0 else ""
    lines.append(f"Portfolio: ${total_value:,.2f} ({sign}${total_day_pl:,.2f} today)")

    movers.sort(key=lambda m: m[1], reverse=True)
    if len(movers) > 1:
        best, worst = movers[0], movers[-1]
        lines.append(f"Best: {best[0]} {'+' if best[1] >= 0 else ''}{best[1]:.2f}%")
        lines.append(f"Worst: {worst[0]} {'+' if worst[1] >= 0 else ''}{worst[1]:.2f}%")

    if near_stop:
        lines.append("")
        lines.append("For tomorrow:")
        lines.extend(near_stop)

    suppressed = await pop_suppressed_for_digest(session, user_id)
    if suppressed:
        lines.append("")
        lines.append("Cooled-down alerts today (no separate ping sent):")
        for s in suppressed[:8]:
            lines.append(f"• {s['ticker']} ({s['event_type']}): {s['suppressed_count']} more filing(s)")

    return "\n".join(lines)[:3500]


async def send_premarket_briefings(ctx: dict) -> dict:
    """arq cron entrypoint — schedule frequently (e.g. every 5 min); no-ops
    outside the 09:00-09:15 ET weekday window."""
    now = _ny_now()
    if now.weekday() >= 5 or not _in_window(now, _PREMARKET_WINDOW):
        return {"skipped": True}
    return await _run_briefing_cycle("premarket", build_premarket_briefing, "☀️ Pre-market briefing")


async def send_postmarket_briefings(ctx: dict) -> dict:
    """arq cron entrypoint — schedule frequently (e.g. every 5 min); no-ops
    outside the 16:30-16:45 ET weekday window."""
    now = _ny_now()
    if now.weekday() >= 5 or not _in_window(now, _POSTMARKET_WINDOW):
        return {"skipped": True}
    return await _run_briefing_cycle("postmarket", build_postmarket_briefing, "🌙 Post-market recap")


async def _run_briefing_cycle(briefing_type: str, builder, title: str) -> dict:
    stats = {"sent": 0, "empty": 0, "errors": 0}
    today = _ny_now().date()
    async with _SessionLocal() as session:
        books, _sectors = await load_books(session)
        for uid, book in books.items():
            try:
                if await _already_sent(session, uid, briefing_type, today):
                    continue
                text = await builder(session, uid, book)
                _mark_sent(session, uid, briefing_type, today)
                if text:
                    await notify_soft_stop(
                        db=session,
                        user_id=uid,
                        event_type="daily_briefing",
                        title=title,
                        body=text,
                        metadata={"source": "daily_briefing", "kind": briefing_type},
                        ntfy_priority=3,
                        send_in_app=True,
                        send_telegram=True,
                        send_ntfy=False,
                    )
                    stats["sent"] += 1
                else:
                    stats["empty"] += 1
                await session.commit()
            except Exception:
                await session.rollback()
                stats["errors"] += 1
                logger.exception("daily_briefing_failed", user_id=str(uid), briefing_type=briefing_type)
    logger.info("daily_briefing_cycle_done", briefing_type=briefing_type, **stats)
    return stats
