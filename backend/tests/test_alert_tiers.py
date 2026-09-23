from app.trading.alert_tiers import DIGEST, REALTIME, delivery_tier


def test_critical_always_realtime():
    assert delivery_tier("critical", held=True) == REALTIME
    assert delivery_tier("critical", held=False) == REALTIME


def test_warning_realtime_only_when_held():
    assert delivery_tier("warning", held=True) == REALTIME
    assert delivery_tier("warning", held=False) == DIGEST


def test_info_always_digest():
    assert delivery_tier("info", held=True) == DIGEST
    assert delivery_tier("info", held=False) == DIGEST


def test_unknown_severity_defaults_to_digest():
    assert delivery_tier("", held=True) == DIGEST
    assert delivery_tier(None, held=True) == DIGEST
