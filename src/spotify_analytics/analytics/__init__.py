"""Read-only analytics service and export API.

``AnalyticsService(session)`` exposes ``overview``, ``artists``, ``tracks``,
``artist_detail``, ``timeline``, ``distribution``, and ``insights`` methods
that return frozen core response schemas. It also provides
``export_artists_csv``, ``export_tracks_csv``, and ``export_json``. The service
uses the supplied synchronous SQLAlchemy session; it performs no commits,
library mutations, or Spotify/network access.

This package implements the analytics service/export seam only. HTTP routes,
schemas, database models, and other frozen shared contracts remain owned by
their respective packages.
"""

from spotify_analytics.analytics.service import AnalyticsService

__all__ = ["AnalyticsService"]
