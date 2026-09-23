"""google_link.py — Settings API for linking a user's own Google Calendar.

See integrations/google_calendar.py for the flow and security notes. The
callback is the only unauthenticated endpoint: it identifies the user solely
through the single-use state record, and never returns tokens or Google error
details to the browser — only a status keyword on the redirect.

Deliberately no `from __future__ import annotations`: slowapi's @limiter.limit
wraps the endpoints, and FastAPI would then resolve string annotations against
slowapi's module globals (NameError on AsyncSession at import time).
"""
import os
import secrets
import uuid
from datetime import datetime
from typing import Optional

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import token_crypto
from ..db import get_db
from ..integrations import google_calendar as gc
from ..limiter import limiter
from ..models import AuditLog, GoogleCalendarLink, User
from ..observability import get_redis_client
from .auth_routes import get_current_user

logger = structlog.get_logger("google_link")
router = APIRouter(prefix="/integrations/google", tags=["integrations"])


class LinkStatusOut(BaseModel):
    configured: bool
    linked: bool
    status: Optional[str] = None
    detail_level: Optional[str] = None
    calendar_created: bool = False
    linked_at: Optional[datetime] = None
    last_sync_at: Optional[datetime] = None
    last_error: Optional[str] = None


class AuthorizeOut(BaseModel):
    url: str


class LinkSettingsIn(BaseModel):
    detail_level: str = Field(..., regex="^(full|minimal)$")


def _settings_redirect(outcome: str) -> RedirectResponse:
    app_url = os.getenv("APP_URL", "https://ticker-tap.com").rstrip("/")
    return RedirectResponse(f"{app_url}/settings?google={outcome}", status_code=303)


def _audit(db: AsyncSession, user_id, action: str, request: Request, new_values: Optional[dict] = None) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            table_name="google_calendar_links",
            new_values=new_values,
            ip_address=request.client.host if request.client else None,
            user_agent=(request.headers.get("user-agent") or "")[:500] or None,
        )
    )


async def _get_link(db: AsyncSession, user_id) -> Optional[GoogleCalendarLink]:
    res = await db.execute(select(GoogleCalendarLink).where(GoogleCalendarLink.user_id == user_id))
    return res.scalar_one_or_none()


@router.get("/status", response_model=LinkStatusOut)
async def link_status(db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    link = await _get_link(db, current_user.user_id)
    configured = gc.get_config() is not None
    if link is None:
        return LinkStatusOut(configured=configured, linked=False)
    return LinkStatusOut(
        configured=configured,
        linked=True,
        status=link.status,
        detail_level=link.detail_level,
        calendar_created=bool(link.google_calendar_id),
        linked_at=link.linked_at,
        last_sync_at=link.last_sync_at,
        last_error=link.last_error,
    )


@router.post("/authorize", response_model=AuthorizeOut)
@limiter.limit("10/minute")
async def authorize(request: Request, current_user=Depends(get_current_user)):
    cfg = gc.get_config()
    if cfg is None:
        raise HTTPException(status_code=503, detail="Google Calendar linking is not configured on this server yet")
    state = secrets.token_urlsafe(32)
    verifier, challenge = gc.pkce_pair()
    await gc.save_state(get_redis_client(), state, str(current_user.user_id), verifier)
    return AuthorizeOut(url=gc.build_authorization_url(cfg, state, challenge))


@router.get("/callback", include_in_schema=False)
@limiter.limit("20/minute")
async def callback(
    request: Request,
    state: str = Query(""),
    code: Optional[str] = Query(None),
    error: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    pending = await gc.pop_state(get_redis_client(), state)
    if pending is None:
        return _settings_redirect("expired")
    user_id = uuid.UUID(pending["user_id"])
    if error or not code:
        # access_denied when the user clicks Cancel on Google's consent screen.
        return _settings_redirect("denied")
    cfg = gc.get_config()
    if cfg is None:
        return _settings_redirect("error")
    try:
        tokens = await gc.exchange_code(cfg, code, pending["verifier"])
    except (gc.GoogleAuthError, httpx.HTTPError) as exc:
        logger.warning("google_link_exchange_failed", user_id=str(user_id), error=str(exc)[:120])
        return _settings_redirect("error")

    if gc.SCOPE not in gc.granted_scopes(tokens):
        # The user unticked the calendar permission — nothing useful to keep.
        await gc.revoke(tokens.get("refresh_token") or tokens.get("access_token", ""))
        return _settings_redirect("scope_missing")
    if not tokens.get("refresh_token"):
        return _settings_redirect("error")
    user = (await db.execute(select(User).where(User.user_id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        # Account deleted/deactivated between authorize and callback.
        await gc.revoke(tokens["refresh_token"])
        return _settings_redirect("error")

    try:
        link, previous = await gc.store_link(db, user_id, tokens)
    except token_crypto.TokenCryptoError:
        logger.error("google_link_encrypt_failed", user_id=str(user_id))
        await gc.revoke(tokens["refresh_token"])
        return _settings_redirect("error")
    _audit(db, user_id, "google_calendar_linked", request, {"scopes": link.granted_scopes})
    await db.commit()
    if previous and previous != tokens["refresh_token"]:
        await gc.revoke(previous)
    logger.info("google_calendar_linked", user_id=str(user_id))
    return _settings_redirect("linked")


@router.patch("/settings", response_model=LinkStatusOut)
async def update_link_settings(
    body: LinkSettingsIn,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    link = await _get_link(db, current_user.user_id)
    if link is None:
        raise HTTPException(status_code=404, detail="Google Calendar is not linked")
    link.detail_level = body.detail_level
    await db.commit()
    return await link_status(db=db, current_user=current_user)


@router.delete("/link")
async def unlink(
    request: Request,
    delete_calendar: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    result = await gc.unlink(db, current_user.user_id, delete_google_calendar=delete_calendar)
    if not result["linked"]:
        raise HTTPException(status_code=404, detail="Google Calendar is not linked")
    _audit(db, current_user.user_id, "google_calendar_unlinked", request, result)
    await db.commit()
    return result
