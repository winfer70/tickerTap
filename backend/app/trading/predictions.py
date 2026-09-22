"""predictions.py — daily calls on held positions that learn from their own misses.

Loop:
  1. Pre-market: the LLM gets each held ticker's recent price action, news,
     insider activity, phase/stops, upcoming earnings, overnight futures —
     plus its own graded track record and the lessons distilled from past
     reflections — and calls today's session UP / DOWN / FLAT with a
     confidence, rationale and a rules-consistent action.
  2. Post-market: each call is graded against the actual regular-session
     close (FLAT = within ±FLAT_BAND_PCT).
  3. The LLM reflects on why each call was right or wrong (separating
     market-wide moves from stock-specific ones) and distils at most one
     reusable lesson per ticker plus a couple of general ones. Lessons are
     stored in prediction_lessons and fed back into step 1 the next morning.

Ungraded predictions (data not in yet at 16:30, a crashed run) are caught up
on the next pre-market cycle; a date with no session (market holiday) is
marked NO_SESSION instead of staying ungraded forever.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
import structlog
import yfinance as yf
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import DailyPrediction, InsiderFiling, PositionReviewReminder, PredictionLesson
from .briefing_advice import load_investment_rules, vol_class
from .insider_gate import BookSnapshot
from .market_context import fetch_ticker_news

logger = structlog.get_logger("predictions")

_OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
PREDICTION_MODEL = os.getenv("PREDICTION_MODEL", "qwen3:14b")
FLAT_BAND_PCT = 0.5
HIGH_CONFIDENCE = 70
_TRACK_RECORD_DAYS = 30
_MAX_TICKERS = 20
_MAX_GENERAL_LESSONS_IN_PROMPT = 10
_MAX_TICKER_LESSONS_IN_PROMPT = 3
_MAX_GENERAL_LESSONS_PER_DAY = 2
_NY_TZ = ZoneInfo("America/New_York")
DIRECTIONS = ("UP", "DOWN", "FLAT")
ARROWS = {"UP": "↑", "DOWN": "↓", "FLAT": "→"}


# ── pure helpers ────────────────────────────────────────────────────────────

def classify_move(pct: float, band: float = FLAT_BAND_PCT) -> str:
    if pct > band:
        return "UP"
    if pct < -band:
        return "DOWN"
    return "FLAT"


def grade(direction: str, actual_pct: float) -> str:
    return "CORRECT" if classify_move(actual_pct) == direction else "WRONG"


def _clean_text(value, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).strip()
    if not text or text.lower() in ("null", "none", "n/a", "-"):
        return None
    return text[:limit]


def parse_predictions(raw: dict, allowed: set[str]) -> list[dict]:
    """Validate the LLM's prediction JSON: known tickers only, one each,
    direction in UP/DOWN/FLAT, confidence clamped to 0-100."""
    out, seen = [], set()
    items = raw.get("predictions") if isinstance(raw, dict) else None
    for item in items or []:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").upper().strip()
        direction = str(item.get("direction") or "").upper().strip()
        if ticker not in allowed or ticker in seen or direction not in DIRECTIONS:
            continue
        try:
            confidence = int(round(float(item.get("confidence"))))
        except (TypeError, ValueError):
            continue
        try:
            move = float(item.get("expected_move_pct"))
            move = max(-25.0, min(25.0, move))
        except (TypeError, ValueError):
            move = None
        seen.add(ticker)
        out.append(
            {
                "ticker": ticker,
                "direction": direction,
                "confidence": max(0, min(100, confidence)),
                "expected_move_pct": move,
                "rationale": _clean_text(item.get("rationale"), 500),
                "action": _clean_text(item.get("action"), 120),
            }
        )
    return out


def _norm_lesson(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def parse_reflection(raw: dict, tickers: set[str]) -> tuple[dict, list[str], Optional[str]]:
    """-> ({ticker: {"why", "lesson"}}, general_lessons, summary)."""
    reviews: dict = {}
    if not isinstance(raw, dict):
        return reviews, [], None
    for item in raw.get("reviews") or []:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").upper().strip()
        if ticker not in tickers or ticker in reviews:
            continue
        lesson = _clean_text(item.get("lesson"), 300)
        reviews[ticker] = {
            "why": _clean_text(item.get("why"), 600),
            "lesson": lesson if lesson and len(lesson) >= 15 else None,
        }
    general = []
    for g in raw.get("general_lessons") or []:
        text = _clean_text(g, 300)
        if text and len(text) >= 15:
            general.append(text)
    return reviews, general[:_MAX_GENERAL_LESSONS_PER_DAY], _clean_text(raw.get("summary"), 600)


def summarize_track_record(rows: list) -> dict:
    """rows: graded predictions (outcome CORRECT/WRONG) with ticker, direction,
    confidence, outcome attributes."""
    stats = {"total": 0, "correct": 0, "high_total": 0, "high_correct": 0}
    per_ticker: dict = defaultdict(lambda: [0, 0])
    per_direction: dict = defaultdict(lambda: [0, 0])
    for r in rows:
        if r.outcome not in ("CORRECT", "WRONG"):
            continue
        ok = r.outcome == "CORRECT"
        stats["total"] += 1
        stats["correct"] += ok
        if (r.confidence or 0) >= HIGH_CONFIDENCE:
            stats["high_total"] += 1
            stats["high_correct"] += ok
        per_ticker[r.ticker][0] += ok
        per_ticker[r.ticker][1] += 1
        per_direction[r.direction][0] += ok
        per_direction[r.direction][1] += 1
    stats["per_ticker"] = {k: tuple(v) for k, v in per_ticker.items()}
    stats["per_direction"] = {k: tuple(v) for k, v in per_direction.items()}
    return stats


def _rate(correct: int, total: int) -> str:
    return f"{correct}/{total} ({correct / total:.0%})" if total else "no graded calls yet"


def _fmt_pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:+.1f}%"


def build_prediction_prompt(
    trade_date: date,
    blocks: list[str],
    market_lines: list[str],
    stats: dict,
    general_lessons: list[str],
    ticker_lessons: dict,
    rules_text: list[str],
) -> str:
    track = [f"- Overall: {_rate(stats['correct'], stats['total'])}"]
    if stats["high_total"]:
        track.append(f"- High-confidence (>={HIGH_CONFIDENCE}) calls: {_rate(stats['high_correct'], stats['high_total'])}")
    for d, (c, t) in sorted(stats["per_direction"].items()):
        track.append(f"- {d} calls: {_rate(c, t)}")
    for tk, (c, t) in sorted(stats["per_ticker"].items()):
        track.append(f"- {tk}: {_rate(c, t)}")

    lessons = [f"- [general] {l}" for l in general_lessons]
    for tk, ls in sorted(ticker_lessons.items()):
        lessons.extend(f"- [{tk}] {l}" for l in ls)

    rules = "\n".join(f"- {r}" for r in rules_text) or "- (none loaded)"
    return f"""You are a disciplined equity analyst making next-session calls for a personal portfolio.
