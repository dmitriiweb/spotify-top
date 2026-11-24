# Frozen application contracts

This file is the shared interface for the next implementation phases. It is frozen after Phase 0. Any change to it, `core/*`, `pyproject.toml`, `uv.lock`, or `main.py` requires explicit coordination and agreement from the affected owners before editing. Do not change a shared contract to make one module easier to implement.

## Phase boundary

Phase 0 implements only `GET /` and `GET /health`. `/` is a Jinja landing page explaining Phase 0, setup and demo instructions, with a link to `/docs`; its packaged CSS and vanilla JavaScript may show health status. `/health` returns `{"status":"ok"}` after a database `SELECT 1`. Phase 0 has no Spotify credentials requirement, Spotify network calls, authentication, sync, analytics implementation, dashboard, or `/api/*` and `/auth/*` routes. The contracts below define future behavior only. Do not install placeholder routes, fake analytical responses, or 501 stubs in Phase 0.

## Runtime and configuration

`main.py` exposes `main()` with `--init-db` (initialize then exit), `--load-demo` (initialize and transactionally load demo data then exit), `--host` (default `127.0.0.1`), and `--port` (default `8000`). `uv run spotify-analytics` launches the application. `core/config.py` exposes `Settings` using `pydantic-settings`; it reads `.env` and environment variables with prefix `SPOTIFY_ANALYTICS_`. Do not require Spotify secrets for app startup, demo, or local analytics.

Settings are `database_url` default `sqlite:///./data/spotify-analytics.db`, `host` default `127.0.0.1`, `port` default `8000`, optional `spotify_client_id`, optional `spotify_client_secret`, `redirect_uri` default `http://127.0.0.1:8000/auth/callback`, and optional `session_secret` (`SPOTIFY_ANALYTICS_SESSION_SECRET`). Credentials and session secret are not required in Phase 0. Real OAuth requires configured credentials and a securely configured session secret. Never log or expose secrets.

`web/app.py` exposes `create_app(settings: Settings | None = None) -> FastAPI`. Its lifespan initializes the database, stores `app.state.settings`, `app.state.engine`, and `app.state.session_factory`, and disposes the engine on shutdown. There is no global engine or I/O on import. Future modules use these app-state values instead of editing app setup or inventing shared configuration. `GET /health` returns `{"status":"ok"}` only after `SELECT 1`.

## Module ownership and coordination

After Phase 0, `core/*`, `pyproject.toml`, `uv.lock`, `main.py`, and this file are frozen. Any required shared change needs explicit coordination and agreement before editing. Spotify owns `spotify/` and `tests/spotify/`; analytics owns `analytics/` and `tests/analytics/`; dashboard/web owns `web/` and `tests/web/`. The future `spotify/routes.py` exports `router` for `/auth/*` and `/api/sync*`. The future `analytics/service.py` exports `AnalyticsService(session: Session)` with the methods and exact keyword arguments documented in [the analytics task](tasks/analytics.md). Web composition mounts the Spotify router and adapts analytical HTTP requests to that service. Each implementer edits only its owned module/test directories; coordinate explicitly rather than cross-editing files. Missing future modules are not a reason to fake functionality.

## Database contract

The SQLAlchemy models live in `spotify_analytics.core.models`. IDs and names are strings; artist names are not unique. Preserve Spotify IDs and allow distinct artist IDs with the same name. A database represents one active account/library; never mix accounts. Switching accounts must atomically replace the library. `SyncState` describes that active account; the demo account ID is `demo`.

| Model / table | Fields and constraints |
|---|---|
| `Track` | `id: str` primary key; `name: str`; `duration_ms: int` with `duration_ms >= 0`; `album_id: str \| None` foreign key to `Album.id`, `ON DELETE SET NULL`; `explicit: bool`; `spotify_url: str \| None`. |
| `Artist` | `id: str` primary key; `name: str`; `image_url: str \| None`; `spotify_url: str \| None`. |
| `Album` | `id: str` primary key; `name: str`; `release_date: str \| None`; `cover_url: str \| None`. Preserve Spotify release-date precision as `YYYY`, `YYYY-MM`, or `YYYY-MM-DD`; unknown is null. |
| `TrackArtist` | `track_id: str` primary key and FK to `Track.id`; `artist_id: str` primary key and FK to `Artist.id`; `artist_order: int >= 0`; unique constraint on `(track_id, artist_order)`. Deleting a track or artist cascades to credits. The pair `(track_id, artist_id)` is the composite primary key. |
| `SavedTrack` | `track_id: str` primary key and FK to `Track.id` with delete cascade; `added_at: datetime`, timezone-aware UTC. Naive timestamps are rejected. SQLite storage must round-trip aware UTC values. |
| `SyncState` | `account_id: str` primary key; `last_successful_sync: datetime \| None`, aware UTC; `status: idle \| running \| succeeded \| failed`, default `idle`; `error: str \| None`; `saved_tracks`, `artists`, `albums`, `processed_tracks`: nonnegative integer counts, each default `0`. |

