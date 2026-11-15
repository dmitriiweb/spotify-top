# Dashboard and web integration task (future phase)

## Scope and ownership

Implement the future web application integration only in `src/spotify_analytics/web/` and `tests/web/`. Shared `core/*`, `pyproject.toml`, `uv.lock`, `main.py`, and `docs/contracts.md` are frozen after Phase 0. Obtain explicit coordination before any shared change. Do not edit `spotify/`, `analytics/`, their tests, shared schemas/settings, entry point, dependencies, lockfile, or contracts. Do not add parallel configuration or response models.

Phase 0 app behavior remains limited to `/` and `/health`. Do not expose fake future endpoints or 501 placeholders. Complete future integration only when the Spotify and analytics modules are present.

## Composition and exact seams

Keep `web/app.py` as the composition root. Preserve `create_app(settings: Settings | None = None) -> FastAPI`, lifespan database initialization/disposal, `app.state.engine`, `app.state.session_factory`, and `app.state.settings`. Do not create a global engine or perform database/network I/O on import. Use `request.app.state.session_factory` for per-request SQLAlchemy sessions and close each session after use.

Mount the future `spotify.routes.router`, which owns `/auth/*` and `/api/sync*`. Dashboard/web owns the analytical HTTP adapters and calls `AnalyticsService(session: Session)` from `analytics/service.py`; it does not reimplement calculations or Spotify operations. Exact service keyword interfaces:

```python
overview(filters: AnalyticsFilter)
artists(filters: AnalyticsFilter, page: PageParams, sort: ArtistSort)
artist_detail(artist_id: str, filters: AnalyticsFilter, page: PageParams, sort: TrackSort)
tracks(filters: AnalyticsFilter, page: PageParams, sort: TrackSort)
timeline(filters: AnalyticsFilter, interval: Literal["month", "year"])
distribution(filters: AnalyticsFilter, dimension: Literal["release_decade", "duration", "explicit"])
insights(filters: AnalyticsFilter)
```

The return values are the corresponding shared Pydantic models. Route parsing must construct those shared filter/page/sort types and let their validation govern input. Unknown query parameters are 422; do not silently ignore them. For each analytical route, return exactly its contract response model. Map the built-in `LookupError(artist_id)` raised by `AnalyticsService.artist_detail` only for an absent persisted artist to 404 `not_found`; do not convert other service failures or a known artist with zero matching tracks into 404. Sync status and auth errors use the shared `ErrorResponse` shape. Do not require authentication to read locally persisted demo/library data.

Compose the future routes exactly as frozen: `GET /api/overview`; `GET /api/artists` with `limit`, `offset`, `sort`; `GET /api/artists/{artist_id}` with `limit`, `offset`, `sort`; `GET /api/tracks` with `limit`, `offset`, `sort`; `GET /api/timeline` with `interval`; `GET /api/distribution` with `dimension`; `GET /api/insights`; plus Spotify router paths. Common filters are `added_from`, `added_to`, `artist_id`, `album_id`, `q`, `explicit`. Defaults and all response shapes, status codes, date/tie/empty rules are in [the frozen contract](../contracts.md). Do not duplicate or diverge from those rules in web code.

## Dashboard presentation

The landing page in Phase 0 explains that only `/` and `/health` work and may show health status using the existing packaged CSS and vanilla JavaScript. In the future dashboard, present real results from the service and sync/auth APIs; do not imply unavailable results or use fixture-shaped fake data. Bundle Plotly.js as a local application asset and serve it locally. Do not use a CDN, remote script, or require an internet connection to render charts. Keep static assets packaged with the application. Render user/Spotify text safely; do not inject untrusted values as HTML. Never send OAuth tokens or secrets to browser JavaScript or logs.

## Error and security behavior

Return the shared standard error JSON for route failures. Validation errors are 422 `invalid_query` with field details; unknown artist is 404 `not_found`; generic unexpected failure is 500 `internal_error` without internal exception details. Spotify missing configuration and availability errors are 503/502 respectively. Do not catch failures and convert them to empty analytics. OAuth cookie requirements and sync atomicity are owned by the Spotify module and defined in the shared contract. Never expose secrets in errors, HTML, URLs other than the provider authorization redirect, or browser bundles.

## Required tests

Add tests only under `tests/web/`. Cover:

- App construction with explicit settings and lifespan: database init, app-state settings/engine/session factory, disposal, `/health` issuing a successful DB check, and `/` phase-boundary content/link.
- Analytical route query parsing/defaults, all filters, all sort and interval/dimension parameters, bounds/unknown-key rejection, exact response serialization from service models, pagination, and 404/empty artist-detail distinction.
- Session lifecycle and service invocation with exact keyword names, ensuring route adapters do not perform analytics themselves or leak sessions.
- Inclusion of the Spotify router without changing its ownership; auth/sync response/error integration and local analytics readability without Spotify authentication.
- Error status/shape behavior without leaking tracebacks or secrets; no fake successful analytics when a dependency fails.
- Static dashboard asset delivery and offline chart rendering using the bundled Plotly.js asset with no CDN/network script dependency; safe rendering of user-controlled names/text.

Use test doubles at module/service/HTTP boundaries as needed, but do not create production stubs or modify other modules to satisfy the tests. Coordinate with module owners if a real service/router interface mismatch is found.
