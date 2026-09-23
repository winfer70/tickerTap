"""token_crypto.py — AES-256-GCM encryption for third-party OAuth tokens at rest.

Keys come from OAUTH_TOKEN_KEYS, a comma-separated list of "version:base64key"
entries (32-byte keys). The highest version encrypts; any listed version can
decrypt, so rotation is: add a new version, re-encrypt rows, drop the old one.
The key never lives in the database or git.

Each ciphertext is bound to its owner via AES-GCM associated data (the user
id), so a token row copied onto another user fails to decrypt.

Generate a key:  python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_NONCE_BYTES = 12


class TokenCryptoError(Exception):
    """Missing/invalid key configuration or a ciphertext that fails to authenticate."""


def _keys() -> dict[int, bytes]:
    raw = os.getenv("OAUTH_TOKEN_KEYS", "").strip()
    keys: dict[int, bytes] = {}
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        try:
            version_s, key_b64 = entry.split(":", 1)
            key = base64.b64decode(key_b64, validate=True)
            version = int(version_s)
        except ValueError as exc:
            raise TokenCryptoError("OAUTH_TOKEN_KEYS entries must be 'version:base64key'") from exc
        if len(key) != 32:
            raise TokenCryptoError(f"OAUTH_TOKEN_KEYS version {version} is not a 32-byte key")
        keys[version] = key
    return keys


def is_configured() -> bool:
    try:
        return bool(_keys())
    except TokenCryptoError:
        return False


def encrypt(plaintext: str, owner: str) -> tuple[bytes, int]:
    """-> (nonce + ciphertext, key version)."""
    keys = _keys()
    if not keys:
        raise TokenCryptoError("OAUTH_TOKEN_KEYS is not configured")
    version = max(keys)
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = AESGCM(keys[version]).encrypt(nonce, plaintext.encode(), owner.encode())
    return nonce + ciphertext, version


def decrypt(blob: bytes, version: int, owner: str) -> str:
    key = _keys().get(version)
    if key is None:
        raise TokenCryptoError(f"No key configured for version {version}")
    try:
        plaintext = AESGCM(key).decrypt(blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], owner.encode())
    except Exception as exc:  # InvalidTag: wrong key, wrong owner, or tampered data
        raise TokenCryptoError("Token failed to decrypt") from exc
    return plaintext.decode()
