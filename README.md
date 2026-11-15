# Spotify Top

A local dashboard for exploring a Spotify saved-track library. Library data and analytics are stored in a local SQLite database. Spotify access is optional: use the isolated demo library without credentials, or connect a Spotify account to import its saved tracks.

## Requirements and setup

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) are required. From the repository root:

```sh
uv sync
cp .env.example .env
```

Edit `.env` only when connecting Spotify or changing local settings. Start the dashboard:

```sh
uv run spotify-analytics
```

The CLI initializes the configured database, starts the server, and opens the dashboard at <http://127.0.0.1:8000/> after server startup is complete. Use `--no-browser` in automation or when opening the URL yourself. `--host` and `--port` override the configured bind address and port:

```sh
uv run spotify-analytics --no-browser
uv run spotify-analytics --host 127.0.0.1 --port 8001
```

The launcher disables Uvicorn access logs so OAuth callback URLs (which may contain authorization codes) are not written to request logs. Do not expose the local app publicly without adding appropriate deployment security.

## Demo mode

Run a deterministic demo library without Spotify credentials:

```sh
uv run spotify-analytics --demo
```

Demo mode derives a separate sibling database file by adding `-demo` before the configured SQLite database suffix. For example, `sqlite:///./data/spotify-analytics.db` uses `./data/spotify-analytics-demo.db`. It initializes and seeds only that file, then serves it. It never writes demo rows to or replaces the configured real-library database. The demo fixture uses account ID `demo`; it is local sample data, not a Spotify account or a sync. `--demo` requires a file-backed SQLite URL.

For one-shot database operations, which exit without serving:

```sh
uv run spotify-analytics --init-db
uv run spotify-analytics --load-demo
```

`--init-db` creates the configured database schema. `--load-demo` initializes and transactionally loads deterministic demo rows into the configured database; the fixture loader is idempotent and refuses to overwrite a nonempty non-demo library. Prefer `--demo` for exploring sample data alongside an existing library.

## Connect Spotify

1. Sign in to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and create an app. Enable the Spotify Web API for the app.
2. In the app's Redirect URIs settings, add exactly `http://127.0.0.1:8000/auth/callback`. Use `127.0.0.1`, not `localhost`; Spotify requires the configured redirect URI to match exactly.
3. Copy the app's client ID and client secret into `SPOTIFY_ANALYTICS_SPOTIFY_CLIENT_ID` and `SPOTIFY_ANALYTICS_SPOTIFY_CLIENT_SECRET` in `.env`.
4. Generate a unique session signing secret with a cryptographically secure generator, for example `openssl rand -hex 32`, and set it as `SPOTIFY_ANALYTICS_SESSION_SECRET` in `.env`. Keep `.env` private and never commit or share these values.
5. Start the normal app with `uv run spotify-analytics`, choose **Connect Spotify**, approve access, then choose **Sync library** in Settings. Connecting alone does not import tracks.

The owner of a development-mode app must have Spotify Premium. Add each account that will use the app to its user allowlist. Spotify can impose API access or quota restrictions; this app does not bypass them. This project has not been verified with a real Spotify account.
See Spotify's official [Web API getting-started guide](https://developer.spotify.com/documentation/web-api/tutorials/getting-started), [redirect URI rules](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri), and [quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes) for current platform requirements.

The app requests access to saved tracks and stores the synchronized library in SQLite. Each sync fetches a complete snapshot and publishes it atomically; a failed sync keeps the previous queryable library. OAuth tokens are held only in process memory: restarting the server requires connecting again before another sync. The local library and its successful-sync timestamp persist in the database and remain available offline without an active Spotify connection. Sync requires Spotify network access; there is no listening-history or streaming-activity data.

## Dashboard

The dashboard provides:

