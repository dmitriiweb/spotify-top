"""Deterministic UTF-8 analytics exports.

Artist CSV columns are ``rank,id,name,saved_tracks,share,image_url,spotify_url``.
Track CSV columns are ``id,name,duration_ms,explicit,spotify_url,album_id,album_name,
album_release_date,album_cover_url,artist_ids,artist_names,added_at``.
CSV uses LF line endings, keeps headers for empty results, writes null metadata as
blank cells, serializes credit IDs and names as Unicode JSON arrays, and preserves
artist credit order. Track rows are ordered by saved date ascending and then
track ID.

JSON exports contain ``overview``, ``artists``, ``tracks``, ``timeline_month``,
``timeline_year``, ``distributions``, and ``insights``. They use Unicode, sorted
keys, and compact separators. Artist and track arrays contain every matching
result in their deterministic export order.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from spotify_analytics.core.schemas import AnalyticsFilter, PageParams

_PAGE_SIZE = 200


def _safe_csv_text(value: str | None) -> str:
    """Prefix spreadsheet formula markers in untrusted text cells."""
    if value is None:
        return ""
    for char in value:
        if char in ("=", "+", "-", "@", "\t", "\r", "\n"):
            return "'" + value
        if not char.isspace():
            break
    return value


def _safe_csv_value(value: Any) -> Any:
    return _safe_csv_text(value) if isinstance(value, str) else value


def _write_csv_row(writer: Any, values: tuple[Any, ...]) -> None:
    writer.writerow(map(_safe_csv_value, values))


class ExportMixin:
    """Add complete CSV and JSON exports to an analytics service."""

    def _all_artists(self, filters: AnalyticsFilter) -> list[Any]:
        items: list[Any] = []
        offset = 0
        total: int | None = None
        while total is None or offset < total:
            page = self.artists(
                filters=filters,
                page=PageParams(limit=_PAGE_SIZE, offset=offset),
                sort="saved_tracks_desc",
            )
            if total is None:
                total = page.total
            if not page.items:
                break
            items.extend(page.items)
            offset += len(page.items)
        return items

    def _all_tracks(self, filters: AnalyticsFilter) -> list[Any]:
        items: list[Any] = []
        offset = 0
        total: int | None = None
        while total is None or offset < total:
            page = self.tracks(
                filters=filters,
                page=PageParams(limit=_PAGE_SIZE, offset=offset),
                sort="added_at_asc",
            )
            if total is None:
                total = page.total
            if not page.items:
                break
            items.extend(page.items)
            offset += len(page.items)
        return items

    def export_artists_csv(self, filters: AnalyticsFilter) -> bytes:
        """Return all matching artists as deterministic UTF-8 CSV."""
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(
            ("rank", "id", "name", "saved_tracks", "share", "image_url", "spotify_url")
        )
        for ranking in self._all_artists(filters):
            artist = ranking.artist
            _write_csv_row(
                writer,
                (
                    ranking.rank,
                    artist.id,
                    artist.name,
                    ranking.saved_tracks,
                    ranking.share,
                    artist.image_url or "",
                    artist.spotify_url or "",
                ),
            )
        return output.getvalue().encode("utf-8")

    def export_tracks_csv(self, filters: AnalyticsFilter) -> bytes:
        """Return every matching saved track as deterministic UTF-8 CSV."""
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(
            (
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
            )
        )
        for track in self._all_tracks(filters):
            album = track.album
            _write_csv_row(
                writer,
                (
                    track.id,
                    track.name,
                    track.duration_ms,
                    "true" if track.explicit else "false",
                    track.spotify_url or "",
                    album.id if album is not None else "",
                    album.name if album is not None else "",
                    album.release_date
                    if album is not None and album.release_date is not None
                    else "",
                    album.cover_url
                    if album is not None and album.cover_url is not None
                    else "",
                    json.dumps(
                        [artist.id for artist in track.artists], ensure_ascii=False
                    ),
                    json.dumps(
                        [artist.name for artist in track.artists], ensure_ascii=False
                    ),
                    track.model_dump(mode="json")["added_at"],
                ),
            )
        return output.getvalue().encode("utf-8")

    def export_json(self, filters: AnalyticsFilter) -> bytes:
        """Return the complete filtered analytics document as compact JSON."""
        overview = self.overview(filters=filters)
        artists = self._all_artists(filters)
        tracks = self._all_tracks(filters)
        timeline_month = self.timeline(filters=filters, interval="month")
        timeline_year = self.timeline(filters=filters, interval="year")
        distributions = {
            dimension: self.distribution(filters=filters, dimension=dimension)
            for dimension in ("release_decade", "duration", "explicit")
        }
        insights = self.insights(filters=filters)
        document = {
            "overview": overview.model_dump(mode="json"),
            "artists": [item.model_dump(mode="json") for item in artists],
            "tracks": [item.model_dump(mode="json") for item in tracks],
            "timeline_month": timeline_month.model_dump(mode="json"),
            "timeline_year": timeline_year.model_dump(mode="json"),
            "distributions": {
                key: value.model_dump(mode="json")
                for key, value in distributions.items()
            },
            "insights": insights.model_dump(mode="json"),
        }
        return json.dumps(
            document,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
