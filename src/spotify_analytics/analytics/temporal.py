from __future__ import annotations

import calendar
from datetime import date
from typing import Literal

from sqlalchemy import Integer, String, case, cast, func, select
from sqlalchemy.orm import Session

from spotify_analytics.analytics.queries import filtered_tracks, register_casefold
from spotify_analytics.core.models import Album, Artist, TrackArtist
from spotify_analytics.core.schemas import (
    AnalyticsFilter,
    DistributionBucket,
    DistributionResponse,
    Insight,
    InsightsResponse,
    TimelineBucket,
    TimelineResponse,
)

_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class TemporalAnalyticsMixin:
    """SQL-backed timeline, distribution, and deterministic insight queries."""

    session: Session

    def timeline(
        self, filters: AnalyticsFilter, interval: Literal["month", "year"]
    ) -> TimelineResponse:
        register_casefold(self.session)
        if interval not in ("month", "year"):
            raise ValueError(f"Unsupported timeline interval: {interval}")

        tracks = filtered_tracks(filters)
        period = func.strftime(
            "%Y-%m" if interval == "month" else "%Y", tracks.c.added_at
        )
        rows = self.session.execute(
            select(period.label("period"), func.count().label("tracks"))
            .select_from(tracks)
            .group_by(period)
        ).all()
        counts = {str(row.period): int(row.tracks) for row in rows}
        if not counts:
            return TimelineResponse(filters=filters, interval=interval, buckets=[])

        first = min(
            date.fromisoformat(key + ("-01" if interval == "month" else "-01-01"))
            for key in counts
        )
        last = max(
            date.fromisoformat(key + ("-01" if interval == "month" else "-01-01"))
            for key in counts
        )
        if filters.added_from is not None:
            first = min(first, _period_start(filters.added_from, interval))
        if filters.added_to is not None:
            last = max(last, _period_start(filters.added_to, interval))

        buckets: list[TimelineBucket] = []
        current = _period_start(first, interval)
        last = _period_start(last, interval)
        while current <= last:
            if interval == "month":
                end = date(
                    current.year,
                    current.month,
                    calendar.monthrange(current.year, current.month)[1],
                )
                key = f"{current.year:04d}-{current.month:02d}"
                next_start = (
                    date(
                        current.year + (current.month == 12),
                        1 if current.month == 12 else current.month + 1,
                        1,
                    )
                    if not (current.year == 9999 and current.month == 12)
                    else None
                )
            else:
                end = date(current.year, 12, 31)
                key = f"{current.year:04d}"
                next_start = (
                    date(current.year + 1, 1, 1) if current.year < 9999 else None
                )
            buckets.append(
                TimelineBucket(start=current, end=end, tracks=counts.get(key, 0))
            )
            if next_start is None:
                break
            current = next_start

        return TimelineResponse(filters=filters, interval=interval, buckets=buckets)

    def distribution(
        self,
        filters: AnalyticsFilter,
        dimension: Literal["release_decade", "duration", "explicit"],
    ) -> DistributionResponse:
        register_casefold(self.session)
        if dimension not in ("release_decade", "duration", "explicit"):
            raise ValueError(f"Unsupported distribution dimension: {dimension}")

        tracks = filtered_tracks(filters)
        if dimension == "duration":
            key_expr = case(
                (tracks.c.duration_ms < 180_000, "under_180"),
                (tracks.c.duration_ms < 300_000, "180_to_299"),
                else_="300_plus",
            )
            labels = {
                "under_180": "Under 3 minutes",
                "180_to_299": "3 to under 5 minutes",
                "300_plus": "5 minutes or more",
            }
            order = ("under_180", "180_to_299", "300_plus")
            query = (
                select(key_expr.label("key"), func.count().label("tracks"))
                .select_from(tracks)
                .group_by(key_expr)
            )
        elif dimension == "explicit":
            key_expr = case((tracks.c.explicit.is_(True), "explicit"), else_="clean")
            labels = {"clean": "Clean", "explicit": "Explicit"}
            order = ("clean", "explicit")
            query = (
                select(key_expr.label("key"), func.count().label("tracks"))
                .select_from(tracks)
                .group_by(key_expr)
            )
        else:
            release_date = Album.release_date
            year = cast(func.substr(release_date, 1, 4), String)
            month = cast(func.substr(release_date, 6, 2), Integer)
            day = cast(func.substr(release_date, 9, 2), Integer)
            year_int = cast(func.substr(release_date, 1, 4), Integer)
            max_day = case(
                (
                    month == 2,
                    case(
                        (
                            (year_int % 4 == 0)
                            & ((year_int % 100 != 0) | (year_int % 400 == 0)),
                            29,
                        ),
                        else_=28,
                    ),
                ),
                (month.in_((4, 6, 9, 11)), 30),
                else_=31,
            )
            valid_year = (func.length(release_date) == 4) & year_int.between(1, 9999)
            valid_month = (
                (func.length(release_date) == 7)
                & (func.substr(release_date, 5, 1) == "-")
                & year_int.between(1, 9999)
                & month.between(1, 12)
            )
            valid_day = (
                (func.length(release_date) == 10)
                & (func.substr(release_date, 5, 1) == "-")
                & (func.substr(release_date, 8, 1) == "-")
                & year_int.between(1, 9999)
                & month.between(1, 12)
                & day.between(1, max_day)
            )
            numeric_parts = (
                release_date.bool_op("GLOB")("[0-9][0-9][0-9][0-9]")
                | release_date.bool_op("GLOB")("[0-9][0-9][0-9][0-9]-[0-9][0-9]")
                | release_date.bool_op("GLOB")(
                    "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"
                )
            )
            valid = numeric_parts & (valid_year | valid_month | valid_day)
            decade = func.substr(year, 1, 3) + "0"
            key_expr = case((valid, decade), else_="unknown")
            query = (
                select(key_expr.label("key"), func.count().label("tracks"))
                .select_from(tracks)
                .outerjoin(Album, Album.id == tracks.c.album_id)
                .group_by(key_expr)
            )
            labels = {}
            order = ()
        rows = self.session.execute(query).all()
        counts = {str(row.key): int(row.tracks) for row in rows}
        if dimension == "release_decade":
            decades = sorted((key for key in counts if key != "unknown"), key=int)
            order = tuple(decades + (["unknown"] if "unknown" in counts else []))
            labels = {key: f"{key}s" for key in decades}
            labels["unknown"] = "Unknown"
        buckets = [
            DistributionBucket(key=key, label=labels[key], tracks=counts[key])
            for key in order
            if counts.get(key, 0)
        ]
        return DistributionResponse(
            filters=filters, dimension=dimension, buckets=buckets
        )

    def insights(self, filters: AnalyticsFilter) -> InsightsResponse:
        register_casefold(self.session)
        tracks = filtered_tracks(filters)
        total, duration = self.session.execute(
            select(
                func.count(), func.coalesce(func.sum(tracks.c.duration_ms), 0)
            ).select_from(tracks)
        ).one()
        if not total:
            return InsightsResponse(filters=filters, items=[])

        artist_rows = self.session.execute(
            select(Artist.id, Artist.name, func.count().label("tracks"))
            .select_from(tracks)
            .join(TrackArtist, TrackArtist.track_id == tracks.c.track_id)
            .join(Artist, Artist.id == TrackArtist.artist_id)
            .group_by(Artist.id, Artist.name)
            .order_by(
                func.count().desc(),
                func.analytics_casefold(Artist.name),
                Artist.id,
            )
            .limit(1)
        ).all()
        items: list[Insight] = []
        if artist_rows:
            artist = artist_rows[0]
            items.append(
                Insight(
                    code="top_artist",
                    title="Most saved tracks",
                    description=(
                        f"{artist.name} appears on {artist.tracks} saved tracks."
                    ),
                    artist_id=artist.id,
                    value=int(artist.tracks),
                )
            )

        month = func.strftime("%Y-%m", tracks.c.added_at)
        month_row = self.session.execute(
            select(month.label("month"), func.count().label("tracks"))
            .select_from(tracks)
            .group_by(month)
            .order_by(
                func.count().desc(),
                month.asc(),
            )
            .limit(1)
        ).one()
        month_start = date.fromisoformat(f"{month_row.month}-01")
        items.append(
            Insight(
                code="largest_save_month",
                title="Largest save month",
                description=(
                    f"{_MONTH_NAMES[month_start.month - 1]} {month_start.year} "
                    f"had {month_row.tracks} saved tracks."
                ),
                value=int(month_row.tracks),
            )
        )
        items.append(
            Insight(
                code="library_duration",
                title="Saved-track duration",
                description=f"The matching tracks total {int(duration)} ms.",
                value=int(duration),
            )
        )
        return InsightsResponse(filters=filters, items=items)


def _period_start(value: date, interval: str) -> date:
    return date(value.year, value.month if interval == "month" else 1, 1)
