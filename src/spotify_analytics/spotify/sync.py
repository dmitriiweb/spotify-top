from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from spotify_analytics.core.models import (
    Album,
    Artist,
    SavedTrack,
    SyncState,
    Track,
    TrackArtist,
)
from spotify_analytics.core.schemas import SyncStatusResponse


class SyncInProgress(Exception):
    """Raised when another synchronization currently owns the database claim."""


@dataclass(frozen=True, slots=True)
class _TrackSnapshot:
    track: dict[str, Any]
    added_at: datetime


class SyncService:
    """Fetch status and atomically reconcile one active Spotify library."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def status(self) -> SyncStatusResponse:
        with self._session_factory() as session:
            state = session.scalar(select(SyncState).limit(1))
            return self._response(state)

    def start(self, account_id: str) -> SyncStatusResponse:
        if not account_id:
            raise ValueError("account_id must not be empty")

        # SQLite's immediate write transaction serializes claims across service
        # instances, including the first claim when the table has no row.
        with self._session_factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            try:
                state = session.scalar(select(SyncState).limit(1))
                if state is None:
                    session.add(
                        SyncState(
                            account_id=account_id,
                            status="running",
                            error=None,
                            processed_tracks=0,
                        )
                    )
                    try:
                        session.commit()
                    except IntegrityError:
                        session.rollback()
                        state = session.scalar(select(SyncState).limit(1))
                        if state is None or state.status == "running":
                            raise SyncInProgress from None
                        return self.start(account_id)
                else:
                    claimed = session.execute(
                        update(SyncState)
                        .where(
                            SyncState.account_id == state.account_id,
                            SyncState.status != "running",
                        )
                        .values(status="running", error=None, processed_tracks=0)
                    )
                    if claimed.rowcount != 1:
                        session.rollback()
                        raise SyncInProgress
                    session.commit()
                state = session.scalar(select(SyncState).limit(1))
                return self._response(state)
            except SyncInProgress:
                session.rollback()
                raise

    def progress(self, processed_tracks: int) -> None:
        if processed_tracks < 0:
            raise ValueError("processed_tracks must be nonnegative")
        with self._session_factory() as session:
            state = session.scalar(select(SyncState).limit(1))
            if state is None or state.status != "running":
                raise RuntimeError("No synchronization is running")
            session.execute(
                update(SyncState)
                .where(
                    SyncState.account_id == state.account_id,
                    SyncState.status == "running",
                )
                .values(processed_tracks=processed_tracks)
            )
            session.commit()

    def publish(
        self,
        account_id: str,
        items: list[dict[str, Any]],
        processed_tracks: int,
    ) -> SyncStatusResponse:
        if not account_id or processed_tracks < 0:
            raise ValueError("Invalid synchronization snapshot")
        # Validate every item before opening the publishing transaction.
        snapshot = self._map_snapshot(items)
        if processed_tracks < len(items):
            raise ValueError("processed_tracks cannot be less than the snapshot size")

        now = datetime.now(UTC)
        with self._session_factory() as session:
            with session.begin():
                state = session.scalar(select(SyncState).limit(1))
                if state is None or state.status != "running":
                    raise RuntimeError("No synchronization is running")

                if state.account_id != account_id:
                    # Metadata remains queryable, but all saved links are replaced
                    # within this transaction so no account libraries are mixed.
                    session.execute(delete(SavedTrack))
                    session.execute(delete(SyncState))
                    state = SyncState(account_id=account_id)
                    session.add(state)
                else:
                    session.execute(delete(SavedTrack))

                album_cache: dict[str, Album] = {}
                artist_cache: dict[str, Artist] = {}
                for track_id, value in snapshot.items():
                    track = value.track
                    album_data = track.get("album")
                    album_id: str | None = None
                    if album_data is not None:
                        album_id = album_data["id"]
                        album = album_cache.get(album_id)
                        if album is None:
                            album = session.get(Album, album_id)
                            if album is None:
                                album = Album(id=album_id, name=album_data["name"])
                                session.add(album)
                            album_cache[album_id] = album
                        album.name = album_data["name"]
                        album.release_date = album_data["release_date"]
                        album.cover_url = album_data["cover_url"]

                    persisted_track = session.get(Track, track_id)
                    if persisted_track is None:
                        persisted_track = Track(
                            id=track_id,
                            name=track["name"],
                            duration_ms=track["duration_ms"],
                            explicit=track["explicit"],
                        )
                        session.add(persisted_track)
                    persisted_track.name = track["name"]
                    persisted_track.duration_ms = track["duration_ms"]
                    persisted_track.explicit = track["explicit"]
                    persisted_track.spotify_url = track["spotify_url"]
                    persisted_track.album_id = album_id

                    session.execute(
                        delete(TrackArtist).where(TrackArtist.track_id == track_id)
                    )
                    for order, artist_data in enumerate(track["artists"]):
                        artist_id = artist_data["id"]
                        artist = artist_cache.get(artist_id)
                        if artist is None:
                            artist = session.get(Artist, artist_id)
                            if artist is None:
                                artist = Artist(id=artist_id, name=artist_data["name"])
                                session.add(artist)
                            artist_cache[artist_id] = artist
                        artist.name = artist_data["name"]
                        artist.image_url = artist_data["image_url"]
                        artist.spotify_url = artist_data["spotify_url"]
                        session.add(
                            TrackArtist(
                                track_id=track_id,
                                artist_id=artist.id,
                                artist_order=order,
                            )
                        )
                    session.add(SavedTrack(track_id=track_id, added_at=value.added_at))

                # Flush before counting so counts represent persisted relations;
                # a failure anywhere still rolls the complete transaction back.
                session.flush()
                saved_count = (
                    session.scalar(select(func.count()).select_from(SavedTrack)) or 0
                )
                artist_count = (
                    session.scalar(
                        select(func.count(func.distinct(TrackArtist.artist_id))).join(
                            SavedTrack, SavedTrack.track_id == TrackArtist.track_id
                        )
                    )
                    or 0
                )
                album_count = (
                    session.scalar(
                        select(func.count(func.distinct(Track.album_id)))
                        .join(SavedTrack, SavedTrack.track_id == Track.id)
                        .where(Track.album_id.is_not(None))
                    )
                    or 0
                )
                state.last_successful_sync = now
                state.status = "succeeded"
                state.error = None
                state.saved_tracks = saved_count
                state.artists = artist_count
                state.albums = album_count
                state.processed_tracks = processed_tracks
            return self._response(state)

    def fail(self, message: str) -> SyncStatusResponse:
        safe_message = self._safe_error(message)
        with self._session_factory() as session:
            state = session.scalar(select(SyncState).limit(1))
            if state is None:
                raise RuntimeError("No synchronization is running")
            session.execute(
                update(SyncState)
                .where(
                    SyncState.account_id == state.account_id,
                    SyncState.status == "running",
                )
                .values(status="failed", error=safe_message)
            )
            session.commit()
            return self._response(session.get(SyncState, state.account_id))

    async def run(self, client: Any, account_id: str) -> None:
        try:
            items: list[dict[str, Any]] = []
            processed = 0
            async for page in client.iter_saved_tracks():
                items.extend(page)
                processed += len(page)
                await asyncio.to_thread(self.progress, processed)
            await asyncio.to_thread(self.publish, account_id, items, processed)
        except asyncio.CancelledError:
            try:
                await asyncio.to_thread(self.fail, "Synchronization was cancelled")
            except Exception:
                pass
            raise
        except Exception:
            # Deliberately discard provider, mapping and database exception text.
            try:
                await asyncio.to_thread(self.fail, "Synchronization failed")
            except Exception:
                pass

    @staticmethod
    def _response(state: SyncState | None) -> SyncStatusResponse:
        if state is None:
            return SyncStatusResponse(
                account_id=None,
                status="idle",
                last_successful_sync=None,
                error=None,
                saved_tracks=0,
                artists=0,
                albums=0,
                processed_tracks=0,
            )
        return SyncStatusResponse(
            account_id=state.account_id,
            status=state.status,
            last_successful_sync=state.last_successful_sync,
            error=state.error,
            saved_tracks=state.saved_tracks,
            artists=state.artists,
            albums=state.albums,
            processed_tracks=state.processed_tracks,
        )

    @classmethod
    def _safe_error(cls, message: str) -> str:
        allowed = {
            "Synchronization failed",
            "Synchronization was cancelled",
            "Spotify request failed",
            "Spotify response could not be processed",
        }
        return message if message in allowed else "Synchronization failed"

    @classmethod
    def _map_snapshot(
        cls, items: Iterable[dict[str, Any]]
    ) -> dict[str, _TrackSnapshot]:
        snapshot: dict[str, _TrackSnapshot] = {}
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Invalid saved track")
            track = item.get("track")
            if not isinstance(track, dict):
                raise ValueError("Invalid saved track")
            track_id = cls._required_string(track.get("id"))
            name = cls._required_string(track.get("name"))
            duration = track.get("duration_ms")
            explicit = track.get("explicit")
            if (
                isinstance(duration, bool)
                or not isinstance(duration, int)
                or duration < 0
            ):
                raise ValueError("Invalid track duration")
            if not isinstance(explicit, bool):
                raise ValueError("Invalid explicit flag")
            artists: list[dict[str, Any]] = []
            seen_artist_ids: set[str] = set()
            raw_artists = track.get("artists")
            if not isinstance(raw_artists, list):
                raise ValueError("Invalid artist credits")
            for raw_artist in raw_artists:
                if not isinstance(raw_artist, dict):
                    raise ValueError("Invalid artist credit")
                artist_id = cls._required_string(raw_artist.get("id"))
                if artist_id in seen_artist_ids:
                    continue
                seen_artist_ids.add(artist_id)
                images = raw_artist.get("images") or []
                if not isinstance(images, list):
                    raise ValueError("Invalid artist images")
                artists.append(
                    {
                        "id": artist_id,
                        "name": cls._required_string(raw_artist.get("name")),
                        "image_url": cls._image_url(images),
                        "spotify_url": cls._external_url(
                            raw_artist.get("external_urls")
                        ),
                    }
                )

            album_data = track.get("album")
            mapped_album: dict[str, Any] | None = None
            if album_data is not None:
                if not isinstance(album_data, dict):
                    raise ValueError("Invalid album")
                album_id = cls._required_string(album_data.get("id"))
                images = album_data.get("images") or []
                if not isinstance(images, list):
                    raise ValueError("Invalid album images")
                release_date = album_data.get("release_date")
                if release_date is not None:
                    if not isinstance(release_date, str) or not re.fullmatch(
                        r"\d{4}(?:-\d{2}(?:-\d{2})?)?", release_date
                    ):
                        raise ValueError("Invalid release date")
                    try:
                        if len(release_date) == 7:
                            date.fromisoformat(f"{release_date}-01")
                        elif len(release_date) == 10:
                            date.fromisoformat(release_date)
                    except ValueError as exc:
                        raise ValueError("Invalid release date") from exc
                mapped_album = {
                    "id": album_id,
                    "name": cls._required_string(album_data.get("name")),
                    "release_date": release_date,
                    "cover_url": cls._image_url(images),
                }

            saved_at = item.get("added_at")
            if not isinstance(saved_at, str):
                raise ValueError("Invalid saved timestamp")
            try:
                added_at = datetime.fromisoformat(saved_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("Invalid saved timestamp") from exc
            if added_at.tzinfo is None or added_at.utcoffset() is None:
                raise ValueError("Saved timestamp must include a timezone")
            added_at = added_at.astimezone(UTC)

            mapped_track = {
                "id": track_id,
                "name": name,
                "duration_ms": duration,
                "explicit": explicit,
                "spotify_url": cls._external_url(track.get("external_urls")),
                "artists": artists,
                "album": mapped_album,
            }
            # Duplicate saved IDs collapse to one relation; the last occurrence
            # represents the most recent metadata and added timestamp.
            snapshot[track_id] = _TrackSnapshot(mapped_track, added_at)
        return snapshot

    @staticmethod
    def _required_string(value: Any) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("Invalid Spotify identifier or name")
        return value

    @staticmethod
    def _external_url(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("Invalid Spotify URLs")
        url = value.get("spotify")
        if url is not None and not isinstance(url, str):
            raise ValueError("Invalid Spotify URL")
        return url

    @staticmethod
    def _image_url(images: list[Any]) -> str | None:
        if not images:
            return None
        image = images[0]
        if not isinstance(image, dict):
            raise ValueError("Invalid Spotify image")
        url = image.get("url")
        if url is not None and not isinstance(url, str):
            raise ValueError("Invalid Spotify image URL")
        return url
