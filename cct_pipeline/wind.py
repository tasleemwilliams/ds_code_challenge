"""Section 5.2: obtain the 2020 wind data and add it to the subsample.

Handling the unreliable wind endpoint
-------------------------------------
This data describes a finished year, so a good copy never goes stale. That makes the job
simple: find one valid copy, from the most trustworthy place available, and then stop
depending on the network. The order is:

1. A validated copy cached by an earlier run. No network call means no failure risk.
2. Each configured source in turn. Downloader retries faults that may clear (timeouts,
   dropped connections, 429 and 5xx) with exponential backoff and jitter, so a brief
   outage is survived without hammering a struggling server. A 404 will not change on a
   retry, so it moves on to the next source immediately. Attempts are capped, so a dead
   source costs seconds and cannot hang an unattended run.
3. A copy committed to the repository. Reviewers must be able to clone and run this with
   no human interaction, even if every source is down.

Every candidate has to pass the same content validation before use, because a source can
return HTTP 200 with an error page or a changed layout. A failed run is worse than a
fallback, but silently wrong data is worse than a failed run. The source actually used
and its checksum go in the run report, so the provenance of each result is on record.
"""

from __future__ import annotations

import hashlib
import logging
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from cct_pipeline.config import WindConfig
from cct_pipeline.downloads import Downloader, DownloadError
from cct_pipeline.errors import DataValidationError, SourceUnavailableError

logger = logging.getLogger(__name__)

TIMEZONE = "Africa/Johannesburg"
STATUS_OK = "ok"
STATUS_NO_RECORD = "no_record"
STATUS_OUT_OF_RANGE = "out_of_range"

DIRECTION_COLUMN = "wind_direction_deg"
SPEED_COLUMN = "wind_speed_ms"
WIND_STATUS_COLUMN = "wind_status"

_HEADER_MARKER = "Date & Time"
_DIRECTION_LABEL = "Wind Dir V"
_SPEED_LABEL = "Wind Speed V"
_MAX_PLAUSIBLE_SPEED_MS = 75.0  # Far beyond any recorded Cape Town wind.


class WindDataError(ValueError):
    """A workbook does not contain the expected wind data."""


@dataclass(frozen=True)
class WindAcquisition:
    hourly: pd.DataFrame
    provenance: dict[str, Any]


