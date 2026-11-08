"""Section 1: extract H3 resolution 8 features with S3 Select and check them."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

import boto3
import numpy as np
from botocore.config import Config

from cct_pipeline.config import ExtractionConfig, SourcesConfig
from cct_pipeline.downloads import Downloader
from cct_pipeline.errors import DataValidationError, PipelineError

logger = logging.getLogger(__name__)

Feature = dict[str, Any]

COORDINATE_TOLERANCE_DEGREES = 1e-9


@dataclass(frozen=True)
class ReferenceComparison:
    """How the extracted features differ from the reference file."""

    extracted_features: int
    reference_features: int
    missing_indexes: int
    unexpected_indexes: int
    duplicate_indexes: int
    geometry_mismatches: int
    property_mismatches: int

    @property
    def matches(self) -> bool:
        return (
            self.extracted_features == self.reference_features
            and not self.missing_indexes
            and not self.unexpected_indexes
            and not self.duplicate_indexes
            and not self.geometry_mismatches
            and not self.property_mismatches
        )

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "matches": self.matches}


def create_s3_client(
    extraction: ExtractionConfig, sources: SourcesConfig, downloader: Downloader
) -> Any:
    """Create an S3 client from the challenge's public dummy credentials.

    S3 Select refuses anonymous callers, so credentials are needed even though the
    objects are public. They are held in memory and never logged or written to disk.
    """
    body = downloader.get_bytes(sources.url(sources.credentials_file))
    try:
        credentials = json.loads(body)["s3"]
        return boto3.client(
            "s3",
            region_name=extraction.region,
            aws_access_key_id=credentials["access_key"],
            aws_secret_access_key=credentials["secret_key"],
            config=Config(retries={"max_attempts": 5, "mode": "standard"}),
        )
    except (ValueError, KeyError) as exc:
        raise PipelineError("The S3 credentials file has an unexpected format") from exc


def build_select_query(resolution: int) -> str:
    """Build the query. Only the fields needed are projected, to limit bytes returned."""
    return (
        "SELECT s.type, s.properties, s.geometry "
        "FROM S3Object[*].features[*] s "
        f"WHERE s.properties.resolution = {int(resolution)}"
    )


def parse_select_events(events: Iterable[dict[str, Any]]) -> tuple:
    """Turn an S3 Select event stream into features and scan statistics.

    One record can span two Records events, so payloads are joined before parsing. The
    stream only counts as complete if it ends with an End event, because a connection
    can otherwise drop part way and look like a smaller, valid result.
    """
    payloads: list[bytes] = []
    stats: dict[str, int] = {}
    finished = False
    for event in events:
        if "Records" in event:
            payloads.append(event["Records"]["Payload"])
        elif "Stats" in event:
            stats = dict(event["Stats"]["Details"])
        elif "End" in event:
            finished = True
    if not finished:
        raise PipelineError("The S3 Select stream ended early, so the result is incomplete")
    text = b"".join(payloads).decode("utf-8")
    features = [json.loads(line) for line in text.splitlines() if line.strip()]
    return features, stats


def select_features(client: Any, config: ExtractionConfig) -> list[Feature]:
    """Run S3 Select against the combined GeoJSON and return features for one resolution."""
    response = client.select_object_content(
        Bucket=config.bucket,
        Key=config.key,
        ExpressionType="SQL",
        Expression=build_select_query(config.resolution),
        InputSerialization={"JSON": {"Type": "DOCUMENT"}},
        OutputSerialization={"JSON": {"RecordDelimiter": "\n"}},
    )
    features, stats = parse_select_events(response["Payload"])
    logger.info(
        "s3 select features=%d bytes_scanned=%s bytes_returned=%s",
        len(features),
        stats.get("BytesScanned"),
        stats.get("BytesReturned"),
    )
    return features


def normalise_features(features: Iterable[Feature]) -> list[Feature]:
    """Drop the resolution property, which only exists in the combined source file."""
    normalised = []
    for feature in features:
        properties = {k: v for k, v in feature["properties"].items() if k != "resolution"}
        normalised.append({**feature, "properties": properties})
    return normalised


def to_feature_collection(features: list[Feature]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def _coordinates_match(left: Any, right: Any) -> bool:
    left_array, right_array = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    return left_array.shape == right_array.shape and bool(
        np.allclose(left_array, right_array, rtol=0, atol=COORDINATE_TOLERANCE_DEGREES)
    )


def _properties_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if set(left) != set(right):
        return False
    for key, value in left.items():
        other = right[key]
        if isinstance(value, float) or isinstance(other, float):
            if not _coordinates_match(value, other):
                return False
        elif value != other:
            return False
    return True


def compare_to_reference(extracted: list[Feature], reference: list[Feature]) -> ReferenceComparison:
    """Compare normalised features with the reference, matching on the H3 index."""
    extracted_by_index = {feature["properties"]["index"]: feature for feature in extracted}
    reference_by_index = {feature["properties"]["index"]: feature for feature in reference}
    shared = extracted_by_index.keys() & reference_by_index.keys()

    geometry_mismatches = 0
    property_mismatches = 0
    for index in shared:
        left, right = extracted_by_index[index], reference_by_index[index]
        if left["geometry"]["type"] != right["geometry"]["type"] or not _coordinates_match(
            left["geometry"]["coordinates"], right["geometry"]["coordinates"]
        ):
            geometry_mismatches += 1
        if not _properties_match(left["properties"], right["properties"]):
            property_mismatches += 1

    return ReferenceComparison(
        extracted_features=len(extracted),
        reference_features=len(reference),
        missing_indexes=len(reference_by_index.keys() - extracted_by_index.keys()),
        unexpected_indexes=len(extracted_by_index.keys() - reference_by_index.keys()),
        duplicate_indexes=len(extracted) - len(extracted_by_index),
        geometry_mismatches=geometry_mismatches,
        property_mismatches=property_mismatches,
    )


def require_reference_match(comparison: ReferenceComparison) -> None:
    if not comparison.matches:
        raise DataValidationError(
            f"Extracted hexagons differ from the reference file: {comparison.as_dict()}"
        )
