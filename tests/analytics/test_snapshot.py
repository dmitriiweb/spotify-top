from __future__ import annotations

import csv
import io
import sqlite3
from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from spotify_analytics.analytics.service import AnalyticsService
from spotify_analytics.core.database import (
    create_db_engine,
    create_session_factory,
    init_db,
)
from spotify_analytics.core.models import Artist, SavedTrack, Track, TrackArtist
from spotify_analytics.core.schemas import AnalyticsFilter, PageParams


@pytest.mark.parametrize("as_export", [False, True])
def test_multi_query_analytics_reads_keep_one_library_snapshot(tmp_path, as_export):
    database_path = tmp_path / "snapshot.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
    finally:
        connection.close()
    engine = create_db_engine(f"sqlite:///{database_path}")
    init_db(engine)
    session_factory = create_session_factory(engine)
    with session_factory.begin() as session:
        session.add_all(
            [
                Artist(id="first-artist", name="First"),
                Track(
                    id="first-track",
                    name="First track",
                    duration_ms=1000,
                    explicit=False,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                SavedTrack(
                    track_id="first-track",
                    added_at=datetime(2024, 1, 1, tzinfo=UTC),
                ),
                TrackArtist(
                    track_id="first-track",
                    artist_id="first-artist",
                    artist_order=0,
                ),
            ]
        )

    changed_library = False

    def add_concurrent_track(connection, cursor, statement, parameters, context, many):
        nonlocal changed_library
        if changed_library or not statement.lstrip().upper().startswith("SELECT"):
            return
        changed_library = True
        with session_factory.begin() as writer:
            writer.add_all(
                [
                    Artist(id="second-artist", name="Second"),
                    Track(
                        id="second-track",
                        name="Second track",
                        duration_ms=1000,
                        explicit=False,
                    ),
                ]
            )
            writer.flush()
            writer.add_all(
                [
                    SavedTrack(
                        track_id="second-track",
                        added_at=datetime(2024, 1, 2, tzinfo=UTC),
                    ),
                    TrackArtist(
                        track_id="second-track",
                        artist_id="second-artist",
                        artist_order=0,
                    ),
                ]
            )

    event.listen(engine, "after_cursor_execute", add_concurrent_track)
    try:
        with session_factory() as session:
            service = AnalyticsService(session)
            if as_export:
                text = service.export_artists_csv(AnalyticsFilter()).decode("utf-8")
                rows = list(csv.DictReader(io.StringIO(text, newline="")))
                assert [row["id"] for row in rows] == ["first-artist"]
            else:
                page = service.artists(
                    filters=AnalyticsFilter(),
                    page=PageParams(limit=50, offset=0),
                    sort="saved_tracks_desc",
                )
                assert page.total == 1
                assert [item.artist.id for item in page.items] == ["first-artist"]
        assert changed_library
    finally:
        event.remove(engine, "after_cursor_execute", add_concurrent_track)
        engine.dispose()