Use `CHECK` constraints for nonnegative track duration, artist order and sync counts. Index saved-track `added_at` and `TrackArtist.artist_id`. Foreign keys must be enabled for SQLite connections. The initial database has no `SyncState` row; its API status is represented as the fresh-database values below. Demo fixture timestamps are stable, not based on the current clock. Demo loading is idempotent, owns no transaction, and must refuse to overwrite a nonempty non-demo library.

## Shared query models and rules

All analytical GET endpoints accept these filter query parameters. Unknown query parameters are rejected with 422. The endpoint-specific parameters are listed below. Dates are inclusive UTC calendar days compared to `SavedTrack.added_at`, never the album release date. `added_from` later than `added_to` is invalid. `artist_id` and `album_id` cannot be empty. `q` is trimmed; empty becomes null; maximum length is 200. `q` is a Unicode-casefolded substring search over track name, album name, and credited artist names. All provided filters intersect. `artist_id` first selects tracks, but all artists credited on those selected tracks remain eligible in artist results. Artist detail intersects its artist scope with these filters; it does not replace them.

| Model | Fields / defaults |
|---|---|
| `AnalyticsFilter` | `added_from: date \| None = null`, `added_to: date \| None = null`, `artist_id: str \| None = null`, `album_id: str \| None = null`, `q: str \| None = null`, `explicit: bool \| None = null`. |
| `PageParams` | `limit: int = 50` (1–200), `offset: int = 0` (>= 0). An offset past the last item returns `items: []` and the unchanged total. |
| `ArtistSort` | `saved_tracks_desc` (default), `name_asc`. |
| `TrackSort` | `added_at_desc` (default), `added_at_asc`, `name_asc`, `duration_desc`. |

Artists are counted by distinct saved track IDs per credited artist. Ranking is global over the complete filtered artist set before pagination. Rank order is saved-track count descending, Unicode-casefolded name ascending, then ID ascending; ranks are unique sequential integers. `name_asc` changes page item order to casefolded name ascending then ID ascending, but each item retains its ranking rank. Track sort ties use ID ascending; `name_asc` uses casefolded name. Share is `artist.saved_tracks / filtered total_tracks`, or zero if there are no filtered tracks.

All response objects use Pydantic models in `spotify_analytics.core.schemas`; every model rejects extra fields. Dates serialize as ISO 8601 dates. Response datetimes must be timezone-aware UTC. Counts and durations are nonnegative. Field names and nullability below are exact.

## Analytical response shapes and examples

Examples are representative valid JSON; values are illustrative. No endpoint listed here is installed in Phase 0.

### `GET /api/overview` — 200 `OverviewResponse`

Fields: `filters: AnalyticsFilter`; `total_tracks`, `total_artists`, `total_albums`, `total_duration_ms: int`; `first_saved_at`, `last_saved_at: datetime | null`.

```json
{"filters":{"added_from":null,"added_to":null,"artist_id":null,"album_id":null,"q":null,"explicit":null},"total_tracks":2,"total_artists":3,"total_albums":1,"total_duration_ms":420000,"first_saved_at":"2024-01-10T09:30:00Z","last_saved_at":"2024-02-03T12:00:00Z"}
```

Empty library: all counts and duration are zero; both dates are null.

### `GET /api/artists` — 200 `ArtistPage`

Additional query parameters: `limit`, `offset`, `sort: ArtistSort` (default `saved_tracks_desc`). Fields: `filters`; `items: ArtistRanking[]`; `total`, `limit`, `offset: int`. `ArtistRanking` has `artist: ArtistSummary`, `saved_tracks: int`, `rank: int >= 1`, `share: float` in `[0,1]`. `ArtistSummary` has `id`, `name: str`, `image_url`, `spotify_url: str | null`.

```json
{"filters":{"added_from":null,"added_to":null,"artist_id":null,"album_id":null,"q":null,"explicit":null},"items":[{"artist":{"id":"artist-1","name":"Aster","image_url":null,"spotify_url":"https://open.spotify.com/artist/artist-1"},"saved_tracks":2,"rank":1,"share":1.0}],"total":1,"limit":50,"offset":0}
```

Empty result: `items: []`, `total: 0`; no synthetic artist rows.

### `GET /api/artists/{artist_id}` — 200 `ArtistDetailResponse`

