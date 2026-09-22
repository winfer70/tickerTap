from datetime import date

from app.trading.review_reminders import earnings_milestone, phase_milestones, price_rule_checks

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


def test_price_rule_checks_t1_t2():
    pos = {"purchase_price": 100.0}
    assert price_rule_checks("AAPL", pos, 103.0, RULES) == []
    assert "T1" in price_rule_checks("AAPL", pos, 107.5, RULES)[0]
    assert "T2" in price_rule_checks("AAPL", pos, 113.0, RULES)[0]
    assert price_rule_checks("AAPL", {"purchase_price": None}, 113.0, RULES) == []
