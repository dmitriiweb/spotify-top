"""Async Spotify Web API client with bounded retries and safe pagination."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, urlsplit

import httpx

from spotify_analytics.core.config import Settings

_API_BASE = "https://api.spotify.com/v1"
_TOKEN_URL = "https://accounts.spotify.com/api/token"
_PAGE_SIZE = 50
_LIBRARY_CONTAINS_LIMIT = 40
_MAX_RETRIES = 3
_RETRY_BASE_SECONDS = 0.25
_MAX_RETRY_WAIT_SECONDS = 30.0


class SpotifyError(Exception):
    """Safe error raised for Spotify API and OAuth failures.

    Examples:
        raise SpotifyError("Spotify request failed")
    """

    def __init__(self, message: str = "Spotify request failed") -> None:
        super().__init__(message)


@dataclass
class TokenSet:
    """Server-side OAuth token data.

    Examples:
        tokens = TokenSet("access-token", "refresh-token", time.time() + 3600)
    """

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float
    _generation: int = field(default=0, init=False, repr=False, compare=False)
    _refresh_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, init=False, repr=False, compare=False
    )


class SpotifyClient:
    """Perform Spotify requests using a supplied HTTP client.

    Examples:
        client = SpotifyClient(http, settings)
        account_id = await client.current_account()
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        settings: Settings,
        tokens: TokenSet | None = None,
    ) -> None:
        self.http = http
        self.settings = settings
        self.tokens = tokens

    async def exchange_code(self, code: str, verifier: str) -> TokenSet:
        """Exchange a PKCE authorization code and store the resulting tokens."""
        response = await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.settings.redirect_uri,
                "client_id": self.settings.spotify_client_id or "",
                "code_verifier": verifier,
            }
        )
        tokens = self._parse_tokens(response, None)
        self.tokens = tokens
        return tokens

    async def current_account(self) -> str:
        """Return the current account's immutable ID, falling back to its ID."""
        payload = await self._api_get(f"{_API_BASE}/me")
        if not isinstance(payload, dict):
            raise SpotifyError("Spotify returned an invalid account profile")
        account_id = payload.get("account_id")
        if not isinstance(account_id, str) or not account_id:
            account_id = payload.get("id")
        if not isinstance(account_id, str) or not account_id:
            raise SpotifyError("Spotify returned an invalid account profile")
        return account_id

    async def artist_follow_status(self, artist_ids: list[str]) -> dict[str, bool]:
        """Return the current account's follow status for each artist ID."""
        status: dict[str, bool] = {}
        for start in range(0, len(artist_ids), _LIBRARY_CONTAINS_LIMIT):
            batch = artist_ids[start : start + _LIBRARY_CONTAINS_LIMIT]
            payload = await self._api_get(
                f"{_API_BASE}/me/library/contains",
                params={"uris": ",".join(f"spotify:artist:{id_}" for id_ in batch)},
            )
            if (
                not isinstance(payload, list)
                or len(payload) != len(batch)
                or any(type(value) is not bool for value in payload)
            ):
                raise SpotifyError("Spotify returned invalid artist follow status")
            status.update(zip(batch, payload, strict=True))
        return status

    async def iter_saved_tracks(self) -> AsyncIterator[list[dict]]:
        """Yield complete, validated saved-track pages in Spotify order."""
        offset = 0
        expected_total: int | None = None
        url = f"{_API_BASE}/me/tracks"
        params: dict[str, int] | None = {"limit": _PAGE_SIZE, "offset": 0}
        pages = 0
        while True:
            payload = await self._api_get(url, params=params)
            params = None
            if not isinstance(payload, dict):
                raise SpotifyError("Spotify returned an invalid saved-tracks page")
            items = payload.get("items")
            page_offset = payload.get("offset")
            total = payload.get("total")
            limit = payload.get("limit")
            next_url = payload.get("next")
            if (
                not isinstance(items, list)
                or any(not isinstance(item, dict) for item in items)
                or type(page_offset) is not int
                or type(total) is not int
                or type(limit) is not int
                or total < 0
                or limit != _PAGE_SIZE
                or page_offset != offset
                or len(items) > _PAGE_SIZE
                or offset + len(items) > total
                or (next_url is not None and not isinstance(next_url, str))
            ):
                raise SpotifyError("Spotify returned an invalid saved-tracks page")
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise SpotifyError("Spotify pagination changed during the request")
            pages += 1
            if pages > (expected_total // _PAGE_SIZE) + 2:
                raise SpotifyError("Spotify pagination exceeded safe bounds")
            yield items
            next_offset = offset + len(items)
            if next_url is None:
                if next_offset != expected_total:
                    raise SpotifyError(
                        "Spotify returned an incomplete saved-tracks library"
                    )
                return
            if not items or next_offset >= expected_total:
                raise SpotifyError("Spotify returned inconsistent pagination")
            self._validate_next_url(next_url, next_offset)
            url = next_url
            offset = next_offset

    async def _token_request(self, data: Mapping[str, str]) -> dict:
        retry_count = 0
        retry_wait = 0.0
        while True:
            try:
                response = await self.http.post(
                    _TOKEN_URL, data=data, follow_redirects=False
                )
            except httpx.HTTPError:
                if retry_count >= _MAX_RETRIES:
                    raise SpotifyError(
                        "Spotify token exchange failed after retries"
                    ) from None
                retry_wait += await self._wait_before_retry(
                    None, retry_count, retry_wait
                )
                retry_count += 1
                continue
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if retry_count >= _MAX_RETRIES:
                    raise SpotifyError("Spotify token exchange failed after retries")
                retry_wait += await self._wait_before_retry(
                    response, retry_count, retry_wait
                )
                retry_count += 1
                continue
            if response.status_code != 200:
                raise SpotifyError("Spotify token exchange failed")
            try:
                payload = response.json()
            except ValueError:
                raise SpotifyError("Spotify token exchange failed") from None
            if not isinstance(payload, dict):
                raise SpotifyError("Spotify returned an invalid token response")
            return payload

    @staticmethod
    def _parse_tokens(payload: dict, previous: TokenSet | None) -> TokenSet:
        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        expires_in = payload.get("expires_in")
        if (
            not isinstance(access_token, str)
            or not access_token
            or (refresh_token is not None and not isinstance(refresh_token, str))
            or type(expires_in) is not int
            or expires_in <= 0
        ):
            raise SpotifyError("Spotify returned invalid token data")
        if not refresh_token and previous is not None:
            refresh_token = previous.refresh_token
        if not isinstance(refresh_token, str) or not refresh_token:
            raise SpotifyError("Spotify returned invalid token data")
        return TokenSet(access_token, refresh_token, time.time() + expires_in)

    async def _refresh(
        self,
        tokens: TokenSet,
        expected_access_token: str,
        expected_generation: int,
    ) -> TokenSet:
        async with tokens._refresh_lock:
            if (
                tokens.access_token != expected_access_token
                or tokens._generation != expected_generation
            ):
                return tokens
            response = await self._token_request(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": tokens.refresh_token,
                    "client_id": self.settings.spotify_client_id or "",
                }
            )
            refreshed = self._parse_tokens(response, tokens)
            tokens.access_token = refreshed.access_token
            tokens.refresh_token = refreshed.refresh_token
            tokens.expires_at = refreshed.expires_at
            tokens._generation += 1
            return tokens

    async def _api_get(
        self, url: str, params: Mapping[str, int | str] | None = None
    ) -> object:
        if self.tokens is None:
            raise SpotifyError("Spotify authentication is unavailable")
        refreshed_after_401 = False
        retry_count = 0
        retry_wait = 0.0
        while True:
            tokens = self.tokens
            if tokens is None:
                raise SpotifyError("Spotify authentication is unavailable")
            if tokens.expires_at <= time.time():
                expected_access_token = tokens.access_token
                expected_generation = tokens._generation
                tokens = await self._refresh(
                    tokens, expected_access_token, expected_generation
                )
            request_access_token = tokens.access_token
            request_generation = tokens._generation
            try:
                response = await self.http.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {request_access_token}"},
                    follow_redirects=False,
                )
            except httpx.HTTPError:
                if retry_count >= _MAX_RETRIES:
                    raise SpotifyError("Spotify request failed after retries") from None
                retry_wait += await self._wait_before_retry(
                    None, retry_count, retry_wait
                )
                retry_count += 1
                continue
            if response.status_code == 401:
                if refreshed_after_401:
                    raise SpotifyError("Spotify authorization failed")
                await self._refresh(tokens, request_access_token, request_generation)
                refreshed_after_401 = True
                continue
            if response.status_code == 403:
                raise SpotifyError("Spotify denied access to this resource")
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if retry_count >= _MAX_RETRIES:
                    raise SpotifyError("Spotify request failed after retries")
                retry_wait += await self._wait_before_retry(
                    response, retry_count, retry_wait
                )
                retry_count += 1
                continue
            if not 200 <= response.status_code < 300:
                raise SpotifyError("Spotify request failed")
            try:
                return response.json()
            except ValueError:
                raise SpotifyError("Spotify returned an invalid response") from None

    async def _wait_before_retry(
        self,
        response: httpx.Response | None,
        retry_count: int,
        total_wait: float,
    ) -> float:
        delay = _RETRY_BASE_SECONDS * (2**retry_count)
        if response is not None:
            header = response.headers.get("Retry-After")
            if header:
                try:
                    retry_after = float(header)
                    if math.isfinite(retry_after) and retry_after >= 0:
                        delay = max(delay, retry_after)
                    else:
                        delay = _RETRY_BASE_SECONDS * (2**retry_count)
                except ValueError:
                    try:
                        retry_at = parsedate_to_datetime(header)
                        if retry_at.tzinfo is None:
                            retry_at = retry_at.replace(tzinfo=UTC)
                        delay = max(
                            delay,
                            max(
                                0.0,
                                (retry_at - datetime.now(UTC)).total_seconds(),
                            ),
                        )
                    except (TypeError, ValueError, OverflowError):
                        delay = _RETRY_BASE_SECONDS * (2**retry_count)
        if delay > _MAX_RETRY_WAIT_SECONDS - total_wait:
            raise SpotifyError("Spotify retry delay exceeds the safe wait limit")
        await asyncio.sleep(delay)
        return delay

    @staticmethod
    def _validate_next_url(url: str, expected_offset: int) -> None:
        try:
            parsed = urlsplit(url)
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
            valid = (
                parsed.scheme == "https"
                and parsed.netloc == "api.spotify.com"
                and parsed.path == "/v1/me/tracks"
                and not parsed.username
                and not parsed.password
                and not parsed.fragment
                and set(query) == {"limit", "offset"}
                and query["limit"] == [str(_PAGE_SIZE)]
                and query["offset"] == [str(expected_offset)]
            )
        except (ValueError, UnicodeError):
            valid = False
        if not valid:
            raise SpotifyError("Spotify returned an unsafe pagination link")
