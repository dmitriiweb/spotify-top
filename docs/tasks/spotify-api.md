# Spotify API task (future phase)

## Scope and ownership

Implement only `src/spotify_analytics/spotify/` and `tests/spotify/`. The shared contract is [../contracts.md](../contracts.md). It is frozen together with `core/*`, `pyproject.toml`, `uv.lock`, and `main.py` after Phase 0. Obtain explicit coordination before proposing any shared change; do not edit those files or the analytics, web, or other owner's test directories. Do not introduce an alternate schema, settings object, configuration path, or response shape.

Phase 0 has no Spotify integration or `/auth/*` and `/api/sync*` routes. This task implements the real future OAuth and sync behavior; do not replace unavailable integration with fake responses or stubs.

## Exact integration seam

Provide `spotify/routes.py` exporting `router`, a FastAPI `APIRouter` containing `/auth/login`, `/auth/callback`, `/api/sync`, and `/api/sync/status`. The dashboard owner includes this router in the app. Use the shared `Request.app.state` rather than modifying application creation: `app.state.settings` contains `Settings`; `app.state.engine` and `app.state.session_factory` are supplied by the app lifespan. `Settings` fields are `spotify_client_id`, `spotify_client_secret`, `redirect_uri`, and optional `session_secret` in addition to common settings. Do not require credentials at import or for demo/local analytics. A real OAuth flow requires credentials and a securely configured session secret.

Use `spotify/` for Spotify-specific HTTP client, OAuth/session, pagination, mapping, and reconciliation implementation. Use the exact shared models and Pydantic schemas from `core`; do not create parallel models or edit shared code. FastAPI sync routes may use synchronous DB sessions for blocking DB work; Spotify network I/O uses `httpx` async client behavior. Always close clients and sessions.

## Frozen route behavior

- `GET /auth/login`: no query parameters. Redirect 302 to Spotify authorization with state, PKCE, and `user-library-read`. If client credentials are missing, return the standard `ErrorResponse` with 503 `spotify_not_configured`.
- `GET /auth/callback`: successful callback requires `code` and `state`; return 302 to `/` after successful exchange. Spotify denial uses `error` and `state` and returns 400 `oauth_denied`; invalid/missing or mismatched state returns 400 `invalid_oauth_state`; exchange failure returns 502 `spotify_unavailable`. Keep tokens server-side. Session cookie is `HttpOnly`, `SameSite=Lax`, and `Secure` under HTTPS. Never put tokens, secrets, or authorization codes in frontend content or logs.
- `POST /api/sync`: accepts no body. Unknown query keys or unexpected JSON body return 422 `invalid_query`. Missing authenticated Spotify session returns 401 `auth_required`. Already-running sync returns 409 `sync_in_progress`. Accepted work returns 202 and `SyncStatusResponse` with `status: "running"`.
- `GET /api/sync/status`: no supported query parameters; unknown query keys return 422. Returns `SyncStatusResponse` and on a fresh database returns `account_id: null`, `status: "idle"`, null timestamp/error, and all counts zero.

Every error is `{"error":{"code":"...","message":"...","details":[]}}`; validation may populate details. Do not leak tokens, traces or secrets. See [the contract](../contracts.md) for complete field definitions, examples, and shared error rules.

## Sync correctness

Fetch the full saved-track snapshot, including all Spotify pages, before publishing changes. Reconcile the library atomically: a failed fetch, mapping, or database operation must leave the previous queryable library intact. Only a complete successful snapshot updates the active account/library and successful-sync timestamp. A removed saved item deletes its `SavedTrack` relation only; shared track, artist, album, and credit metadata must not be deleted merely because that item is no longer saved. Account switching replaces the old library atomically and never mixes accounts. Persist sync status and durable error through `SyncState`; do not mark failure as success or erase the previous successful timestamp on a failed attempt. Counts describe persisted saved tracks, distinct artists/albums, and processed tracks as defined by the sync implementation and contract. Handle retries/pagination according to Spotify API semantics without exposing partial snapshots.

## Required tests

Add tests only under `tests/spotify/`. Cover:

- Login URL construction, state validation, PKCE, scope, redirects, and missing credentials; verify cookie flags, HTTPS Secure behavior, denial, invalid state, exchange failure, and that tokens/secrets do not appear in responses or captured logs.
- All callback and sync HTTP status/error shapes, missing authentication, concurrent/in-progress sync, rejection of request body and unknown query keys, status for a fresh database, and serialization matching shared schemas.
- Multi-page saved-track retrieval and mapping of album, ordered artist credits, nullable metadata, explicit flag, duration, and aware UTC save timestamps.
- Atomic successful reconciliation, removed saved items without deleting shared metadata, account replacement without account mixing, durable success counts/state, and failure preserving the previous library and last successful timestamp.
- Session/client cleanup and expected network or database failure handling without partial persistence.

Tests must use controlled HTTP/database fixtures, not Spotify credentials or live network calls. Do not edit app composition or shared fixtures/contracts to make these tests pass; request coordination if the seam proves insufficient.
