"""Shared test data builders. Unit and integration tests make no network calls."""

from __future__ import annotations

import io
from pathlib import Path

import h3
import pandas as pd
import pytest
import requests
import yaml

from cct_pipeline.config import PROJECT_ROOT, DownloadConfig, parse_config

# A real cell in Atlantis, so generated geometry is inside the schema's city bounds.
ATLANTIS_CELL = h3.latlng_to_cell(-33.5512, 18.4881, 8)


def make_feature(cell: str, resolution: int | None = None) -> dict:
    """Build a GeoJSON feature for an H3 cell, shaped like the challenge's files."""
    boundary = [[lon, lat] for lat, lon in h3.cell_to_boundary(cell)]
    lat, lon = h3.cell_to_latlng(cell)
    properties = {"index": cell, "centroid_lat": lat, "centroid_lon": lon}
    if resolution is not None:
        properties["resolution"] = resolution
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": {"type": "Polygon", "coordinates": [boundary + [boundary[0]]]},
    }


def make_features(count: int = 7, resolution: int | None = None) -> list[dict]:
    """Build features for the Atlantis cell and its neighbours."""
    neighbours = sorted(set(h3.grid_disk(ATLANTIS_CELL, 3)) - {ATLANTIS_CELL})
    return [make_feature(cell, resolution) for cell in [ATLANTIS_CELL, *neighbours][:count]]


class FakeResponse:
    def __init__(self, status_code: int = 200, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def close(self) -> None:
        return None

    def iter_content(self, _chunk_size: int) -> list[bytes]:
        return [self.content]


class FakeSession:
    """Answers from a mapping of URL prefix to a response, or a list of outcomes in turn."""

    def __init__(self, routes: dict[str, object] | None = None) -> None:
        self.routes = routes or {}
        self.calls: list[str] = []

    def get(self, url: str, **_: object) -> FakeResponse:
        self.calls.append(url)
        for prefix, outcome in self.routes.items():
            if url.startswith(prefix):
                if isinstance(outcome, list):
                    outcome = outcome.pop(0) if len(outcome) > 1 else outcome[0]
                if isinstance(outcome, Exception):
                    raise outcome
                if isinstance(outcome, bytes):
                    return FakeResponse(200, outcome)
                return outcome  # type: ignore[return-value]
        return FakeResponse(404)


@pytest.fixture
def download_config() -> DownloadConfig:
    return DownloadConfig(
        attempts=3,
        backoff_seconds=0.0,
        max_backoff_seconds=0.0,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
    )


@pytest.fixture
def connection_error() -> requests.ConnectionError:
    return requests.ConnectionError("connection refused")


def wind_sheet(year: int = 2020, station: str = "Atlantis AQM Site") -> pd.DataFrame:
    """Build a multi-station workbook laid out like the City's file, footer rows included."""
    hours = pd.date_range(f"{year}-01-01", f"{year}-12-31 23:00", freq="h")
    header = [
        ["MultiStation: Periodically", None, None, None, None],
        [None, None, None, None, None],
        ["Date & Time", station, station, "Other AQM Site", "Other AQM Site"],
        [None, "Wind Dir V", "Wind Speed V", "Wind Dir V", "Wind Speed V"],
        [None, "Deg", "m/s", "Deg", "m/s"],
    ]
    rows = [
        [hour.strftime("%d/%m/%Y %H:%M"), float(i % 360), 1.0 + (i % 5), 999.0, 999.0]
        for i, hour in enumerate(hours)
    ]
    footer = [["Avg", 180.0, 3.0, 999.0, 999.0], ["Data[%]", 26.5, 26.5, 100.0, 100.0]]
    return pd.DataFrame(header + rows + footer)


def workbook_bytes(sheet: pd.DataFrame | None = None, engine: str = "openpyxl") -> bytes:
    buffer = io.BytesIO()
    (wind_sheet() if sheet is None else sheet).to_excel(
        buffer, header=False, index=False, engine=engine
    )
    return buffer.getvalue()


@pytest.fixture(scope="session")
def wind_workbook() -> bytes:
    return workbook_bytes()


@pytest.fixture
def base_config_raw() -> dict:
    with (PROJECT_ROOT / "config" / "pipeline.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture
def anonymisation_config(base_config_raw: dict):
    raw = base_config_raw
    return parse_config(raw).anonymisation


def build_config(raw: dict, root: Path):
    raw["extraction"]["schema_file"] = str(PROJECT_ROOT / "config" / "hex_schema.yaml")
    return parse_config(raw, root=root)
