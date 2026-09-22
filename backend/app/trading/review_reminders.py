"""review_reminders.py — rules-driven review calendar for open positions.

Turns the Phase Framework in config/investment_rules.json (phase0_grace_days,
phase1_end_days, phase2_extension_days, BEP triggers) into dated review
milestones per position, plus the next earnings date (a catalyst that can
justify a Phase 2 extension). Only milestones due today or later are created,
so a position first seen mid-life doesn't arrive with a backlog of overdue
items.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Portfolio, PortfolioPosition, PositionReviewReminder
from .briefing_advice import load_investment_rules, vol_class

logger = structlog.get_logger("review_reminders")

EARNINGS_KIND = "earnings"
_EARNINGS_HORIZON_DAYS = 60
_OVERDUE_LOOKBACK_DAYS = 7


@dataclass(frozen=True)
class Milestone:
    kind: str
    due_date: date
    title: str
    detail: str


def _bep_pct(klass: str, rules: dict) -> float:
    if klass == "volatile":
        return float(rules.get("volatile_bep_trigger_pct", 5.0))
    return float(rules.get("stable_bep_trigger_pct", 2.0))


def phase_milestones(
    ticker: str,
    date_entered: Optional[date],
    entry_price: Optional[float],
    klass: str,
    rules: dict,
    today: date,
) -> list[Milestone]:
    if date_entered is None:
        return []
    p0 = int(rules.get("phase0_grace_days", 3))
    p1 = int(rules.get("phase1_end_days", 10))
    ext = max(1, int(rules.get("phase2_extension_days", 5)))
    bep = _bep_pct(klass, rules)
    label = {"volatile": "Volatile", "stable": "Stable"}.get(klass, "Unclassified")
    entry = f" from entry ${entry_price:,.2f}" if entry_price else ""
    target = f" (${entry_price * (1 + bep / 100):,.2f})" if entry_price else ""
    premium_warn = rules.get("analyst_target_premium_warn_pct", 40)

    out = [
        Milestone(
            "grace_end",
            date_entered + timedelta(days=p0 + 1),
            f"{ticker}: grace period over — BEP check",
            f"Day {p0 + 1}: Phase 1 starts. {label} ticker needs +{bep:g}%{entry}{target} before a "
            "partial exit is on the table. Hard stop stays where it was set — never moved against the position.",
        ),
        Milestone(
            "bep_decision",
            date_entered + timedelta(days=p1),
            f"{ticker}: BEP decision day",
            f"Day {p1}: last day of Phase 1. Not at +{bep:g}% and no catalyst → plan the exit "
            f"(Phase 2 starts tomorrow). A clear catalyst (earnings, news) earns a +{ext}d extension.",
        ),
        Milestone(
            "extension_end",
            date_entered + timedelta(days=p1 + ext),
            f"{ticker}: extension window ends",
            f"Day {p1 + ext}: if you extended for a catalyst, it has had {ext} days. Has it played out? "
            "Close, or re-justify the hold with a new reason.",
        ),
    ]
    k = 2
    while True:
        days = p1 + k * ext
        due = date_entered + timedelta(days=days)
        if due >= today:
            out.append(
                Milestone(
                    "phase2_review",
                    due,
                    f"{ticker}: Phase 2 review (day {days})",
                    f"Still holding on day {days}. Re-check: thesis intact, at/above BEP (+{bep:g}%), "
                    f"stop still valid, analyst target not already baked in (>{premium_warn:g}% premium warning).",
                )
            )
            break
        k += 1
    return [m for m in out if m.due_date >= today]


def earnings_milestone(ticker: str, due: date) -> Milestone:
    return Milestone(
        EARNINGS_KIND,
        due,
        f"{ticker}: earnings",
        "Earnings catalyst. Per the phase rules it can justify a Phase 2 extension; per the swing rules, "
        "no new swing trades this week. Review size and stop before the print.",
    )


def price_rule_checks(ticker: str, pos: dict, price: Optional[float], rules: dict) -> list[str]:
    """Rule 4 profit-taking checks for the pre-market briefing (not calendar
    entries — they depend on price, not date)."""
    entry = pos.get("purchase_price")
    if not entry or not price:
        return []
    gain = (price / entry - 1) * 100
    t1 = float(rules.get("target1_pct", rules.get("swing_target1_pct", 7.0)))
    t2 = float(rules.get("target2_pct", rules.get("swing_target2_pct", 12.0)))
    if gain >= t2:
        return [f"🎯 {ticker} +{gain:.1f}% from entry — past T2 (+{t2:g}%): rule 4 says exit the remainder."]
    if gain >= t1:
        return [f"🎯 {ticker} +{gain:.1f}% from entry — T1 (+{t1:g}%) hit: take partial profits (rule 4)."]
    return []


async def _upcoming_earnings(tickers: list[str], today: date) -> tuple[dict, set]:
    """Next earnings date within the horizon per ticker, plus the set of
    tickers whose lookup actually returned data — an empty result can mean a
    transient yfinance failure, so those tickers' existing rows are left alone."""
    from ..routes.market import _fetch_earnings_dates_set

    horizon = today + timedelta(days=_EARNINGS_HORIZON_DAYS)

    async def one(sym: str):
        try:
            raw = await asyncio.to_thread(_fetch_earnings_dates_set, sym)
        except Exception:
            return sym, None, False
        dates = []
        for s in raw or ():
            try:
                dates.append(date.fromisoformat(s))
            except ValueError:
                continue
        upcoming = sorted(d for d in dates if today <= d <= horizon)
        return sym, (upcoming[0] if upcoming else None), bool(raw)

    results = await asyncio.gather(*(one(t) for t in tickers))
    return {t: d for t, d, _ in results if d}, {t for t, _, known in results if known}


