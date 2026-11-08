"""Section 5.3: anonymise the augmented subsample and queue risky records for review.

The method and its justification are in docs/ANONYMISATION.md. To run this step alone,
after the main pipeline has produced its inputs:

    .venv/bin/python -m cct_pipeline.anonymise
"""

from __future__ import annotations

import argparse
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h3
import numpy as np
import pandas as pd

from cct_pipeline.config import DEFAULT_CONFIG_PATH, AnonymisationConfig, load_config
from cct_pipeline.errors import DataValidationError
from cct_pipeline.join import HEX_COLUMN, NO_HEX
from cct_pipeline.wind import DIRECTION_COLUMN, SPEED_COLUMN, TIMEZONE, WIND_STATUS_COLUMN

logger = logging.getLogger(__name__)

CREATED_BLOCK_COLUMN = "creation_time_block"
COMPLETED_BLOCK_COLUMN = "completion_time_block"
BLOCK_DIRECTION_COLUMN = "wind_direction_deg_block_mean"
BLOCK_SPEED_COLUMN = "wind_speed_ms_block_mean"
BLOCK_HOURS_COLUMN = "wind_valid_hours_in_block"

REVIEW_REASON_COLUMN = "review_reason"
REASON_INVALID_LOCATION = "invalid_location"
REASON_SMALL_GROUP = "small_group"

ANONYMISED_FILENAME = "service_requests_anonymised.csv"
REVIEW_QUEUE_FILENAME = "manual_review_queue.csv"


@dataclass(frozen=True)
class AnonymisationResult:
    anonymised: pd.DataFrame
    review_queue: pd.DataFrame
    stats: dict[str, Any]


def time_blocks(timestamps: pd.Series, hours: int) -> pd.Series:
    """Floor timestamps to the start of their local-time block. Missing stays missing."""
    parsed = pd.to_datetime(timestamps, utc=True, format="ISO8601")
    naive = parsed.dt.tz_convert(TIMEZONE).dt.tz_localize(None).dt.floor(f"{hours}h")
    return naive.dt.tz_localize(TIMEZONE)


def circular_mean_degrees(directions: pd.Series) -> float:
    """Average bearings on a circle, so that the mean of 350 and 10 degrees is 0, not 180."""
    radians = np.radians(directions.to_numpy(dtype=float))
    mean = np.degrees(np.arctan2(np.sin(radians).mean(), np.cos(radians).mean()))
    return float(round(mean % 360, 1))


def aggregate_wind_to_blocks(hourly: pd.DataFrame, hours: int) -> pd.DataFrame:
    """Summarise hourly wind by block, using every valid reading in the block.

    Exact hourly values would undo the time coarsening, because anyone could match them
    against the public wind file to recover a request's hour. Every request in a block
    gets the same summary, so these values say nothing about its time within the block.
    """
    valid = hourly[hourly[WIND_STATUS_COLUMN] == "ok"]
    blocks = valid["hour"].dt.floor(f"{hours}h").dt.tz_localize(TIMEZONE)
    grouped = valid.groupby(blocks.rename(CREATED_BLOCK_COLUMN))
    return pd.DataFrame(
        {
            BLOCK_DIRECTION_COLUMN: grouped[DIRECTION_COLUMN].apply(circular_mean_degrees),
            BLOCK_SPEED_COLUMN: grouped[SPEED_COLUMN].mean().round(1),
            BLOCK_HOURS_COLUMN: grouped[SPEED_COLUMN].size(),
        }
    )


def _sensitive_reasons(records: pd.DataFrame, config: AnonymisationConfig) -> pd.Series:
    """Label each record with the first sensitive category it matches, otherwise NA."""
    reasons = pd.Series(pd.NA, index=records.index, dtype="object")
    for category, pattern in config.sensitive_patterns.items():
        matched = pd.Series(False, index=records.index)
        for field in config.sensitive_fields:
            values = records[field].astype("object").fillna("").astype(str)
            matched |= values.str.contains(pattern, flags=re.IGNORECASE, regex=True)
        reasons = reasons.mask(matched & reasons.isna(), f"sensitive:{category}")
    return reasons


def _is_valid_hex(cell: Any) -> bool:
    return isinstance(cell, str) and cell != NO_HEX and h3.is_valid_cell(cell)


def _group_sizes(records: pd.DataFrame, columns: Sequence[str]) -> pd.Series:
    """How many records share each record's values. Missing counts as a value of its own."""
    keys = records[list(columns)].astype("object").fillna("<missing>")
    return keys.groupby(list(columns))[columns[0]].transform("size")


def assign_review_reasons(records: pd.DataFrame, config: AnonymisationConfig) -> pd.Series:
    """Decide which records need human review, and why.

    The group size check runs last and only counts records that would otherwise be
    published. If it ran first, removing sensitive records could leave a group smaller
    than the minimum in the output.
    """
    reasons = pd.Series(pd.NA, index=records.index, dtype="object")
    invalid_location = ~records[HEX_COLUMN].map(_is_valid_hex).astype(bool)
    reasons = reasons.mask(invalid_location, REASON_INVALID_LOCATION)

    sensitive = _sensitive_reasons(records, config)
    reasons = reasons.where(reasons.notna(), sensitive)

    remaining = records[reasons.isna()]
    if not remaining.empty:
        sizes = _group_sizes(remaining, config.quasi_identifiers)
        small = sizes.index[sizes < config.min_group_size]
        reasons.loc[small] = REASON_SMALL_GROUP
    return reasons


