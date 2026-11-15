from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from spotify_analytics.core.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from spotify_analytics.core.models import (
    Album,
    Artist,
    SavedTrack,
    SyncState,
    Track,
    TrackArtist,
)
from spotify_analytics.spotify.sync import SyncInProgress, SyncService


def test_run_publishes_only_after_all_pages_and_persists_progress(database):
    _, factory = database
    service = SyncService(factory)
    service.start("account-a")

    class PagedClient:
        async def iter_saved_tracks(self):
            yield [saved("page-1")]
            assert service.status().processed_tracks == 1
            yield [saved("page-2")]

    asyncio.run(service.run(PagedClient(), "account-a"))
    status = service.status()
    assert status.status == "succeeded"
    assert status.processed_tracks == 2
    assert status.saved_tracks == 2


def test_run_fetch_failure_marks_failed_without_publishing_partial_pages(database):
    _, factory = database
    service = SyncService(factory)
    service.start("account-a")
    prior = service.publish("account-a", [saved("stable")], 1)
    service.start("account-a")

    class BrokenClient:
        async def iter_saved_tracks(self):
            yield [saved("partial")]
            raise RuntimeError("secret token in provider exception")

    asyncio.run(service.run(BrokenClient(), "account-a"))
    status = service.status()
    assert status.status == "failed"
    assert status.error == "Synchronization failed"
    assert status.last_successful_sync == prior.last_successful_sync
    assert status.saved_tracks == 1
    with factory() as db:
        assert db.get(SavedTrack, "stable") is not None
        assert db.get(SavedTrack, "partial") is None


def test_run_mapping_failure_preserves_previous_snapshot(database):
    _, factory = database
    service = SyncService(factory)
    service.start("account-a")
    previous = service.publish("account-a", [saved("stable")], 1)
    service.start("account-a")
    malformed = saved("malformed")
    malformed["track"]["artists"] = "not-a-list"

    class MappingFailureClient:
        async def iter_saved_tracks(self):
            yield [malformed]

    asyncio.run(service.run(MappingFailureClient(), "account-a"))
    status = service.status()
    assert status.status == "failed"
    assert status.last_successful_sync == previous.last_successful_sync
    assert status.saved_tracks == 1
    with factory() as db:
        assert db.get(SavedTrack, "stable") is not None
        assert db.get(SavedTrack, "malformed") is None


@pytest.fixture
def database(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'sync.db'}")
    init_db(engine)
    factory = create_session_factory(engine)
    yield engine, factory
    engine.dispose()


_DEFAULT_ALBUM = object()


def saved(
    track_id: str,
    *,
    name: str | None = None,
    artists=None,
    album=_DEFAULT_ALBUM,
    added_at="2024-01-02T03:04:05Z",
):
    return {
        "added_at": added_at,
        "track": {
            "id": track_id,
            "name": name or f"Track {track_id}",
            "duration_ms": 120_000,
            "explicit": False,
            "external_urls": {"spotify": f"https://open.spotify.com/track/{track_id}"},
            "artists": artists
            if artists is not None
            else [
                {
                    "id": "artist-a",
                    "name": "Artist A",
                    "images": [{"url": "https://image/a"}],
                    "external_urls": {"spotify": "https://open.spotify.com/artist/a"},
                },
                {
                    "id": "artist-b",
                    "name": "Artist B",
                    "images": [],
                    "external_urls": {},
                },
            ],
            "album": {
                "id": "album-a",
                "name": "Album A",
                "release_date": "2024-03",
                "images": [{"url": "https://image/album"}],
            }
            if album is _DEFAULT_ALBUM
            else album,
        },
    }


def test_fresh_status_claim_progress_and_cross_service_claim(database):
    _, factory = database
    first = SyncService(factory)
    second = SyncService(factory)
    assert first.status().model_dump() == {
        "account_id": None,
        "status": "idle",
        "last_successful_sync": None,
        "error": None,
        "saved_tracks": 0,
        "artists": 0,
        "albums": 0,
        "processed_tracks": 0,
    }
    assert first.start("account-a").status == "running"
    with pytest.raises(SyncInProgress):
        second.start("account-b")
    second.progress(4)
    assert first.status().processed_tracks == 4


