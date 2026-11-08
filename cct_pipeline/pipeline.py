"""Runs sections 1, 2 and 5 in order, timing each step and stopping on any failure."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from cct_pipeline import anonymise as anonymise_step
from cct_pipeline import extraction, join, schema_validation, subsample, wind
from cct_pipeline.config import PipelineConfig
from cct_pipeline.downloads import Downloader
from cct_pipeline.reporting import RunReport, timed
from cct_pipeline.service_requests import load_service_requests

logger = logging.getLogger(__name__)

# Faster to write than the default gzip level, for a file reviewers can regenerate.
_FAST_GZIP = {"method": "gzip", "compresslevel": 1}


def _run_extraction(
    config: PipelineConfig, downloader: Downloader, s3_client: Any, report: RunReport
) -> set[str]:
    """Section 1. Returns the city's hexagon indexes for the join."""
    output_dir = config.paths.output_dir
    with timed("1_s3_select_extract", report):
        client = s3_client or extraction.create_s3_client(
            config.extraction, config.sources, downloader
        )
        selected = extraction.select_features(client, config.extraction)
        features = extraction.normalise_features(selected)

    with timed("1_schema_conformance", report):
        schema = schema_validation.load_schema(config.extraction.schema_file)
        conformance = schema_validation.score_features(selected, schema)
        report.add_metrics("schema_conformance", conformance.as_dict())
        schema_validation.enforce_conformance(conformance)

    with timed("1_reference_validation", report):
        reference_path = downloader.download_cached(
            config.sources.url(config.sources.reference_hexes_file),
            config.paths.cache_dir / config.sources.reference_hexes_file,
        )
        reference = json.loads(reference_path.read_text("utf-8"))["features"]
        comparison = extraction.compare_to_reference(features, reference)
        report.add_metrics("hex_reference_comparison", comparison.as_dict())
        logger.info("hexagon reference comparison %s", comparison.as_dict())
        extraction.require_reference_match(comparison)

    with timed("1_write_hexagons", report):
        (output_dir / "city_hex_level8.geojson").write_text(
            json.dumps(extraction.to_feature_collection(features)), encoding="utf-8"
        )
    return {feature["properties"]["index"] for feature in features}


def _run_join(
    config: PipelineConfig, downloader: Downloader, city_hexes: set[str], report: RunReport
) -> pd.DataFrame:
    """Section 2. Returns the sr_hex reference, which section 5 also uses."""
    sources, cache_dir = config.sources, config.paths.cache_dir
    with timed("2_download_inputs", report):
        requests_path = downloader.download_cached(
            sources.url(sources.service_requests_file),
            cache_dir / sources.service_requests_file,
        )
        hex_path = downloader.download_cached(
            sources.url(sources.service_requests_hex_file),
            cache_dir / sources.service_requests_hex_file,
        )

    with timed("2_load_service_requests", report):
        requests = load_service_requests(requests_path)

    with timed("2_assign_hexes", report):
        joined, stats = join.assign_hexes(requests, city_hexes, config.extraction.resolution)
        report.add_metrics("join", stats.as_dict())
        join.enforce_join_thresholds(stats, config.join)

    with timed("2_write_joined_requests", report):
        restricted = config.paths.output_dir / "restricted"
        restricted.mkdir(parents=True, exist_ok=True)
        joined.to_csv(
            restricted / "service_requests_with_hex.csv.gz",
            index=False,
            compression=_FAST_GZIP,
        )

    # Only the identifiers are needed from here. Releasing the rest before loading the
    # second file means two full copies of about a million rows are never in memory.
    assigned = joined[["notification_number", join.HEX_COLUMN]]
    del requests, joined

    with timed("2_load_reference", report):
        reference = load_service_requests(hex_path)

    with timed("2_reference_validation", report):
        comparison = join.compare_to_reference(assigned, reference)
        report.add_metrics("join_reference_comparison", comparison)
        join.require_reference_match(comparison)
    return reference


def _run_further_transformations(
    config: PipelineConfig, downloader: Downloader, requests_hex: pd.DataFrame, report: RunReport
) -> None:
    """Section 5: subsample, add wind and anonymise."""
    paths = config.paths
    settings = config.subsample
    with timed("5_1_select_subsample", report):
        feature, source = subsample.fetch_suburb_feature(
            settings, downloader, paths.cache_dir, paths.fallback_dir
        )
        centroid = subsample.compute_centroid(feature, settings.atlantis_area)
        selected = subsample.select_within_radius(
            requests_hex, centroid, settings.radius_arcminutes
        )
        report.add_metrics(
            "subsample",
            {
                "suburb": settings.suburb,
                "boundary_source": source,
                "centroid_lat": centroid.lat,
                "centroid_lon": centroid.lon,
                "radius_arcminutes": settings.radius_arcminutes,
                "records": len(selected),
            },
        )
        logger.info(
            "subsample suburb=%s source=%s centroid=(%.6f, %.6f) records=%d",
            settings.suburb,
            source,
            centroid.lat,
            centroid.lon,
            len(selected),
        )

    with timed("5_2_acquire_wind", report):
        acquired = wind.acquire_wind_data(
            config.wind, downloader, paths.cache_dir, paths.fallback_dir
        )
        report.add_metrics("wind_source", acquired.provenance)
        acquired.hourly.to_csv(paths.output_dir / "wind_hourly.csv", index=False)

    with timed("5_2_join_wind", report):
        augmented = wind.join_wind(selected, acquired.hourly)
        coverage = wind.wind_coverage(augmented)
        report.add_metrics("wind_join", coverage)
        wind.enforce_wind_coverage(coverage, config.wind.min_request_coverage)
        augmented.to_csv(paths.output_dir / "restricted" / "subsample_with_wind.csv", index=False)

    with timed("5_3_anonymise", report):
        result = anonymise_step.anonymise(augmented, acquired.hourly, config.anonymisation)
        report.add_metrics("anonymisation", result.stats)
        anonymise_step.write_outputs(result, paths.output_dir)


def run_pipeline(
    config: PipelineConfig, downloader: Downloader | None = None, s3_client: Any = None
) -> RunReport:
    """Run all sections. The report is written even if a step fails part way."""
    downloader = downloader or Downloader(config.download)
    report = RunReport()
    output_dir: Path = config.paths.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        city_hexes = _run_extraction(config, downloader, s3_client, report)
        requests_hex = _run_join(config, downloader, city_hexes, report)
        _run_further_transformations(config, downloader, requests_hex, report)
    finally:
        report.write(output_dir / "run_report.json")
    return report
