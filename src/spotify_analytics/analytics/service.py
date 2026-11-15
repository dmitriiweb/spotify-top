from __future__ import annotations

from typing import cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from spotify_analytics.analytics.export import ExportMixin
from spotify_analytics.analytics.queries import filtered_tracks, register_casefold
from spotify_analytics.analytics.temporal import TemporalAnalyticsMixin
from spotify_analytics.core.models import Album, Artist, TrackArtist
from spotify_analytics.core.schemas import (
    AlbumSummary,
    AnalyticsFilter,
    ArtistDetailResponse,
    ArtistPage,
    ArtistRanking,
    ArtistSort,
    ArtistSummary,
    OverviewResponse,
    PageParams,
    SavedTrackResponse,
    TrackPage,
    TrackSort,
)


class AnalyticsService(TemporalAnalyticsMixin, ExportMixin):
    """Calculate read-only analytics using the supplied synchronous session."""

    def __init__(self, session: Session) -> None:
        self.session = session
        register_casefold(session)

    def overview(self, filters: AnalyticsFilter) -> OverviewResponse:
        register_casefold(self.session)
        tracks = filtered_tracks(filters)
        totals = self.session.execute(
            select(
                func.count(tracks.c.track_id),
                func.coalesce(func.sum(tracks.c.duration_ms), 0),
                func.min(tracks.c.added_at),
                func.max(tracks.c.added_at),
            )
        ).one()
        total_artists = (
            self.session.scalar(
                select(func.count(func.distinct(TrackArtist.artist_id))).join(
                    tracks, tracks.c.track_id == TrackArtist.track_id
                )
            )
            or 0
        )
        total_albums = (
            self.session.scalar(
                select(func.count(func.distinct(tracks.c.album_id))).where(
                    tracks.c.album_id.is_not(None)
                )
            )
            or 0
        )
        return OverviewResponse(
            filters=filters,
            total_tracks=totals[0],
            total_artists=total_artists,
            total_albums=total_albums,
            total_duration_ms=totals[1],
            first_saved_at=totals[2],
            last_saved_at=totals[3],
        )

    def artists(
        self, filters: AnalyticsFilter, page: PageParams, sort: ArtistSort
    ) -> ArtistPage:
        register_casefold(self.session)
        if sort not in ("saved_tracks_desc", "name_asc"):
            raise ValueError(f"Unsupported artist sort: {sort}")
        tracks = filtered_tracks(filters)
        counts = (
            select(
                TrackArtist.artist_id.label("artist_id"),
                func.count(func.distinct(tracks.c.track_id)).label("saved_tracks"),
            )
            .join(tracks, tracks.c.track_id == TrackArtist.track_id)
            .group_by(TrackArtist.artist_id)
            .subquery("artist_counts")
        )
        ranked = (
            select(
                counts.c.artist_id,
                counts.c.saved_tracks,
                Artist.name.label("name"),
                func.row_number()
                .over(
                    order_by=(
                        counts.c.saved_tracks.desc(),
                        func.analytics_casefold(Artist.name).asc(),
                        Artist.id.asc(),
                    )
                )
                .label("rank"),
            )
            .join(Artist, Artist.id == counts.c.artist_id)
            .subquery("ranked_artists")
        )
        total = self.session.scalar(select(func.count()).select_from(ranked)) or 0
        order = (
            (func.analytics_casefold(ranked.c.name).asc(), ranked.c.artist_id.asc())
            if sort == "name_asc"
            else (ranked.c.rank.asc(),)
        )
        rows = (
            self.session.execute(
                select(ranked).order_by(*order).offset(page.offset).limit(page.limit)
            )
            .mappings()
            .all()
        )
        artist_ids = [row["artist_id"] for row in rows]
        artists_by_id = self._artist_summaries(artist_ids)
        denominator = self.session.scalar(select(func.count()).select_from(tracks)) or 0
        items = [
            ArtistRanking(
                artist=artists_by_id[row["artist_id"]],
                saved_tracks=row["saved_tracks"],
                rank=row["rank"],
                share=(row["saved_tracks"] / denominator if denominator else 0.0),
            )
            for row in rows
        ]
        return ArtistPage(
            filters=filters,
            items=items,
            total=total,
            limit=page.limit,
            offset=page.offset,
        )

    def tracks(
        self, filters: AnalyticsFilter, page: PageParams, sort: TrackSort
    ) -> TrackPage:
        register_casefold(self.session)
        return self._track_page(filters, page, sort)

    def artist_detail(
        self,
        artist_id: str,
        filters: AnalyticsFilter,
        page: PageParams,
        sort: TrackSort,
    ) -> ArtistDetailResponse:
        register_casefold(self.session)
        if sort not in ("added_at_desc", "added_at_asc", "name_asc", "duration_desc"):
            raise ValueError(f"Unsupported track sort: {sort}")
        artist = self.session.get(Artist, artist_id)
        if artist is None:
            raise LookupError(artist_id)
        summary = self._summary(artist)
        tracks = filtered_tracks(filters)
        scoped = (
            select(tracks)
            .where(
                select(TrackArtist.track_id)
                .where(
                    TrackArtist.track_id == tracks.c.track_id,
                    TrackArtist.artist_id == artist_id,
                )
                .exists()
            )
            .subquery("artist_tracks")
        )
        aggregate = self.session.execute(
            select(
                func.count(scoped.c.track_id),
                func.coalesce(func.sum(scoped.c.duration_ms), 0),
            )
        ).one()
        rank = self._artist_rank(filters, artist_id)
        track_page = self._track_page(filters, page, sort, artist_id=artist_id)
        return ArtistDetailResponse(
            filters=filters,
            artist=summary,
            saved_tracks=aggregate[0],
            total_duration_ms=aggregate[1],
            rank=rank,
            tracks=track_page,
        )

    def _artist_rank(self, filters: AnalyticsFilter, artist_id: str) -> int | None:
        tracks = filtered_tracks(filters)
        counts = (
            select(
                TrackArtist.artist_id.label("artist_id"),
                func.count(func.distinct(tracks.c.track_id)).label("saved_tracks"),
            )
            .join(tracks, tracks.c.track_id == TrackArtist.track_id)
            .group_by(TrackArtist.artist_id)
            .subquery("artist_counts")
        )
        ranked = (
            select(
                counts.c.artist_id,
                func.row_number()
                .over(
                    order_by=(
                        counts.c.saved_tracks.desc(),
                        func.analytics_casefold(Artist.name).asc(),
                        Artist.id.asc(),
                    )
                )
                .label("rank"),
            )
            .join(Artist, Artist.id == counts.c.artist_id)
            .subquery("ranked_artists")
        )
        rank = self.session.scalar(
            select(ranked.c.rank).where(ranked.c.artist_id == artist_id)
        )
        return cast(int | None, rank)

    def _track_page(
        self,
        filters: AnalyticsFilter,
        page: PageParams,
        sort: TrackSort,
        *,
        artist_id: str | None = None,
    ) -> TrackPage:
        if sort not in ("added_at_desc", "added_at_asc", "name_asc", "duration_desc"):
            raise ValueError(f"Unsupported track sort: {sort}")
        tracks = filtered_tracks(filters)
        scoped = tracks
        if artist_id is not None:
            scoped = (
                select(tracks)
                .where(
                    select(TrackArtist.track_id)
                    .where(
                        TrackArtist.track_id == tracks.c.track_id,
                        TrackArtist.artist_id == artist_id,
                    )
                    .exists()
                )
                .subquery("artist_tracks")
            )
        total = self.session.scalar(select(func.count()).select_from(scoped)) or 0
        if sort == "added_at_asc":
            order = (scoped.c.added_at.asc(), scoped.c.track_id.asc())
        elif sort == "name_asc":
            order = (
                func.analytics_casefold(scoped.c.name).asc(),
                scoped.c.track_id.asc(),
            )
        elif sort == "duration_desc":
            order = (scoped.c.duration_ms.desc(), scoped.c.track_id.asc())
        else:
            order = (scoped.c.added_at.desc(), scoped.c.track_id.asc())
        rows = (
            self.session.execute(
                select(scoped).order_by(*order).offset(page.offset).limit(page.limit)
            )
            .mappings()
            .all()
        )
        track_ids = [row["track_id"] for row in rows]
        album_ids = {row["album_id"] for row in rows if row["album_id"] is not None}
        albums = (
            {
                album.id: AlbumSummary(
                    id=album.id,
                    name=album.name,
                    release_date=album.release_date,
                    cover_url=album.cover_url,
                )
                for album in self.session.scalars(
                    select(Album).where(Album.id.in_(album_ids))
                ).all()
            }
            if album_ids
            else {}
        )
        artists_by_id = self._artist_summaries_for_tracks(track_ids)
        responses = [
            SavedTrackResponse(
                id=row["track_id"],
                name=row["name"],
                duration_ms=row["duration_ms"],
                explicit=row["explicit"],
                spotify_url=row["spotify_url"],
                album=albums.get(row["album_id"]),
                artists=artists_by_id.get(row["track_id"], []),
                added_at=row["added_at"],
            )
            for row in rows
        ]
        return TrackPage(
            filters=filters,
            items=responses,
            total=total,
            limit=page.limit,
            offset=page.offset,
        )

    def _artist_summaries(self, artist_ids: list[str]) -> dict[str, ArtistSummary]:
        if not artist_ids:
            return {}
        return {
            artist.id: self._summary(artist)
            for artist in self.session.scalars(
                select(Artist).where(Artist.id.in_(artist_ids))
            ).all()
        }

    def _artist_summaries_for_tracks(
        self, track_ids: list[str]
    ) -> dict[str, list[ArtistSummary]]:
        if not track_ids:
            return {}
        rows = self.session.execute(
            select(TrackArtist.track_id, Artist)
            .join(Artist, Artist.id == TrackArtist.artist_id)
            .where(TrackArtist.track_id.in_(track_ids))
            .order_by(TrackArtist.track_id, TrackArtist.artist_order)
        ).all()
        result: dict[str, list[ArtistSummary]] = {}
        for track_id, artist in rows:
            result.setdefault(track_id, []).append(self._summary(artist))
        return result

    @staticmethod
    def _summary(artist: Artist) -> ArtistSummary:
        return ArtistSummary(
            id=artist.id,
            name=artist.name,
            image_url=artist.image_url,
            spotify_url=artist.spotify_url,
        )
