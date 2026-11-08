"""Section 2: assign each service request to one H3 resolution 8 hexagon.

The H3 index is computed from the coordinates and then checked against the extracted
city hexagons. This is the equivalent of joining points to polygons, with two advantages:
a point can never land in two hexagons or fall between them, and it is far faster than a
polygon intersection across a million rows.

docs/DESIGN.md explains the failure threshold.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from dataclasses import asdict, dataclass
from typing import Any

import h3
import numpy as np
import pandas as pd

from cct_pipeline.config import JoinConfig
from cct_pipeline.errors import DataValidationError

logger = logging.getLogger(__name__)

HEX_COLUMN = "h3_level8_index"
STATUS_COLUMN = "join_status"
NO_HEX = "0"

STATUS_MATCHED = "matched"
STATUS_NO_COORDINATES = "no_coordinates"
STATUS_INVALID_COORDINATES = "invalid_coordinates"
STATUS_OUTSIDE_CITY = "outside_city_hexes"


@dataclass(frozen=True)
class JoinStats:
    total: int
    matched: int
    no_coordinates: int
    invalid_coordinates: int
    outside_city_hexes: int

    @property
    def with_coordinates(self) -> int:
        return self.total - self.no_coordinates

    @property
    def failed(self) -> int:
        return self.invalid_coordinates + self.outside_city_hexes

    @property
    def failure_rate(self) -> float:
        """Failures among requests that could have joined."""
        return self.failed / self.with_coordinates if self.with_coordinates else 0.0

    @property
    def missing_coordinate_rate(self) -> float:
        return self.no_coordinates / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "failed": self.failed,
            "failure_rate": self.failure_rate,
            "missing_coordinate_rate": self.missing_coordinate_rate,
        }


def assign_hexes(
    requests: pd.DataFrame, city_hexes: Collection[str], resolution: int = 8
) -> tuple[pd.DataFrame, JoinStats]:
    """Return the requests with an H3 index and join status, plus summary counts.

    A request missing either coordinate gets index "0". So does one with an impossible
    coordinate, which counts as a failure. A request outside the city's hexagons keeps
    its computed index, because it is still a true location, and counts as a failure.
    """
    latitude = pd.to_numeric(requests["latitude"], errors="coerce").to_numpy(dtype=float)
    longitude = pd.to_numeric(requests["longitude"], errors="coerce").to_numpy(dtype=float)

    missing = np.isnan(latitude) | np.isnan(longitude)
    with np.errstate(invalid="ignore"):
        in_range = (np.abs(latitude) <= 90) & (np.abs(longitude) <= 180)
    computable = in_range & ~missing

    cells = np.full(len(requests), NO_HEX, dtype=object)
    cells[computable] = [
        h3.latlng_to_cell(lat, lon, resolution)
        for lat, lon in zip(latitude[computable].tolist(), longitude[computable].tolist())
    ]
    # A hash lookup. np.isin sorts text values, which took about 25 seconds on this data.
    in_city = pd.Series(cells).isin(city_hexes).to_numpy() & computable

    status = np.full(len(requests), STATUS_INVALID_COORDINATES, dtype=object)
    status[missing] = STATUS_NO_COORDINATES
    status[computable & ~in_city] = STATUS_OUTSIDE_CITY
    status[in_city] = STATUS_MATCHED

    joined = requests.assign(**{HEX_COLUMN: cells, STATUS_COLUMN: status})
    stats = JoinStats(
        total=len(requests),
        matched=int(in_city.sum()),
        no_coordinates=int(missing.sum()),
        invalid_coordinates=int((~missing & ~in_range).sum()),
        outside_city_hexes=int((computable & ~in_city).sum()),
    )
    return joined, stats


def enforce_join_thresholds(stats: JoinStats, config: JoinConfig) -> None:
    """Log join quality and stop the pipeline if a threshold is breached."""
    logger.info(
        "join total=%d matched=%d no_coordinates=%d invalid_coordinates=%d "
        "outside_city_hexes=%d failed=%d failure_rate=%.6f threshold=%.6f",
        stats.total,
        stats.matched,
        stats.no_coordinates,
        stats.invalid_coordinates,
        stats.outside_city_hexes,
        stats.failed,
        stats.failure_rate,
        config.max_failure_rate,
    )
    if stats.failure_rate > config.max_failure_rate:
        raise DataValidationError(
            f"{stats.failed} requests failed to join ({stats.failure_rate:.4%}), above the "
            f"{config.max_failure_rate:.4%} threshold"
        )
    if stats.missing_coordinate_rate > config.max_missing_coordinate_rate:
        raise DataValidationError(
            f"{stats.missing_coordinate_rate:.2%} of requests have no coordinates, above the "
            f"{config.max_missing_coordinate_rate:.2%} threshold"
        )


def compare_to_reference(joined: pd.DataFrame, reference: pd.DataFrame) -> dict[str, int]:
    """Count differences from the reference file, matching on notification number."""
    for name, frame in (("joined", joined), ("reference", reference)):
        if not frame["notification_number"].is_unique:
            raise DataValidationError(f"The {name} notification numbers are not unique")

    expected = reference.set_index("notification_number")[HEX_COLUMN].astype("string")
    actual = joined.set_index("notification_number")[HEX_COLUMN].astype("string")
    shared = actual.index.intersection(expected.index)
    return {
        "compared": len(shared),
        "missing_from_reference": len(actual.index.difference(expected.index)),
        "missing_from_joined": len(expected.index.difference(actual.index)),
        "index_mismatches": int((actual.loc[shared] != expected.loc[shared]).sum()),
    }


def require_reference_match(comparison: dict[str, int]) -> None:
    logger.info("join reference comparison %s", comparison)
    differences = {key: value for key, value in comparison.items() if key != "compared" and value}
    if differences:
        raise DataValidationError(f"The join differs from sr_hex.csv.gz: {differences}")
