from __future__ import annotations

import ipaddress
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import QueryParams

from spotify_analytics.core.schemas import ErrorResponse, SyncStatusResponse
from spotify_analytics.spotify.auth import COOKIE_NAME, SessionStore, authorization_url
from spotify_analytics.spotify.client import SpotifyClient
from spotify_analytics.spotify.sync import SyncInProgress, SyncService

router = APIRouter()


@dataclass
class _Runtime:
    session_store: SessionStore
    sync_service: SyncService
    http_client_factory: Any


def _error(
    status: int, code: str, message: str, details: list[dict] | None = None
) -> JSONResponse:
    payload = ErrorResponse(
        error={"code": code, "message": message, "details": details or []}
    )
    return JSONResponse(payload.model_dump(mode="json"), status_code=status)


def _runtime(request: Request) -> _Runtime:
    runtime = getattr(request.app.state, "spotify_runtime", None)
    if runtime is None:
        settings = request.app.state.settings
        secret = settings.session_secret
        if not _secure_secret(secret):
            # OAuth stays disabled until a valid secret is configured.
            secret = secrets.token_urlsafe(32)
        runtime = _Runtime(
            session_store=SessionStore(secret),
            sync_service=SyncService(request.app.state.session_factory),
            http_client_factory=httpx.AsyncClient,
        )
        request.app.state.spotify_runtime = runtime
    return runtime


def _secure_secret(secret: str | None) -> bool:
    if not isinstance(secret, str):
        return False
    try:
        if len(secret.encode("utf-8")) < 32:
            return False
    except UnicodeEncodeError:
        return False
    normalized = secret.strip().lower()
    common_weak = {
        "changeme",
        "change-me",
        "secret",
        "development",
        "development-secret",
        "your-secret-here",
        "password",
        "passwordpasswordpasswordpassword",
    }
    return (
        bool(normalized)
        and normalized not in common_weak
        and len(set(normalized)) != 1
        and not normalized.startswith(
            ("change-me", "your-secret", "replace-me", "example")
        )
    )


def _redirect_uri_ready(uri: str | None) -> bool:
    if not isinstance(uri, str) or uri != uri.strip():
        return False
    try:
        parsed = urlsplit(uri)
        hostname = parsed.hostname
        port = parsed.port
        if parsed.fragment:
            return False
        if parsed.scheme == "https":
            return bool(
                hostname
                and parsed.netloc
                and not parsed.username
                and not parsed.password
            )
        if (
            parsed.scheme != "http"
            or not hostname
            or parsed.username
            or parsed.password
        ):
            return False
        if port == 0:
            return False
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _credentials_ready(settings: Any) -> bool:
    return bool(
        isinstance(settings.spotify_client_id, str)
        and settings.spotify_client_id.strip()
        and isinstance(settings.spotify_client_secret, str)
        and settings.spotify_client_secret.strip()
        and _secure_secret(settings.session_secret)
        and _redirect_uri_ready(settings.redirect_uri)
    )


def _cookie_secure(request: Request) -> bool:
    return urlsplit(str(request.base_url)).scheme.lower() == "https"


def _set_cookie(response: RedirectResponse, request: Request, cookie: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        cookie,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(request),
        path="/",
    )


def _query_error(request: Request, allowed: set[str]) -> JSONResponse | None:
    params: QueryParams = request.query_params
    keys = [key for key, _ in params.multi_items()]
    unknown = sorted(set(keys) - allowed)
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    details = [{"field": key, "message": "is not allowed"} for key in unknown]
    details.extend(
        {"field": key, "message": "must be supplied once"} for key in duplicates
    )
    if details:
        return _error(422, "invalid_query", "Request validation failed", details)
    return None


