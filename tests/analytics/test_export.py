from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
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
from spotify_analytics.core.schemas import AnalyticsFilter

ARTIST_HEADER = [
    "rank",
    "id",
    "name",
    "saved_tracks",
    "share",
    "image_url",
    "spotify_url",
]
TRACK_HEADER = [
    "id",
    "name",
    "duration_ms",
    "explicit",
    "spotify_url",
    "album_id",
    "album_name",
    "album_release_date",
    "album_cover_url",
    "artist_ids",
    "artist_names",
    "added_at",
]


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'export.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value
    engine.dispose()


def add_track(
    session: Session,
    track_id: str,
    name: str,
    added: datetime,
    *,
    duration: int = 120000,
    explicit: bool = False,
    album: tuple[str, str, str | None, str | None] | None = None,
    credits: tuple[tuple[str, str], ...] = (),
) -> None:
    album_id = None
    if album is not None:
        album_id, album_name, release, cover_url = album
        if session.get(Album, album_id) is None:
            session.add(
                Album(
                    id=album_id,
                    name=album_name,
                    release_date=release,
                    cover_url=cover_url,
                )
            )
    session.add(
        Track(
            id=track_id,
            name=name,
            duration_ms=duration,
            explicit=explicit,
            spotify_url=None,
            album_id=album_id,
        )
    )
    session.add(SavedTrack(track_id=track_id, added_at=added))
    for order, (artist_id, artist_name) in enumerate(credits):
        if session.get(Artist, artist_id) is None:
            session.add(Artist(id=artist_id, name=artist_name))
        session.add(
            TrackArtist(track_id=track_id, artist_id=artist_id, artist_order=order)
        )
    session.flush()


def parse_csv(payload: bytes) -> tuple[list[str], list[dict[str, str]]]:
    text = payload.decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text, newline="")))
    return next(csv.reader(io.StringIO(text))), rows


def test_empty_csv_exports_have_contract_headers_and_deterministic_utf8(session):
    service = AnalyticsService(session)
    artists = service.export_artists_csv(AnalyticsFilter())
    tracks = service.export_tracks_csv(AnalyticsFilter())
    assert not artists.startswith(b"\xef\xbb\xbf")
    assert not tracks.startswith(b"\xef\xbb\xbf")
    assert parse_csv(artists) == (ARTIST_HEADER, [])
    assert parse_csv(tracks) == (TRACK_HEADER, [])
    assert artists.endswith(b"\n") and tracks.endswith(b"\n")
    assert artists == service.export_artists_csv(AnalyticsFilter())
    assert tracks == service.export_tracks_csv(AnalyticsFilter())


def test_csv_exports_unicode_quoting_newlines_metadata_and_credit_order(session):
    add_track(
        session,
        "b",
        'Quoted, "line"\n二',
        datetime(2024, 1, 2, 3, tzinfo=UTC),
        explicit=True,
        album=("album", 'Album, "quoted"\n名称', "2020-05", "https://img/cover"),
        credits=(("artist-z", "Zoë"), ("artist-a", "Åsa")),
    )
    add_track(session, "a", "No metadata", datetime(2024, 1, 1, tzinfo=UTC))
    artist_csv = AnalyticsService(session).export_artists_csv(AnalyticsFilter())
    track_csv = AnalyticsService(session).export_tracks_csv(AnalyticsFilter())
    assert b"\xef\xbb\xbf" not in artist_csv and b"\xef\xbb\xbf" not in track_csv
    assert b"\r\n" not in artist_csv and b"\r\n" not in track_csv
    artist_header, artist_rows = parse_csv(artist_csv)
    assert artist_header == ARTIST_HEADER
    assert [row["id"] for row in artist_rows] == ["artist-z", "artist-a"]
    track_header, rows = parse_csv(track_csv)
    assert track_header == TRACK_HEADER
    assert [row["id"] for row in rows] == ["a", "b"]
    assert rows[0]["album_id"] == rows[0]["album_name"] == ""
    assert rows[0]["album_release_date"] == rows[0]["album_cover_url"] == ""
    assert rows[0]["artist_ids"] == "[]" and rows[0]["artist_names"] == "[]"
    assert rows[1]["explicit"] == "true"
    assert rows[1]["album_name"] == 'Album, "quoted"\n名称'
    assert rows[1]["album_release_date"] == "2020-05"
    assert json.loads(rows[1]["artist_ids"]) == ["artist-z", "artist-a"]
    assert json.loads(rows[1]["artist_names"]) == ["Zoë", "Åsa"]
    assert rows[1]["added_at"] == "2024-01-02T03:00:00Z"
    assert "二".encode() in track_csv and "Åsa".encode() in artist_csv
    assert track_csv == AnalyticsService(session).export_tracks_csv(AnalyticsFilter())


