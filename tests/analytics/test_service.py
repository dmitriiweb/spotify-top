from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from spotify_analytics.analytics.service import AnalyticsService
from spotify_analytics.core.models import (
    Album,
    Artist,
    Base,
    SavedTrack,
    Track,
    TrackArtist,
)
from spotify_analytics.core.schemas import AnalyticsFilter, PageParams


@pytest.fixture
def service_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Artist(
                    id="a1",
                    name="Shared Name",
                    image_url="image:a1",
                    spotify_url="artist:a1",
                ),
                Artist(id="a2", name="Shared Name", spotify_url="artist:a2"),
                Artist(id="b", name="Zeta"),
                Artist(id="unicode", name="Straße"),
                Artist(id="empty", name="No Tracks"),
                Artist(id="unsaved", name="Unsaved Metadata"),
                Album(
                    id="album1",
                    name="First Album",
                    release_date="2025",
                    cover_url="cover:1",
                ),
                Album(
                    id="album2",
                    name="Crème Brûlée",
                    release_date="1999-12",
                    cover_url="cover:2",
                ),
                Album(id="unsaved-album", name="Unsaved Album", release_date="2024"),
                Track(
                    id="t1",
                    name="100%_Song",
                    duration_ms=180_000,
                    album_id="album1",
                    explicit=False,
                    spotify_url="track:t1",
                ),
                Track(
                    id="t2",
                    name="CAFÉ",
                    duration_ms=300_000,
                    album_id="album2",
                    explicit=True,
                ),
                Track(id="t3", name="Straße", duration_ms=0, explicit=False),
                Track(
                    id="t4",
                    name="Needle",
                    duration_ms=299_999,
                    album_id="album1",
                    explicit=True,
                ),
                Track(id="t5", name="Orphan Track", duration_ms=42, explicit=False),
                Track(
                    id="t6",
                    name="Album match",
                    duration_ms=400_000,
                    album_id="album2",
                    explicit=False,
                ),
                Track(
                    id="not-saved",
                    name="Not Saved",
                    duration_ms=12,
                    album_id="unsaved-album",
                    explicit=False,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                TrackArtist(track_id="t1", artist_id="a1", artist_order=0),
                TrackArtist(track_id="t1", artist_id="b", artist_order=1),
                TrackArtist(track_id="t2", artist_id="a1", artist_order=0),
                TrackArtist(track_id="t3", artist_id="unicode", artist_order=0),
                TrackArtist(track_id="t4", artist_id="a2", artist_order=0),
                TrackArtist(track_id="t4", artist_id="b", artist_order=1),
                TrackArtist(track_id="t6", artist_id="b", artist_order=0),
            ]
        )
        session.add_all(
            [
                SavedTrack(track_id="t1", added_at=datetime(2024, 1, 1, tzinfo=UTC)),
                SavedTrack(
                    track_id="t2",
                    added_at=datetime(2024, 1, 31, 23, 59, 59, tzinfo=UTC),
                ),
                SavedTrack(track_id="t3", added_at=datetime(2024, 2, 1, tzinfo=UTC)),
                SavedTrack(track_id="t4", added_at=datetime(2024, 2, 2, tzinfo=UTC)),
                SavedTrack(track_id="t5", added_at=datetime(2024, 2, 3, tzinfo=UTC)),
                SavedTrack(track_id="t6", added_at=datetime(2024, 3, 1, tzinfo=UTC)),
            ]
        )
        session.commit()
        yield session, AnalyticsService(session)
    engine.dispose()


def _track_ids(page) -> list[str]:
    return [track.id for track in page.items]


def _artist_rows(page) -> list[tuple[str, int, int, float]]:
    return [
        (row.artist.id, row.saved_tracks, row.rank, row.share) for row in page.items
    ]


def test_overview_counts_filtered_saved_tracks_and_utc_bounds(service_session):
    _, service = service_session
    result = service.overview(AnalyticsFilter())
    assert (result.total_tracks, result.total_artists, result.total_albums) == (6, 4, 2)
    assert result.total_duration_ms == 1_180_041
    assert result.first_saved_at == datetime(2024, 1, 1, tzinfo=UTC)
    assert result.last_saved_at == datetime(2024, 3, 1, tzinfo=UTC)
    assert result.first_saved_at.utcoffset().total_seconds() == 0

    filtered = service.overview(AnalyticsFilter(explicit=True))
    assert (filtered.total_tracks, filtered.total_artists, filtered.total_albums) == (
        2,
        3,
        2,
    )
    assert filtered.total_duration_ms == 599_999