def test_publish_maps_order_deduplicates_and_updates_metadata_idempotently(database):
    _, factory = database
    service = SyncService(factory)
    service.start("account-a")
    duplicate_artist = {
        "id": "artist-a",
        "name": "Artist A changed",
        "images": [],
        "external_urls": {},
    }
    records = [
        saved(
            "track-1",
            artists=[
                {
                    "id": "artist-b",
                    "name": "Artist B",
                    "images": [],
                    "external_urls": {},
                },
                {
                    "id": "artist-a",
                    "name": "Artist A",
                    "images": [],
                    "external_urls": {},
                },
                duplicate_artist,
            ],
        ),
        saved(
            "track-2",
            album={
                "id": "album-a",
                "name": "Album updated",
                "release_date": "2025",
                "images": [],
            },
        ),
        saved(
            "track-2",
            name="Track two latest",
            artists=[duplicate_artist],
            added_at="2024-02-03T00:00:00+02:00",
        ),
    ]
    first = service.publish("account-a", records, 3)
    assert first.status == "succeeded"
    assert (
        first.saved_tracks,
        first.artists,
        first.albums,
        first.processed_tracks,
    ) == (2, 2, 1, 3)
    with factory() as db:
        credits = db.scalars(
            select(TrackArtist)
            .where(TrackArtist.track_id == "track-1")
            .order_by(TrackArtist.artist_order)
        ).all()
        assert [(credit.artist_id, credit.artist_order) for credit in credits] == [
            ("artist-b", 0),
            ("artist-a", 1),
        ]
        assert db.get(Artist, "artist-a").name == "Artist A changed"
        assert db.get(Album, "album-a").name == "Album A"
        track = db.get(Track, "track-2")
        assert track.name == "Track two latest"
        assert db.get(SavedTrack, "track-2").added_at == datetime(
            2024, 2, 2, 22, 0, tzinfo=UTC
        )
        assert db.scalar(select(func.count()).select_from(SavedTrack)) == 2

    service.start("account-a")
    repeated = service.publish("account-a", records, 3)
    assert (repeated.saved_tracks, repeated.artists, repeated.albums) == (2, 2, 1)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(SavedTrack)) == 2
        assert db.scalar(select(func.count()).select_from(TrackArtist)) == 3

    service.start("account-a")
    changed = saved(
        "track-1",
        name="Updated",
        artists=[
            {"id": "artist-c", "name": "Artist C", "images": [], "external_urls": {}}
        ],
        album=None,
    )
    second = service.publish("account-a", [changed], 1)
    assert (second.saved_tracks, second.artists, second.albums) == (1, 1, 0)
    with factory() as db:
        assert db.get(Track, "track-1").name == "Updated"
        assert db.get(Track, "track-1").album_id is None
        assert db.get(Album, "album-a") is not None
        assert db.get(Track, "track-2") is not None
        assert db.get(SavedTrack, "track-2") is None
        assert (
            db.scalar(
                select(func.count())
                .select_from(TrackArtist)
                .where(TrackArtist.track_id == "track-1")
            )
            == 1
        )


def test_empty_snapshot_and_account_replacement(database):
    _, factory = database
    service = SyncService(factory)
    service.start("old-account")
    service.publish("old-account", [saved("old-track")], 1)
    service.start("new-account")
    result = service.publish("new-account", [], 0)
    assert result.account_id == "new-account"
    assert result.status == "succeeded"
    assert (
        result.saved_tracks,
        result.artists,
        result.albums,
        result.processed_tracks,
    ) == (0, 0, 0, 0)
    with factory() as db:
        assert db.scalars(select(SyncState.account_id)).all() == ["new-account"]
        assert db.scalar(select(func.count()).select_from(SavedTrack)) == 0
        # Metadata is retained even when the saved relation is removed.
        assert db.get(Track, "old-track") is not None
        assert db.get(Artist, "artist-a") is not None


