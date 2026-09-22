from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.trading.predictions import (
    build_prediction_prompt,
    classify_move,
    format_calls,
    format_scorecard,
    grade,
    parse_predictions,
    parse_reflection,
    summarize_track_record,
)


def test_classify_and_grade_use_flat_band():
    assert classify_move(0.6) == "UP"
    assert classify_move(-0.6) == "DOWN"
    assert classify_move(0.3) == "FLAT"
    assert grade("UP", 1.2) == "CORRECT"
    assert grade("UP", 0.2) == "WRONG"
    assert grade("FLAT", -0.4) == "CORRECT"


def test_parse_predictions_validates_and_clamps():
    raw = {
        "predictions": [
            {"ticker": "aapl", "direction": "up", "confidence": 140, "expected_move_pct": 80, "rationale": "  gap up\n on news ", "action": "hold"},
            {"ticker": "AAPL", "direction": "DOWN", "confidence": 50},  # duplicate ticker dropped
            {"ticker": "TSLA", "direction": "UP", "confidence": 60},  # not held
            {"ticker": "INTC", "direction": "SIDEWAYS", "confidence": 60},  # bad direction
            {"ticker": "HALO", "direction": "FLAT", "confidence": "n/a"},  # bad confidence
            {"ticker": "SONY", "direction": "DOWN", "confidence": 55.4, "expected_move_pct": "x", "rationale": "null"},
        ]
    }
    out = parse_predictions(raw, {"AAPL", "INTC", "HALO", "SONY"})
    assert [p["ticker"] for p in out] == ["AAPL", "SONY"]
    assert out[0]["direction"] == "UP" and out[0]["confidence"] == 100
    assert out[0]["expected_move_pct"] == 25.0
    assert out[0]["rationale"] == "gap up on news"
    assert out[1]["confidence"] == 55 and out[1]["expected_move_pct"] is None and out[1]["rationale"] is None


def test_parse_predictions_handles_garbage():
    assert parse_predictions({}, {"AAPL"}) == []
    assert parse_predictions({"predictions": "nope"}, {"AAPL"}) == []
    assert parse_predictions(None, {"AAPL"}) == []


def test_parse_reflection_filters_lessons():
    raw = {
        "summary": "Broad selloff.",
        "reviews": [
            {"ticker": "AAPL", "why": "Market fell 2%.", "lesson": "null"},
            {"ticker": "INTC", "why": "Beat.", "lesson": "When futures are down >1%, cap UP confidence at 55 on volatile names."},
            {"ticker": "ZZZ", "why": "?", "lesson": "not ours"},
        ],
        "general_lessons": ["short", "Weight overnight futures more heavily than prior-day momentum.", "a", "b" * 20, "c" * 20],
    }
    reviews, general, summary = parse_reflection(raw, {"AAPL", "INTC"})
    assert set(reviews) == {"AAPL", "INTC"}
    assert reviews["AAPL"]["lesson"] is None
    assert reviews["INTC"]["lesson"].startswith("When futures")
    assert len(general) == 2 and general[0].startswith("Weight overnight")
    assert summary == "Broad selloff."


def _row(ticker, direction, confidence, outcome):
    return SimpleNamespace(ticker=ticker, direction=direction, confidence=confidence, outcome=outcome)


def test_summarize_track_record():
    rows = [
        _row("AAPL", "UP", 80, "CORRECT"),
        _row("AAPL", "UP", 75, "WRONG"),
        _row("INTC", "DOWN", 50, "CORRECT"),
        _row("INTC", "DOWN", 50, "NO_SESSION"),
    ]
    s = summarize_track_record(rows)
    assert (s["correct"], s["total"]) == (2, 3)
    assert (s["high_correct"], s["high_total"]) == (1, 2)
    assert s["per_ticker"]["AAPL"] == (1, 2)
    assert s["per_direction"]["DOWN"] == (1, 1)


def test_prompt_contains_track_record_and_lessons():
    stats = summarize_track_record([_row("AAPL", "UP", 80, "WRONG")])
    prompt = build_prediction_prompt(
        date(2026, 9, 23),
        ["AAPL — held, stable"],
        ["- SPY previous session: +0.4%"],
        stats,
        ["Respect futures direction."],
        {"AAPL": ["Fades after gap-ups."]},
        ["Never chase"],
    )
    assert "0/1 (0%)" in prompt
    assert "[general] Respect futures direction." in prompt
    assert "[AAPL] Fades after gap-ups." in prompt
    assert "Never chase" in prompt
    assert '"predictions"' in prompt


def _pred(ticker, direction, conf, outcome=None, actual=None, reflection=None):
    return SimpleNamespace(
        ticker=ticker, direction=direction, confidence=conf, expected_move_pct=Decimal("1.2"),
        action="hold", rationale="momentum", outcome=outcome,
        actual_change_pct=Decimal(str(actual)) if actual is not None else None, reflection=reflection,
    )


def test_format_calls_and_scorecard():
    stats = summarize_track_record([])
    calls = format_calls([_pred("AAPL", "UP", 60), _pred("INTC", "DOWN", 80)], "Risk-off open.", stats)
    assert calls[0].startswith("Today's calls")
    assert "INTC DOWN" in calls[2]  # highest confidence first
    lessons = [SimpleNamespace(ticker="INTC", lesson="Earnings week: widen FLAT expectations.")]
    card = format_scorecard(
        [_pred("AAPL", "UP", 60, "CORRECT", 1.4), _pred("INTC", "DOWN", 80, "WRONG", 2.0, "Beat on margins.")],
        lessons,
        summarize_track_record([_row("AAPL", "UP", 60, "CORRECT"), _row("INTC", "DOWN", 80, "WRONG")]),
    )
    assert card[0].startswith("Prediction scorecard: 1/2")
    assert "❌ INTC" in card[1] and "why: Beat on margins." in card[2]
    assert any("[INTC] Earnings week" in l for l in card)
    assert format_scorecard([], [], stats) == []


def test_clip_cuts_on_word_boundary():
    from app.trading.predictions import _clip

    assert _clip("short", 10) == "short"
    out = _clip("broader market sentiment or unanticipated catalysts", 40)
    assert out.endswith("…") and len(out) <= 40 and "catal" not in out
