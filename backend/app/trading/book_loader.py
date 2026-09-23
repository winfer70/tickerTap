"""book_loader.py — Build per-user BookSnapshots (portfolio + watchlist).

Extracted from insider_monitor.py so form144_monitor.py (and any other
poller that needs to know "which users care about this ticker") can share
the exact same book-building logic without insider_monitor.py <-> that
poller becoming a circular import — insider_monitor.py already imports
from form144_monitor.py (get_recent_144_notice) for the sell-side
correlation note, so this had to live somewhere both can reach.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Portfolio, PortfolioPosition, Watchlist, WatchlistItem
from .briefing_advice import load_investment_rules
from .insider_gate import BookSnapshot, sector_exposure


def entry_date(pos) -> Optional[date]:
    """date_entered is rarely set (DeGiro sync/manual adds fill purchase_date
    instead), so fall back to purchase_date for phase-framework math."""
    if pos.date_entered is not None:
        return pos.date_entered
    return pos.purchase_date.date() if pos.purchase_date is not None else None


def is_stock(asset_type: Optional[str]) -> bool:
    """Phase framework / T1-T2 / daily predictions apply to stock trades,
    not long-term crypto or physical-gold holdings."""
    return (asset_type or "stock").lower() in ("stock", "etf")


def _merge_lot(positions: dict, ticker: str, pos) -> None:
    """One entry per ticker even when it's held in several lots: quantities
    summed, cost basis quantity-weighted, earliest entry date, tightest
    (highest) stops. Previously each lot overwrote the last, so a two-lot
    holding counted only its last lot's quantity and price."""
    qty = float(pos.quantity or 0)
    price = float(pos.purchase_price or 0)
    lot = {
        "quantity": qty,
        "purchase_price": price,
        "hard_stop": float(pos.hard_stop_loss) if pos.hard_stop_loss is not None else None,
        "soft_stop": float(pos.soft_stop_loss) if pos.soft_stop_loss is not None else None,
        "date_entered": entry_date(pos),
        "asset_type": pos.asset_type,
    }
    prev = positions.get(ticker)
    if prev is None:
        positions[ticker] = lot
        return
    total = prev["quantity"] + qty
    if total:
        prev["purchase_price"] = (prev["quantity"] * prev["purchase_price"] + qty * price) / total
    prev["quantity"] = total
    for key in ("hard_stop", "soft_stop"):
        values = [v for v in (prev[key], lot[key]) if v is not None]
        prev[key] = max(values) if values else None
    dates = [d for d in (prev["date_entered"], lot["date_entered"]) if d is not None]
    prev["date_entered"] = min(dates) if dates else None


def load_avoid_tickers() -> set:
    return {str(t).upper() for t in load_investment_rules().get("avoid_tickers", [])}


async def load_books(session: AsyncSession) -> tuple[dict, dict]:
    """Open positions + watchlist items -> one BookSnapshot per user who has
    either, keyed by user_id, plus a shared ticker->sector map (issuer-level
    metadata, not user-specific, so it's fine to pool across everyone's
    positions).

    Watchlist tickers get full gating parity with real holdings (a watchlist
    ticker's insider sell/buy alerts the same way a held position's would —
    the closest available signal for a "planned buy" alert, since unlike
    Form 144's real "planned sell" notice, there's no SEC filing that
    telegraphs an intended purchase) but are kept in a separate
    watchlist_tickers set, not folded into held_tickers or positions, so
    sector-exposure math and stop/phase advice stay scoped to real
    positions only — a watchlist ticker has no shares, cost basis, or entry
    date to compute those from.

    Replaces the old single-aggregate load_book(), which combined every
    user's positions into one BookSnapshot and picked "whichever user_id
    was encountered first" for every notification — meaning a second user
    (e.g. a friend with their own linked Telegram chat) never got their own
    personalized gate/advice, and could even have their position silently
    overwritten in the shared `positions` dict if they and another user both
    held the same ticker (dict keys are ticker, not (user, ticker)).
    """
    res = await session.execute(
        select(PortfolioPosition, Portfolio.user_id)
        .join(Portfolio, Portfolio.portfolio_id == PortfolioPosition.portfolio_id)
        .where(
            PortfolioPosition.closed_at.is_(None),
            PortfolioPosition.is_excluded.is_(False),
        )
    )
    rows = res.all()
    by_user: dict = {}
    sectors: dict[str, str] = {}
    for pos, uid in rows:
        by_user.setdefault(uid, []).append(pos)
        ticker = (pos.ticker or "").upper()
        if pos.sector:
            sectors[ticker] = pos.sector

    watchlist_res = await session.execute(
        select(WatchlistItem.symbol, Watchlist.user_id).join(
            Watchlist, Watchlist.watchlist_id == WatchlistItem.watchlist_id
        )
    )
    watchlist_by_user: dict = {}
    for symbol, uid in watchlist_res.all():
        t = (symbol or "").strip().upper()
        if t:
            watchlist_by_user.setdefault(uid, set()).add(t)

    avoid = load_avoid_tickers()
    all_user_ids = set(by_user.keys()) | set(watchlist_by_user.keys())
    books: dict = {}
    for uid in all_user_ids:
        user_positions = by_user.get(uid, [])
        pos_dicts = []
        held = set()
        positions: dict = {}
        for pos in user_positions:
            ticker = (pos.ticker or "").upper()
            held.add(ticker)
            if pos.t2_usd is not None:
                mv = Decimal(str(pos.t2_usd))
            else:
                mv = Decimal(str(pos.purchase_price or 0)) * Decimal(str(pos.quantity or 0))
            pos_dicts.append({"sector": pos.sector or "Unknown", "market_value": mv})
            _merge_lot(positions, ticker, pos)
        total = sum((p["market_value"] for p in pos_dicts), Decimal("0"))
        books[uid] = BookSnapshot(
            total_value=total,
            sector_values=sector_exposure(pos_dicts, total),
            held_tickers=held,
            avoid_tickers=avoid,
            positions=positions,
            watchlist_tickers=watchlist_by_user.get(uid, set()),
        )
    return books, sectors
