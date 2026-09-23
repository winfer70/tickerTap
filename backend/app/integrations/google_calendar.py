"""google_calendar.py — per-user Google OAuth link for the review calendar.

Web-server OAuth flow with a single scope (calendar.app.created: the app can
create its own secondary calendar and manage events on it, nothing else):

  1. POST /integrations/google/authorize (authenticated) creates a random,
     single-use `state` stored in Redis for STATE_TTL seconds together with
     the user id and a PKCE verifier, and returns Google's consent URL. The
     app's JWT lives in sessionStorage, so Google's redirect back can't carry
     it — the server-side state record is what ties the callback to the user,
     and it doubles as the CSRF check.
  2. GET /integrations/google/callback consumes the state (GETDEL), exchanges
     the code server-side with the client secret, verifies the scope was
     actually granted (users can deny it), and stores only the refresh token,
     encrypted and bound to the user (token_crypto.py).

Unlinking revokes the token at Google before deleting the row.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import token_crypto
from ..models import GoogleCalendarLink

logger = structlog.get_logger("google_calendar")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
CALLBACK_PATH = "/api/v1/integrations/google/callback"
STATE_TTL = 600
_STATE_PREFIX = "tickertap:google_oauth_state:"
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class GoogleAuthError(Exception):
    """Google rejected a grant — e.g. the user revoked access (invalid_grant)."""


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str


def get_config() -> Optional[OAuthConfig]:
    """None unless the OAuth client and the token-encryption key are all set."""
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
    if not (client_id and client_secret and redirect_uri and token_crypto.is_configured()):
        return None
    return OAuthConfig(client_id, client_secret, redirect_uri)


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def build_authorization_url(cfg: OAuthConfig, state: str, code_challenge: str) -> str:
    params = {
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        # Always re-consent so Google returns a refresh token on every link,
        # including relinks after an unlink.
        "prompt": "consent",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


async def save_state(redis, state: str, user_id: str, verifier: str) -> None:
    await redis.set(_STATE_PREFIX + state, json.dumps({"user_id": user_id, "verifier": verifier}), ex=STATE_TTL)


async def pop_state(redis, state: str) -> Optional[dict]:
    """Single-use: fetch-and-delete atomically so a replayed callback fails."""
    if not state or len(state) > 256:
        return None
    raw = await redis.getdel(_STATE_PREFIX + state)
    return json.loads(raw) if raw else None


def granted_scopes(token_response: dict) -> set[str]:
    return set((token_response.get("scope") or "").split())


async def exchange_code(cfg: OAuthConfig, code: str, verifier: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": cfg.client_id,
                "client_secret": cfg.client_secret,
                "redirect_uri": cfg.redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
    if resp.status_code != 200:
        raise GoogleAuthError(f"code exchange failed: HTTP {resp.status_code} {_error_code(resp)}")
    return resp.json()


async def refresh_access_token(cfg: OAuthConfig, refresh_token: str) -> str:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": cfg.client_id,
                "client_secret": cfg.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code != 200:
        raise GoogleAuthError(f"refresh failed: HTTP {resp.status_code} {_error_code(resp)}")
    return resp.json()["access_token"]


async def revoke(token: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                REVOKE_URL,
                data={"token": token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        logger.warning("google_revoke_failed", error=type(exc).__name__)
        return False
    # 400 typically means the token was already revoked/expired — nothing left to revoke.
    return resp.status_code in (200, 400)


async def delete_calendar(access_token: str, calendar_id: str) -> bool:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.delete(
            f"{CALENDAR_API}/calendars/{calendar_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    return resp.status_code in (200, 204, 404, 410)


def _error_code(resp: httpx.Response) -> str:
    """Google's short error code only — never the body, which can echo request data."""
    try:
        return str(resp.json().get("error", ""))[:60]
    except ValueError:
        return ""


def owner_key(user_id) -> str:
    return str(user_id)


def decrypt_refresh_token(link: GoogleCalendarLink) -> str:
    return token_crypto.decrypt(link.refresh_token_enc, link.key_version, owner_key(link.user_id))


async def store_link(session: AsyncSession, user_id, token_response: dict) -> tuple[GoogleCalendarLink, Optional[str]]:
    """Upsert the user's link with a freshly encrypted refresh token. Returns
    (link, previous refresh token) so the caller can revoke a replaced one."""
    blob, version = token_crypto.encrypt(token_response["refresh_token"], owner_key(user_id))
    link = (
        await session.execute(select(GoogleCalendarLink).where(GoogleCalendarLink.user_id == user_id))
    ).scalar_one_or_none()
    previous = None
    if link is None:
        link = GoogleCalendarLink(user_id=user_id)
        session.add(link)
    else:
        try:
            previous = decrypt_refresh_token(link)
        except token_crypto.TokenCryptoError:
            previous = None
    link.refresh_token_enc = blob
    link.key_version = version
    link.granted_scopes = " ".join(sorted(granted_scopes(token_response)))
    link.status = "active"
    link.last_error = None
    return link, previous


async def unlink(session: AsyncSession, user_id, *, delete_google_calendar: bool = False) -> dict:
    """Revoke the user's Google grant (and optionally delete the app's calendar),
    then delete the stored link. Best-effort at Google — the local row is
    always removed so no token stays on our side. Caller commits."""
    link = (
        await session.execute(select(GoogleCalendarLink).where(GoogleCalendarLink.user_id == user_id))
    ).scalar_one_or_none()
    if link is None:
        return {"linked": False}
    result = {"linked": True, "revoked": False, "calendar_deleted": False}
    try:
        refresh_token = decrypt_refresh_token(link)
    except token_crypto.TokenCryptoError:
        refresh_token = None
    cfg = get_config()
    if refresh_token and delete_google_calendar and link.google_calendar_id and cfg:
        try:
            access_token = await refresh_access_token(cfg, refresh_token)
            result["calendar_deleted"] = await delete_calendar(access_token, link.google_calendar_id)
        except (GoogleAuthError, httpx.HTTPError) as exc:
            logger.warning("google_calendar_delete_failed", error=str(exc)[:120])
    if refresh_token:
        result["revoked"] = await revoke(refresh_token)
    await session.delete(link)
    return result