@router.get("/auth/login")
async def login(request: Request):
    invalid = _query_error(request, set())
    if invalid:
        return invalid
    settings = request.app.state.settings
    if not _credentials_ready(settings):
        return _error(
            503, "spotify_not_configured", "Spotify authentication is not configured"
        )
    runtime = _runtime(request)
    cookie, state, verifier = runtime.session_store.begin_login()
    try:
        target = authorization_url(settings, state, verifier)
    except Exception:
        return _error(500, "internal_error", "An unexpected error occurred")
    response = RedirectResponse(target, status_code=302)
    _set_cookie(response, request, cookie)
    return response


@router.get("/auth/callback")
async def callback(request: Request):
    invalid = _query_error(request, {"code", "state", "error"})
    if invalid:
        return invalid
    params = request.query_params
    state = params.get("state")
    runtime = _runtime(request)
    verifier = runtime.session_store.consume_login(
        request.cookies.get(COOKIE_NAME), state
    )
    if verifier is None:
        return _error(400, "invalid_oauth_state", "OAuth state is invalid or expired")
    denial = params.get("error")
    if denial is not None:
        return _error(400, "oauth_denied", "Spotify authorization was denied")
    code = params.get("code")
    if not code:
        return _error(
            422,
            "invalid_query",
            "Request validation failed",
            [{"field": "code", "message": "is required"}],
        )
    settings = request.app.state.settings
    if not _credentials_ready(settings):
        return _error(
            503, "spotify_not_configured", "Spotify authentication is not configured"
        )

    factory = runtime.http_client_factory
    try:
        async with factory() as http:
            client = SpotifyClient(http, settings)
            tokens = await client.exchange_code(code, verifier)
            account_id = await client.current_account()
        new_cookie = runtime.session_store.authenticate(
            request.cookies.get(COOKIE_NAME), tokens, account_id
        )
    except Exception:
        return _error(
            502, "spotify_unavailable", "Spotify authentication is unavailable"
        )
    response = RedirectResponse("/", status_code=302)
    _set_cookie(response, request, new_cookie)
    return response


@router.post("/api/sync")
async def start_sync(request: Request, background_tasks: BackgroundTasks):
    invalid = _query_error(request, set())
    if invalid:
        return invalid
    try:
        body = await request.body()
    except Exception:
        return _error(422, "invalid_query", "Request validation failed")
    if body:
        return _error(
            422,
            "invalid_query",
            "Request validation failed",
            [{"field": "body", "message": "must be empty"}],
        )
    runtime = _runtime(request)
    session = runtime.session_store.get(request.cookies.get(COOKIE_NAME))
    if session is None:
        return _error(401, "auth_required", "Spotify authentication is required")
    try:
        status = await run_in_threadpool(runtime.sync_service.start, session.account_id)
    except SyncInProgress:
        return _error(409, "sync_in_progress", "A Spotify sync is already running")
    except Exception:
        return _error(500, "internal_error", "An unexpected error occurred")
    background_tasks.add_task(
        _run_sync,
        runtime,
        request.app.state.settings,
        session,
    )
    try:
        response = SyncStatusResponse.model_validate(status)
    except (ValidationError, TypeError):
        return _error(500, "internal_error", "An unexpected error occurred")
    return JSONResponse(response.model_dump(mode="json"), status_code=202)


@router.get("/api/sync/status")
async def sync_status(request: Request):
    invalid = _query_error(request, set())
    if invalid:
        return invalid
    try:
        status = SyncStatusResponse.model_validate(
            await run_in_threadpool(_runtime(request).sync_service.status)
        )
    except Exception:
        return _error(500, "internal_error", "An unexpected error occurred")
    return JSONResponse(status.model_dump(mode="json"))


async def _run_sync(runtime: _Runtime, settings: Any, session: Any) -> None:
    try:
        async with runtime.http_client_factory() as http:
            client = SpotifyClient(http, settings, tokens=session.tokens)
            await runtime.sync_service.run(client, session.account_id)
    except Exception:
        # The service normally persists safe errors itself. Avoid leaking any
        # unexpected route-boundary failure into logs or response content.
        try:
            await run_in_threadpool(runtime.sync_service.fail, "Spotify sync failed")
        except Exception:
            pass