Session date (US/Eastern): {trade_date.isoformat()}. The market opens at 09:30 ET.

MARKET CONTEXT
{chr(10).join(market_lines) or "- unavailable"}

YOUR GRADED TRACK RECORD (last {_TRACK_RECORD_DAYS} days)
{chr(10).join(track)}

LESSONS FROM YOUR PAST REFLECTIONS — apply them
{chr(10).join(lessons) or "- none yet"}

THE USER'S TRADING RULES (actions must respect them)
{rules}

POSITIONS
{chr(10).join(blocks)}

TASK
For every ticker above, call today's regular-session close vs the previous close:
UP (> +{FLAT_BAND_PCT}%), DOWN (< -{FLAT_BAND_PCT}%) or FLAT (within ±{FLAT_BAND_PCT}%).
- confidence 0-100, calibrated against your track record above (if your high-confidence calls miss, lower them).
- expected_move_pct: your point estimate for the % change.
- rationale: one sentence citing the specific data above that drives the call.
- action: a short, rules-consistent instruction for the user (e.g. "hold, stop unchanged", "trim at T1", "review for Phase 2 exit"). Never suggest averaging down or moving a stop against the position.
Return ONLY JSON:
{{"market_view": "one sentence", "predictions": [{{"ticker": "XYZ", "direction": "UP|DOWN|FLAT", "confidence": 0, "expected_move_pct": 0.0, "rationale": "...", "action": "..."}}]}}"""


def build_reflection_prompt(
    trade_date: date,
    market_line: str,
    call_blocks: list[str],
    existing_lessons: list[str],
) -> str:
    existing = "\n".join(f"- {l}" for l in existing_lessons) or "- none"
    return f"""This morning you made the calls below for the {trade_date.isoformat()} session. Here is what actually happened.

