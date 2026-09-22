"""insider.py — Browse ingested Form 4 filings and per-person breakdowns.

Read-only API over insider_filings (populated by trading-worker poller).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import (
    BeneficialOwnership,
    EightKFiling,
    Form3Statement,
    Form13FHolding,
    Form144Notice,
    InsiderAiAnalysis,
    InsiderFiling,
)
from ..trading.book_loader import load_books
from ..trading.finra_short_interest import get_latest_short_interest
from ..trading.insider_track_record import compute_track_record
from .auth_routes import get_current_user
from .market import get_ohlcv_series

router = APIRouter(prefix="/insider", tags=["insider"])

_SORTABLE = {
    "transaction_date": InsiderFiling.transaction_date,
    "ticker": InsiderFiling.ticker,
    "owner_name": InsiderFiling.owner_name,
    "transaction_code": InsiderFiling.transaction_code,
    "shares": InsiderFiling.shares,
    "price": InsiderFiling.price,
    "notional": InsiderFiling.notional,
    "stake_pct": InsiderFiling.stake_pct,
    "created_at": InsiderFiling.created_at,
}


class FilingOut(BaseModel):
    filing_id: UUID
    accession: str
    ticker: str
    owner_name: Optional[str] = None
    owner_cik: Optional[str] = None
    officer_title: Optional[str] = None
    is_director: bool = False
    is_officer: bool = False
    transaction_code: str
    shares: Optional[float] = None
    price: Optional[float] = None
    notional: Optional[float] = None
    shares_after: Optional[float] = None
    stake_pct: Optional[float] = None
    transaction_date: Optional[date] = None
    is_10b5_1: Optional[bool] = None
    filing_url: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        orm_mode = True


class FilingListOut(BaseModel):
    total: int
    items: list[FilingOut]


class OwnerTxnOut(BaseModel):
    accession: str
    ticker: str
    transaction_code: str
    shares: Optional[float] = None
    price: Optional[float] = None
    notional: Optional[float] = None
    stake_pct: Optional[float] = None
    transaction_date: Optional[date] = None
    is_10b5_1: Optional[bool] = None
    filing_url: Optional[str] = None


class TrackRecordOut(BaseModel):
    """Forward-return track record over this owner's past open-market P/S
    trades — a historical pattern, not a guarantee of future performance."""

    sample_size: int
    evaluated: int
    win_rate: Optional[float] = None
    avg_aligned_return_pct: Optional[float] = None
    horizon_days: int
    label: str
    basis: str


class Form144NoticeOut(BaseModel):
    """A Notice of Proposed Sale — a leading indicator filed before or on
    the day of an insider's actual sale, stating the intended share count
    and sale date. See FREE_FILINGS_RESEARCH.md."""

    accession: str
    shares: Optional[float] = None
    aggregate_value: Optional[float] = None
    approx_sale_date: Optional[date] = None
    notice_date: Optional[date] = None
    broker: Optional[str] = None
    filing_url: Optional[str] = None


class ShortInterestOut(BaseModel):
    """Most recent FINRA biweekly short-interest reading for a ticker —
    squeeze/crowding context alongside the owner's Form 4 history."""

    settlement_date: date
    current_short_position: Optional[float] = None
    days_to_cover: Optional[float] = None
    change_percent: Optional[float] = None


class OwnerBreakdownOut(BaseModel):
    owner_cik: str
    owner_name: Optional[str] = None
    officer_title: Optional[str] = None
    ticker: Optional[str] = None
    window_days: int
    buy_count: int
    sell_count: int
    buy_shares: float
    sell_shares: float
    buy_notional: float
    sell_notional: float
    net_shares: float
    avg_sell_interval_days: Optional[float] = None
    pct_10b5_1: Optional[float] = None
    transactions: list[OwnerTxnOut] = Field(default_factory=list)
    track_record: Optional[TrackRecordOut] = None
    pending_144: list[Form144NoticeOut] = Field(default_factory=list)
    short_interest: Optional[ShortInterestOut] = None


def _f(v) -> Optional[float]:
    if v is None:
        return None
    return float(v)


