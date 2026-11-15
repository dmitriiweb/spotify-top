"""PKCE authorization URL and bounded server-side OAuth sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

from spotify_analytics.core.config import Settings
from spotify_analytics.spotify.client import TokenSet

COOKIE_NAME = "spotify_session"
_AUTHORIZATION_URL = "https://accounts.spotify.com/authorize"
_LOGIN_TTL_SECONDS = 10 * 60
_SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
_MAX_SESSIONS = 10_000


def authorization_url(settings: Settings, state: str, verifier: str) -> str:
    """Build Spotify's PKCE authorization URL with the sole required scope."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    query = urlencode(
        {
            "client_id": settings.spotify_client_id or "",
            "response_type": "code",
            "redirect_uri": settings.redirect_uri,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "scope": "user-library-read",
        }
    )
    return f"{_AUTHORIZATION_URL}?{query}"


@dataclass
class AuthSession:
    """Authenticated server-side session; tokens are never represented.

    Examples:
        session = AuthSession(
            TokenSet("access", "refresh", time.time() + 3600), "account-id"
        )
    """

    tokens: TokenSet = field(repr=False)
    account_id: str


@dataclass
class _Login:
    state: str = field(repr=False)
    verifier: str = field(repr=False)
    expires_at: float


@dataclass
class _Authenticated:
    tokens: TokenSet = field(repr=False)
    account_id: str
    expires_at: float


class SessionStore:
    """Store short-lived OAuth state and authenticated sessions in memory.

    Examples:
        store = SessionStore("configured-secret-at-least-32-bytes")
        cookie, state, verifier = store.begin_login()
    """

    def __init__(self, secret: str) -> None:
        self._key = secret.encode("utf-8")
        self._records: dict[str, _Login | _Authenticated] = {}
        self._lock = threading.RLock()

    def begin_login(self) -> tuple[str, str, str]:
        """Create a signed temporary cookie, CSRF state, and PKCE verifier."""
        identifier = secrets.token_urlsafe(32)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        cookie = self._sign(identifier)
        with self._lock:
            now = time.monotonic()
            self._purge(now)
            self._make_room()
            self._records[identifier] = _Login(
                state=state, verifier=verifier, expires_at=now + _LOGIN_TTL_SECONDS
            )
        return cookie, state, verifier

    def consume_login(self, cookie: str | None, state: str | None) -> str | None:
        """Validate and consume a one-time state, returning its PKCE verifier."""
        identifier = self._verify(cookie)
        if identifier is None or not isinstance(state, str) or not state.isascii():
            return None
        with self._lock:
            now = time.monotonic()
            self._purge(now)
            record = self._records.get(identifier)
            if not isinstance(record, _Login) or not hmac.compare_digest(
                record.state, state
            ):
                return None
            del self._records[identifier]
            return record.verifier

    def authenticate(
        self, cookie: str | None, tokens: TokenSet, account_id: str
    ) -> str:
        """Rotate the cookie and replace the pre-authentication login record."""
        old_identifier = self._verify(cookie)
        identifier = secrets.token_urlsafe(32)
        rotated = self._sign(identifier)
        with self._lock:
            now = time.monotonic()
            self._purge(now)
            if old_identifier is not None:
                self._records.pop(old_identifier, None)
            self._make_room()
            self._records[identifier] = _Authenticated(
                tokens=tokens,
                account_id=account_id,
                expires_at=now + _SESSION_TTL_SECONDS,
            )
        return rotated

    def get(self, cookie: str | None) -> AuthSession | None:
        """Return a live session for a valid signed cookie, if present."""
        identifier = self._verify(cookie)
        if identifier is None:
            return None
        with self._lock:
            now = time.monotonic()
            self._purge(now)
            record = self._records.get(identifier)
            if not isinstance(record, _Authenticated):
                return None
            return AuthSession(tokens=record.tokens, account_id=record.account_id)

    def _sign(self, identifier: str) -> str:
        signature = hmac.new(
            self._key, identifier.encode("ascii"), hashlib.sha256
        ).digest()
        encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
        return f"{identifier}.{encoded}"

    def _verify(self, cookie: str | None) -> str | None:
        if not isinstance(cookie, str):
            return None
        identifier, separator, signature = cookie.partition(".")
        if (
            not separator
            or not identifier
            or len(identifier) > 128
            or not identifier.isascii()
            or not all(
                character.isalnum() or character in "_-" for character in identifier
            )
            or not signature
            or not signature.isascii()
            or "." in signature
        ):
            return None
        expected = self._sign(identifier).partition(".")[2]
        if not hmac.compare_digest(expected, signature):
            return None
        return identifier

    def _purge(self, now: float) -> None:
        expired = [
            identifier
            for identifier, record in self._records.items()
            if record.expires_at <= now
        ]
        for identifier in expired:
            del self._records[identifier]

    def _make_room(self) -> None:
        while len(self._records) >= _MAX_SESSIONS:
            del self._records[next(iter(self._records))]
