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

Each briefing is two Telegram messages so neither hits Telegram's 4096-char
limit:
  - Pre-market: the briefing (reviews due from the rules-driven calendar,
    rule-4 profit-taking checks, stop warnings, recent insider buying on
    held/watched names) and "Today's calls" (predictions.py).
  - Post-market: the recap (P&L, movers, reviews coming up, alerts deferred
    to this digest by alert_tiers.py, cooldown-suppressed alerts) and the
    prediction scorecard with the reflections and new lessons.
"""
from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from ..models import DailyBriefingLog, InsiderFiling, Notification
from . import predictions
from .alert_cooldown import pop_suppressed_for_digest
from .book_loader import load_books
from .insider_gate import BookSnapshot
from .notifications import notify_soft_stop
from .review_reminders import record_price_milestones, reminders_between, reminders_due, sync_review_reminders

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
_UPCOMING_REVIEW_DAYS = 3
_MAX_DIGEST_TICKERS = 8
_TELEGRAM_BODY_LIMIT = 3900


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


async def _deferred_alerts(session: AsyncSession, user_id, book: BookSnapshot) -> list[str]:
    """Alerts alert_tiers.py routed to this digest instead of a real-time ping
    (last 24h), grouped per ticker with held positions first."""
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    res = await session.execute(
        select(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.created_at >= since,
            Notification.metadata_json["digest"].astext == "true",
        )
        .order_by(Notification.created_at)
    )
    by_ticker: dict = defaultdict(list)
    for n in res.scalars().all():
        ticker = ((n.metadata_json or {}).get("ticker") or "?").upper()
        by_ticker[ticker].append(n.title)
    if not by_ticker:
        return []
    order = sorted(by_ticker, key=lambda t: (t not in book.held_tickers, -len(by_ticker[t]), t))
    lines = ["Lower-priority alerts today (no ping sent):"]
    for t in order[:_MAX_DIGEST_TICKERS]:
        tag = "held" if t in book.held_tickers else "watchlist" if t in book.watchlist_tickers else "not held"
        titles = "; ".join(title[:80] for title in by_ticker[t][:2])
        more = f" (+{len(by_ticker[t]) - 2} more)" if len(by_ticker[t]) > 2 else ""
        lines.append(f"• {t} ({tag}): {titles}{more}")
    if len(order) > _MAX_DIGEST_TICKERS:
        lines.append(f"…and {len(order) - _MAX_DIGEST_TICKERS} more tickers — see Alerts in the app.")
    return lines


def _join(sections: list[list[str]]) -> Optional[str]:
    parts = ["\n".join(s) for s in sections if s]
    if not parts:
        return None
    return "\n\n".join(parts)[:_TELEGRAM_BODY_LIMIT]


async def build_premarket_messages(
    session: AsyncSession, user_id, book: BookSnapshot, today: date
) -> list[tuple[str, str]]:
    tickers = sorted(book.held_tickers | book.watchlist_tickers)
    if not tickers:
        return []
    quotes = await _fetch_quotes(tickers)
    now = datetime.now(timezone.utc)
    await record_price_milestones(session, user_id, {t: q.price for t, q in quotes.items()}, today)

    reviews: list[str] = []
    for r in await reminders_due(session, user_id, today):
        overdue = "" if r.due_date == today else f" (due {r.due_date:%d %b})"
        reviews.append(f"📅 {r.title}{overdue}")
        if r.detail:
            reviews.append(f"   {r.detail[:220]}")
        if r.notified_at is None:
            r.notified_at = now
    stops: list[str] = []
    for t in sorted(book.held_tickers):
        q = quotes.get(t)
        if not q:
            continue
        note = _near_stop_note(t, book.positions.get(t) or {}, Decimal(str(q.price)))
        if note:
            stops.append(note)
    review_section = (["Reviews due:"] + reviews) if reviews else []

    stop_section: list[str] = []
    if stops:
        stop_section = ["Watch today (near a stop level):"] + stops
    elif book.held_tickers:
        stop_section = [f"{len(book.held_tickers)} holding(s), none near a stop level."]

    idea_section: list[str] = []
    ideas = await _recent_buy_signals(session, tickers)
    if ideas:
        idea_section = ["Worth a look (recent insider buying):"]
        idea_section += [f"• {i['ticker']}: {i['summary']}" for i in ideas[:_MAX_BUY_IDEAS]]

    messages = []
    body = _join([review_section, stop_section, idea_section])
    if body:
        messages.append(("☀️ Pre-market briefing", body))

    # Catch up anything left ungraded from earlier sessions before making
    # today's calls, so today's prompt sees the freshest track record/lessons.
    catch_up = await predictions.grade_pending(session, user_id, upto=today - timedelta(days=1))
    await predictions.reflect(session, user_id, catch_up)
    preds, market_view = await predictions.generate_predictions(session, user_id, book, today)
    stats = await predictions.track_record(session, user_id, today)
    calls = _join([predictions.format_calls(preds, market_view, stats)])
    if calls:
        messages.append(("🔮 Today's calls", calls + "\n\nModel calls, not advice — graded after the close."))
    return messages


async def build_postmarket_messages(
    session: AsyncSession, user_id, book: BookSnapshot, today: date
) -> list[tuple[str, str]]:
    tickers = sorted(book.held_tickers)
    messages = []
    recap: list[str] = []
    near_stop: list[str] = []
    if tickers:
        quotes = await _fetch_quotes(tickers)
        total_value = Decimal("0")
        total_day_pl = Decimal("0")
        movers = []
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
        sign = "+" if total_day_pl >= 0 else ""
        recap.append(f"Portfolio: ${total_value:,.2f} ({sign}${total_day_pl:,.2f} today)")
        movers.sort(key=lambda m: m[1], reverse=True)
        if len(movers) > 1:
            best, worst = movers[0], movers[-1]
            recap.append(f"Best: {best[0]} {best[1]:+.2f}%")
            recap.append(f"Worst: {worst[0]} {worst[1]:+.2f}%")

    upcoming = [
        r for r in await reminders_between(
            session, user_id, today + timedelta(days=1), today + timedelta(days=_UPCOMING_REVIEW_DAYS)
        )
        if r.status == "pending"
    ]
    tomorrow: list[str] = []
    if near_stop or upcoming:
        tomorrow = ["Coming up:"] + near_stop + [f"📅 {r.due_date:%a %d %b}: {r.title}" for r in upcoming]

    deferred = await _deferred_alerts(session, user_id, book)
    cooled: list[str] = []
    suppressed = await pop_suppressed_for_digest(session, user_id)
    if suppressed:
        cooled = ["Cooled-down alerts today (no separate ping sent):"]
        cooled += [f"• {s['ticker']} ({s['event_type']}): {s['suppressed_count']} more filing(s)" for s in suppressed[:8]]

    body = _join([recap, tomorrow, deferred, cooled])
    if body:
        messages.append(("🌙 Post-market recap", body))

    graded = await predictions.grade_pending(session, user_id, upto=today)
    lessons = await predictions.reflect(session, user_id, graded)
    stats = await predictions.track_record(session, user_id, today)
    card = _join([predictions.format_scorecard(graded, lessons, stats)])
    if card:
        messages.append(("📊 Prediction scorecard", card))
    return messages


async def send_premarket_briefings(ctx: dict) -> dict:
    """arq cron entrypoint — schedule frequently (e.g. every 5 min); no-ops
    outside the 09:00-09:15 ET weekday window."""
    now = _ny_now()
    if now.weekday() >= 5 or not _in_window(now, _PREMARKET_WINDOW):
        return {"skipped": True}
    return await _run_briefing_cycle("premarket", build_premarket_messages)


async def send_postmarket_briefings(ctx: dict) -> dict:
    """arq cron entrypoint — schedule frequently (e.g. every 5 min); no-ops
    outside the 16:30-16:45 ET weekday window."""
    now = _ny_now()
    if now.weekday() >= 5 or not _in_window(now, _POSTMARKET_WINDOW):
        return {"skipped": True}
    return await _run_briefing_cycle("postmarket", build_postmarket_messages)


async def _run_briefing_cycle(briefing_type: str, builder) -> dict:
    stats = {"sent": 0, "empty": 0, "errors": 0}
    today = _ny_now().date()
    async with _SessionLocal() as session:
        if briefing_type == "premarket":
            try:
                logger.info("review_reminders_synced", **await sync_review_reminders(session, today))
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("review_reminders_sync_failed")
        books, _sectors = await load_books(session)
        for uid, book in books.items():
            try:
                if await _already_sent(session, uid, briefing_type, today):
                    continue
                messages = await builder(session, uid, book, today)
                _mark_sent(session, uid, briefing_type, today)
                for title, body in messages:
                    await notify_soft_stop(
                        db=session,
                        user_id=uid,
                        event_type="daily_briefing",
                        title=title,
                        body=body,
                        metadata={"source": "daily_briefing", "kind": briefing_type},
                        ntfy_priority=3,
                        send_in_app=True,
                        send_telegram=True,
                        send_ntfy=False,
                    )
                stats["sent" if messages else "empty"] += 1
                await session.commit()
            except Exception:
                await session.rollback()
                stats["errors"] += 1
                logger.exception("daily_briefing_failed", user_id=str(uid), briefing_type=briefing_type)
    logger.info("daily_briefing_cycle_done", briefing_type=briefing_type, **stats)
    return stats