def test_failure_preserves_library_and_success_timestamp(database):
    _, factory = database
    service = SyncService(factory)
    service.start("account-a")
    previous = service.publish("account-a", [saved("track-1")], 1)
    service.start("other-account")
    failed = service.fail("secret-bearing unexpected exception")
    assert failed.status == "failed"
    assert failed.error == "Synchronization failed"
    assert failed.account_id == "account-a"
    assert failed.last_successful_sync == previous.last_successful_sync
    assert (failed.saved_tracks, failed.artists, failed.albums) == (1, 2, 1)
    with factory() as db:
        assert db.get(SavedTrack, "track-1") is not None
        assert db.scalars(select(SyncState.account_id)).all() == ["account-a"]


def test_recover_interrupted_preserves_library_counts_and_success_time(database):
    _, factory = database
    service = SyncService(factory)
    service.start("account-a")
    successful = service.publish("account-a", [saved("track-1")], 1)
    service.start("account-a")
    service.progress(7)

    recovered = service.recover_interrupted()

    assert recovered.status == "failed"
    assert recovered.error == "Synchronization was interrupted"
    assert recovered.last_successful_sync == successful.last_successful_sync
    assert (
        recovered.saved_tracks,
        recovered.artists,
        recovered.albums,
        recovered.processed_tracks,
    ) == (successful.saved_tracks, successful.artists, successful.albums, 7)
    assert service.recover_interrupted() == recovered
    with factory() as db:
        assert db.get(SavedTrack, "track-1") is not None


def test_database_failure_rolls_back_entire_snapshot_and_success_state(database):
    engine, factory = database
    service = SyncService(factory)
    service.start("account-a")
    prior = service.publish("account-a", [saved("stable")], 1)
    service.start("account-a")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TRIGGER reject_new_track BEFORE INSERT ON tracks "
                "WHEN NEW.id = 'broken' BEGIN SELECT RAISE(ABORT, "
                "'injected failure'); END"
            )
        )

    with pytest.raises(IntegrityError):
        service.publish("account-a", [saved("replacement"), saved("broken")], 2)
    # DB failure transaction rolled back, including removal of prior saved row.
    with factory() as db:
        assert db.get(SavedTrack, "stable") is not None
        assert db.get(SavedTrack, "replacement") is None
        assert (
            db.get(SyncState, "account-a").last_successful_sync
            == prior.last_successful_sync
        )
    failed = service.fail("Synchronization failed")
    assert failed.status == "failed"
    assert failed.saved_tracks == 1

    service.start("replacement-account")
    with pytest.raises(IntegrityError):
        service.publish("replacement-account", [saved("broken")], 1)
    with factory() as db:
        assert db.get(SyncState, "account-a") is not None
        assert db.get(SyncState, "replacement-account") is None
        assert db.get(SavedTrack, "stable") is not None


def test_mapping_rejects_invalid_or_naive_snapshot_without_touching_database(database):
    _, factory = database
    service = SyncService(factory)
    service.start("account-a")
    with pytest.raises(ValueError):
        service.publish("account-a", [saved("bad", added_at="2024-01-01T00:00:00")], 1)
    with factory() as db:
        state = db.get(SyncState, "account-a")
        assert state.status == "running"
        assert state.last_successful_sync is None
        assert db.scalar(select(func.count()).select_from(Track)) == 0


def test_nullable_metadata_and_release_date_precision(database):
    _, factory = database
    service = SyncService(factory)
    service.start("account-a")
    item = saved(
        "minimal",
        artists=[{"id": "a", "name": "A", "images": [], "external_urls": {}}],
        album={
            "id": "year-album",
            "name": "Year",
            "release_date": "1984",
            "images": [],
        },
    )
    result = service.publish("account-a", [item], 1)
    assert (result.saved_tracks, result.artists, result.albums) == (1, 1, 1)
    with factory() as db:
        assert db.get(Album, "year-album").release_date == "1984"
        artist = db.get(Artist, "a")
        assert artist.image_url is None
        assert artist.spotify_url is None
