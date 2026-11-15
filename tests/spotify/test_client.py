import asyncio
from urllib.parse import parse_qs

import httpx
import pytest

from spotify_analytics.core.config import Settings
from spotify_analytics.spotify.client import SpotifyClient, SpotifyError, TokenSet


def make_client(handler, tokens=None):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(
        spotify_client_id="client-id", redirect_uri="http://localhost/callback"
    )
    return SpotifyClient(http, settings, tokens), http


def token_payload(access="new-access", refresh="new-refresh"):
    payload = {"access_token": access, "expires_in": 3600}
    if refresh is not None:
        payload["refresh_token"] = refresh
    return payload


async def test_code_exchange_and_refresh_preserves_rotating_refresh_token():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/api/token"):
            return httpx.Response(200, json=token_payload())
        return httpx.Response(200, json={"account_id": "immutable-account"})

    client, http = make_client(handler)
    try:
        tokens = await client.exchange_code("auth-code", "verifier-value")
        assert tokens.access_token == "new-access"
        assert tokens.refresh_token == "new-refresh"
        assert await client.current_account() == "immutable-account"
        form = parse_qs(requests[0].content.decode())
        assert form["grant_type"] == ["authorization_code"]
        assert form["code"] == ["auth-code"]
        assert form["code_verifier"] == ["verifier-value"]
        assert "client_secret" not in form
        assert requests[1].headers["Authorization"] == "Bearer new-access"
    finally:
        await http.aclose()


async def test_expired_access_token_refreshes_and_keeps_refresh_token_when_omitted():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/api/token"):
            return httpx.Response(
                200, json=token_payload(access="rotated", refresh=None)
            )
        return httpx.Response(200, json={"id": "legacy-account"})

    old_tokens = TokenSet("old-access", "long-lived-refresh", 0)
    client, http = make_client(handler, old_tokens)
    try:
        assert await client.current_account() == "legacy-account"
        assert client.tokens.access_token == "rotated"
        assert client.tokens.refresh_token == "long-lived-refresh"
        form = parse_qs(requests[0].content.decode())
        assert form["grant_type"] == ["refresh_token"]
        assert form["refresh_token"] == ["long-lived-refresh"]
    finally:
        await http.aclose()