async def sync_review_reminders(
    session: AsyncSession,
    today: date,
    *,
    user_id=None,
    include_earnings: bool = True,
) -> dict:
    """Create missing milestones for open positions and cancel pending future
    ones that no longer apply (position closed, date_entered edited, earnings
    moved). Caller commits."""
    rules = load_investment_rules()
    q = (
        select(PortfolioPosition, Portfolio.user_id)
        .join(Portfolio, Portfolio.portfolio_id == PortfolioPosition.portfolio_id)
        .where(PortfolioPosition.closed_at.is_(None), PortfolioPosition.is_excluded.is_(False))
        .order_by(PortfolioPosition.date_entered, PortfolioPosition.position_id)
    )
    if user_id is not None:
        q = q.where(Portfolio.user_id == user_id)
    rows = (await session.execute(q)).all()
    open_ids = {pos.position_id for pos, _ in rows}

    earnings, earnings_known = ({}, set())
    if include_earnings and rows:
        tickers = sorted({(pos.ticker or "").upper() for pos, _ in rows if pos.ticker})
        earnings, earnings_known = await _upcoming_earnings(tickers, today)

    eq = select(PositionReviewReminder).where(PositionReviewReminder.due_date >= today)
    if user_id is not None:
        eq = eq.where(PositionReviewReminder.user_id == user_id)
    existing = (await session.execute(eq)).scalars().all()
    by_pos: dict = defaultdict(dict)
    for r in existing:
        by_pos[r.position_id][(r.kind, r.due_date)] = r

    stats = {"created": 0, "cancelled": 0}
    earnings_assigned: set = set()
    for pos, uid in rows:
        ticker = (pos.ticker or "").upper()
        if not ticker:
            continue
        entry = float(pos.purchase_price) if pos.purchase_price is not None else None
        wanted = phase_milestones(ticker, pos.date_entered, entry, vol_class(ticker, rules), rules, today)
        if ticker in earnings and (uid, ticker) not in earnings_assigned:
            wanted.append(earnings_milestone(ticker, earnings[ticker]))
            earnings_assigned.add((uid, ticker))
        wanted_keys = {(m.kind, m.due_date) for m in wanted}
        have = by_pos.get(pos.position_id, {})

        for m in wanted:
            if (m.kind, m.due_date) not in have:
                session.add(
                    PositionReviewReminder(
                        user_id=uid,
                        position_id=pos.position_id,
                        ticker=ticker,
                        kind=m.kind,
                        due_date=m.due_date,
                        title=m.title[:200],
                        detail=m.detail,
                    )
                )
                stats["created"] += 1
        for key, r in have.items():
            if r.status != "pending" or key in wanted_keys:
                continue
            if r.kind == EARNINGS_KIND and (not include_earnings or ticker not in earnings_known):
                continue
            r.status = "cancelled"
            stats["cancelled"] += 1

    for pos_id, reminders in by_pos.items():
        if pos_id in open_ids:
            continue
        for r in reminders.values():
            if r.status == "pending":
                r.status = "cancelled"
                stats["cancelled"] += 1

    await session.flush()
    return stats


async def reminders_due(session: AsyncSession, user_id, today: date) -> list[PositionReviewReminder]:
    """Pending reminders due today, plus recently overdue ones still open."""
    res = await session.execute(
        select(PositionReviewReminder)
        .where(
            PositionReviewReminder.user_id == user_id,
            PositionReviewReminder.status == "pending",
            PositionReviewReminder.due_date <= today,
            PositionReviewReminder.due_date >= today - timedelta(days=_OVERDUE_LOOKBACK_DAYS),
        )
        .order_by(PositionReviewReminder.due_date, PositionReviewReminder.ticker)
    )
    return list(res.scalars().all())


async def reminders_between(session: AsyncSession, user_id, start: date, end: date) -> list[PositionReviewReminder]:
    res = await session.execute(
        select(PositionReviewReminder)
        .where(
            PositionReviewReminder.user_id == user_id,
            PositionReviewReminder.due_date >= start,
            PositionReviewReminder.due_date <= end,
            PositionReviewReminder.status != "cancelled",
        )
        .order_by(PositionReviewReminder.due_date, PositionReviewReminder.ticker)
    )
    return list(res.scalars().all())