Additional query parameters: `limit`, `offset`, `sort: TrackSort` (default `added_at_desc`). Fields: `filters`; `artist: ArtistSummary`; `saved_tracks`, `total_duration_ms: int`; `rank: int | null`; `tracks: TrackPage`. Rank is null when the known artist has no matching saved tracks. `TrackPage` has `filters`, `items: SavedTrackResponse[]`, `total`, `limit`, `offset`. A `SavedTrackResponse` has `id`, `name: str`; `duration_ms: int`; `explicit: bool`; `spotify_url: str | null`; `album: AlbumSummary | null`; `artists: ArtistSummary[]` in credit order; `added_at: datetime`. `AlbumSummary` has `id`, `name: str`, `release_date`, `cover_url: str | null`.

```json
{"filters":{"added_from":null,"added_to":null,"artist_id":null,"album_id":null,"q":null,"explicit":null},"artist":{"id":"artist-1","name":"Aster","image_url":null,"spotify_url":null},"saved_tracks":1,"total_duration_ms":210000,"rank":1,"tracks":{"filters":{"added_from":null,"added_to":null,"artist_id":null,"album_id":null,"q":null,"explicit":null},"items":[{"id":"track-1","name":"North","duration_ms":210000,"explicit":false,"spotify_url":null,"album":{"id":"album-1","name":"First Light","release_date":"2024-01","cover_url":null},"artists":[{"id":"artist-1","name":"Aster","image_url":null,"spotify_url":null},{"id":"artist-2","name":"Briar","image_url":null,"spotify_url":null}],"added_at":"2024-01-10T09:30:00Z"}],"total":1,"limit":50,"offset":0}}
```

Unknown persisted artist: the `AnalyticsService.artist_detail` seam raises the built-in `LookupError(artist_id)` only when the persisted artist is absent; web maps that exception to 404 `not_found`. A known artist with no matching saved tracks is not an exception: return 200, zero counts, `rank: null`, and a nested page with empty items and total zero.

### `GET /api/tracks` — 200 `TrackPage`

Additional query parameters: `limit`, `offset`, `sort: TrackSort` (default `added_at_desc`). Shape is the `TrackPage` above. Example:

```json
{"filters":{"added_from":null,"added_to":null,"artist_id":null,"album_id":null,"q":null,"explicit":null},"items":[{"id":"track-1","name":"North","duration_ms":210000,"explicit":false,"spotify_url":null,"album":null,"artists":[{"id":"artist-1","name":"Aster","image_url":null,"spotify_url":null}],"added_at":"2024-01-10T09:30:00Z"}],"total":1,"limit":50,"offset":0}
```

Empty result: `items: []`, `total: 0` with requested pagination echoed.

### `GET /api/timeline` — 200 `TimelineResponse`

Additional query parameter: `interval: month | year`, default `month`. Fields: `filters`; `interval`; `buckets: TimelineBucket[]`. Each bucket has `start`, `end: date` and `tracks: int`. Count saved tracks by saved date in inclusive bucket boundaries, not release date. Fill missing intervals between filtered minimum and maximum; explicit date filter bounds extend the bucket range. Bucket `start` and `end` always remain full calendar-month/year boundaries, including where explicit filter bounds clip the counted dates within the first or last bucket. No matching tracks always yields `buckets: []`, even when date bounds were provided.

```json
{"filters":{"added_from":null,"added_to":null,"artist_id":null,"album_id":null,"q":null,"explicit":null},"interval":"month","buckets":[{"start":"2024-01-01","end":"2024-01-31","tracks":1},{"start":"2024-02-01","end":"2024-02-29","tracks":0},{"start":"2024-03-01","end":"2024-03-31","tracks":1}]}
```

### `GET /api/distribution` — 200 `DistributionResponse`

Additional query parameter: `dimension: release_decade | duration | explicit`, default `release_decade`. Fields: `filters`; `dimension`; `buckets: DistributionBucket[]`. Each bucket has `key`, `label: str`, `tracks: int`. Omit zero-count buckets. Release decades sort by numeric decade ascending, then `unknown` last; duration order is `under_180`, `180_to_299`, `300_plus`; explicit order is `clean`, then `explicit`. Empty results yield `buckets: []`. Release decade keys are decade years as strings (for example `1990`) and `unknown`. Duration keys are `under_180`, `180_to_299`, `300_plus`, with millisecond ranges `[0,180000)`, `[180000,300000)`, `[300000,infinity)`. Explicit keys are `explicit` and `clean`.

```json
{"filters":{"added_from":null,"added_to":null,"artist_id":null,"album_id":null,"q":null,"explicit":null},"dimension":"duration","buckets":[{"key":"under_180","label":"Under 3 minutes","tracks":1},{"key":"300_plus","label":"5 minutes or more","tracks":1}]}
```

