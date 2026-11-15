from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from sqlalchemy import func, select

from spotify_analytics.core.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from spotify_analytics.core.fixtures import load_demo
from spotify_analytics.core.models import (
    Album,
    Artist,
    SavedTrack,
    SyncState,
    Track,
    TrackArtist,
)


@pytest.fixture
def session_factory(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'fixtures.db'}")
    init_db(engine)
    yield create_session_factory(engine)
    engine.dispose()


def test_demo_snapshot_satisfies_library_business_rules(session_factory):
    with session_factory() as session:
        with session.begin():
            assert load_demo(session) == {"tracks": 120, "artists": 24, "albums": 12}
        assert session.scalar(select(func.count()).select_from(Track)) == 120
        assert session.scalar(select(func.count()).select_from(Artist)) == 24
        assert session.scalar(select(func.count()).select_from(Album)) == 12
        assert session.scalar(select(func.count()).select_from(SavedTrack)) == 120

        # Track counts are distinct saved track IDs, not credit-row counts.
        per_artist = session.execute(
            select(
                TrackArtist.artist_id, func.count(func.distinct(SavedTrack.track_id))
            )
            .join(SavedTrack, SavedTrack.track_id == TrackArtist.track_id)
            .group_by(TrackArtist.artist_id)
        ).all()
        counts = dict(per_artist)
        assert len(counts) == 24
        assert counts["demo-artist-23"] == 1
        assert counts["demo-artist-24"] == 1
        assert (
            sum(counts.values()) > 120
        )  # collaborations must not inflate global track totals
        first_track_artist_ids = session.scalars(
            select(TrackArtist.artist_id)
            .where(TrackArtist.track_id == "demo-track-001")
            .order_by(TrackArtist.artist_order)
        ).all()
        assert first_track_artist_ids == ["demo-artist-01", "demo-artist-06"]
        assert (
            session.scalar(select(func.count(func.distinct(SavedTrack.track_id))))
            == 120
        )

        duplicate_names = session.scalars(
            select(Artist.id).where(Artist.name == "Shared Name").order_by(Artist.id)
        ).all()
        assert duplicate_names == ["demo-artist-01", "demo-artist-08"]
        assert (
            session.scalar(
                select(func.count(func.distinct(Artist.id))).where(
                    Artist.name == "Shared Name"
                )
            )
            == 2
        )

        assert (
            session.scalar(
                select(func.count())
                .select_from(TrackArtist)
                .where(TrackArtist.artist_order == 1)
            )
            > 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Track)
                .where(Track.spotify_url.is_(None))
            )
            > 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Artist)
                .where(Artist.image_url.is_(None))
            )
            > 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Album)
                .where(Album.release_date.is_(None))
            )
            > 0
        )
        assert (
            session.scalar(
                select(func.count()).select_from(Album).where(Album.cover_url.is_(None))
            )
            > 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(Track)
                .where(Track.name == "東京の夜 — Café")
            )
            == 1
        )

        added_at = session.scalars(select(SavedTrack.added_at)).all()
        assert all(
            value.tzinfo is not None and value.utcoffset().total_seconds() == 0
            for value in added_at
        )
        assert {value.year for value in added_at} == {2020, 2021, 2022, 2023, 2024}
        assert session.get(Album, "demo-album-01").release_date == "1998-06-15"
        track_dates = session.execute(
            select(SavedTrack.track_id, SavedTrack.added_at, Album.release_date)
            .join(Track, Track.id == SavedTrack.track_id)
            .join(Album, Album.id == Track.album_id)
        ).all()
        assert any(
            release_date is not None and saved_at.year != int(release_date[:4])
            for _, saved_at, release_date in track_dates
        )

        state = session.get(SyncState, "demo")
        assert state is not None
        assert state.status == "succeeded"
        assert state.account_id == "demo"
        assert state.saved_tracks == 120