MARKET TODAY: {market_line}

CALLS AND OUTCOMES
{chr(10).join(call_blocks)}

EXISTING LESSONS (do not repeat these)
{existing}

TASK
For each ticker explain in 1-2 sentences WHY the call was right or wrong. Separate market-wide moves
(compare the stock to the market move) from stock-specific causes (news, insider activity, earnings,
technical levels). If the call was right for the wrong reason, say so.
Then extract lessons that would have improved the call: concrete, testable heuristics that change how
you pick a direction or set confidence tomorrow (e.g. "when futures are down >1% pre-market, cap UP
confidence on high-beta names at 55"). Not descriptions of what happened, not trade ideas. At most one
lesson per ticker and at most {_MAX_GENERAL_LESSONS_PER_DAY} general lessons; a general lesson must not
restate a ticker lesson. Use null when there is no real lesson — a lucky or unlucky day is not a lesson.
Return ONLY JSON:
{{"summary": "one or two sentences on the day", "reviews": [{{"ticker": "XYZ", "why": "...", "lesson": "... or null"}}], "general_lessons": ["..."]}}"""


# ── LLM + market data I/O ───────────────────────────────────────────────────

async def _ollama_json(prompt: str, num_predict: int = 2048) -> Optional[dict]:
    payload = {
        "model": PREDICTION_MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"temperature": 0.3, "num_predict": num_predict},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(240.0, connect=10.0)) as client:
            resp = await client.post(f"{_OLLAMA_URL}/api/generate", json=payload)
            resp.raise_for_status()
            text = resp.json().get("response", "")
    except Exception as exc:
        logger.warning("prediction_llm_failed", error=str(exc))
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except ValueError:
        logger.warning("prediction_llm_bad_json", head=text[:200])
        return None


def _bar_date(ts) -> date:
    if getattr(ts, "tzinfo", None) is not None:
        return ts.astimezone(_NY_TZ).date()
    return ts.date()


def _daily_closes(sym: str, period: str) -> list[tuple[date, float]]:
    hist = yf.Ticker(sym).history(period=period, interval="1d")
    out = []
    if hist is None or hist.empty:
        return out
    for ts, close in zip(hist.index, hist["Close"].tolist()):
        try:
            c = float(close)
        except (TypeError, ValueError):
            continue
        if c == c and c > 0:
            out.append((_bar_date(ts), c))
    return out


def _pct(a: float, b: float) -> Optional[float]:
    return (a / b - 1) * 100 if b else None


def ticker_features(sym: str, trade_date: date) -> Optional[dict]:
    """Price action up to the previous session (today's partial bar dropped)."""
    bars = [b for b in _daily_closes(sym, "3mo") if b[0] < trade_date]
    if len(bars) < 2:
        return None
    c = [x[1] for x in bars]
    moves = [abs(_pct(c[i], c[i - 1])) for i in range(max(1, len(c) - 20), len(c))]
    window = c[-20:]
    return {
        "last_date": bars[-1][0],
        "close": c[-1],
        "chg_1d": _pct(c[-1], c[-2]),
        "chg_5d": _pct(c[-1], c[-6]) if len(c) > 5 else None,
        "chg_20d": _pct(c[-1], c[-21]) if len(c) > 20 else None,
        "vs_sma20": _pct(c[-1], sum(window) / len(window)),
        "avg_abs_move_20d": sum(moves) / len(moves) if moves else None,
    }


def session_change(sym: str, day: date) -> tuple[str, Optional[float], Optional[float]]:
    """("ok", prev_close, close) | ("no_session", None, None) | ("pending", None, None)."""
    bars = _daily_closes(sym, "1mo")
    for i, (d, close) in enumerate(bars):
        if d == day:
            return ("ok", bars[i - 1][1], close) if i > 0 else ("pending", None, None)
    if any(d > day for d, _ in bars):
        return "no_session", None, None
    return "pending", None, None


def overnight_context(trade_date: date) -> list[str]:
    lines = []
    for sym, name in (("ES=F", "S&P 500 futures"), ("NQ=F", "Nasdaq futures")):
        try:
            fi = yf.Ticker(sym).fast_info
            last, prev = float(fi.last_price), float(fi.regular_market_previous_close)
            if last > 0 and prev > 0:
                lines.append(f"- {name} ({sym}) vs prior settle: {_fmt_pct(_pct(last, prev))}")
        except Exception:
            continue
    for sym, name in (("SPY", "SPY"), ("QQQ", "QQQ"), ("^VIX", "VIX")):
        try:
            bars = [b for b in _daily_closes(sym, "1mo") if b[0] < trade_date]
            if len(bars) >= 2:
                chg = _pct(bars[-1][1], bars[-2][1])
                level = f" level {bars[-1][1]:.1f}," if sym == "^VIX" else ""
                lines.append(f"- {name} previous session:{level} {_fmt_pct(chg)}")
        except Exception:
            continue
    return lines


# ── DB helpers ─────────────────────────────────────────────────────────────

async def track_record(session: AsyncSession, user_id, today: date) -> dict:
    res = await session.execute(
        select(DailyPrediction).where(
            DailyPrediction.user_id == user_id,
            DailyPrediction.trade_date >= today - timedelta(days=_TRACK_RECORD_DAYS),
            DailyPrediction.outcome.in_(("CORRECT", "WRONG")),
        )
    )
    return summarize_track_record(res.scalars().all())


async def active_lessons(session: AsyncSession, user_id, tickers: set[str]) -> tuple[list[str], dict]:
    res = await session.execute(
        select(PredictionLesson)
        .where(PredictionLesson.user_id == user_id, PredictionLesson.active.is_(True))
        .order_by(PredictionLesson.created_at.desc())
    )
    general, per_ticker = [], defaultdict(list)
    for l in res.scalars().all():
        if l.ticker is None:
            if len(general) < _MAX_GENERAL_LESSONS_IN_PROMPT:
                general.append(l.lesson)
        elif l.ticker in tickers and len(per_ticker[l.ticker]) < _MAX_TICKER_LESSONS_IN_PROMPT:
            per_ticker[l.ticker].append(l.lesson)
    return general, dict(per_ticker)


async def _insider_summary(session: AsyncSession, tickers: list[str], since: date, until: Optional[date] = None) -> dict:
    q = select(InsiderFiling).where(
        InsiderFiling.ticker.in_(tickers),
        InsiderFiling.transaction_date >= since,
        InsiderFiling.transaction_code.in_(("P", "S")),
    )
    if until is not None:
        q = q.where(InsiderFiling.transaction_date <= until)
    agg: dict = defaultdict(lambda: {"P": [0, 0.0], "S": [0, 0.0]})
    for f in (await session.execute(q)).scalars().all():
        slot = agg[f.ticker][f.transaction_code]
        slot[0] += 1
        slot[1] += float(f.notional or 0)
    out = {}
    for t, d in agg.items():
        parts = []
        if d["P"][0]:
            parts.append(f"{d['P'][0]} open-market buy(s) ~${d['P'][1]:,.0f}")
        if d["S"][0]:
            parts.append(f"{d['S'][0]} sale(s) ~${d['S'][1]:,.0f}")
        out[t] = ", ".join(parts)
    return out


async def _upcoming_earnings(session: AsyncSession, user_id, today: date, days: int = 7) -> dict:
    res = await session.execute(
        select(PositionReviewReminder.ticker, PositionReviewReminder.due_date).where(
            PositionReviewReminder.user_id == user_id,
            PositionReviewReminder.kind == "earnings",
            PositionReviewReminder.status != "cancelled",
            PositionReviewReminder.due_date >= today,
            PositionReviewReminder.due_date <= today + timedelta(days=days),
        )
    )
    return {t: d for t, d in res.all()}


async def predictions_for(session: AsyncSession, user_id, trade_date: date) -> list[DailyPrediction]:
    res = await session.execute(
        select(DailyPrediction)
        .where(DailyPrediction.user_id == user_id, DailyPrediction.trade_date == trade_date)
        .order_by(DailyPrediction.ticker)
    )
    return list(res.scalars().all())


# ── the loop ───────────────────────────────────────────────────────────────

def _news_line(items: list) -> str:
    return "; ".join(f"[{n['score']:+d}] {n['title']}" for n in items[:3]) or "none in the last 48h"


async def generate_predictions(
    session: AsyncSession, user_id, book: BookSnapshot, trade_date: date
) -> tuple[list[DailyPrediction], Optional[str]]:
    """Make today's calls for the user's held tickers (idempotent per day)."""
    existing = await predictions_for(session, user_id, trade_date)
    if existing:
        return existing, None
    tickers = sorted(book.held_tickers)[:_MAX_TICKERS]
    if not tickers:
        return [], None

    rules = load_investment_rules()
    feats_list = await asyncio.gather(
        *(asyncio.to_thread(ticker_features, t, trade_date) for t in tickers), return_exceptions=True
    )
    features = {t: f for t, f in zip(tickers, feats_list) if isinstance(f, dict)}
    tickers = [t for t in tickers if t in features]
    if not tickers:
        return [], None

    market_lines = await asyncio.to_thread(overnight_context, trade_date)
    insider = await _insider_summary(session, tickers, trade_date - timedelta(days=7))
    earnings = await _upcoming_earnings(session, user_id, trade_date)
    stats = await track_record(session, user_id, trade_date)
    general_lessons, ticker_lessons = await active_lessons(session, user_id, set(tickers))

    blocks = []
    for t in tickers:
        f = features[t]
        pos = book.positions.get(t) or {}
        entry = pos.get("purchase_price")
        head = [f"{t} — held, {vol_class(t, rules)}"]
        if pos.get("date_entered"):
            head.append(f"day {(trade_date - pos['date_entered']).days} since entry")
        if entry:
            head.append(f"entry ${entry:,.2f} ({_fmt_pct(_pct(f['close'], entry))} vs entry)")
        stops = []
        if pos.get("soft_stop"):
            stops.append(f"soft stop ${pos['soft_stop']:,.2f}")
        if pos.get("hard_stop"):
            stops.append(f"hard stop ${pos['hard_stop']:,.2f}")
        avg_move = "n/a" if f["avg_abs_move_20d"] is None else f"{f['avg_abs_move_20d']:.1f}%"
        lines = [
            ", ".join(head),
            f"  prev close ${f['close']:,.2f} ({f['last_date']}): 1d {_fmt_pct(f['chg_1d'])}, 5d {_fmt_pct(f['chg_5d'])}, "
            f"20d {_fmt_pct(f['chg_20d'])}, vs SMA20 {_fmt_pct(f['vs_sma20'])}, avg daily move {avg_move}",
        ]
        if stops:
            lines.append("  " + ", ".join(stops))
        if t in earnings:
            lines.append(f"  EARNINGS on {earnings[t].isoformat()}")
        news = await fetch_ticker_news(session, t, hours=48, limit=3)
        lines.append(f"  news 48h: {_news_line(news)}")
        if insider.get(t):
            lines.append(f"  insider 7d: {insider[t]}")
        blocks.append("\n".join(lines))

    prompt = build_prediction_prompt(
        trade_date, blocks, market_lines, stats, general_lessons, ticker_lessons,
        [str(r) for r in rules.get("rules_text", [])],
    )
    raw = await _ollama_json(prompt)
    if raw is None:
        return [], None
    parsed = parse_predictions(raw, set(tickers))
    rows = []
    for p in parsed:
        row = DailyPrediction(
            user_id=user_id,
            trade_date=trade_date,
            ticker=p["ticker"],
            direction=p["direction"],
            confidence=p["confidence"],
            expected_move_pct=Decimal(str(round(p["expected_move_pct"], 3))) if p["expected_move_pct"] is not None else None,
            action=p["action"],
            rationale=p["rationale"],
            reference_close=Decimal(str(round(features[p["ticker"]]["close"], 4))),
            model=PREDICTION_MODEL,
        )
        session.add(row)
        rows.append(row)
    await session.flush()
    logger.info("predictions_generated", user_id=str(user_id), count=len(rows), asked=len(tickers))
    return rows, _clean_text(raw.get("market_view"), 300)


async def grade_pending(session: AsyncSession, user_id, upto: date) -> list[DailyPrediction]:
    """Grade every ungraded prediction dated on or before `upto` whose session
    data is available. Returns the rows graded CORRECT/WRONG in this pass."""
    res = await session.execute(
        select(DailyPrediction).where(
            DailyPrediction.user_id == user_id,
            DailyPrediction.outcome.is_(None),
            DailyPrediction.trade_date <= upto,
        )
    )
    pending = list(res.scalars().all())
    if not pending:
        return []
    days = sorted({p.trade_date for p in pending})
    syms = sorted({p.ticker for p in pending} | {"SPY"})
    lookups = await asyncio.gather(
        *(asyncio.to_thread(session_change, s, d) for d in days for s in syms), return_exceptions=True
    )
    results = {}
    i = 0
    for d in days:
        for s in syms:
            r = lookups[i]
            i += 1
            results[(s, d)] = r if isinstance(r, tuple) else ("pending", None, None)

    graded, now = [], datetime.now(timezone.utc)
    for p in pending:
        status, prev_close, close = results.get((p.ticker, p.trade_date), ("pending", None, None))
        if status == "no_session":
            p.outcome, p.graded_at = "NO_SESSION", now
            continue
        if status != "ok":
            continue
        pct = _pct(close, prev_close)
        p.close_price = Decimal(str(round(close, 4)))
        p.actual_change_pct = Decimal(str(round(pct, 3)))
        m_status, m_prev, m_close = results.get(("SPY", p.trade_date), ("pending", None, None))
        if m_status == "ok":
            p.market_change_pct = Decimal(str(round(_pct(m_close, m_prev), 3)))
        p.outcome = grade(p.direction, pct)
        p.graded_at = now
        graded.append(p)
    await session.flush()
    return graded


async def reflect(session: AsyncSession, user_id, graded: list[DailyPrediction]) -> list[PredictionLesson]:
    """LLM post-mortem per session date: fill each prediction's reflection and
    store new, de-duplicated lessons."""
    new_lessons: list[PredictionLesson] = []
    by_date: dict = defaultdict(list)
    for p in graded:
        if p.outcome in ("CORRECT", "WRONG") and not p.reflection:
            by_date[p.trade_date].append(p)
    if not by_date:
        return new_lessons

    res = await session.execute(
        select(PredictionLesson.lesson)
        .where(PredictionLesson.user_id == user_id, PredictionLesson.active.is_(True))
        .order_by(PredictionLesson.created_at.desc())
    )
    existing = list(res.scalars().all())
    res = await session.execute(select(PredictionLesson.lesson).where(PredictionLesson.user_id == user_id))
    known = {_norm_lesson(l) for l in res.scalars().all()}

    for day, preds in sorted(by_date.items()):
        tickers = sorted({p.ticker for p in preds})
        market = preds[0].market_change_pct
        market_line = f"SPY {_fmt_pct(float(market))}" if market is not None else "SPY n/a"
        insider = await _insider_summary(session, tickers, day, day)
        blocks = []
        for p in preds:
            actual = float(p.actual_change_pct)
            rel = f", {actual - float(market):+.1f}pp vs SPY" if market is not None else ""
            news = await fetch_ticker_news(session, p.ticker, hours=30, limit=3)
            expected = _fmt_pct(float(p.expected_move_pct)) if p.expected_move_pct is not None else "n/a"
            blocks.append(
                f"{p.ticker}: called {p.direction} at {p.confidence}% (expected {expected})"
                f" — \"{p.rationale or ''}\"\n"
                f"  actual {_fmt_pct(actual)} ({classify_move(actual)}{rel}) → {p.outcome}\n"
                f"  news: {_news_line(news)}"
                + (f"\n  insider that day: {insider[p.ticker]}" if insider.get(p.ticker) else "")
            )
        raw = await _ollama_json(build_reflection_prompt(day, market_line, blocks, existing[:30]), num_predict=2048)
        if raw is None:
            continue
        reviews, general, summary = parse_reflection(raw, set(tickers))
        for p in preds:
            rv = reviews.get(p.ticker)
            p.reflection = (rv or {}).get("why") or summary or "No reflection returned."
            lesson = (rv or {}).get("lesson")
            if lesson and _norm_lesson(lesson) not in known:
                known.add(_norm_lesson(lesson))
                existing.insert(0, lesson)
                row = PredictionLesson(
                    user_id=user_id, ticker=p.ticker, lesson=lesson, source_date=day, prediction_id=p.prediction_id
                )
                session.add(row)
                new_lessons.append(row)
        for g in general:
            if _norm_lesson(g) in known:
                continue
            known.add(_norm_lesson(g))
            existing.insert(0, g)
            row = PredictionLesson(user_id=user_id, ticker=None, lesson=g, source_date=day)
            session.add(row)
            new_lessons.append(row)
    await session.flush()
    return new_lessons


# ── Telegram formatting ─────────────────────────────────────────────────────

def format_calls(preds: list[DailyPrediction], market_view: Optional[str], stats: dict) -> list[str]:
    if not preds:
        return []
    lines = [f"Today's calls (model hit rate {_rate(stats['correct'], stats['total'])}):"]
    if market_view:
        lines.append(f"Market: {market_view}")
    for p in sorted(preds, key=lambda x: -x.confidence):
        move = f" {float(p.expected_move_pct):+.1f}%" if p.expected_move_pct is not None else ""
        line = f"{ARROWS.get(p.direction, '?')} {p.ticker} {p.direction}{move} ({p.confidence}%)"
        if p.action:
            line += f" — {p.action}"
        lines.append(line)
        if p.rationale:
            lines.append(f"   {p.rationale[:140]}")
    return lines


def format_scorecard(graded: list[DailyPrediction], lessons: list[PredictionLesson], stats: dict) -> list[str]:
    scored = [p for p in graded if p.outcome in ("CORRECT", "WRONG")]
    if not scored:
        return []
    correct = sum(p.outcome == "CORRECT" for p in scored)
    lines = [f"Prediction scorecard: {correct}/{len(scored)} today · last {_TRACK_RECORD_DAYS}d {_rate(stats['correct'], stats['total'])}"]
    for p in sorted(scored, key=lambda x: (x.outcome != "WRONG", x.ticker)):
        mark = "✅" if p.outcome == "CORRECT" else "❌"
        lines.append(f"{mark} {p.ticker} called {p.direction}, moved {float(p.actual_change_pct):+.1f}%")
        if p.reflection and p.outcome == "WRONG":
            lines.append(f"   why: {p.reflection[:160]}")
    if lessons:
        lines.append("New lessons (fed into tomorrow's calls):")
        lines.extend(f"• {('[' + l.ticker + '] ') if l.ticker else ''}{l.lesson[:160]}" for l in lessons)
    return lines
