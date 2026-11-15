from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from spotify_analytics.core import (
    Album,
    AlbumSummary,
    AnalyticsFilter,
    Artist,
    ArtistDetailResponse,
    ArtistPage,
    ArtistRanking,
    ArtistSummary,
    DistributionBucket,
    DistributionResponse,
    ErrorDetail,
    ErrorResponse,
    Insight,
    InsightsResponse,
    OverviewResponse,
    SavedTrack,
    SavedTrackResponse,
    SyncState,
    SyncStatusResponse,
    TimelineBucket,
    TimelineResponse,
    Track,
    TrackArtist,
    TrackPage,
    create_db_engine,
    create_session_factory,
    init_db,
)


@pytest.fixture
def session(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'core.db'}")
    init_db(engine)
    factory = create_session_factory(engine)
    with factory() as db:
        yield db
    engine.dispose()


def _library_rows(db: Session) -> None:
    db.add_all(
        [
            Album(id="album-1", name="Album", release_date="2024-02", cover_url=None),
            Artist(id="artist-1", name="Same name", image_url=None, spotify_url=None),
            Artist(id="artist-2", name="Same name", image_url=None, spotify_url=None),
            Track(
                id="track-1",
                name="Track",
                duration_ms=1000,
                album_id="album-1",
                explicit=False,
                spotify_url=None,
            ),
        ]
    )
    db.flush()
    db.add_all(
        [
            TrackArtist(track_id="track-1", artist_id="artist-1", artist_order=0),
            TrackArtist(track_id="track-1", artist_id="artist-2", artist_order=1),
            SavedTrack(
                track_id="track-1",
                added_at=datetime(2024, 3, 4, 5, 6, tzinfo=UTC),
            ),
        ]
    )
    db.commit()


def test_relational_constraints_and_delete_actions(session: Session) -> None:
    _library_rows(session)

    track = session.get(Track, "track-1")
    session.delete(session.get(Album, "album-1"))
    session.commit()
    session.refresh(track)
    assert track.album_id is None
    assert (
        session.scalar(
            select(TrackArtist.artist_id).where(TrackArtist.artist_order == 1)
        )
        == "artist-2"
    )

    # An artist delete cascades only its track credit.
    session.delete(session.get(Artist, "artist-1"))
    session.commit()
    assert session.scalar(select(TrackArtist.artist_id)) == "artist-2"

    # Track deletion cascades saved-track and all remaining credits.
    session.delete(session.get(Track, "track-1"))
    session.commit()
    assert session.scalar(select(SavedTrack.track_id)) is None
    assert session.scalar(select(TrackArtist.track_id)) is None


def test_database_rejects_invalid_values_and_duplicate_credit_order(
    session: Session,
) -> None:
    session.add(Track(id="bad-duration", name="bad", duration_ms=-1, explicit=False))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    _library_rows(session)
    session.add(TrackArtist(track_id="track-1", artist_id="artist-1", artist_order=0))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_sync_state_counts_are_nonnegative(session: Session) -> None:
    session.add(SyncState(account_id="account", saved_tracks=-1))
    with pytest.raises(IntegrityError):
        session.commit()


def test_saved_track_roundtrips_as_utc_and_rejects_naive_datetime(
    session: Session,
) -> None:
    instant = datetime(
        2024, 3, 4, 5, 6, tzinfo=timezone(timedelta(hours=5, minutes=30))
    )
    session.add(Track(id="track", name="Track", duration_ms=0, explicit=False))
    session.flush()
    session.add(SavedTrack(track_id="track", added_at=instant))
    session.commit()
    saved = session.get(SavedTrack, "track")
    assert saved.added_at == instant.astimezone(UTC)
    assert saved.added_at.tzinfo == UTC

    saved.added_at = datetime(2024, 1, 1)
    with pytest.raises(StatementError):
        session.commit()


def test_sqlite_engine_enables_foreign_key_integrity(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'missing' / 'nested.db'}")
    init_db(engine)
    with create_session_factory(engine)() as db:
        db.add(
            SavedTrack(track_id="missing", added_at=datetime(2024, 1, 1, tzinfo=UTC))
        )
        with pytest.raises(IntegrityError):
            db.commit()
    engine.dispose()


