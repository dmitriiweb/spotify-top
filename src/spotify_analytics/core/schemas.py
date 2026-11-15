from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


AwareDatetime = Annotated[datetime, AfterValidator(_require_aware)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalyticsFilter(ContractModel):
    added_from: date | None = None
    added_to: date | None = None
    artist_id: str | None = None
    album_id: str | None = None
    q: str | None = Field(default=None, max_length=200)
    explicit: bool | None = None

    @model_validator(mode="after")
    def validate_filter(self) -> AnalyticsFilter:
        if (
            self.added_from is not None
            and self.added_to is not None
            and self.added_from > self.added_to
        ):
            raise ValueError("added_from must be on or before added_to")
        for name in ("artist_id", "album_id"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.q is not None:
            query = self.q.strip()
            self.q = query or None
        return self


class PageParams(ContractModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


ArtistSort = Literal["saved_tracks_desc", "name_asc"]
TrackSort = Literal["added_at_desc", "added_at_asc", "name_asc", "duration_desc"]


class ArtistSummary(ContractModel):
    id: str
    name: str
    image_url: str | None
    spotify_url: str | None


class AlbumSummary(ContractModel):
    id: str
    name: str
    release_date: str | None
    cover_url: str | None


class SavedTrackResponse(ContractModel):
    id: str
    name: str
    duration_ms: int = Field(ge=0)
    explicit: bool
    spotify_url: str | None
    album: AlbumSummary | None
    artists: list[ArtistSummary]
    added_at: AwareDatetime


class ArtistRanking(ContractModel):
    artist: ArtistSummary
    saved_tracks: int = Field(ge=0)
    rank: int = Field(ge=1)
    share: float = Field(ge=0, le=1)


class OverviewResponse(ContractModel):
    filters: AnalyticsFilter
    total_tracks: int = Field(ge=0)
    total_artists: int = Field(ge=0)
    total_albums: int = Field(ge=0)
    total_duration_ms: int = Field(ge=0)
    first_saved_at: AwareDatetime | None
    last_saved_at: AwareDatetime | None


class ArtistPage(ContractModel):
    filters: AnalyticsFilter
    items: list[ArtistRanking]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class TrackPage(ContractModel):
    filters: AnalyticsFilter
    items: list[SavedTrackResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)


class ArtistDetailResponse(ContractModel):
    filters: AnalyticsFilter
    artist: ArtistSummary
    saved_tracks: int = Field(ge=0)
    total_duration_ms: int = Field(ge=0)
    rank: int | None = Field(ge=1)
    tracks: TrackPage


class TimelineBucket(ContractModel):
    start: date
    end: date
    tracks: int = Field(ge=0)


class TimelineResponse(ContractModel):
    filters: AnalyticsFilter
    interval: Literal["month", "year"]
    buckets: list[TimelineBucket]


class DistributionBucket(ContractModel):
    key: str
    label: str
    tracks: int = Field(ge=0)


class DistributionResponse(ContractModel):
    filters: AnalyticsFilter
    dimension: Literal["release_decade", "duration", "explicit"]
    buckets: list[DistributionBucket]


class Insight(ContractModel):
    code: Literal["top_artist", "largest_save_month", "library_duration"]
    title: str
    description: str
    artist_id: str | None = None
    value: int = Field(ge=0)


class InsightsResponse(ContractModel):
    filters: AnalyticsFilter
    items: list[Insight]


class SyncStatusResponse(ContractModel):
    account_id: str | None
    status: Literal["idle", "running", "succeeded", "failed"]
    last_successful_sync: AwareDatetime | None
    error: str | None
    saved_tracks: int = Field(ge=0)
    artists: int = Field(ge=0)
    albums: int = Field(ge=0)
    processed_tracks: int = Field(ge=0)


class ErrorDetail(ContractModel):
    code: str
    message: str
    details: list[dict] = Field(default_factory=list)


class ErrorResponse(ContractModel):
    error: ErrorDetail