def test_artist_collaborations_duplicate_names_shares_and_pagination(service_session):
    _, service = service_session
    page = service.artists(AnalyticsFilter(), PageParams(), "saved_tracks_desc")
    assert page.total == 4
    assert _artist_rows(page) == [
        ("b", 3, 1, 0.5),
        ("a1", 2, 2, 1 / 3),
        ("a2", 1, 3, 1 / 6),
        ("unicode", 1, 4, 1 / 6),
    ]
    name_order = service.artists(AnalyticsFilter(), PageParams(), "name_asc")
    assert _artist_rows(name_order) == [
        ("a1", 2, 2, 1 / 3),
        ("a2", 1, 3, 1 / 6),
        ("unicode", 1, 4, 1 / 6),
        ("b", 3, 1, 0.5),
    ]
    assert (
        service.artists(
            AnalyticsFilter(artist_id="a1"), PageParams(), "saved_tracks_desc"
        ).total
        == 2
    )
    selected = service.artists(
        AnalyticsFilter(artist_id="a1"), PageParams(), "saved_tracks_desc"
    )
    assert {row.artist.id for row in selected.items} == {"a1", "b"}
    assert {row.artist.id: row.share for row in selected.items} == {"a1": 1.0, "b": 0.5}

    narrow = service.artists(
        AnalyticsFilter(), PageParams(limit=2, offset=2), "saved_tracks_desc"
    )
    assert narrow.total == 4
    assert [(row.artist.id, row.rank) for row in narrow.items] == [
        ("a2", 3),
        ("unicode", 4),
    ]
    past_end = service.artists(
        AnalyticsFilter(), PageParams(limit=2, offset=99), "saved_tracks_desc"
    )
    assert (past_end.total, past_end.limit, past_end.offset, past_end.items) == (
        4,
        2,
        99,
        [],
    )
    track_past_end = service.tracks(
        AnalyticsFilter(), PageParams(limit=2, offset=99), "added_at_desc"
    )
    assert (
        track_past_end.total,
        track_past_end.limit,
        track_past_end.offset,
        track_past_end.items,
    ) == (6, 2, 99, [])


def test_all_track_sorts_use_casefold_and_stable_id_ties(service_session):
    _, service = service_session
    expected = {
        "added_at_desc": ["t6", "t5", "t4", "t3", "t2", "t1"],
        "added_at_asc": ["t1", "t2", "t3", "t4", "t5", "t6"],
        "name_asc": ["t1", "t6", "t2", "t4", "t5", "t3"],
        "duration_desc": ["t6", "t2", "t4", "t1", "t5", "t3"],
    }
    for sort, ids in expected.items():
        assert _track_ids(service.tracks(AnalyticsFilter(), PageParams(), sort)) == ids


def test_track_sort_ties_fall_back_to_id(service_session):
    session, service = service_session
    timestamp = datetime(2024, 4, 1, tzinfo=UTC)
    session.add_all(
        [
            Track(id="tie-b", name="eCHO", duration_ms=7, explicit=False),
            Track(id="tie-a", name="Echo", duration_ms=7, explicit=False),
            SavedTrack(track_id="tie-b", added_at=timestamp),
            SavedTrack(track_id="tie-a", added_at=timestamp),
        ]
    )
    session.commit()
    for sort in ("added_at_desc", "added_at_asc", "name_asc", "duration_desc"):
        page = service.tracks(AnalyticsFilter(q="ECHO"), PageParams(), sort)
        assert _track_ids(page) == ["tie-a", "tie-b"]


def test_empty_database_overview_and_pages():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            service = AnalyticsService(session)
            overview = service.overview(AnalyticsFilter())
            assert (
                overview.total_tracks,
                overview.total_artists,
                overview.total_albums,
                overview.total_duration_ms,
            ) == (0, 0, 0, 0)
            assert overview.first_saved_at is None and overview.last_saved_at is None
            assert (
                service.artists(
                    AnalyticsFilter(), PageParams(), "saved_tracks_desc"
                ).items
                == []
            )
            tracks = service.tracks(
                AnalyticsFilter(), PageParams(limit=3, offset=5), "added_at_desc"
            )
            assert (tracks.items, tracks.total, tracks.limit, tracks.offset) == (
                [],
                0,
                3,
                5,
            )
    finally:
        engine.dispose()


def test_tracks_return_credit_order_metadata_and_empty_credit_track(service_session):
    _, service = service_session
    tracks = service.tracks(AnalyticsFilter(), PageParams(), "added_at_asc")
    t1, t3, t5 = tracks.items[0], tracks.items[2], tracks.items[4]
    assert t1.album.id == "album1"
    assert [artist.id for artist in t1.artists] == ["a1", "b"]
    assert t3.album is None and [artist.id for artist in t3.artists] == ["unicode"]
    assert t5.album is None and t5.artists == []
    assert (
        t1.added_at.tzinfo is not None and t1.added_at.utcoffset().total_seconds() == 0
    )