async def test_401_refreshes_once_and_403_is_not_retried(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    api_calls = 0
    refresh_calls = 0

    def handler(request):
        nonlocal api_calls, refresh_calls
        if request.url.path.endswith("/api/token"):
            refresh_calls += 1
            return httpx.Response(200, json=token_payload())
        api_calls += 1
        if api_calls == 1:
            return httpx.Response(401)
        return httpx.Response(403, text="SECRET RESPONSE")

    client, http = make_client(handler, TokenSet("expired", "refresh", 9999999999))
    try:
        with pytest.raises(SpotifyError) as error:
            await client.current_account()
        assert str(error.value) == "Spotify denied access to this resource"
        assert api_calls == 2
        assert refresh_calls == 1
        assert "SECRET RESPONSE" not in str(error.value)
    finally:
        await http.aclose()


async def test_full_pagination_and_empty_library():
    seen = []

    def handler(request):
        seen.append(request)
        offset = int(request.url.params.get("offset", "0"))
        if offset == 0:
            return httpx.Response(
                200,
                json={
                    "offset": 0,
                    "limit": 50,
                    "total": 51,
                    "items": [{"id": str(index)} for index in range(50)],
                    "next": "https://api.spotify.com/v1/me/tracks?limit=50&offset=50",
                },
            )
        return httpx.Response(
            200,
            json={
                "offset": 50,
                "limit": 50,
                "total": 51,
                "items": [{"id": "50"}],
                "next": None,
            },
        )

    client, http = make_client(handler, TokenSet("access", "refresh", 9999999999))
    try:
        pages = [page async for page in client.iter_saved_tracks()]
        assert [len(page) for page in pages] == [50, 1]
        assert len(seen) == 2
        assert dict(seen[0].url.params) == {"limit": "50", "offset": "0"}
        assert dict(seen[1].url.params) == {"limit": "50", "offset": "50"}
    finally:
        await http.aclose()

    empty_client, empty_http = make_client(
        lambda request: httpx.Response(
            200,
            json={"offset": 0, "limit": 50, "total": 0, "items": [], "next": None},
        ),
        TokenSet("access", "refresh", 9999999999),
    )
    try:
        assert [page async for page in empty_client.iter_saved_tracks()] == [[]]
    finally:
        await empty_http.aclose()


async def test_pagination_rejects_hostile_and_inconsistent_pages():
    payload = {
        "offset": 0,
        "limit": 50,
        "total": 2,
        "items": [{"id": "1"}],
        "next": "https://attacker.test/v1/me/tracks?limit=50&offset=1",
    }
    client, http = make_client(
        lambda request: httpx.Response(200, json=payload),
        TokenSet("access", "refresh", 9999999999),
    )
    try:
        pages = client.iter_saved_tracks()
        assert await anext(pages) == [{"id": "1"}]
        with pytest.raises(SpotifyError, match="unsafe pagination"):
            await anext(pages)
    finally:
        await http.aclose()

    bad_page = dict(payload, next=None)
    inconsistent, inconsistent_http = make_client(
        lambda request: httpx.Response(200, json=bad_page),
        TokenSet("access", "refresh", 9999999999),
    )
    try:
        with pytest.raises(SpotifyError, match="incomplete"):
            _ = [page async for page in inconsistent.iter_saved_tracks()]
    finally:
        await inconsistent_http.aclose()


async def test_pagination_rejects_total_changes():
    def handler(request):
        offset = int(request.url.params["offset"])
        total = 2 if offset == 0 else 3
        return httpx.Response(
            200,
            json={
                "offset": offset,
                "limit": 50,
                "total": total,
                "items": [{"id": str(offset)}],
                "next": (
                    "https://api.spotify.com/v1/me/tracks?limit=50&offset=1"
                    if offset == 0
                    else None
                ),
            },
        )

    client, http = make_client(handler, TokenSet("access", "refresh", 9999999999))
    try:
        with pytest.raises(SpotifyError, match="changed"):
            _ = [page async for page in client.iter_saved_tracks()]
    finally:
        await http.aclose()


async def test_429_5xx_network_retry_bounds_and_retry_after(monkeypatch):
    delays = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        if count == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        if count == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"account_id": "account"})

    client, http = make_client(handler, TokenSet("access", "refresh", 9999999999))
    try:
        assert await client.current_account() == "account"
        assert delays == [2.0, 0.5]
        assert count == 3
    finally:
        await http.aclose()

    exhausted_calls = 0

    def failing_handler(request):
        nonlocal exhausted_calls
        exhausted_calls += 1
        return httpx.Response(503, text="do not leak")

    exhausted, exhausted_http = make_client(
        failing_handler, TokenSet("access-secret", "refresh-secret", 9999999999)
    )
    try:
        with pytest.raises(SpotifyError) as error:
            await exhausted.current_account()
        assert exhausted_calls == 4
        assert "do not leak" not in str(error.value)
        assert "access-secret" not in repr(error.value)
    finally:
        await exhausted_http.aclose()


