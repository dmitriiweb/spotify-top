import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

from spotify_analytics.core.config import Settings
from spotify_analytics.spotify.auth import (
    COOKIE_NAME,
    AuthSession,
    SessionStore,
    authorization_url,
)
from spotify_analytics.spotify.client import TokenSet


def test_authorization_url_uses_s256_and_only_library_scope():
    settings = Settings(
        spotify_client_id="client-id", redirect_uri="https://app.test/callback"
    )
    verifier = "a" * 64

    parsed = urlsplit(authorization_url(settings, "csrf-state", verifier))
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.spotify.com"
    assert parsed.path == "/authorize"
    expected_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert query == {
        "client_id": ["client-id"],
        "response_type": ["code"],
        "redirect_uri": ["https://app.test/callback"],
        "state": ["csrf-state"],
        "code_challenge_method": ["S256"],
        "code_challenge": [expected_challenge],
        "scope": ["user-library-read"],
    }


def test_login_state_is_single_use_and_cookie_tampering_fails():
    store = SessionStore("a sufficiently long test secret")
    cookie, state, verifier = store.begin_login()

    assert COOKIE_NAME == "spotify_session"
    assert store.consume_login(cookie + "x", state) is None
    assert store.consume_login(cookie, "wrong") is None
    assert store.consume_login(cookie, state) == verifier
    assert store.consume_login(cookie, state) is None


def test_storage_capacity_evicts_old_sessions(monkeypatch):
    import spotify_analytics.spotify.auth as auth

    monkeypatch.setattr(auth, "_MAX_SESSIONS", 1)
    store = SessionStore("session-secret")
    first_cookie, first_state, _ = store.begin_login()
    second_cookie, second_state, _ = store.begin_login()

    assert store.consume_login(first_cookie, first_state) is None
    assert store.consume_login(second_cookie, second_state) is not None


def test_authentication_rotates_cookie_and_hides_token_repr():
    store = SessionStore("session-secret")
    cookie, _, _ = store.begin_login()
    tokens = TokenSet("ACCESS-SECRET", "REFRESH-SECRET", 123.0)

    authenticated_cookie = store.authenticate(cookie, tokens, "account-1")
    session = store.get(authenticated_cookie)

    assert authenticated_cookie != cookie
    assert store.get(cookie) is None
    assert isinstance(session, AuthSession)
    assert session.account_id == "account-1"
    assert "ACCESS-SECRET" not in repr(tokens)
    assert "REFRESH-SECRET" not in repr(tokens)
    assert "ACCESS-SECRET" not in repr(session)
    assert "REFRESH-SECRET" not in repr(session)
    assert "ACCESS-SECRET" not in authenticated_cookie


def test_login_and_authenticated_sessions_expire(monkeypatch):
    import spotify_analytics.spotify.auth as auth

    now = [100.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: now[0])
    store = SessionStore("session-secret")
    login_cookie, state, _ = store.begin_login()
    now[0] += auth._LOGIN_TTL_SECONDS
    assert store.consume_login(login_cookie, state) is None

    cookie, _, _ = store.begin_login()
    authenticated = store.authenticate(
        cookie, TokenSet("access", "refresh", 999.0), "account-1"
    )
    now[0] += auth._SESSION_TTL_SECONDS
    assert store.get(authenticated) is None


def test_login_state_rejects_non_ascii_without_raising():
    store = SessionStore("session-secret")
    cookie, _, _ = store.begin_login()

    assert store.consume_login(cookie, "malformed-\u2603") is None
    assert store.consume_login(cookie, "malformed-\ud800") is None
