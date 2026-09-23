"""alert_tiers.py — real-time Telegram vs once-a-day digest routing.

Held positions interrupt only at warning/critical severity; anything on a
ticker you don't hold (watchlist, new-idea buys) waits for the post-market
digest unless it's critical. Digest-tier alerts are still stored as in-app
notifications (flagged metadata.digest=true), which is what the digest reads.
"""
from __future__ import annotations

REALTIME = "realtime"
DIGEST = "digest"


def delivery_tier(severity: str, held: bool) -> str:
    sev = (severity or "info").lower()
    if sev == "critical":
        return REALTIME
    if held and sev == "warning":
        return REALTIME
    return DIGEST
