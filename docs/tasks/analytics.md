# Analytics task (future phase)

## Scope and ownership

Implement only `src/spotify_analytics/analytics/` and `tests/analytics/`. The shared contract is [../contracts.md](../contracts.md), frozen after Phase 0. Shared files `core/*`, `pyproject.toml`, `uv.lock`, `main.py`, and `docs/contracts.md` require explicit coordination before any change. Do not edit them, `spotify/`, `web/`, or another owner's test directory. Do not create substitute schemas, configuration, or database models.

Phase 0 has no analytics implementation or analytical routes. Implement calculations through the future service seam below; do not return fake data or install endpoint stubs.

## Exact service interface

Create `analytics/service.py` with `AnalyticsService(session: Session)`. It uses the supplied synchronous SQLAlchemy `Session` and returns the exact corresponding Pydantic response model from `spotify_analytics.core.schemas` for every method. Use the specified keyword names and argument types:

```python
AnalyticsService(session: Session)

overview(filters: AnalyticsFilter) -> OverviewResponse
artists(filters: AnalyticsFilter, page: PageParams, sort: ArtistSort) -> ArtistPage
artist_detail(artist_id: str, filters: AnalyticsFilter, page: PageParams, sort: TrackSort) -> ArtistDetailResponse
timeline(filters: AnalyticsFilter, interval: Literal["month", "year"]) -> TimelineResponse
distribution(filters: AnalyticsFilter, dimension: Literal["release_decade", "duration", "explicit"]) -> DistributionResponse
insights(filters: AnalyticsFilter) -> InsightsResponse
```

Import the shared model and type names; do not redefine them. HTTP parsing and route error conversion belong to web composition, not this service. The dashboard owner calls these methods and adapts query parameters into the exact shared models. No method performs Spotify I/O, commits transactions, or modifies library data.

`artist_detail` raises the built-in `LookupError(artist_id)` only when the persisted artist is absent. A known persisted artist with no tracks matching the filters returns a normal `ArtistDetailResponse` with zero counts, null rank, and an empty nested track page.

## Calculation requirements

Implement all filter rules in [the frozen contract](../contracts.md): inclusive UTC calendar-day boundaries on `SavedTrack.added_at`; reject reversed dates and empty IDs; trim `q`, turn empty into null, limit it to 200 characters; Unicode-casefold substring over track, album, and credited artist names; intersect every filter. `artist_id` selects tracks but does not hide their other credited artists in artist rankings. Artist detail adds its artist scope by intersection. Do not use album release date for saved-date filters.

Artist counts use distinct saved track IDs per artist, including collaborators and singleton artists. Calculate unique sequential global ranks across the complete filtered set before pagination. Ranking ties are saved count descending, Unicode-casefolded name ascending, ID ascending. `name_asc` changes returned page ordering only; each row keeps its ranking rank. Share uses filtered total tracks and is zero when that total is zero. Track ordering has ID ascending as final tie-break; name ordering uses Unicode casefold.

Overview totals and first/last saved timestamps use filtered tracks. Timeline counts saved dates, fills each missing interval between min/max, extends to explicit date bounds, and returns no buckets when no tracks match. Year and month buckets use inclusive full calendar boundaries: bucket start/end remain calendar boundaries even when explicit filter bounds clip the counted dates in the first or last bucket. Distribution omits zero-count buckets; release decades sort by numeric decade ascending then unknown last, duration order is `under_180`, `180_to_299`, `300_plus`, and explicit order is `clean`, then `explicit`. Duration boundaries are exactly `[0,180000)`, `[180000,300000)`, `[300000,infinity)` milliseconds. Insights are deterministic in `top_artist`, `largest_save_month`, `library_duration` order; `largest_save_month` selects the earliest calendar month when months tie for highest saved-track count. Insights make no listening/frequency claims. Follow each type, nullable field, default, and empty state exactly as defined in the contract. Empty analytics return zeros, null dates/ranks, or empty lists as appropriate, never fabricated records.

## HTTP contract owned by dashboard

This task does not own routes. The dashboard composes these future GET routes and must pass exact models/parameters:

- `GET /api/overview` → `overview(filters)`
- `GET /api/artists` + `limit`, `offset`, `sort` → `artists(filters, page, sort)`
- `GET /api/artists/{artist_id}` + `limit`, `offset`, `sort` → `artist_detail(artist_id, filters, page, sort)`
- `GET /api/tracks` + `limit`, `offset`, `sort` → `tracks(filters, page, sort)`
- `GET /api/timeline` + `interval` → `timeline(filters, interval)`
- `GET /api/distribution` + `dimension` → `distribution(filters, dimension)`
- `GET /api/insights` → `insights(filters)`

All analytical filter names are `added_from`, `added_to`, `artist_id`, `album_id`, `q`, and `explicit`; pagination defaults to limit 50/offset 0, limit 1–200, offset nonnegative. Defaults: artist sort `saved_tracks_desc`, track sort `added_at_desc`, timeline interval `month`, distribution dimension `release_decade`. Unknown query keys and invalid values are 422. Artist detail returns 404 `not_found` when its service raises the built-in `LookupError(artist_id)` for an absent persisted artist; a known artist with no matching saved tracks returns 200 with zero/null/empty values. See [the frozen contract](../contracts.md) for complete response JSON, errors, and examples.

## Required tests

Add tests only under `tests/analytics/`. Use actual SQLite data and shared core models; test service results, not a second implementation of the production logic. Cover:

- Empty database and every response empty-state shape.
- Every filter alone and combinations/intersections; inclusive UTC date boundaries, album release date independence, empty/reversed/overlong input validation, Unicode casefold and q matching across track/album/artist names.
- Artist filter selecting tracks while retaining all credited artists; artist detail intersection; duplicate artist names with distinct IDs; collaborators, distinct track counts, singleton artists, tied counts and deterministic tie ordering/ranks.
- Pagination totals and out-of-range offsets; sort modes and deterministic ID tie-breakers; share denominator/zero behavior.
- UTC aware timestamps, date and month/year boundaries, gap-filled timeline and explicit bound extension; no-match timeline despite bounds.
- Release decade and unknown, exact duration boundaries, explicit/clean distributions, fixed order, omitted zero buckets.
- Deterministic insight order/values/descriptions, no-match insights, and no listening/frequency claims.
- Persisted artist not found versus known artist with no matching tracks.

No test may require Spotify credentials or network access. Do not edit the web layer, schemas, or fixtures; coordinate if the documented service seam cannot support the contract.
