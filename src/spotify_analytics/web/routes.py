from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from spotify_analytics.analytics.service import AnalyticsService
from spotify_analytics.core.schemas import (
    AnalyticsFilter,
    ArtistSort,
    ErrorResponse,
    PageParams,
    TrackSort,
)

router = APIRouter(prefix="/api", tags=["analytics"])
_FILTER_KEYS = {"added_from", "added_to", "artist_id", "album_id", "q", "explicit"}


def _error(status: int, code: str, message: str, details: list[dict] | None = None):
    payload = ErrorResponse(
        error={"code": code, "message": message, "details": details or []}
    )
    return JSONResponse(payload.model_dump(mode="json"), status_code=status)


def _validation_details(error: ValidationError) -> list[dict]:
    details = []
    for item in error.errors(include_url=False, include_input=False):
        loc = item.get("loc", ())
        field = str(loc[0]) if loc else "query"
        details.append({"field": field, "message": item["msg"]})
    return details


def _parse(
    request: Request,
    model: type[BaseModel],
    extra: set[str] | frozenset[str] = frozenset(),
) -> Any:
    allowed = _FILTER_KEYS | set(extra)
    pairs = list(request.query_params.multi_items())
    seen: set[str] = set()
    duplicates: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    if duplicates:
        return _error(
            422,
            "invalid_query",
            "Request validation failed",
            [
                {"field": key, "message": "must not be provided more than once"}
                for key in sorted(duplicates)
            ],
        )
    unknown = sorted(seen - allowed)
    if unknown:
        return _error(
            422,
            "invalid_query",
            "Request validation failed",
            [
                {"field": key, "message": "extra fields not permitted"}
                for key in unknown
            ],
        )
    values = dict(pairs)
    try:
        filters = AnalyticsFilter.model_validate(
            {key: value for key, value in values.items() if key in _FILTER_KEYS}
        )
        parsed_extra = model.model_validate(
            {key: value for key, value in values.items() if key in extra}
        )
    except ValidationError as error:
        return _error(
            422,
            "invalid_query",
            "Request validation failed",
            _validation_details(error),
        )
    return filters, parsed_extra


def _service(
    request: Request,
    operation: Callable[[AnalyticsService], Any],
    *,
    artist_not_found: bool = False,
):
    try:
        with request.app.state.session_factory() as session:
            service = AnalyticsService(session)
            try:
                return operation(service)
            except LookupError:
                if artist_not_found:
                    return _error(404, "not_found", "Artist not found")
                return _error(500, "internal_error", "An internal error occurred")
    except Exception:
        return _error(500, "internal_error", "An internal error occurred")


@router.get("/overview")
def overview(request: Request):
    parsed = _parse(request, _EmptyQuery)
    if not isinstance(parsed, tuple):
        return parsed
    filters, _ = parsed
    return _service(request, lambda service: service.overview(filters=filters))


@router.get("/artists")
def artists(request: Request):
    parsed = _parse(request, _PageQuery, {"limit", "offset", "sort"})
    if not isinstance(parsed, tuple):
        return parsed
    filters, page_query = parsed
    sort: ArtistSort = page_query.sort or "saved_tracks_desc"
    if sort not in ("saved_tracks_desc", "name_asc"):
        return _invalid_literal("sort")
    page = PageParams(limit=page_query.limit, offset=page_query.offset)
    return _service(
        request,
        lambda service: service.artists(filters=filters, page=page, sort=sort),
    )


@router.get("/artists/{artist_id}")
def artist_detail(request: Request, artist_id: str):
    parsed = _parse(request, _PageQuery, {"limit", "offset", "sort"})
    if not isinstance(parsed, tuple):
        return parsed
    filters, page_query = parsed
    sort: TrackSort = page_query.sort or "added_at_desc"
    if sort not in ("added_at_desc", "added_at_asc", "name_asc", "duration_desc"):
        return _invalid_literal("sort")
    page = PageParams(limit=page_query.limit, offset=page_query.offset)
    return _service(
        request,
        lambda service: service.artist_detail(
            artist_id=artist_id, filters=filters, page=page, sort=sort
        ),
        artist_not_found=True,
    )


@router.get("/tracks")
def tracks(request: Request):
    parsed = _parse(request, _PageQuery, {"limit", "offset", "sort"})
    if not isinstance(parsed, tuple):
        return parsed
    filters, page_query = parsed
    sort: TrackSort = page_query.sort or "added_at_desc"
    if sort not in ("added_at_desc", "added_at_asc", "name_asc", "duration_desc"):
        return _invalid_literal("sort")
    page = PageParams(limit=page_query.limit, offset=page_query.offset)
    return _service(
        request,
        lambda service: service.tracks(filters=filters, page=page, sort=sort),
    )


@router.get("/timeline")
def timeline(request: Request):
    parsed = _parse(request, _TimelineQuery, {"interval"})
    if not isinstance(parsed, tuple):
        return parsed
    filters, extra = parsed
    interval = extra.interval or "month"
    return _service(
        request, lambda service: service.timeline(filters=filters, interval=interval)
    )


@router.get("/distribution")
def distribution(request: Request):
    parsed = _parse(request, _DistributionQuery, {"dimension"})
    if not isinstance(parsed, tuple):
        return parsed
    filters, extra = parsed
    dimension = extra.dimension or "release_decade"
    return _service(
        request,
        lambda service: service.distribution(filters=filters, dimension=dimension),
    )


@router.get("/insights")
def insights(request: Request):
    parsed = _parse(request, _EmptyQuery)
    if not isinstance(parsed, tuple):
        return parsed
    filters, _ = parsed
    return _service(request, lambda service: service.insights(filters=filters))


@router.get("/export/artists.csv")
def export_artists(request: Request):
    return _export(
        request, "export_artists_csv", "artists.csv", "text/csv; charset=utf-8"
    )


@router.get("/export/tracks.csv")
def export_tracks(request: Request):
    return _export(
        request, "export_tracks_csv", "tracks.csv", "text/csv; charset=utf-8"
    )


@router.get("/export/library.json")
def export_json(request: Request):
    return _export(
        request, "export_json", "library.json", "application/json; charset=utf-8"
    )


def _export(request: Request, method: str, filename: str, media_type: str):
    parsed = _parse(request, _EmptyQuery)
    if not isinstance(parsed, tuple):
        return parsed
    filters, _ = parsed
    payload = _service(
        request, lambda service: getattr(service, method)(filters=filters)
    )
    if not isinstance(payload, bytes):
        return payload
    return Response(
        payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _invalid_literal(field: str):
    return _error(
        422,
        "invalid_query",
        "Request validation failed",
        [{"field": field, "message": "Input should be a valid value"}],
    )


class _EmptyQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _PageQuery(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    sort: ArtistSort | TrackSort | None = None


class _TimelineQuery(BaseModel):
    interval: Literal["month", "year"] = "month"


class _DistributionQuery(BaseModel):
    dimension: Literal["release_decade", "duration", "explicit"] = "release_decade"
