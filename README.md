# Spotify Top

A local application for exploring a Spotify saved-track library. This repository is at Phase 0: it provides the application shell, database initialization and optional deterministic demo data. Spotify integration and analytical endpoints are contracts for later phases, not working features yet.

## Requirements and setup

Python 3.12 or newer and [uv](https://docs.astral.sh/uv/) are required. From the repository root:

```sh
uv sync
cp .env.example .env  # optional; defaults work without Spotify credentials
uv run spotify-analytics --init-db
uv run spotify-analytics
```

Open <http://127.0.0.1:8000/>. The landing page links to FastAPI's `/docs`; in Phase 0 only `/` and `/health` are application routes. The health endpoint runs `SELECT 1` and returns `{"status":"ok"}`.
## Developer verification

Run the test suite and lint checks from the repository root:

```sh
uv run pytest
uv run ruff check .
```


`uv sync` installs project dependencies; it is not needed just to edit documentation. Do not commit `.env`. Settings are read from `.env` and environment variables prefixed `SPOTIFY_ANALYTICS_`; explicit environment values override `.env`. Defaults are:

| Setting | Environment variable | Default |
|---|---|---|
| Database URL | `SPOTIFY_ANALYTICS_DATABASE_URL` | `sqlite:///./data/spotify-analytics.db` |
| Host | `SPOTIFY_ANALYTICS_HOST` | `127.0.0.1` |
| Port | `SPOTIFY_ANALYTICS_PORT` | `8000` |
| Spotify client ID | `SPOTIFY_ANALYTICS_SPOTIFY_CLIENT_ID` | unset |
| Spotify client secret | `SPOTIFY_ANALYTICS_SPOTIFY_CLIENT_SECRET` | unset |
| OAuth redirect URI | `SPOTIFY_ANALYTICS_REDIRECT_URI` | `http://127.0.0.1:8000/auth/callback` |
| Session signing secret | `SPOTIFY_ANALYTICS_SESSION_SECRET` | unset |

Spotify credentials and the session secret are optional for Phase 0 and demo use. Real OAuth must not be enabled until credentials and a securely configured session secret are available. Never put secrets in source control. The redirect URI must match the Spotify application configuration when OAuth is implemented.

## Initialize or load demo data

The CLI supports one-shot initialization and demo loading:

```sh
uv run spotify-analytics --init-db
uv run spotify-analytics --load-demo
```

Both commands create the database and exit instead of starting the server. `--load-demo` initializes and transactionally loads deterministic demo data. The fixture loader is idempotent and refuses to overwrite a nonempty non-demo library; it does not reset or delete real user data. Demo rows use IDs prefixed with `demo-` and a `demo` sync account. They represent saved-track fixture data, not a Spotify login or sync.

Start the server with non-default bind settings if needed:

```sh
uv run spotify-analytics --host 0.0.0.0 --port 8001
```

Do not expose the local app publicly without adding appropriate deployment security.

## Scope and next phases

Phase 0 serves only `/` and `/health`; it does not implement Spotify authentication, sync, analytics, a dashboard, or fake/stub `/api/*` endpoints. The future HTTP shapes, query rules, database contract, status codes, and empty states are frozen in [the contracts](docs/contracts.md). The future task boundaries are [Spotify API](docs/tasks/spotify-api.md), [analytics](docs/tasks/analytics.md), and [dashboard](docs/tasks/dashboard.md).

Shared interfaces are frozen after Phase 0: `core/*`, `pyproject.toml`, `uv.lock`, `main.py`, and `docs/contracts.md`. Any change requires explicit coordination before editing. Each future implementer owns only its module and test directories and must integrate through the documented seams; do not edit another owner's files. No future module may invent endpoints or behavior that diverges from the shared contracts.