def test_csv_exports_escape_formula_markers_in_all_text_metadata(session):
    add_track(
        session,
        "\t=track",
        " \t=track name",
        datetime(2024, 1, 2, tzinfo=UTC),
        album=("+album", " \t=album name", "-2024", "\r@cover"),
        credits=(("@artist", " \n+artist"),),
    )
    track = session.get(Track, "\t=track")
    artist = session.get(Artist, "@artist")
    track.spotify_url = "\n@track"
    artist.image_url = "\t-IMAGE()"
    artist.spotify_url = " \r=HYPERLINK()"

    service = AnalyticsService(session)
    _, artist_rows = parse_csv(service.export_artists_csv(AnalyticsFilter()))
    _, track_rows = parse_csv(service.export_tracks_csv(AnalyticsFilter()))
    assert artist_rows == [
        {
            "rank": "1",
            "id": "'@artist",
            "name": "' \n+artist",
            "saved_tracks": "1",
            "share": "1.0",
            "image_url": "'\t-IMAGE()",
            "spotify_url": "' \r=HYPERLINK()",
        }
    ]
    assert track_rows[0]["id"] == "'\t=track"
    assert track_rows[0]["name"] == "' \t=track name"
    assert track_rows[0]["spotify_url"] == "'\n@track"
    assert track_rows[0]["album_id"] == "'+album"
    assert track_rows[0]["album_name"] == "' \t=album name"
    assert track_rows[0]["album_release_date"] == "'-2024"
    assert track_rows[0]["album_cover_url"] == "'\r@cover"
    document = json.loads(service.export_json(AnalyticsFilter()))
    assert document["tracks"][0]["id"] == "\t=track"
    assert document["tracks"][0]["name"] == " \t=track name"
    assert document["tracks"][0]["album"]["name"] == " \t=album name"
    assert document["artists"][0]["artist"]["name"] == " \n+artist"


def test_csv_filters_intersect_and_exports_all_matching_artist_and_track_rows(session):
    add_track(
        session,
        "match",
        "Café",
        datetime(2024, 2, 1, tzinfo=UTC),
        explicit=True,
        album=("a", "Álbum", "1999", None),
        credits=(("one", "Straße"), ("two", "Other")),
    )
    add_track(
        session,
        "wrong-date",
        "Café",
        datetime(2023, 2, 1, tzinfo=UTC),
        explicit=True,
        album=("a", "Álbum", "1999", None),
        credits=(("one", "Straße"),),
    )
    add_track(
        session,
        "wrong-explicit",
        "Café",
        datetime(2024, 2, 2, tzinfo=UTC),
        explicit=False,
        album=("a", "Álbum", "1999", None),
        credits=(("one", "Straße"),),
    )
    add_track(
        session,
        "wrong-artist",
        "Café",
        datetime(2024, 2, 3, tzinfo=UTC),
        explicit=True,
        album=("a", "Álbum", "1999", None),
        credits=(("three", "Other"),),
    )
    filters = AnalyticsFilter(
        added_from=datetime(2024, 2, 1).date(),
        added_to=datetime(2024, 2, 1).date(),
        artist_id="one",
        album_id="a",
        q="STRASSE",
        explicit=True,
    )
    service = AnalyticsService(session)
    _, track_rows = parse_csv(service.export_tracks_csv(filters))
    assert [row["id"] for row in track_rows] == ["match"]
    _, artist_rows = parse_csv(service.export_artists_csv(filters))
    assert [row["id"] for row in artist_rows] == ["two", "one"]


def test_exports_include_more_than_200_rows_and_repeat_as_identical_bytes(session):
    for index in range(205):
        add_track(
            session,
            f"track-{index:03}",
            f"Track {index:03}",
            datetime(2024, 1, 1, tzinfo=UTC),
            credits=((f"artist-{index:03}", f"Artist {index:03}"),),
        )
    service = AnalyticsService(session)
    artists_first = service.export_artists_csv(AnalyticsFilter())
    tracks_first = service.export_tracks_csv(AnalyticsFilter())
    artist_rows = parse_csv(artists_first)[1]
    assert len(parse_csv(tracks_first)[1]) == 205
    assert len(artist_rows) == 205
    assert {row["id"] for row in artist_rows} == {
        f"artist-{index:03}" for index in range(205)
    }
    assert artists_first == service.export_artists_csv(AnalyticsFilter())
    assert tracks_first == service.export_tracks_csv(AnalyticsFilter())


def test_json_export_contains_analytic_values_all_tracks_and_stable_bytes(session):
    add_track(
        session,
        "later",
        "Later",
        datetime(2024, 2, 1, tzinfo=UTC),
        duration=300000,
        credits=(("artist", "Zoë"),),
    )
    add_track(
        session,
        "earlier",
        "Earlier",
        datetime(2024, 1, 1, tzinfo=UTC),
        duration=180000,
        explicit=True,
        album=("album", "Album", "1980-01", None),
        credits=(("artist", "Zoë"),),
    )
    service = AnalyticsService(session)
    payload = service.export_json(AnalyticsFilter())
    document = json.loads(payload.decode("utf-8"))
    assert payload == service.export_json(AnalyticsFilter())
    assert not payload.startswith(b"\xef\xbb\xbf")
    assert list(document) == [
        "artists",
        "distributions",
        "insights",
        "overview",
        "timeline_month",
        "timeline_year",
        "tracks",
    ]
    assert document["overview"]["total_tracks"] == 2
    assert document["overview"]["total_duration_ms"] == 480000
    assert [row["id"] for row in document["tracks"]] == ["earlier", "later"]
    assert document["tracks"][0]["added_at"] == "2024-01-01T00:00:00Z"
    assert document["distributions"]["duration"]["buckets"] == [
        {"key": "180_to_299", "label": "3 to under 5 minutes", "tracks": 1},
        {"key": "300_plus", "label": "5 minutes or more", "tracks": 1},
    ]
    assert [item["code"] for item in document["insights"]["items"]] == [
        "top_artist",
        "largest_save_month",
        "library_duration",
    ]
    assert "Zoë" in payload.decode("utf-8")
