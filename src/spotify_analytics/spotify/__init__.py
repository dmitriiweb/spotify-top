"""Spotify OAuth and saved-library synchronization integration.

Mount :data:`spotify_analytics.spotify.routes.router` in the application with
``app.include_router(router)``. The module uses ``app.state.settings`` and
``app.state.session_factory`` supplied by the application lifecycle; Spotify
credentials and a strong session secret are needed only when OAuth is used.

Sessions and OAuth state are held in memory and are valid only in the process
that created them. Run one application process: multiple workers do not share
sessions. A process restart requires users to log in again, while the saved
library and sync status remain in the database. ``POST /api/sync`` always
retrieves every saved-track page and publishes a complete replacement snapshot;
it accepts no request body or query parameters.

In sync status, ``processed_tracks`` counts fetched saved items in the current
attempt, including duplicates. ``saved_tracks``, ``artists``, and ``albums``
describe the last atomically published library and do not change after failure.

Spotify currently requires the app owner to have Premium and limits Development
Mode access to explicitly allowlisted users. The profile request uses only the
basic account ID (`account_id`, with `id` as a fallback). It does not request
optional private, email, country, or product fields that require extra scopes.
Spotify decides which apps can use the API. This integration does not check
users' plan types, and Premium Family members are not locally excluded. See the
official documentation for the current rules:

- [Quota modes and Development Mode requirements](https://developer.spotify.com/documentation/web-api/concepts/quota-modes)
- [PKCE authorization](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow)
- [Current user's profile](https://developer.spotify.com/documentation/web-api/reference/get-current-users-profile)
- [Saved tracks endpoint](https://developer.spotify.com/documentation/web-api/reference/get-users-saved-tracks)
- [Redirect URI requirements](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri)

Never log OAuth credentials, authorization codes, tokens, or session cookies.
Configure proxies and access logs to redact query strings for
``/auth/callback`` because the authorization code is returned in that URL.
"""