def _build_anonymised(
    records: pd.DataFrame, hourly_wind: pd.DataFrame, config: AnonymisationConfig
) -> pd.DataFrame:
    output = records[list(config.retained_columns)].astype("object").copy()
    hours = config.time_block_hours
    output[CREATED_BLOCK_COLUMN] = time_blocks(records["creation_timestamp"], hours)
    output[COMPLETED_BLOCK_COLUMN] = time_blocks(records["completion_timestamp"], hours)
    output = output.join(aggregate_wind_to_blocks(hourly_wind, hours), on=CREATED_BLOCK_COLUMN)
    output[BLOCK_HOURS_COLUMN] = output[BLOCK_HOURS_COLUMN].fillna(0).astype(int)
    # Sorting removes any link between row position and the original notification order.
    return output.sort_values(
        [CREATED_BLOCK_COLUMN, HEX_COLUMN, "code"], kind="stable"
    ).reset_index(drop=True)


def verify_anonymised(anonymised: pd.DataFrame, config: AnonymisationConfig) -> None:
    """Check the guarantees on the published file itself, independently of how it was built."""
    allowed = {
        *config.retained_columns,
        CREATED_BLOCK_COLUMN,
        COMPLETED_BLOCK_COLUMN,
        BLOCK_DIRECTION_COLUMN,
        BLOCK_SPEED_COLUMN,
        BLOCK_HOURS_COLUMN,
    }
    if set(anonymised.columns) != allowed:
        raise DataValidationError("The anonymised output has unexpected columns")
    if anonymised.empty:
        return

    resolutions = anonymised[HEX_COLUMN].map(h3.get_resolution)
    if not (resolutions == 8).all():
        raise DataValidationError("The anonymised output has a location that is not H3 level 8")

    hours = config.time_block_hours
    for column in (CREATED_BLOCK_COLUMN, COMPLETED_BLOCK_COLUMN):
        times = anonymised[column].dropna().dt.tz_convert(TIMEZONE)
        on_block = (times.dt.hour % hours == 0) & (times.dt.minute == 0) & (times.dt.second == 0)
        if not on_block.all():
            raise DataValidationError(f"{column} has values that are not on a {hours}-hour block")

    sizes = _group_sizes(anonymised, config.quasi_identifiers)
    if (sizes < config.min_group_size).any():
        raise DataValidationError("The anonymised output has a group below the minimum size")

    if _sensitive_reasons(anonymised, config).notna().any():
        raise DataValidationError("The anonymised output has a record with sensitive content")


def anonymise(
    augmented: pd.DataFrame, hourly_wind: pd.DataFrame, config: AnonymisationConfig
) -> AnonymisationResult:
    """Split records into a publishable anonymised set and a restricted review queue."""
    reasons = assign_review_reasons(augmented, config)
    needs_review = reasons.notna()

    anonymised = _build_anonymised(augmented[~needs_review], hourly_wind, config)
    verify_anonymised(anonymised, config)

    review_queue = augmented[needs_review].copy()
    review_queue.insert(0, REVIEW_REASON_COLUMN, reasons[needs_review])
    review_queue["reviewer_decision"] = ""
    review_queue["reviewer_notes"] = ""

    if len(anonymised) + len(review_queue) != len(augmented):
        raise DataValidationError("Some records are in neither the output nor the review queue")

    stats = {
        "input_records": len(augmented),
        "anonymised_records": len(anonymised),
        "review_records": len(review_queue),
        "review_share": len(review_queue) / len(augmented) if len(augmented) else 0.0,
        "review_reasons": {
            str(key): int(value) for key, value in reasons[needs_review].value_counts().items()
        },
        "anonymised_records_with_block_wind": int((anonymised[BLOCK_HOURS_COLUMN] > 0).sum()),
    }
    logger.info("anonymisation %s", stats)
    return AnonymisationResult(anonymised=anonymised, review_queue=review_queue, stats=stats)


def write_outputs(result: AnonymisationResult, output_dir: Path) -> dict[str, Path]:
    """Write the public file and the restricted queue to separate folders."""
    public = output_dir / "anonymised" / ANONYMISED_FILENAME
    restricted = output_dir / "restricted" / REVIEW_QUEUE_FILENAME
    public.parent.mkdir(parents=True, exist_ok=True)
    restricted.parent.mkdir(parents=True, exist_ok=True)
    result.anonymised.to_csv(public, index=False)
    result.review_queue.to_csv(restricted, index=False)
    return {"anonymised": public, "review_queue": restricted}


def main(argv: Sequence[str] | None = None) -> int:
    """Run the anonymisation step alone, using files from an earlier pipeline run."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = load_config(arguments.config)
    output_dir = config.paths.output_dir
    augmented = pd.read_csv(
        output_dir / "restricted" / "subsample_with_wind.csv",
        dtype={"reference_number": "string", HEX_COLUMN: "string"},
    )
    hourly = pd.read_csv(output_dir / "wind_hourly.csv", parse_dates=["hour"])
    result = anonymise(augmented, hourly, config.anonymisation)
    for name, path in write_outputs(result, output_dir).items():
        logger.info("wrote %s to %s", name, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