### `GET /api/insights` — 200 `InsightsResponse`

Fields: `filters`; `items: Insight[]`. Each insight has `code: top_artist | largest_save_month | library_duration`, `title`, `description: str`, `artist_id: str | null` (default null), `value: int`. Deterministic order is `top_artist`, `largest_save_month`, `library_duration`; no listening or frequency claims. `top_artist.value` is saved-track count; `largest_save_month.value` is track count and description identifies the month. When multiple months tie for the highest saved-track count, choose the earliest calendar month. `library_duration.value` is milliseconds. Empty result: `items: []`.

```json
{"filters":{"added_from":null,"added_to":null,"artist_id":null,"album_id":null,"q":null,"explicit":null},"items":[{"code":"top_artist","title":"Most saved tracks","description":"Aster appears on 2 saved tracks.","artist_id":"artist-1","value":2},{"code":"largest_save_month","title":"Largest save month","description":"January 2024 had 2 saved tracks.","artist_id":null,"value":2},{"code":"library_duration","title":"Saved-track duration","description":"The matching tracks total 420000 ms.","artist_id":null,"value":420000}]}
```

### `GET /api/sync/status` — 200 `SyncStatusResponse`

No filter parameters. Unknown query keys still produce 422. Fields: `account_id: str | null`; `status: idle | running | succeeded | failed`; `last_successful_sync: datetime | null`; `error: str | null`; `saved_tracks`, `artists`, `albums`, `processed_tracks: int`.

```json
{"account_id":null,"status":"idle","last_successful_sync":null,"error":null,"saved_tracks":0,"artists":0,"albums":0,"processed_tracks":0}
```

This is the fresh-database response. Demo data may report account `demo` and `succeeded`; it does not imply Spotify activity.

## Authentication and synchronization

These contracts are future behavior only; Phase 0 does not include these routes.

| Method and path | Status and behavior |
|---|---|
| `POST /api/sync` | No body. 202 returns `SyncStatusResponse` in `running` state. 401 `auth_required` when unauthenticated. 409 `sync_in_progress` when already running. Unexpected JSON body is 422. Example accepted response: `{"account_id":"account-1","status":"running","last_successful_sync":null,"error":null,"saved_tracks":0,"artists":0,"albums":0,"processed_tracks":0}`. |
| `GET /auth/login` | No parameters. 302 to Spotify authorization URL with state, PKCE, and the `user-library-read` and `user-follow-read` scopes. 503 `spotify_not_configured` if credentials are absent. |
| `GET /auth/callback` | Successful callback requires `code` and `state`, then 302 to `/`. Denial uses `error` and `state`, returning 400 `oauth_denied`. Invalid state is 400 `invalid_oauth_state`; token exchange failure is 502 `spotify_unavailable`. |
| `GET /api/artist-follow-status` | `ids` requires 1 to 50 comma-separated Spotify artist IDs. Returns a JSON object keyed by artist ID with boolean values for the current Spotify session, or `{}` without a session. Responses with status data are not cached. Invalid parameters return 422 `invalid_query`; a Spotify failure returns 502 `spotify_unavailable`. |

OAuth session cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` when HTTPS is used. Never expose access or refresh tokens to the frontend or logs. Sync status and durable error are owned by the Spotify module. Build a complete new Spotify snapshot and reconcile atomically: only publish it after full reconciliation succeeds. Delete no-longer-saved tracks from `SavedTrack` only; do not delete shared track/artist/album metadata merely because a saved-track relation disappeared. Preserve the previous queryable library on failure. Account switching atomically replaces, rather than mixes, libraries.

The follow-status route uses Spotify's [Check User's Saved Items endpoint](https://developer.spotify.com/documentation/web-api/reference/check-library-contains), the replacement for `/me/following/contains`. It sends artist URIs in batches of at most 40. Follow status is not stored in SQLite or added to analytics and exports.

## Errors

All errors use `ErrorResponse`: `{"error":{"code":str,"message":str,"details":list[dict]}}`. `details` defaults to `[]`. Validation errors use 422 `invalid_query` and include field information in `details`. Never include tokens, traces, or secrets. Unknown resources return 404 `not_found`; malformed query/body returns 422; unexpected failures return 500 `internal_error` with a generic message; Spotify exchange/API failures return 502 `spotify_unavailable`; missing Spotify credentials return 503 `spotify_not_configured`.

```json
{"error":{"code":"invalid_query","message":"Request validation failed","details":[{"field":"added_from","message":"must be on or before added_to"}]}}
```

```json
{"error":{"code":"not_found","message":"Artist not found","details":[]}}
```
