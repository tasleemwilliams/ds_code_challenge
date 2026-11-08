"""Section 5.1: find a suburb's centroid and select the requests close to it."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from shapely.geometry import shape

from cct_pipeline.config import AreaConfig, SubsampleConfig
from cct_pipeline.downloads import Downloader, DownloadError
from cct_pipeline.errors import DataValidationError, SourceUnavailableError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Centroid:
    lat: float
    lon: float


def _slug(suburb: str) -> str:
    return "".join(c.lower() if c.isalnum() else "_" for c in suburb)


def _query_parameters(config: SubsampleConfig) -> dict[str, str]:
    escaped = config.suburb.replace("'", "''")
    return {
        "where": f"{config.suburb_name_field} = '{escaped}'",
        "outFields": config.suburb_name_field,
        "outSR": "4326",
        "f": "geojson",
    }


def _single_feature(content: dict[str, Any], config: SubsampleConfig) -> dict[str, Any]:
    features: list[dict[str, Any]] = content.get("features") or []
    matches = [
        feature
        for feature in features
        if feature.get("properties", {}).get(config.suburb_name_field) == config.suburb
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one {config.suburb} feature, found {len(matches)}")
    return matches[0]


def fetch_suburb_feature(
    config: SubsampleConfig, downloader: Downloader, cache_dir: Path, fallback_dir: Path
) -> tuple[dict[str, Any], str]:
    """Return the suburb boundary and where it came from: cache, service or fallback."""
    filename = f"suburb_{_slug(config.suburb)}.geojson"
    cached = cache_dir / filename

    if cached.exists():
        try:
            return _single_feature(json.loads(cached.read_text("utf-8")), config), "cache"
        except ValueError as exc:
            logger.warning("ignoring an unusable suburb cache file: %s", exc)

    try:
        body = downloader.get_bytes(config.suburbs_query_url, params=_query_parameters(config))
        content = json.loads(body)
        feature = _single_feature(content, config)
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(content), encoding="utf-8")
        return feature, "city_service"
    except (DownloadError, ValueError) as exc:
        logger.warning("suburb service unavailable: %s", exc)

    fallback = fallback_dir / filename
    if fallback.exists():
        logger.warning("using the committed suburb boundary %s", fallback.name)
        return _single_feature(json.loads(fallback.read_text("utf-8")), config), "fallback"
    raise SourceUnavailableError(
        f"No boundary is available for {config.suburb}, from the service or {fallback.name}"
    )


def compute_centroid(feature: dict[str, Any], area: AreaConfig) -> Centroid:
    """Compute the polygon's area-weighted centroid and check it is in the expected area.

    Treating longitude and latitude as planar coordinates is sound at this scale. Over a
    few kilometres, the true ground distances are almost exactly a linear rescaling of
    those coordinates, and a centroid is unchanged by linear rescaling.
    """
    geometry = shape(feature["geometry"])
    if geometry.is_empty or not geometry.is_valid:
        raise DataValidationError("The suburb boundary is empty or not a valid polygon")
    centroid = geometry.centroid
    if not area.contains(centroid.y, centroid.x):
        raise DataValidationError(
            f"The suburb centroid ({centroid.y:.5f}, {centroid.x:.5f}) is not near Atlantis"
        )
    return Centroid(lat=centroid.y, lon=centroid.x)


def great_circle_arcminutes(
    lat: float, lon: float, latitudes: np.ndarray, longitudes: np.ndarray
) -> np.ndarray:
    """Great-circle separation in minutes of arc, using the haversine formula."""
    lat_1, lat_2 = np.radians(lat), np.radians(latitudes)
    half_chord = (
        np.sin((lat_2 - lat_1) / 2) ** 2
        + np.cos(lat_1) * np.cos(lat_2) * np.sin(np.radians(longitudes - lon) / 2) ** 2
    )
    return np.degrees(2 * np.arcsin(np.sqrt(half_chord))) * 60


def select_within_radius(
    requests: pd.DataFrame, centroid: Centroid, radius_arcminutes: float
) -> pd.DataFrame:
    """Select requests within the radius. Those without coordinates are never selected."""
    distance = great_circle_arcminutes(
        centroid.lat,
        centroid.lon,
        requests["latitude"].to_numpy(dtype=float),
        requests["longitude"].to_numpy(dtype=float),
    )
    return requests[distance <= radius_arcminutes].copy()
