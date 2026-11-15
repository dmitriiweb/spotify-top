from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Store aware datetimes as UTC and restore timezone awareness on SQLite."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> DateTime:
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime must be timezone-aware")
        return value.astimezone(UTC)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class Track(Base):
    __tablename__ = "tracks"
    __table_args__ = (
        CheckConstraint("duration_ms >= 0", name="ck_tracks_duration_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    duration_ms: Mapped[int] = mapped_column(nullable=False)
    album_id: Mapped[str | None] = mapped_column(
        ForeignKey("albums.id", ondelete="SET NULL"), nullable=True
    )
    explicit: Mapped[bool] = mapped_column(nullable=False)
    spotify_url: Mapped[str | None] = mapped_column(String, nullable=True)


class Artist(Base):
    __tablename__ = "artists"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    spotify_url: Mapped[str | None] = mapped_column(String, nullable=True)


class Album(Base):
    __tablename__ = "albums"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    release_date: Mapped[str | None] = mapped_column(String, nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String, nullable=True)


class TrackArtist(Base):
    __tablename__ = "track_artists"
    __table_args__ = (
        UniqueConstraint("track_id", "artist_order", name="uq_track_artists_order"),
        CheckConstraint("artist_order >= 0", name="ck_track_artists_order_nonnegative"),
        Index("ix_track_artists_artist_id", "artist_id"),
    )

    track_id: Mapped[str] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True
    )
    artist_id: Mapped[str] = mapped_column(
        ForeignKey("artists.id", ondelete="CASCADE"), primary_key=True
    )
    artist_order: Mapped[int] = mapped_column(nullable=False)


class SavedTrack(Base):
    __tablename__ = "saved_tracks"
    __table_args__ = (Index("ix_saved_tracks_added_at", "added_at"),)

    track_id: Mapped[str] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class SyncState(Base):
    __tablename__ = "sync_state"
    __table_args__ = (
        CheckConstraint(
            "status IN ('idle', 'running', 'succeeded', 'failed')",
            name="ck_sync_state_status",
        ),
        CheckConstraint(
            "saved_tracks >= 0", name="ck_sync_state_saved_tracks_nonnegative"
        ),
        CheckConstraint("artists >= 0", name="ck_sync_state_artists_nonnegative"),
        CheckConstraint("albums >= 0", name="ck_sync_state_albums_nonnegative"),
        CheckConstraint(
            "processed_tracks >= 0", name="ck_sync_state_processed_tracks_nonnegative"
        ),
    )

    account_id: Mapped[str] = mapped_column(String, primary_key=True)
    last_successful_sync: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    status: Mapped[Literal["idle", "running", "succeeded", "failed"]] = mapped_column(
        String, nullable=False, default="idle", server_default=text("'idle'")
    )
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    saved_tracks: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0")
    )
    artists: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0")
    )
    albums: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0")
    )
    processed_tracks: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0")
    )