- **Overview:** saved-track, artist, and album counts; saved-library duration and date range; recent additions; data-backed insights; top artists; and saved-track timeline and distribution charts.
- **Artists:** a ranked artist directory with pagination, ranking/name sort, and artist details including matching saved tracks, duration, rank, and Spotify profile links.
- **Tracks:** a paginated saved-track table with recently added, oldest added, name, and duration sorting.
- **Timeline:** monthly or yearly saved-track counts with a selectable date range.
- **Discovery:** deterministic observations about top saved artists, the largest save month, and total saved-track duration. These describe saved-library data, not listening behavior.
- **Settings:** Spotify connection and sync status, last successful sync, counts, and the action to synchronize the library.

The shared filters are inclusive saved-date bounds, text search, explicit/clean content, album ID, and artist scope where applicable. Search is a case-insensitive substring search over track, album, and credited artist names. Filters intersect; date bounds apply to when a track was saved, not its album release date. Artist and track views also have search and sort controls. Filters apply across dashboard data, and active filters can be removed one at a time or cleared.

Use the dashboard download controls to export filtered artists or tracks as CSV, or the full filtered report as JSON. The API routes are `GET /api/export/artists.csv`, `GET /api/export/tracks.csv`, and `GET /api/export/library.json`. These accept the common filter query parameters (`added_from`, `added_to`, `artist_id`, `album_id`, `q`, `explicit`), return all matching results without pagination, and reject unknown query parameters. Downloads are UTF-8 attachments named `artists.csv`, `tracks.csv`, and `library.json`. JSON includes filtered overview, artists, tracks, monthly and yearly timelines, distributions, and insights. CSV text is protected against spreadsheet formulas; JSON keeps the original text.

Analytics run locally against persisted library data; no Spotify connection is required to browse existing data or use demo mode. The charts use a bundled Plotly asset and do not require a chart CDN. Empty libraries show empty states. Saved-track counts, dates, and analytics do not measure plays or listening frequency.

Optional artist images can be unavailable offline. The demo uses sample image URLs that do not resolve; the dashboard shows a local fallback. These image requests can appear as network errors in browser tools, but do not stop charts or local analytics.

## Settings

Settings are read from `.env` and environment variables prefixed `SPOTIFY_ANALYTICS_`; environment values override `.env`.

| Setting | Environment variable | Default |
|---|---|---|
| Database URL | `SPOTIFY_ANALYTICS_DATABASE_URL` | `sqlite:///./data/spotify-analytics.db` |
| Host | `SPOTIFY_ANALYTICS_HOST` | `127.0.0.1` |
| Port | `SPOTIFY_ANALYTICS_PORT` | `8000` |
| Spotify client ID | `SPOTIFY_ANALYTICS_SPOTIFY_CLIENT_ID` | unset |
| Spotify client secret | `SPOTIFY_ANALYTICS_SPOTIFY_CLIENT_SECRET` | unset |
| OAuth redirect URI | `SPOTIFY_ANALYTICS_REDIRECT_URI` | `http://127.0.0.1:8000/auth/callback` |
| Session signing secret | `SPOTIFY_ANALYTICS_SESSION_SECRET` | unset |

Without Spotify credentials and a secure session secret, Spotify login and sync remain unavailable; local analytics continue to work. For a non-default port, update the redirect URI in both `.env` and the Spotify Developer Dashboard to the exact same URL. The demo database name is derived from the configured database URL as described above.

## Manual smoke procedure

This is a procedure, not a claim that a manual smoke test has been run:

1. Run `uv sync`, then `uv run spotify-analytics --demo`.
2. Confirm the browser opens only after the server is ready, the dashboard loads populated demo views, and Settings identifies the local demo sync state.
3. Stop the demo server and confirm the configured real-library database did not change. Restart with `uv run spotify-analytics --demo --no-browser` and confirm the demo data remains available. Use the normal command to browse a previously synchronized real library.
4. If Spotify credentials are configured, use **Connect Spotify**, complete authorization, and run **Sync library**. Reconnect after restarting the process because tokens are process-local.

## Development

```sh
uv run pytest
uv run ruff check .
```

The frozen database, API, filter, authentication, and synchronization contracts are documented in [docs/contracts.md](docs/contracts.md). Export routes are documented above.
