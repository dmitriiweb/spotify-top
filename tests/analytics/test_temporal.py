from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from spotify_analytics.analytics.service import AnalyticsService
from spotify_analytics.core.database import create_db_engine
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
def session(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'temporal.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value
    engine.dispose()


def add_track(
    session: Session,
    track_id: str,
    added: datetime,
    *,
    name: str = "Track",
    duration: int = 1000,
    explicit: bool = False,
    release: str | None = None,
    album_id: str | None = None,
    credits: tuple[tuple[str, str], ...] = (),
) -> None:
    if album_id is not None and session.get(Album, album_id) is None:
        session.add(Album(id=album_id, name=f"Album {album_id}", release_date=release))
        session.flush()
    session.add(
        Track(
            id=track_id,
            name=name,
            duration_ms=duration,
            explicit=explicit,
            album_id=album_id,
            spotify_url=None,
        )
    )
    session.flush()
    session.add(SavedTrack(track_id=track_id, added_at=added))
    for order, (artist_id, artist_name) in enumerate(credits):
        if session.get(Artist, artist_id) is None:
            session.add(Artist(id=artist_id, name=artist_name))
            session.flush()
        session.add(
            TrackArtist(track_id=track_id, artist_id=artist_id, artist_order=order)
        )
    session.flush()


def test_empty_timeline_distribution_and_insights(session):
    service = AnalyticsService(session)
    assert service.timeline(AnalyticsFilter(), "month").buckets == []
    assert service.timeline(AnalyticsFilter(), "year").buckets == []
    assert service.distribution(AnalyticsFilter(), "release_decade").buckets == []
    assert service.distribution(AnalyticsFilter(), "duration").buckets == []
    assert service.distribution(AnalyticsFilter(), "explicit").buckets == []
    assert service.insights(AnalyticsFilter()).items == []


def test_month_timeline_uses_utc_saved_dates_fills_gaps_and_keeps_full_bounds(session):
    add_track(session, "a", datetime(2024, 1, 1, 0, 30, tzinfo=UTC))
    add_track(session, "b", datetime(2024, 3, 31, 23, 30, tzinfo=UTC))
    result = AnalyticsService(session).timeline(AnalyticsFilter(), "month")
    assert [(b.start, b.end, b.tracks) for b in result.buckets] == [
        (date(2024, 1, 1), date(2024, 1, 31), 1),
        (date(2024, 2, 1), date(2024, 2, 29), 0),
        (date(2024, 3, 1), date(2024, 3, 31), 1),
    ]


def test_month_bounds_extend_buckets_but_count_only_inclusive_clipped_dates(session):
    add_track(session, "before", datetime(2023, 12, 31, 23, 59, tzinfo=UTC))
    add_track(session, "first", datetime(2024, 1, 2, 0, 0, tzinfo=UTC))
    add_track(session, "middle", datetime(2024, 2, 15, tzinfo=UTC))
    add_track(session, "last", datetime(2024, 3, 30, 23, 59, tzinfo=UTC))
    add_track(session, "after", datetime(2024, 4, 1, tzinfo=UTC))
    result = AnalyticsService(session).timeline(
        AnalyticsFilter(added_from=date(2024, 1, 10), added_to=date(2024, 3, 20)),
        "month",
    )
    assert [(b.start, b.end, b.tracks) for b in result.buckets] == [
        (date(2024, 1, 1), date(2024, 1, 31), 0),
        (date(2024, 2, 1), date(2024, 2, 29), 1),
        (date(2024, 3, 1), date(2024, 3, 31), 0),
    ]
    no_match = AnalyticsService(session).timeline(
        AnalyticsFilter(added_from=date(2025, 1, 1), added_to=date(2025, 3, 1)), "month"
    )
    assert no_match.buckets == []


def test_year_timeline_fills_gaps(session):
    add_track(session, "y1", datetime(2020, 12, 31, 23, 59, tzinfo=UTC))
    add_track(session, "y3", datetime(2022, 1, 1, tzinfo=UTC))
    year = AnalyticsService(session).timeline(AnalyticsFilter(), "year")
    assert [(b.start, b.end, b.tracks) for b in year.buckets] == [
        (date(2020, 1, 1), date(2020, 12, 31), 1),
        (date(2021, 1, 1), date(2021, 12, 31), 0),
        (date(2022, 1, 1), date(2022, 12, 31), 1),
    ]


def test_year_timeline_explicit_bounds_extend_and_clip_counted_dates(session):
    add_track(session, "outside-start", datetime(2022, 12, 31, 23, 59, tzinfo=UTC))
    add_track(session, "inside", datetime(2023, 6, 1, tzinfo=UTC))
    add_track(session, "outside-end", datetime(2024, 1, 1, tzinfo=UTC))
    result = AnalyticsService(session).timeline(
        AnalyticsFilter(added_from=date(2022, 1, 10), added_to=date(2024, 12, 20)),
        "year",
    )
    assert [(b.start, b.end, b.tracks) for b in result.buckets] == [
        (date(2022, 1, 1), date(2022, 12, 31), 1),
        (date(2023, 1, 1), date(2023, 12, 31), 1),
        (date(2024, 1, 1), date(2024, 12, 31), 1),
    ]


def test_artist_filter_keeps_collaboration_and_release_precision_distribution(session):
    for artist_id, artist_name in (("z", "Zed"), ("a", "Äster")):
        session.add(Artist(id=artist_id, name=artist_name))
    add_track(
        session,
        "one",
        datetime(2024, 1, 1, tzinfo=UTC),
        album_id="old",
        release="1984",
        credits=(("z", "Zed"), ("a", "Äster")),
    )
    add_track(
        session,
        "two",
        datetime(2024, 1, 2, tzinfo=UTC),
        album_id="month",
        release="2001-07",
        credits=(("z", "Zed"),),
    )
    add_track(
        session,
        "three",
        datetime(2024, 1, 3, tzinfo=UTC),
        album_id="day",
        release="2010-04-03",
        credits=(("a", "Äster"),),
    )
    add_track(
        session,
        "four",
        datetime(2024, 1, 4, tzinfo=UTC),
        album_id="unknown",
        release=None,
    )
    result = AnalyticsService(session).distribution(
        AnalyticsFilter(artist_id="z"), "release_decade"
    )
    assert [(b.key, b.label, b.tracks) for b in result.buckets] == [
        ("1980", "1980s", 1),
        ("2000", "2000s", 1),
    ]
    all_result = AnalyticsService(session).distribution(
        AnalyticsFilter(), "release_decade"
    )
    assert [(b.key, b.label, b.tracks) for b in all_result.buckets] == [
        ("1980", "1980s", 1),
        ("2000", "2000s", 1),
        ("2010", "2010s", 1),
        ("unknown", "Unknown", 1),
    ]
    artists = AnalyticsService(session).artists(
        AnalyticsFilter(artist_id="z"), page=PageParams(), sort="saved_tracks_desc"
    )
    assert {item.artist.id for item in artists.items} == {"z", "a"}


def test_distribution_boundaries_fixed_order_and_omits_zero_buckets(session):
    for key, duration, explicit in [
        ("a", 179999, False),
        ("b", 180000, True),
        ("c", 299999, True),
        ("d", 300000, False),
    ]:
        add_track(
            session,
            key,
            datetime(2024, 1, 1, tzinfo=UTC),
            duration=duration,
            explicit=explicit,
        )
    service = AnalyticsService(session)
    duration_result = service.distribution(AnalyticsFilter(), "duration")
    assert [(b.key, b.label, b.tracks) for b in duration_result.buckets] == [
        ("under_180", "Under 3 minutes", 1),
        ("180_to_299", "3 to under 5 minutes", 2),
        ("300_plus", "5 minutes or more", 1),
    ]
    filtered_duration = service.distribution(AnalyticsFilter(explicit=True), "duration")
    assert [b.key for b in filtered_duration.buckets] == ["180_to_299"]
    explicit_result = service.distribution(AnalyticsFilter(), "explicit")
    assert [(b.key, b.label, b.tracks) for b in explicit_result.buckets] == [
        ("clean", "Clean", 2),
        ("explicit", "Explicit", 2),
    ]
    only = service.distribution(AnalyticsFilter(explicit=True), "explicit")
    assert [b.key for b in only.buckets] == ["explicit"]


def test_date_max_bounds_and_malformed_release_are_safe(session):
    add_track(
        session,
        "max",
        datetime(9999, 12, 31, 23, 59, tzinfo=UTC),
        album_id="bad",
        release="not-a-date",
    )
    service = AnalyticsService(session)
    result = service.timeline(
        AnalyticsFilter(added_from=date.max, added_to=date.max), "month"
    )
    assert [(b.start, b.end, b.tracks) for b in result.buckets] == [
        (date(9999, 12, 1), date(9999, 12, 31), 1)
    ]
    distribution = service.distribution(AnalyticsFilter(), "release_decade")
    assert [(b.key, b.tracks) for b in distribution.buckets] == [("unknown", 1)]


def test_insights_values_descriptions_ties_and_unicode_artist_tie(session):
    # Equal artist counts use Unicode casefolded names; equal month counts use
    # the earliest month.
    add_track(
        session,
        "1",
        datetime(2024, 2, 1, tzinfo=UTC),
        duration=100,
        credits=(("b", "Straße"),),
    )
    add_track(
        session,
        "2",
        datetime(2024, 1, 1, tzinfo=UTC),
        duration=200,
        credits=(("a", "STRASSE"),),
    )
    result = AnalyticsService(session).insights(AnalyticsFilter())
    assert [
        (item.code, item.title, item.description, item.artist_id, item.value)
        for item in result.items
    ] == [
        (
            "top_artist",
            "Most saved tracks",
            "STRASSE appears on 1 saved tracks.",
            "a",
            1,
        ),
        (
            "largest_save_month",
            "Largest save month",
            "January 2024 had 1 saved tracks.",
            None,
            1,
        ),
        (
            "library_duration",
            "Saved-track duration",
            "The matching tracks total 300 ms.",
            None,
            300,
        ),
    ]
    assert all(
        "listen" not in item.description.casefold()
        and "frequency" not in item.description.casefold()
        for item in result.items
    )
