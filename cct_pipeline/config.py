"""Typed access to config/pipeline.yaml. Unknown or missing keys fail at load time."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "pipeline.yaml"


@dataclass(frozen=True)
class PathsConfig:
    cache_dir: Path
    fallback_dir: Path
    output_dir: Path


@dataclass(frozen=True)
class SourcesConfig:
    base_url: str
    credentials_file: str
    reference_hexes_file: str
    service_requests_file: str
    service_requests_hex_file: str

    def url(self, filename: str) -> str:
        return f"{self.base_url}/{filename}"


@dataclass(frozen=True)
class DownloadConfig:
    attempts: int
    backoff_seconds: float
    max_backoff_seconds: float
    connect_timeout_seconds: float
    read_timeout_seconds: float


@dataclass(frozen=True)
class ExtractionConfig:
    bucket: str
    region: str
    key: str
    resolution: int
    schema_file: Path


@dataclass(frozen=True)
class JoinConfig:
    max_failure_rate: float
    max_missing_coordinate_rate: float


@dataclass(frozen=True)
class AreaConfig:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def contains(self, lat: float, lon: float) -> bool:
        return self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon


@dataclass(frozen=True)
class SubsampleConfig:
    suburb: str
    radius_arcminutes: float
    suburbs_query_url: str
    suburb_name_field: str
    atlantis_area: AreaConfig


@dataclass(frozen=True)
class WindSource:
    name: str
    url: str


@dataclass(frozen=True)
class WindConfig:
    station: str
    year: int
    fallback_file: str
    sources: tuple[WindSource, ...]
    min_request_coverage: float


@dataclass(frozen=True)
class AnonymisationConfig:
    time_block_hours: int
    min_group_size: int
    quasi_identifiers: tuple[str, ...]
    retained_columns: tuple[str, ...]
    sensitive_fields: tuple[str, ...]
    sensitive_patterns: dict[str, str]


@dataclass(frozen=True)
class PipelineConfig:
    paths: PathsConfig
    sources: SourcesConfig
    download: DownloadConfig
    extraction: ExtractionConfig
    join: JoinConfig
    subsample: SubsampleConfig
    wind: WindConfig
    anonymisation: AnonymisationConfig


def _resolve(root: Path, value: str) -> Path:
    return (root / value).resolve()


def _tuple(values: list[str]) -> tuple[str, ...]:
    return tuple(values)


def parse_config(raw: dict, root: Path = PROJECT_ROOT) -> PipelineConfig:
    """Build a PipelineConfig from a parsed YAML mapping."""
    paths = raw["paths"]
    extraction = raw["extraction"]
    subsample = raw["subsample"]
    wind = raw["wind"]
    anonymisation = raw["anonymisation"]
    return PipelineConfig(
        paths=PathsConfig(**{key: _resolve(root, value) for key, value in paths.items()}),
        sources=SourcesConfig(**raw["sources"]),
        download=DownloadConfig(**raw["download"]),
        extraction=ExtractionConfig(
            **{**extraction, "schema_file": _resolve(root, extraction["schema_file"])}
        ),
        join=JoinConfig(**raw["join"]),
        subsample=SubsampleConfig(
            **{**subsample, "atlantis_area": AreaConfig(**subsample["atlantis_area"])}
        ),
        wind=WindConfig(
            **{**wind, "sources": tuple(WindSource(**source) for source in wind["sources"])}
        ),
        anonymisation=AnonymisationConfig(
            **{
                **anonymisation,
                "quasi_identifiers": _tuple(anonymisation["quasi_identifiers"]),
                "retained_columns": _tuple(anonymisation["retained_columns"]),
                "sensitive_fields": _tuple(anonymisation["sensitive_fields"]),
            }
        ),
    )


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> PipelineConfig:
    """Load the pipeline configuration from a YAML file."""
    with Path(path).open(encoding="utf-8") as handle:
        return parse_config(yaml.safe_load(handle))
