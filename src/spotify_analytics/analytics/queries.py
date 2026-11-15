from __future__ import annotations

from datetime import UTC, datetime, time
from typing import TYPE_CHECKING

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from spotify_analytics.core.models import Album, Artist, SavedTrack, Track, TrackArtist
from spotify_analytics.core.schemas import AnalyticsFilter

if TYPE_CHECKING:
    from sqlalchemy.sql.selectable import Subquery


def register_casefold(session: Session) -> None:
    """Register the Unicode casefold function on the session's SQLite connection."""
    connection = session.connection().connection
    driver_connection = getattr(connection, "dbapi_connection", connection)
    driver_connection.create_function(
        "analytics_casefold",
        1,
        lambda value: value.casefold() if value is not None else None,
        deterministic=True,
    )


def filtered_tracks(filters: AnalyticsFilter) -> Subquery:
    """Return one filtered row per saved track, including its saved timestamp."""
    conditions = []
    if filters.added_from is not None:
        start = datetime.combine(filters.added_from, time.min, tzinfo=UTC)
        conditions.append(SavedTrack.added_at >= start)
    if filters.added_to is not None:
        end = datetime.combine(filters.added_to, time.max, tzinfo=UTC)
        conditions.append(SavedTrack.added_at <= end)
    if filters.artist_id is not None:
        conditions.append(
            exists(
                select(1).where(
                    TrackArtist.track_id == Track.id,
                    TrackArtist.artist_id == filters.artist_id,
                )
            )
        )
    if filters.album_id is not None:
        conditions.append(Track.album_id == filters.album_id)
    if filters.explicit is not None:
        conditions.append(Track.explicit == filters.explicit)
    if filters.q:
        needle = filters.q.casefold()
        album_match = exists(
            select(1)
            .select_from(Album)
            .where(
                Album.id == Track.album_id,
                func.instr(func.analytics_casefold(Album.name), needle) > 0,
            )
        )
        artist_match = exists(
            select(1)
            .select_from(TrackArtist)
            .join(Artist, Artist.id == TrackArtist.artist_id)
            .where(
                TrackArtist.track_id == Track.id,
                func.instr(func.analytics_casefold(Artist.name), needle) > 0,
            )
        )
        conditions.append(
            or_(
                func.instr(func.analytics_casefold(Track.name), needle) > 0,
                album_match,
                artist_match,
            )
        )

    return (
        select(
            Track.id.label("track_id"),
            Track.name.label("name"),
            Track.duration_ms.label("duration_ms"),
            Track.explicit.label("explicit"),
            Track.spotify_url.label("spotify_url"),
            Track.album_id.label("album_id"),
            SavedTrack.added_at.label("added_at"),
        )
        .join(SavedTrack, SavedTrack.track_id == Track.id)
        .where(*conditions)
        .subquery("filtered_tracks")
    )
