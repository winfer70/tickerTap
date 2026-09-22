from datetime import date

from app.trading.review_reminders import T1_KIND, T2_KIND, earnings_milestone, phase_milestones, price_milestone

RULES = {
    "phase0_grace_days": 3,
    "phase1_end_days": 10,
    "phase2_extension_days": 5,
    "volatile_bep_trigger_pct": 5.0,
    "stable_bep_trigger_pct": 2.0,
    "swing_target1_pct": 7.0,
    "swing_target2_pct": 12.0,
}
ENTERED = date(2026, 9, 1)


def _by_kind(ms):
    return {m.kind: m for m in ms}


def test_new_position_gets_full_milestone_set():
    ms = _by_kind(phase_milestones("AAPL", ENTERED, 100.0, "stable", RULES, today=ENTERED))
    assert ms["grace_end"].due_date == date(2026, 9, 5)
    assert ms["bep_decision"].due_date == date(2026, 9, 11)
    assert ms["extension_end"].due_date == date(2026, 9, 16)
    assert ms["phase2_review"].due_date == date(2026, 9, 21)
    assert "+2%" in ms["grace_end"].detail
    assert "$102.00" in ms["grace_end"].detail


def test_volatile_uses_volatile_bep_trigger():
    ms = _by_kind(phase_milestones("INTC", ENTERED, 20.0, "volatile", RULES, today=ENTERED))
    assert "+5%" in ms["grace_end"].detail
    assert "Volatile" in ms["grace_end"].detail


def test_position_seen_midlife_gets_no_past_milestones():
    ms = phase_milestones("AAPL", ENTERED, 100.0, "stable", RULES, today=date(2026, 9, 14))
    kinds = {m.kind for m in ms}
    assert "grace_end" not in kinds
    assert "bep_decision" not in kinds
    assert all(m.due_date >= date(2026, 9, 14) for m in ms)


def test_only_next_phase2_review_is_generated():
    ms = phase_milestones("AAPL", ENTERED, 100.0, "stable", RULES, today=date(2026, 10, 1))
    reviews = [m for m in ms if m.kind == "phase2_review"]
    assert len(reviews) == 1
    # day 30 (Oct 1) is itself a review day: 10 + 4*5
    assert reviews[0].due_date == date(2026, 10, 1)


def test_unknown_entry_date_yields_nothing():
    assert phase_milestones("AAPL", None, 100.0, "stable", RULES, today=ENTERED) == []


def test_earnings_milestone():
    m = earnings_milestone("AAPL", date(2026, 10, 30))
    assert m.kind == "earnings" and m.due_date == date(2026, 10, 30)


def test_price_milestone_fires_once_per_level():
    today = date(2026, 9, 22)
    assert price_milestone("AAPL", 100.0, 103.0, RULES, set(), today) is None
    t1 = price_milestone("AAPL", 100.0, 107.5, RULES, set(), today)
    assert t1.kind == T1_KIND and "T1" in t1.title and t1.due_date == today
    assert price_milestone("AAPL", 100.0, 108.0, RULES, {T1_KIND}, today) is None
    t2 = price_milestone("AAPL", 100.0, 113.0, RULES, {T1_KIND}, today)
    assert t2.kind == T2_KIND
    assert price_milestone("AAPL", 100.0, 120.0, RULES, {T2_KIND}, today) is None


def test_price_milestone_skips_straight_to_t2_when_first_seen_above_it():
    m = price_milestone("SNDK", 1535.0, 1886.0, RULES, set(), date(2026, 9, 22))
    assert m.kind == T2_KIND and "+22.9%" in m.title


def test_price_milestone_needs_entry_and_price():
    assert price_milestone("AAPL", None, 113.0, RULES, set(), date(2026, 9, 22)) is None
    assert price_milestone("AAPL", 100.0, None, RULES, set(), date(2026, 9, 22)) is None


def test_entry_date_falls_back_to_purchase_date():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.trading.book_loader import entry_date, is_stock

    assert entry_date(SimpleNamespace(date_entered=date(2026, 9, 1), purchase_date=None)) == date(2026, 9, 1)
    pd = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)
    assert entry_date(SimpleNamespace(date_entered=None, purchase_date=pd)) == date(2026, 9, 2)
    assert entry_date(SimpleNamespace(date_entered=None, purchase_date=None)) is None
    assert is_stock("stock") and is_stock(None) and not is_stock("crypto") and not is_stock("physical")