def test_analytics_filter_normalizes_query_and_validates_bounds_and_ids() -> None:
    filters = AnalyticsFilter(
        q="  Café  ", added_from=date(2024, 1, 1), added_to=date(2024, 1, 2)
    )
    assert filters.q == "Café"
    assert AnalyticsFilter(q="   ").q is None
    with pytest.raises(ValidationError):
        AnalyticsFilter(added_from=date(2024, 2, 1), added_to=date(2024, 1, 1))
    with pytest.raises(ValidationError):
        AnalyticsFilter(artist_id="  ")
    with pytest.raises(ValidationError):
        AnalyticsFilter(q="x" * 201)
    with pytest.raises(ValidationError):
        AnalyticsFilter(unexpected="no")


def test_pagination_and_response_contracts_validate_nested_types_and_counts() -> None:
    filters = AnalyticsFilter()
    artist = ArtistSummary(id="a", name="Artist", image_url=None, spotify_url=None)
    album = AlbumSummary(id="b", name="Album", release_date="1999", cover_url=None)
    item = SavedTrackResponse(
        id="t",
        name="Track",
        duration_ms=10,
        explicit=False,
        spotify_url=None,
        album=album,
        artists=[artist],
        added_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    ranking = ArtistRanking(artist=artist, saved_tracks=1, rank=1, share=1.0)
    tracks = TrackPage(filters=filters, items=[item], total=1, limit=50, offset=0)
    artist_page = ArtistPage(
        filters=filters, items=[ranking], total=1, limit=50, offset=0
    )

    assert tracks.items[0].artists[0].id == "a"
    assert artist_page.items[0].rank == 1
    assert (
        ArtistDetailResponse(
            filters=filters,
            artist=artist,
            saved_tracks=1,
            total_duration_ms=10,
            rank=1,
            tracks=tracks,
        ).tracks.total
        == 1
    )
    assert (
        OverviewResponse(
            filters=filters,
            total_tracks=1,
            total_artists=1,
            total_albums=1,
            total_duration_ms=10,
            first_saved_at=item.added_at,
            last_saved_at=item.added_at,
        ).total_tracks
        == 1
    )
    assert (
        TimelineResponse(
            filters=filters,
            interval="month",
            buckets=[
                TimelineBucket(start=date(2024, 1, 1), end=date(2024, 1, 31), tracks=1)
            ],
        )
        .buckets[0]
        .tracks
        == 1
    )
    assert (
        DistributionResponse(
            filters=filters,
            dimension="duration",
            buckets=[
                DistributionBucket(key="under_180", label="Under 3 minutes", tracks=1)
            ],
        )
        .buckets[0]
        .key
        == "under_180"
    )
    assert (
        InsightsResponse(
            filters=filters,
            items=[
                Insight(
                    code="top_artist",
                    title="Top artist",
                    description="Artist",
                    artist_id="a",
                    value=1,
                )
            ],
        )
        .items[0]
        .value
        == 1
    )
    assert (
        SyncStatusResponse(
            account_id=None,
            status="idle",
            last_successful_sync=None,
            error=None,
            saved_tracks=0,
            artists=0,
            albums=0,
            processed_tracks=0,
        ).status
        == "idle"
    )
    assert (
        ErrorResponse(
            error=ErrorDetail(code="invalid_query", message="Invalid query")
        ).error.details
        == []
    )

    with pytest.raises(ValidationError):
        SavedTrackResponse(
            id="t",
            name="Track",
            duration_ms=-1,
            explicit=False,
            spotify_url=None,
            album=None,
            artists=[],
            added_at=datetime(2024, 1, 1),
        )
    with pytest.raises(ValidationError):
        SavedTrackResponse(
            id="t",
            name="Track",
            duration_ms=1,
            explicit=False,
            spotify_url=None,
            album=None,
            artists=[],
            added_at=datetime(2024, 1, 1),
        )
    assert (
        ArtistDetailResponse(
            filters=filters,
            artist=artist,
            saved_tracks=0,
            total_duration_ms=0,
            rank=None,
            tracks=TrackPage(filters=filters, items=[], total=0, limit=50, offset=0),
        ).rank
        is None
    )
    with pytest.raises(ValidationError):
        TimelineResponse(filters=filters, interval="week", buckets=[])