async def test_network_failure_retries_and_oversized_retry_after_fails(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    network_calls = 0

    def network_handler(request):
        nonlocal network_calls
        network_calls += 1
        if network_calls == 1:
            raise httpx.ConnectError("SECRET NETWORK DETAIL", request=request)
        return httpx.Response(200, json={"id": "account"})

    client, http = make_client(
        network_handler, TokenSet("access", "refresh", 9999999999)
    )
    try:
        assert await client.current_account() == "account"
        assert network_calls == 2
    finally:
        await http.aclose()

    slow, slow_http = make_client(
        lambda request: httpx.Response(429, headers={"Retry-After": "31"}),
        TokenSet("access", "refresh", 9999999999),
    )
    try:
        with pytest.raises(SpotifyError, match="wait limit"):
            await slow.current_account()
    finally:
        await slow_http.aclose()


async def test_supplied_http_client_is_not_owned_or_closed():
    client, http = make_client(
        lambda request: httpx.Response(200, json={"account_id": "account"}),
        TokenSet("access", "refresh", 9999999999),
    )
    await client.current_account()
    assert not http.is_closed
    await http.aclose()


async def no_sleep(delay):
    return None


async def test_refresh_mutates_shared_session_tokens_across_clients():
    refresh_requests = []

    def handler(request):
        if request.url.path.endswith("/api/token"):
            form = parse_qs(request.content.decode())
            refresh_requests.append(form["refresh_token"][0])
            if len(refresh_requests) == 1:
                return httpx.Response(
                    200,
                    json=token_payload(
                        access="rotated-access", refresh="rotated-refresh"
                    ),
                )
            return httpx.Response(
                200, json=token_payload(access="latest-access", refresh=None)
            )
        return httpx.Response(200, json={"account_id": "account"})

    tokens = TokenSet("expired-access", "initial-refresh", 0)
    first, first_http = make_client(handler, tokens)
    second, second_http = make_client(handler, tokens)
    try:
        await first.current_account()
        assert tokens.access_token == "rotated-access"
        assert tokens.refresh_token == "rotated-refresh"

        tokens.expires_at = 0
        await second.current_account()

        assert first.tokens is tokens
        assert second.tokens is tokens
        assert tokens.access_token == "latest-access"
        assert tokens.refresh_token == "rotated-refresh"
        assert refresh_requests == ["initial-refresh", "rotated-refresh"]
    finally:
        await first_http.aclose()
        await second_http.aclose()


async def test_concurrent_clients_share_one_refresh(monkeypatch):
    refresh_requests = 0

    async def token_request(self, data):
        nonlocal refresh_requests
        refresh_requests += 1
        await asyncio.sleep(0)
        return token_payload(access="shared-access", refresh="shared-refresh")

    monkeypatch.setattr(SpotifyClient, "_token_request", token_request)

    def handler(request):
        return httpx.Response(200, json={"account_id": "account"})

    tokens = TokenSet("expired-access", "refresh", 0)
    first, first_http = make_client(handler, tokens)
    second, second_http = make_client(handler, tokens)
    try:
        assert await asyncio.gather(
            first.current_account(), second.current_account()
        ) == ["account", "account"]
        assert refresh_requests == 1
        assert tokens.access_token == "shared-access"
    finally:
        await first_http.aclose()
        await second_http.aclose()


async def test_token_endpoint_retries_transient_failures_with_retry_after(monkeypatch):
    delays = []
    monkeypatch.setattr(asyncio, "sleep", lambda delay: _record_delay(delays, delay))
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("secret network detail", request=request)
        if calls == 2:
            return httpx.Response(429, headers={"Retry-After": "0"})
        if calls == 3:
            return httpx.Response(503, headers={"Retry-After": "bad"})
        return httpx.Response(200, json=token_payload())

    client, http = make_client(handler)
    try:
        await client.exchange_code("code", "verifier")
        assert calls == 4
        assert delays == [0.25, 0.5, 1.0]
    finally:
        await http.aclose()


async def test_token_endpoint_retry_exhaustion_and_invalid_grant_no_retry(monkeypatch):
    delays = []
    monkeypatch.setattr(asyncio, "sleep", lambda delay: _record_delay(delays, delay))
    calls = 0

    def unavailable(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="secret")

    client, http = make_client(unavailable)
    try:
        with pytest.raises(SpotifyError, match="after retries"):
            await client.exchange_code("code", "verifier")
        assert calls == 4
        assert delays == [0.25, 0.5, 1.0]
    finally:
        await http.aclose()

    for status in (400, 401):
        invalid_calls = 0

        def invalid_grant(request, status=status):
            nonlocal invalid_calls
            invalid_calls += 1
            return httpx.Response(status, text="invalid_grant")

        client, http = make_client(invalid_grant)
        try:
            with pytest.raises(SpotifyError, match="token exchange failed"):
                await client.exchange_code("code", "verifier")
            assert invalid_calls == 1
        finally:
            await http.aclose()


async def _record_delay(delays, delay):
    delays.append(delay)