def test_each_filter_intersections_and_literal_unicode_search(service_session):
    _, service = service_session
    cases = [
        (AnalyticsFilter(added_from=date(2024, 1, 31)), {"t2", "t3", "t4", "t5", "t6"}),
        (AnalyticsFilter(added_to=date(2024, 1, 31)), {"t1", "t2"}),
        (AnalyticsFilter(artist_id="a1"), {"t1", "t2"}),
        (AnalyticsFilter(album_id="album1"), {"t1", "t4"}),
        (AnalyticsFilter(explicit=True), {"t2", "t4"}),
        (AnalyticsFilter(q="  100%_  "), {"t1"}),
        (AnalyticsFilter(q="STRASSE"), {"t3"}),
        (AnalyticsFilter(q="CRÈME"), {"t2", "t6"}),
        (AnalyticsFilter(q="SHARED NAME"), {"t1", "t2", "t4"}),
        (
            AnalyticsFilter(
                added_from=date(2024, 1, 31),
                added_to=date(2024, 2, 2),
                artist_id="a1",
                album_id="album2",
                explicit=True,
            ),
            {"t2"},
        ),
        (AnalyticsFilter(artist_id="a1", explicit=True, q="CAFÉ"), {"t2"}),
    ]
    for filters, expected in cases:
        page = service.tracks(filters, PageParams(), "added_at_asc")
        assert set(_track_ids(page)) == expected
        assert page.total == len(expected)
    assert (
        service.tracks(AnalyticsFilter(q="not saved"), PageParams(), "name_asc").total
        == 0
    )


def test_artist_detail_known_empty_unknown_and_filter_intersection(service_session):
    _, service = service_session
    detail = service.artist_detail(
        "a1",
        AnalyticsFilter(artist_id="a1", album_id="album2"),
        PageParams(),
        "added_at_desc",
    )
    assert detail.artist.id == "a1"
    assert (detail.saved_tracks, detail.total_duration_ms, detail.rank) == (
        1,
        300_000,
        1,
    )
    assert _track_ids(detail.tracks) == ["t2"]

    empty = service.artist_detail(
        "empty", AnalyticsFilter(), PageParams(), "added_at_desc"
    )
    assert (
        empty.saved_tracks,
        empty.total_duration_ms,
        empty.rank,
        empty.tracks.total,
    ) == (0, 0, None, 0)
    assert empty.tracks.items == []
    with pytest.raises(LookupError, match="missing"):
        service.artist_detail(
            "missing", AnalyticsFilter(), PageParams(), "added_at_desc"
        )


def test_empty_result_shapes_and_zero_artist_share(service_session):
    _, service = service_session
    filters = AnalyticsFilter(q="does not occur")
    overview = service.overview(filters)
    assert (
        overview.total_tracks,
        overview.total_artists,
        overview.total_albums,
        overview.total_duration_ms,
    ) == (0, 0, 0, 0)
    assert overview.first_saved_at is None and overview.last_saved_at is None
    assert service.artists(filters, PageParams(), "saved_tracks_desc").items == []
    page = service.tracks(filters, PageParams(limit=7, offset=4), "added_at_desc")
    assert (page.items, page.total, page.limit, page.offset) == ([], 0, 7, 4)


def test_duplicate_credit_is_rejected_and_session_recovers(service_session):
    session, _ = service_session
    session.add(TrackArtist(track_id="t1", artist_id="a1", artist_order=2))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()
    assert session.get(TrackArtist, ("t1", "a1")).artist_order == 0


def test_album_release_date_does_not_affect_saved_date_filter_date_max_is_safe(
    service_session,
):
    _, service = service_session
    by_saved_day = service.tracks(
        AnalyticsFilter(added_from=date(2024, 1, 1), added_to=date(2024, 1, 1)),
        PageParams(),
        "added_at_asc",
    )
    assert _track_ids(by_saved_day) == ["t1"]
    through_max = service.tracks(
        AnalyticsFilter(added_to=date.max), PageParams(), "added_at_asc"
    )
    assert through_max.total == 6


def test_artist_name_sort_uses_id_for_casefold_ties_and_keeps_global_rank(
    service_session,
):
    _, service = service_session
    result = service.artists(AnalyticsFilter(artist_id="a1"), PageParams(), "name_asc")
    assert [(row.artist.id, row.rank) for row in result.items] == [("a1", 1), ("b", 2)]
