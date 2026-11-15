from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from spotify_analytics.core.models import (
    Album,
    Artist,
    SavedTrack,
    SyncState,
    Track,
    TrackArtist,
)

_TRACK_COUNT = 120
_ARTIST_COUNT = 24
_ALBUM_COUNT = 12
_DEMO_PREFIX = "demo-"


def _demo_artists() -> list[Artist]:
    artists = []
    for index in range(_ARTIST_COUNT):
        name = f"Artist {index + 1:02d}"
        if index == 0 or index == 7:
            name = "Shared Name"
        elif index == 3:
            name = "Björk & the 北 Stars"
        artists.append(
            Artist(
                id=f"demo-artist-{index + 1:02d}",
                name=name,
                image_url=None
                if index % 4 == 0
                else f"https://example.invalid/artists/{index + 1:02d}.jpg",
                spotify_url=None
                if index % 5 == 0
                else f"https://open.spotify.com/artist/demo-{index + 1:02d}",
            )
        )
    return artists


def _demo_albums() -> list[Album]:
    return [
        Album(
            id=f"demo-album-{index + 1:02d}",
            name=f"Album {index + 1:02d}" if index != 4 else "Étoiles — Volume 五",
            release_date=None if index == 10 else f"{1998 + index}-06-15",
            cover_url=None
            if index % 3 == 0
            else f"https://example.invalid/albums/{index + 1:02d}.jpg",
        )
        for index in range(_ALBUM_COUNT)
    ]


def _demo_tracks() -> tuple[list[Track], list[TrackArtist], list[SavedTrack]]:
    tracks = []
    credits = []
    saved = []
    singleton_indexes = {118: 22, 119: 23}

    for index in range(_TRACK_COUNT):
        track_number = index + 1
        track_id = f"demo-track-{track_number:03d}"
        album_index = index // 10
        artist_index = singleton_indexes.get(index, index % 22)
        title = f"Track {track_number:03d}"
        if index == 5:
            title = "東京の夜 — Café"
        album_id = f"demo-album-{album_index + 1:02d}"
        tracks.append(
            Track(
                id=track_id,
                name=title,
                duration_ms=150_000 + (index * 1_237) % 210_000,
                album_id=album_id,
                explicit=index % 7 == 0,
                spotify_url=None
                if index % 11 == 0
                else f"https://open.spotify.com/track/demo-{track_number:03d}",
            )
        )
        credits.append(
            TrackArtist(
                track_id=track_id,
                artist_id=f"demo-artist-{artist_index + 1:02d}",
                artist_order=0,
            )
        )
        if index not in singleton_indexes and index % 3 == 0:
            collaborator_index = (artist_index + 5) % 22
            credits.append(
                TrackArtist(
                    track_id=track_id,
                    artist_id=f"demo-artist-{collaborator_index + 1:02d}",
                    artist_order=1,
                )
            )

        # Saved dates are a stable five-year snapshot, independent of release dates.
        saved.append(
            SavedTrack(
                track_id=track_id,
                added_at=datetime(
                    2020 + index % 5,
                    1 + index % 12,
                    1 + index % 27,
                    12,
                    tzinfo=UTC,
                ),
            )
        )

    return tracks, credits, saved


def _ensure_demo_only(session: Session) -> None:
    """Refuse mixed or real libraries before inserting any fixture rows."""
    tables = (
        (Track, Track.id),
        (Artist, Artist.id),
        (Album, Album.id),
        (SavedTrack, SavedTrack.track_id),
    )
    for _model, id_column in tables:
        ids = session.scalars(select(id_column)).all()
        if any(not value.startswith(_DEMO_PREFIX) for value in ids):
            raise ValueError("Refusing to load demo data into a non-demo library")

    credits = session.execute(select(TrackArtist.track_id, TrackArtist.artist_id)).all()
    if any(
        not track_id.startswith(_DEMO_PREFIX) or not artist_id.startswith(_DEMO_PREFIX)
        for track_id, artist_id in credits
    ):
        raise ValueError("Refusing to load demo data into a non-demo library")

    account_ids = session.scalars(select(SyncState.account_id)).all()
    if any(
        account_id != "demo" and not account_id.startswith(_DEMO_PREFIX)
        for account_id in account_ids
    ):
        raise ValueError("Refusing to load demo data into a non-demo library")


def load_demo(session: Session) -> dict[str, int]:
    """Load deterministic demo rows; the caller owns the transaction."""
    _ensure_demo_only(session)
    entities = [*_demo_albums(), *_demo_artists()]
    tracks, credits, saved = _demo_tracks()
    entities.extend(tracks)
    for entity in entities:
        model = type(entity)
        primary_key = entity.id
        if session.get(model, primary_key) is None:
            session.add(entity)

    for credit in credits:
        if session.get(TrackArtist, (credit.track_id, credit.artist_id)) is None:
            session.add(credit)

    for saved_track in saved:
        if session.get(SavedTrack, saved_track.track_id) is None:
            session.add(saved_track)

    if session.get(SyncState, "demo") is None:
        session.add(
            SyncState(
                account_id="demo",
                last_successful_sync=datetime(2024, 1, 1, tzinfo=UTC),
                status="succeeded",
                error=None,
                saved_tracks=_TRACK_COUNT,
                artists=_ARTIST_COUNT,
                albums=_ALBUM_COUNT,
                processed_tracks=_TRACK_COUNT,
            )
        )

    return {"tracks": _TRACK_COUNT, "artists": _ARTIST_COUNT, "albums": _ALBUM_COUNT}