def test_added_date_filter_uses_inclusive_utc_saved_dates(session_factory):
    with session_factory() as session:
        with session.begin():
            load_demo(session)
        start = datetime.combine(date(2021, 3, 1), time.min, tzinfo=UTC)
        end = datetime.combine(date(2023, 9, 20), time.max, tzinfo=UTC)
        actual_ids = set(
            session.scalars(
                select(SavedTrack.track_id).where(
                    SavedTrack.added_at >= start, SavedTrack.added_at <= end
                )
            ).all()
        )
        reference_ids = {
            f"demo-track-{index + 1:03d}"
            for index in range(120)
            if start
            <= datetime(
                2020 + index % 5,
                1 + index % 12,
                1 + index % 27,
                12,
                tzinfo=UTC,
            )
            <= end
        }
        assert actual_ids == reference_ids
        assert 0 < len(actual_ids) < 120

        # Saved-date filtering must not use the unrelated album release dates.
        release_match_ids = set(
            session.scalars(
                select(Track.id)
                .join(Album, Track.album_id == Album.id)
                .where(
                    Album.release_date >= start.date().isoformat(),
                    Album.release_date <= end.date().isoformat(),
                )
            ).all()
        )
        assert actual_ids != release_match_ids


def test_artist_rank_reference_uses_distinct_tracks_and_deterministic_ties(
    session_factory,
):
    with session_factory() as session:
        with session.begin():
            load_demo(session)
        counts = session.execute(
            select(
                Artist.id,
                Artist.name,
                func.count(func.distinct(TrackArtist.track_id)).label("saved_tracks"),
            )
            .join(TrackArtist, TrackArtist.artist_id == Artist.id)
            .join(SavedTrack, SavedTrack.track_id == TrackArtist.track_id)
            .group_by(Artist.id, Artist.name)
        ).all()
        ranking = sorted(
            counts, key=lambda row: (-row.saved_tracks, row.name.casefold(), row.id)
        )
        assert len(ranking) == 24
        duplicate_name_counts = {
            row.id: row.saved_tracks for row in counts if row.name == "Shared Name"
        }
        assert duplicate_name_counts == {"demo-artist-01": 8, "demo-artist-08": 8}
        ranks = {row.id: rank for rank, row in enumerate(ranking, start=1)}
        assert ranks["demo-artist-01"] < ranks["demo-artist-08"]
        assert ranks["demo-artist-23"] == 23
        assert ranks["demo-artist-24"] == 24
        assert ranking[22].saved_tracks == ranking[23].saved_tracks == 1
        assert all(1 <= row.saved_tracks <= 120 for row in ranking)


def test_repeated_load_is_idempotent_and_does_not_overwrite_existing_rows(
    session_factory,
):
    with session_factory() as session:
        with session.begin():
            load_demo(session)
        track = session.get(Track, "demo-track-001")
        track.name = "Locally preserved title"
        session.flush()
        before = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in (Track, Artist, Album, TrackArtist, SavedTrack, SyncState)
        }
        saved_time = session.get(SavedTrack, "demo-track-001").added_at
        session.commit()

        with session.begin():
            assert load_demo(session) == {"tracks": 120, "artists": 24, "albums": 12}
        after = {
            model: session.scalar(select(func.count()).select_from(model))
            for model in (Track, Artist, Album, TrackArtist, SavedTrack, SyncState)
        }
        assert after == before
        assert session.get(Track, "demo-track-001").name == "Locally preserved title"
        assert session.get(SavedTrack, "demo-track-001").added_at == saved_time


def test_demo_loader_refuses_real_library_without_changing_it(session_factory):
    with session_factory() as session:
        session.add(
            Track(
                id="spotify-track-1",
                name="Real track",
                duration_ms=1000,
                explicit=False,
            )
        )
        session.flush()
        with pytest.raises(ValueError, match="non-demo library"):
            load_demo(session)
        assert session.scalar(select(func.count()).select_from(Track)) == 1
        assert session.get(Track, "spotify-track-1").name == "Real track"
        assert session.scalar(select(func.count()).select_from(Artist)) == 0
        assert session.scalar(select(func.count()).select_from(Album)) == 0


def test_caller_rollback_removes_all_demo_rows(session_factory):
    with session_factory() as session:
        transaction = session.begin()
        assert load_demo(session) == {"tracks": 120, "artists": 24, "albums": 12}
        assert session.in_transaction()
        transaction.rollback()
        for model in (Track, Artist, Album, TrackArtist, SavedTrack, SyncState):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        session.commit()

        # A rolled-back load leaves the database ready for a complete later load.
        with session.begin():
            load_demo(session)
        assert session.scalar(select(func.count()).select_from(Track)) == 120