async def _compute_track_record(
    rows: list[InsiderFiling],
    ticker: Optional[str],
) -> Optional[TrackRecordOut]:
    """Thin wrapper around the shared insider_track_record.compute_track_record
    (also used by insider_monitor.py's Telegram path, so the API/UI and the
    actual alert show the same number) — converts ORM rows to plain dicts
    and fetches OHLCV bars via the cached market-data endpoint.

    Only meaningful scoped to a single ticker (the UI always passes one when
    opening this panel from a filing row) — returns None otherwise.
    """
    if not ticker:
        return None
    ps_rows = [
        r for r in rows
        if (r.transaction_code or "").upper() in ("P", "S") and r.transaction_date
    ]
    if len(ps_rows) < 2:
        return None

    today = date.today()
    earliest = min(r.transaction_date for r in ps_rows)
    years_needed = max(1, min(6, (today - earliest).days // 365 + 2))
    try:
        series = await get_ohlcv_series(ticker, years_needed)
    except Exception:
        return None
    bars = [(b.date, b.close) for b in series.bars]

    row_dicts = [
        {"transaction_code": r.transaction_code, "transaction_date": r.transaction_date, "shares": r.shares}
        for r in ps_rows
    ]
    tr = compute_track_record(row_dicts, bars, today=today)
    if tr is None:
        return None
    return TrackRecordOut(
        sample_size=tr.sample_size,
        evaluated=tr.evaluated,
        win_rate=tr.win_rate,
        avg_aligned_return_pct=tr.avg_aligned_return_pct,
        horizon_days=tr.horizon_days,
        label=tr.label,
        basis=tr.basis,
    )


@router.get("/filings", response_model=FilingListOut)
async def list_filings(
    ticker: Optional[str] = Query(None),
    owner_cik: Optional[str] = Query(None),
    code: Optional[str] = Query(None, description="P or S"),
    days: int = Query(90, ge=1, le=730),
    sort: str = Query("transaction_date"),
    order: str = Query("desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Paginated Form 4 rows with sortable columns."""
    col = _SORTABLE.get(sort, InsiderFiling.transaction_date)
    direction = desc if order.lower() != "asc" else asc
    since = datetime.now(timezone.utc).date() - timedelta(days=days)

    filters = [InsiderFiling.transaction_date >= since]
    if ticker:
        filters.append(InsiderFiling.ticker == ticker.upper())
    if owner_cik:
        filters.append(InsiderFiling.owner_cik == owner_cik[:10])
    if code:
        filters.append(InsiderFiling.transaction_code == code.upper()[:4])

    total = (
        await db.execute(select(func.count()).select_from(InsiderFiling).where(*filters))
    ).scalar() or 0

    res = await db.execute(
        select(InsiderFiling)
        .where(*filters)
        .order_by(direction(col).nullslast(), desc(InsiderFiling.created_at))
        .offset(offset)
        .limit(limit)
    )
    rows = res.scalars().all()
    items = []
    for r in rows:
        items.append(
            FilingOut(
                filing_id=r.filing_id,
                accession=r.accession,
                ticker=r.ticker,
                owner_name=r.owner_name,
                owner_cik=r.owner_cik,
                officer_title=r.officer_title,
                is_director=bool(r.is_director),
                is_officer=bool(r.is_officer),
                transaction_code=r.transaction_code,
                shares=_f(r.shares),
                price=_f(r.price),
                notional=_f(r.notional),
                shares_after=_f(r.shares_after),
                stake_pct=_f(r.stake_pct),
                transaction_date=r.transaction_date,
                is_10b5_1=r.is_10b5_1,
                filing_url=r.filing_url,
                created_at=r.created_at,
            )
        )
    return FilingListOut(total=int(total), items=items)


@router.get("/owners/{owner_cik}", response_model=OwnerBreakdownOut)
async def owner_breakdown(
    owner_cik: str,
    ticker: Optional[str] = Query(None),
    days: int = Query(365, ge=30, le=730),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Per-person Form 4 breakdown (buys vs sells, cadence, 10b5-1 share)."""
    cik = owner_cik.strip()[:10]
    if not cik:
        raise HTTPException(status_code=400, detail="owner_cik required")
    since = datetime.now(timezone.utc).date() - timedelta(days=days)
    filters = [
        InsiderFiling.owner_cik == cik,
        InsiderFiling.transaction_date >= since,
    ]
    if ticker:
        filters.append(InsiderFiling.ticker == ticker.upper())

    res = await db.execute(
        select(InsiderFiling)
        .where(*filters)
        .order_by(asc(InsiderFiling.transaction_date), asc(InsiderFiling.created_at))
    )
    rows = list(res.scalars().all())
    if not rows:
        raise HTTPException(status_code=404, detail="No filings for this owner in window")

    buy_count = sell_count = 0
    buy_shares = sell_shares = buy_notional = sell_notional = 0.0
    ten_b5 = 0
    sell_dates = []
    txns = []
    for r in rows:
        code = (r.transaction_code or "").upper()
        # Most Form 4 activity for a typical executive is grants (A), tax
        # withholding on vests (F), and option exercises (M) rather than
        # open-market P/S trades — counting only "P"/"S" left this panel
        # showing all-zero figures for owners with real filings but no
        # open-market trades in the window. acquired_disposed is the SEC
        # form's own A/D flag and covers every transaction type, so it
        # reflects the owner's real net share change; transaction_code is
        # kept as a fallback for any older/malformed row missing it.
        ad = (r.acquired_disposed or "").upper()
        if not ad:
            ad = "A" if code == "P" else "D" if code == "S" else ""
        sh = float(r.shares or 0)
        nt = float(r.notional or 0)
        if ad == "A":
            buy_count += 1
            buy_shares += sh
            buy_notional += nt
        elif ad == "D":
            sell_count += 1
            sell_shares += sh
            sell_notional += nt
        # Sell cadence is specifically about open-market selling intent
        # (e.g. a 10b5-1 plan's fixed schedule) — tax-withholding disposals
        # on every vest would swamp this with noise unrelated to trading.
        if code == "S" and r.transaction_date:
            sell_dates.append(r.transaction_date)
        if r.is_10b5_1 is True:
            ten_b5 += 1
        txns.append(
            OwnerTxnOut(
                accession=r.accession,
                ticker=r.ticker,
                transaction_code=r.transaction_code,
                shares=_f(r.shares),
                price=_f(r.price),
                notional=_f(r.notional),
                stake_pct=_f(r.stake_pct),
                transaction_date=r.transaction_date,
                is_10b5_1=r.is_10b5_1,
                filing_url=r.filing_url,
            )
        )

    avg_iv = None
    if len(sell_dates) >= 2:
        sell_dates = sorted(set(sell_dates))
        gaps = [(sell_dates[i] - sell_dates[i - 1]).days for i in range(1, len(sell_dates))]
        if gaps:
            avg_iv = sum(gaps) / len(gaps)

    track_record = await _compute_track_record(rows, ticker)

    notice_filters = [Form144Notice.owner_cik == cik]
    if ticker:
        notice_filters.append(Form144Notice.ticker == ticker.upper())
    notice_res = await db.execute(
        select(Form144Notice)
        .where(*notice_filters)
        .order_by(desc(Form144Notice.notice_date))
        .limit(10)
    )
    pending_144 = [
        Form144NoticeOut(
            accession=n.accession,
            shares=_f(n.shares),
            aggregate_value=_f(n.aggregate_value),
            approx_sale_date=n.approx_sale_date,
            notice_date=n.notice_date,
            broker=n.broker,
            filing_url=n.filing_url,
        )
        for n in notice_res.scalars().all()
    ]

    short_interest = None
    if ticker:
        si = await get_latest_short_interest(db, ticker)
        if si is not None:
            short_interest = ShortInterestOut(
                settlement_date=si.settlement_date,
                current_short_position=_f(si.current_short_position),
                days_to_cover=_f(si.days_to_cover),
                change_percent=_f(si.change_percent),
            )

    first = rows[0]
    return OwnerBreakdownOut(
        owner_cik=cik,
        owner_name=first.owner_name,
        officer_title=first.officer_title,
        ticker=ticker.upper() if ticker else None,
        window_days=days,
        buy_count=buy_count,
        sell_count=sell_count,
        buy_shares=buy_shares,
        sell_shares=sell_shares,
        buy_notional=buy_notional,
        sell_notional=sell_notional,
        net_shares=buy_shares - sell_shares,
        avg_sell_interval_days=avg_iv,
        pct_10b5_1=(ten_b5 / len(rows)) if rows else None,
        transactions=txns,
        track_record=track_record,
        pending_144=pending_144,
        short_interest=short_interest,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Unified "All Filings" browser — Form 144, Form 3, Schedule 13D/13G,
# Form 8-K, Form 13F normalized into one sortable/filterable list.
# ═══════════════════════════════════════════════════════════════════════════

_ALL_SOURCES = ("form144", "form3", "13d", "13g", "8k", "13f")


class AllFilingOut(BaseModel):
    """One row from any of the five non-Form-4 filing types, normalized to
    a common shape so the frontend can sort/filter across all of them at
    once. `detail`/`person`/`amount`/`value_usd` are populated per-source —
    see FREE_FILINGS_RESEARCH.md for what each type actually reports."""

    source: str
    ticker: Optional[str] = None
    filing_date: Optional[date] = None
    headline: str
    detail: Optional[str] = None
    person: Optional[str] = None
    # CIK of the person/filer behind this row (owner_cik for 144/3, filer_cik
    # for 13D/13G/13F) — lets the frontend link into the same owner-breakdown
    # panel Form 4 rows already use. None for 8-K, which has no person concept.
    owner_cik: Optional[str] = None
    amount: Optional[float] = None
    value_usd: Optional[float] = None
    is_amendment: bool = False
    filing_url: Optional[str] = None


class AllFilingsListOut(BaseModel):
    total: int
    items: list[AllFilingOut]


async def _fetch_form144_rows(db: AsyncSession, ticker: Optional[str], since: date) -> list[AllFilingOut]:
    filters = [Form144Notice.notice_date >= since]
    if ticker:
        filters.append(Form144Notice.ticker == ticker.upper())
    res = await db.execute(select(Form144Notice).where(*filters))
    out = []
    for n in res.scalars().all():
        out.append(
            AllFilingOut(
                source="form144",
                ticker=n.ticker,
                filing_date=n.notice_date,
                headline=f"Form 144: {n.owner_name or 'Insider'} proposed sale",
                detail=(
                    f"{n.relationships or ''} — proposed sale date {n.approx_sale_date.isoformat()}"
                    if n.approx_sale_date
                    else n.relationships
                ),
                person=n.owner_name,
                owner_cik=n.owner_cik,
                amount=_f(n.shares),
                value_usd=_f(n.aggregate_value),
                filing_url=n.filing_url,
            )
        )
    return out


async def _fetch_form3_rows(db: AsyncSession, ticker: Optional[str], since: date) -> list[AllFilingOut]:
    filters = [Form3Statement.period_of_report >= since]
    if ticker:
        filters.append(Form3Statement.ticker == ticker.upper())
    res = await db.execute(select(Form3Statement).where(*filters))
    out = []
    for s in res.scalars().all():
        role = s.officer_title or ("Director" if s.is_director else "Officer" if s.is_officer else "Insider")
        out.append(
            AllFilingOut(
                source="form3",
                ticker=s.ticker,
                filing_date=s.period_of_report,
                headline=f"Form 3: {s.owner_name or 'Insider'} initial statement",
                detail=f"{role} — starting position",
                person=s.owner_name,
                owner_cik=s.owner_cik,
                amount=_f(s.shares_owned),
                filing_url=s.filing_url,
            )
        )
    return out


async def _fetch_schedule13_rows(
    db: AsyncSession, ticker: Optional[str], since: date, want_13d: bool, want_13g: bool
) -> list[AllFilingOut]:
    if not want_13d and not want_13g:
        return []
    filters = [BeneficialOwnership.event_date >= since]
    if ticker:
        filters.append(BeneficialOwnership.ticker == ticker.upper())
    if want_13d and not want_13g:
        filters.append(BeneficialOwnership.is_13d.is_(True))
    elif want_13g and not want_13d:
        filters.append(BeneficialOwnership.is_13d.is_(False))
    res = await db.execute(select(BeneficialOwnership).where(*filters))
    out = []
    for o in res.scalars().all():
        kind = "13D (activist)" if o.is_13d else "13G (passive)"
        headline = f"Schedule {kind}: {o.filer_name or 'Filer'}"
        if o.pct_owned:
            headline += f" — {float(o.pct_owned):.1f}%"
        out.append(
            AllFilingOut(
                source="13d" if o.is_13d else "13g",
                ticker=o.ticker,
                filing_date=o.event_date,
                headline=headline,
                detail=(o.purpose_text or "")[:280] or None,
                person=o.filer_name,
                owner_cik=o.filer_cik,
                amount=_f(o.shares_owned),
                is_amendment=bool(o.is_amendment),
                filing_url=o.filing_url,
            )
        )
    return out


async def _fetch_8k_rows(db: AsyncSession, ticker: Optional[str], since: date) -> list[AllFilingOut]:
    since_dt = datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)
    filters = [EightKFiling.filed_at >= since_dt]
    if ticker:
        filters.append(EightKFiling.ticker == ticker.upper())
    res = await db.execute(select(EightKFiling).where(*filters))
    out = []
    for f in res.scalars().all():
        items = f.items or []
        descriptions = [i.get("description", "") for i in items if i.get("description")]
        out.append(
            AllFilingOut(
                source="8k",
                ticker=f.ticker,
                filing_date=f.filed_at.date() if f.filed_at else None,
                headline=f"8-K: {descriptions[0] if descriptions else 'Material event'}",
                detail="; ".join(descriptions[1:3]) or None,
                is_amendment=bool(f.is_amendment),
                filing_url=f.filing_url,
            )
        )
    return out


async def _fetch_13f_rows(db: AsyncSession, ticker: Optional[str], since: date) -> list[AllFilingOut]:
    since_dt = datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)
    filters = [Form13FHolding.filed_at >= since_dt]
    if ticker:
        filters.append(Form13FHolding.ticker == ticker.upper())
    res = await db.execute(select(Form13FHolding).where(*filters))
    out = []
    for h in res.scalars().all():
        out.append(
            AllFilingOut(
                source="13f",
                ticker=h.ticker,
                filing_date=h.filed_at.date() if h.filed_at else None,
                headline=f"13F: {h.filer_name or 'Fund'} holds this name",
                detail="positioning data, up to 45 days stale",
                person=h.filer_name,
                amount=_f(h.shares),
                value_usd=_f(h.value_usd),
                is_amendment=bool(h.is_amendment),
                filing_url=h.filing_url,
            )
        )
    return out


@router.get("/filings-all", response_model=AllFilingsListOut)
async def list_all_filings(
    ticker: Optional[str] = Query(None),
    source: str = Query("all", description="all|form144|form3|13d|13g|8k|13f"),
    days: int = Query(90, ge=1, le=730),
    sort: str = Query("date", description="date|ticker|source"),
    order: str = Query("desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Form 144 + Form 3 + Schedule 13D/13G + 8-K + 13F, normalized into one
    sortable/filterable list — every non-Form-4 filing type ingested by the
    trading-worker pollers documented in FREE_FILINGS_RESEARCH.md, in one
    place instead of six separate endpoints.
    """
    wanted = {s.strip().lower() for s in source.split(",")} if source != "all" else set(_ALL_SOURCES)
    unknown = wanted - set(_ALL_SOURCES)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown source(s): {', '.join(sorted(unknown))}")

    since = datetime.now(timezone.utc).date() - timedelta(days=days)
    items: list[AllFilingOut] = []
    if "form144" in wanted:
        items += await _fetch_form144_rows(db, ticker, since)
    if "form3" in wanted:
        items += await _fetch_form3_rows(db, ticker, since)
    if "13d" in wanted or "13g" in wanted:
        items += await _fetch_schedule13_rows(db, ticker, since, "13d" in wanted, "13g" in wanted)
    if "8k" in wanted:
        items += await _fetch_8k_rows(db, ticker, since)
    if "13f" in wanted:
        items += await _fetch_13f_rows(db, ticker, since)

    sort_key = {
        "date": lambda x: x.filing_date or date.min,
        "ticker": lambda x: x.ticker or "",
        "source": lambda x: x.source,
    }.get(sort, lambda x: x.filing_date or date.min)
    items.sort(key=sort_key, reverse=(order.lower() != "asc"))

    total = len(items)
    page = items[offset : offset + limit]
    return AllFilingsListOut(total=total, items=page)


# ── Analyze with AI (Kamilo) ─────────────────────────────────────────────────
# "Analyze with AI" on the Insider page: takes whatever filings the current
# view/filters would show, keeps only the ones for tickers actually held or
# watchlisted (no point asking for portfolio advice on names not in the
# portfolio), and sends that plus position context to Kamilo — a separate
# personal-assistant service (winfer70/kamilo), not this app's own Ollama
# trade-analysis stack — for a critical verdict. Kamilo's /analyze/insider
# also feeds the verdict into its own memory (the "self-learning loop" the
# request asked for); this table is tickerTap's own copy so past analyses
# are browsable in-app and can later be graded against outcomes.

KAMILO_URL = os.getenv("KAMILO_URL", "http://192.168.0.125:8100")
KAMILO_ANALYSIS_TOKEN = os.getenv("KAMILO_ANALYSIS_TOKEN", "")


class AnalyzeIn(BaseModel):
    source_tab: str = Field(..., description="form4 | all — which tab triggered this")
    ticker: Optional[str] = None
    days: int = Field(90, ge=1, le=730)
    code: Optional[str] = Field(None, description="Form 4 code filter (P/S), form4 tab only")
    source: Optional[str] = Field(None, description="all|form144|form3|... , all-filings tab only")


class AnalyzeOut(BaseModel):
    analysis_id: UUID
    rating: Optional[str] = None
    confidence: Optional[int] = None
    verdict: Optional[str] = None
    tickers_analyzed: list[str]
    filing_count: int
    created_at: datetime


def _fmt_form4_summary(f: "FilingOut") -> str:
    title = f.officer_title or ("Director" if f.is_director else "insider")
    parts = [f"{f.transaction_date} {f.transaction_code} — {f.owner_name or 'unknown'} ({title})"]
    if f.shares is not None:
        parts.append(f"{f.shares:g} sh")
    if f.price is not None:
        parts.append(f"@ ${f.price:.2f}")
    if f.notional is not None:
        parts.append(f"(${f.notional:,.0f})")
    if f.stake_pct is not None:
        parts.append(f"— {f.stake_pct * 100:.2f}% of stake")
    return " ".join(parts)


def _build_analysis_prompt(ticker_groups: dict, portfolio: dict) -> str:
    lines = [
        "Analyze this SEC insider/institutional filing activity against my "
        "actual portfolio. Give ONE overall rating focused on whichever "
        "ticker below is most material; mention the others in the verdict "
        "if relevant.",
        "",
    ]
    for ticker, rows in ticker_groups.items():
        pos = portfolio.get(ticker)
        if pos:
            lines.append(
                f"TICKER: {ticker} — HELD: {pos['quantity']:g} sh @ ${pos['purchase_price']:.2f} cost basis"
                + (f", soft stop ${pos['soft_stop']:.2f}" if pos.get("soft_stop") else "")
            )
        else:
            lines.append(f"TICKER: {ticker} — watchlisted only, no position")
        for r in rows[:15]:
            lines.append(f"  - {r}")
        lines.append("")
    return "\n".join(lines)


@router.post("/analyze", response_model=AnalyzeOut)
async def analyze_with_ai(
    body: AnalyzeIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Filter the current view down to portfolio-relevant activity, send it
    to Kamilo for a critical verdict, and store the result."""
    if not KAMILO_ANALYSIS_TOKEN:
        raise HTTPException(status_code=503, detail="AI analysis is not configured (KAMILO_ANALYSIS_TOKEN unset)")

    books, _sectors = await load_books(db)
    book = books.get(current_user.user_id) or books.get(str(current_user.user_id))
    tracked = (set(book.held_tickers) | set(book.watchlist_tickers)) if book else set()
    positions = book.positions if book else {}

    # Reuse the exact same query logic the list endpoints use — every
    # Query(...)-defaulted param must be passed explicitly since calling a
    # route function directly bypasses FastAPI's own default resolution.
    if body.source_tab == "form4":
        result = await list_filings(
            ticker=body.ticker, owner_cik=None, code=body.code, days=body.days,
            sort="transaction_date", order="desc", limit=200, offset=0, db=db,
        )
        raw_rows = [
            {"ticker": (f.ticker or "").upper(), "summary": _fmt_form4_summary(f)}
            for f in result.items
        ]
    else:
        result = await list_all_filings(
            ticker=body.ticker, source=body.source or "all", days=body.days,
            sort="date", order="desc", limit=200, offset=0, db=db,
        )
        raw_rows = [
            {
                "ticker": (f.ticker or "").upper(),
                "summary": (
                    f"{f.filing_date} [{f.source.upper()}{'/A' if f.is_amendment else ''}]"
                    f" {f.headline}" + (f" — {f.detail}" if f.detail else "")
                ),
            }
            for f in result.items
            if f.ticker
        ]

    ticker_groups: dict = {}
    for r in raw_rows:
        if r["ticker"] not in tracked:
            continue
        ticker_groups.setdefault(r["ticker"], []).append(r["summary"])

    filters_json = {"days": body.days, "code": body.code, "source": body.source, "ticker": body.ticker}

    if not ticker_groups:
        analysis = InsiderAiAnalysis(
            analysis_id=uuid4(),
            user_id=current_user.user_id,
            source_tab=body.source_tab,
            ticker=body.ticker,
            filters_json=filters_json,
            filings_considered={"count": 0},
            portfolio_snapshot=None,
            rating="NOISE",
            confidence=100,
            verdict="None of the filings currently shown match a ticker you hold or watch — nothing here is relevant to your portfolio.",
        )
        db.add(analysis)
        await db.commit()
        # created_at is server-generated (func.now()) and wasn't populated
        # onto the in-memory object by the flush — a bare attribute access
        # here would trigger an implicit lazy-load outside the AsyncSession's
        # greenlet context and raise MissingGreenlet. db.refresh() reloads it
        # the async-safe way.
        await db.refresh(analysis)
        return AnalyzeOut(
            analysis_id=analysis.analysis_id, rating=analysis.rating, confidence=analysis.confidence,
            verdict=analysis.verdict, tickers_analyzed=[], filing_count=0, created_at=analysis.created_at,
        )

    portfolio_snapshot = {t: positions[t] for t in ticker_groups if t in positions}
    prompt = _build_analysis_prompt(ticker_groups, positions)

    analysis_id = uuid4()
    try:
        async with httpx.AsyncClient(timeout=100) as client:
            resp = await client.post(
                f"{KAMILO_URL}/analyze/insider",
                json={"ticker": body.ticker or next(iter(ticker_groups)), "context": prompt},
                headers={"X-Kamilo-Token": KAMILO_ANALYSIS_TOKEN},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Kamilo returned HTTP {resp.status_code}: {resp.text[:200]}")
        kamilo_out = resp.json()
        analysis = InsiderAiAnalysis(
            analysis_id=analysis_id,
            user_id=current_user.user_id,
            source_tab=body.source_tab,
            ticker=body.ticker,
            filters_json=filters_json,
            filings_considered={t: rows[:15] for t, rows in ticker_groups.items()},
            portfolio_snapshot=portfolio_snapshot or None,
            rating=kamilo_out.get("rating"),
            confidence=kamilo_out.get("confidence"),
            verdict=kamilo_out.get("verdict"),
            raw_response=kamilo_out.get("raw"),
        )
    except Exception as exc:
        analysis = InsiderAiAnalysis(
            analysis_id=analysis_id,
            user_id=current_user.user_id,
            source_tab=body.source_tab,
            ticker=body.ticker,
            filters_json=filters_json,
            filings_considered={t: rows[:15] for t, rows in ticker_groups.items()},
            portfolio_snapshot=portfolio_snapshot or None,
            error_message=str(exc)[:500],
        )
        db.add(analysis)
        await db.commit()
        raise HTTPException(status_code=502, detail=f"Kamilo analysis failed: {exc}") from exc

    db.add(analysis)
    await db.commit()
    await db.refresh(analysis)
    return AnalyzeOut(
        analysis_id=analysis.analysis_id, rating=analysis.rating, confidence=analysis.confidence,
        verdict=analysis.verdict, tickers_analyzed=sorted(ticker_groups), filing_count=len(raw_rows),
        created_at=analysis.created_at,
    )