def _spreadsheet_engine(path: Path) -> str:
    """Pick a reader from the file's contents, since sources differ in format."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile as exc:
        raise WindDataError("The file is not a spreadsheet") from exc
    if "xl/workbook.xml" in names:
        return "openpyxl"
    if "content.xml" in names:
        return "odf"
    raise WindDataError("The file is not an xlsx or ods spreadsheet")


def _find_header_row(sheet: pd.DataFrame) -> int:
    matches = sheet.index[sheet.iloc[:, 0].astype(str).str.strip() == _HEADER_MARKER]
    if len(matches) != 1:
        raise WindDataError(f"Expected one '{_HEADER_MARKER}' header row, found {len(matches)}")
    return int(matches[0])


def _find_station_column(sheet: pd.DataFrame, header: int, station: str, label: str) -> int:
    stations = sheet.iloc[header].astype(str).str.strip()
    labels = sheet.iloc[header + 1].astype(str).str.strip()
    matches = [
        position
        for position in range(sheet.shape[1])
        if stations.iloc[position] == station and labels.iloc[position] == label
    ]
    if len(matches) != 1:
        raise WindDataError(f"Expected one '{station}' / '{label}' column, found {len(matches)}")
    return matches[0]


def _parse_times(values: pd.Series) -> pd.Series:
    """Parse timestamps. Text is day-first, while spreadsheet date cells come through ISO."""
    text = values.astype(str).str.strip()
    parsed = pd.to_datetime(text, format="%d/%m/%Y %H:%M", errors="coerce")
    remaining = parsed.isna()
    # Summary rows such as "Avg" contain no digits and are left as NaT, then dropped.
    iso_like = remaining & text.str.match(r"^\d{4}-\d{2}-\d{2}")
    if iso_like.any():
        parsed.loc[iso_like] = pd.to_datetime(text[iso_like], errors="coerce")
    return parsed


def parse_workbook(path: Path, station: str, year: int) -> pd.DataFrame:
    """Read one station's hourly wind from a multi-station workbook and validate it.

    The hour label marks the start of the averaging period: the file's stated range runs
    to 23:59 on 31 December while its last label is 23:00. Times are local (SAST).

    Non-numeric entries such as NoData, Down and <Samp are kept in the status column, so
    that the reason for each gap is preserved.
    """
    sheet = pd.read_excel(path, header=None, engine=_spreadsheet_engine(path))
    header = _find_header_row(sheet)
    direction_position = _find_station_column(sheet, header, station, _DIRECTION_LABEL)
    speed_position = _find_station_column(sheet, header, station, _SPEED_LABEL)

    body = sheet.iloc[header + 1 :]
    times = _parse_times(body.iloc[:, 0])
    rows = times.notna()
    direction_raw = body.iloc[:, direction_position][rows]
    speed_raw = body.iloc[:, speed_position][rows]
    direction = pd.to_numeric(direction_raw, errors="coerce").astype(float)
    speed = pd.to_numeric(speed_raw, errors="coerce").astype(float)

    # Where a reading is not numeric, its raw text (such as "Down") explains why.
    flags = direction_raw.where(direction.isna(), speed_raw).astype("object")
    status = flags.where(flags.notna(), STATUS_NO_RECORD).astype(str).str.strip()
    both_present = direction.notna() & speed.notna()
    in_range = direction.between(0, 360) & speed.between(0, _MAX_PLAUSIBLE_SPEED_MS)
    status = status.mask(both_present & in_range, STATUS_OK)
    status = status.mask(both_present & ~in_range, STATUS_OUT_OF_RANGE)

    usable = status == STATUS_OK
    hourly = pd.DataFrame(
        {
            "hour": times[rows].to_numpy(),
            DIRECTION_COLUMN: direction.where(usable).to_numpy(),
            SPEED_COLUMN: speed.where(usable).to_numpy(),
            WIND_STATUS_COLUMN: status.to_numpy(),
        }
    )
    _validate_hourly(hourly, year)
    return hourly


def _validate_hourly(hourly: pd.DataFrame, year: int) -> None:
    expected_hours = len(pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="h")) - 1
    if len(hourly) != expected_hours or not hourly["hour"].is_unique:
        raise WindDataError(f"Expected {expected_hours} unique hours, found {len(hourly)} rows")
    if not (hourly["hour"].dt.year == year).all():
        raise WindDataError(f"The workbook contains hours outside {year}")
    if not (hourly[WIND_STATUS_COLUMN] == STATUS_OK).any():
        raise WindDataError("The workbook has no usable readings for this station")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def acquire_wind_data(
    config: WindConfig, downloader: Downloader, cache_dir: Path, fallback_dir: Path
) -> WindAcquisition:
    """Return validated hourly wind data from the first usable source."""
    cached = cache_dir / f"wind_{config.year}.workbook"
    attempts: list[dict[str, str]] = []

    def load(path: Path) -> pd.DataFrame:
        return parse_workbook(path, config.station, config.year)

    def from_download(url: str) -> Callable[[], tuple[pd.DataFrame, Path]]:
        def fetch() -> tuple[pd.DataFrame, Path]:
            candidate = cache_dir / f"wind_{config.year}.candidate"
            try:
                downloader.download(url, candidate)
                hourly = load(candidate)
                os.replace(candidate, cached)
            finally:
                candidate.unlink(missing_ok=True)
            return hourly, cached

        return fetch

    def from_file(path: Path) -> Callable[[], tuple[pd.DataFrame, Path]]:
        def read() -> tuple[pd.DataFrame, Path]:
            if not path.exists():
                raise DownloadError(f"{path.name} does not exist")
            return load(path), path

        return read

    candidates: list[tuple[str, str | None, Callable[[], tuple[pd.DataFrame, Path]]]] = [
        ("cache", None, from_file(cached))
    ]
    candidates += [
        (source.name, source.url, from_download(source.url)) for source in config.sources
    ]
    candidates.append(("committed_fallback", None, from_file(fallback_dir / config.fallback_file)))

    for name, url, obtain in candidates:
        try:
            hourly, path = obtain()
        except (DownloadError, WindDataError, ValueError, OSError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            attempts.append({"source": name, "outcome": reason})
            log = logger.info if name == "cache" else logger.warning
            log("wind source unavailable source=%s reason=%s", name, reason)
            continue
        attempts.append({"source": name, "outcome": "used"})
        logger.info("wind source used source=%s", name)
        return WindAcquisition(
            hourly=hourly,
            provenance={"source": name, "url": url, "sha256": _sha256(path), "attempts": attempts},
        )
    raise SourceUnavailableError(f"No usable wind data from any source: {attempts}")


def local_hour(timestamps: pd.Series) -> pd.Series:
    """Convert timestamps to naive South African time, floored to the hour label."""
    parsed = pd.to_datetime(timestamps, utc=True, format="ISO8601")
    return parsed.dt.tz_convert(TIMEZONE).dt.tz_localize(None).dt.floor("h")


def join_wind(requests: pd.DataFrame, hourly: pd.DataFrame) -> pd.DataFrame:
    """Add wind for the hour each request was created, keeping every request.

    Gaps are left empty with their reason in the status column. They are not estimated:
    the Atlantis gaps run for months, so interpolated values would be invented data.
    """
    if requests["creation_timestamp"].isna().any():
        raise DataValidationError("Some requests have no creation timestamp")
    lookup = hourly.set_index("hour")
    hours = local_hour(requests["creation_timestamp"])
    augmented = requests.copy()
    for column in (DIRECTION_COLUMN, SPEED_COLUMN, WIND_STATUS_COLUMN):
        augmented[column] = hours.map(lookup[column])
    augmented[WIND_STATUS_COLUMN] = augmented[WIND_STATUS_COLUMN].fillna(STATUS_NO_RECORD)
    return augmented


def wind_coverage(augmented: pd.DataFrame) -> dict[str, Any]:
    counts = augmented[WIND_STATUS_COLUMN].value_counts().to_dict()
    with_reading = int(counts.get(STATUS_OK, 0))
    total = len(augmented)
    return {
        "requests": total,
        "requests_with_reading": with_reading,
        "request_coverage": with_reading / total if total else 0.0,
        "status_counts": {str(key): int(value) for key, value in counts.items()},
    }


def enforce_wind_coverage(coverage: dict[str, Any], minimum: float) -> None:
    """Stop if coverage is implausibly low, which would suggest a broken time alignment."""
    logger.info(
        "wind join requests=%d with_reading=%d coverage=%.4f minimum=%.4f statuses=%s",
        coverage["requests"],
        coverage["requests_with_reading"],
        coverage["request_coverage"],
        minimum,
        coverage["status_counts"],
    )
    if coverage["request_coverage"] < minimum:
        raise DataValidationError(
            f"Only {coverage['request_coverage']:.2%} of requests have wind data, "
            f"below the {minimum:.2%} minimum"
        )
