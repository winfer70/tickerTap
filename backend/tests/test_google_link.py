import base64
import hashlib
import logging
import os
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("JWT_SECRET", "test-secret-key-that-is-long-enough-for-jwt-validation-purposes")

import pytest

from app import token_crypto
from app.integrations import google_calendar as gc

KEY1 = base64.b64encode(b"1" * 32).decode()
KEY2 = base64.b64encode(b"2" * 32).decode()


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("OAUTH_TOKEN_KEYS", f"1:{KEY1}")


def test_encrypt_round_trip_and_owner_binding(keys):
    blob, version = token_crypto.encrypt("refresh-abc", "user-a")
    assert version == 1 and b"refresh-abc" not in blob
    assert token_crypto.decrypt(blob, version, "user-a") == "refresh-abc"
    with pytest.raises(token_crypto.TokenCryptoError):
        token_crypto.decrypt(blob, version, "user-b")


def test_tampered_ciphertext_is_rejected(keys):
    blob, version = token_crypto.encrypt("refresh-abc", "user-a")
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(token_crypto.TokenCryptoError):
        token_crypto.decrypt(tampered, version, "user-a")


def test_nonce_is_random(keys):
    a, _ = token_crypto.encrypt("same", "u")
    b, _ = token_crypto.encrypt("same", "u")
    assert a != b


def test_key_rotation(monkeypatch):
    monkeypatch.setenv("OAUTH_TOKEN_KEYS", f"1:{KEY1}")
    old_blob, old_version = token_crypto.encrypt("tok", "u")
    monkeypatch.setenv("OAUTH_TOKEN_KEYS", f"1:{KEY1},2:{KEY2}")
    new_blob, new_version = token_crypto.encrypt("tok", "u")
    assert (old_version, new_version) == (1, 2)
    assert token_crypto.decrypt(old_blob, 1, "u") == "tok"
    assert token_crypto.decrypt(new_blob, 2, "u") == "tok"
    monkeypatch.setenv("OAUTH_TOKEN_KEYS", f"2:{KEY2}")
    with pytest.raises(token_crypto.TokenCryptoError):
        token_crypto.decrypt(old_blob, 1, "u")


def test_bad_key_config(monkeypatch):
    monkeypatch.setenv("OAUTH_TOKEN_KEYS", "")
    assert not token_crypto.is_configured()
    with pytest.raises(token_crypto.TokenCryptoError):
        token_crypto.encrypt("x", "u")
    monkeypatch.setenv("OAUTH_TOKEN_KEYS", "1:" + base64.b64encode(b"short").decode())
    assert not token_crypto.is_configured()


def test_config_requires_everything(monkeypatch, keys):
    for k in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI"):
        monkeypatch.setenv(k, "x")
    assert gc.get_config() is not None
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
    assert gc.get_config() is None


def test_authorization_url_requests_only_the_app_calendar_scope():
    cfg = gc.OAuthConfig("cid", "secret", "https://ticker-tap.com/api/v1/integrations/google/callback")
    verifier, challenge = gc.pkce_pair()
    q = parse_qs(urlparse(gc.build_authorization_url(cfg, "st4te", challenge)).query)
    assert q["scope"] == ["https://www.googleapis.com/auth/calendar.app.created"]
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert q["state"] == ["st4te"] and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == [cfg.redirect_uri]
    assert "secret" not in gc.build_authorization_url(cfg, "st4te", challenge)
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == [expected]


class FakeRedis:
    def __init__(self):
        self.data, self.ttl = {}, {}

    async def set(self, key, value, ex=None):
        self.data[key], self.ttl[key] = value, ex

    async def getdel(self, key):
        return self.data.pop(key, None)


@pytest.mark.asyncio
async def test_state_is_single_use_and_expires():
    r = FakeRedis()
    await gc.save_state(r, "abc", "user-1", "verifier-1")
    assert list(r.ttl.values()) == [gc.STATE_TTL]
    assert await gc.pop_state(r, "abc") == {"user_id": "user-1", "verifier": "verifier-1"}
    assert await gc.pop_state(r, "abc") is None
    assert await gc.pop_state(r, "") is None
    assert await gc.pop_state(r, "x" * 300) is None


def test_granted_scopes_parsing():
    assert gc.granted_scopes({"scope": "a b"}) == {"a", "b"}
    assert gc.granted_scopes({}) == set()


def test_access_log_filter_strips_callback_query():
    from app.main import _RedactOAuthCallbackQuery

    f = _RedactOAuthCallbackQuery()
    rec = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/api/v1/integrations/google/callback?code=SECRET&state=S", "1.1", 303), None,
    )
    assert f.filter(rec)
    assert "SECRET" not in rec.getMessage() and "/api/v1/integrations/google/callback" in rec.getMessage()
    other = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/api/v1/news?limit=5", "1.1", 200), None,
    )
    f.filter(other)
    assert "limit=5" in other.getMessage()
