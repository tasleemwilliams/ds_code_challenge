import copy
import json

import pytest

from cct_pipeline import extraction
from cct_pipeline.errors import DataValidationError, PipelineError
from tests.conftest import make_features


def _events(features, chunk_size=None, end=True):
    payload = "\n".join(json.dumps(feature) for feature in features).encode() + b"\n"
    size = chunk_size or len(payload)
    events = [{"Records": {"Payload": payload[i : i + size]}} for i in range(0, len(payload), size)]
    events.append({"Stats": {"Details": {"BytesScanned": 100, "BytesReturned": len(payload)}}})
    if end:
        events.append({"End": {}})
    return events


def test_query_filters_on_requested_resolution():
    assert "s.properties.resolution = 8" in extraction.build_select_query(8)


def test_parses_a_record_split_across_events():
    features = make_features(3, resolution=8)
    parsed, stats = extraction.parse_select_events(_events(features, chunk_size=50))
    assert parsed == features
    assert stats["BytesScanned"] == 100


def test_stream_without_end_event_is_rejected():
    with pytest.raises(PipelineError, match="ended early"):
        extraction.parse_select_events(_events(make_features(2), end=False))


def test_normalise_removes_only_the_resolution_property():
    feature = extraction.normalise_features(make_features(1, resolution=8))[0]
    assert set(feature["properties"]) == {"index", "centroid_lat", "centroid_lon"}


def test_identical_features_match_the_reference():
    features = make_features()
    comparison = extraction.compare_to_reference(features, copy.deepcopy(features))
    assert comparison.matches
    extraction.require_reference_match(comparison)


def test_tiny_float_differences_are_tolerated():
    features = make_features(1)
    reference = copy.deepcopy(features)
    reference[0]["geometry"]["coordinates"][0][0][0] += 1e-12
    assert extraction.compare_to_reference(features, reference).matches


def test_changed_geometry_is_detected():
    features = make_features()
    reference = copy.deepcopy(features)
    reference[0]["geometry"]["coordinates"][0][0][0] += 0.001
    comparison = extraction.compare_to_reference(features, reference)
    assert comparison.geometry_mismatches == 1
    with pytest.raises(DataValidationError):
        extraction.require_reference_match(comparison)


def test_missing_and_extra_features_are_detected():
    features = make_features(5)
    comparison = extraction.compare_to_reference(features[1:], features[:-1])
    assert comparison.missing_indexes == 1
    assert comparison.unexpected_indexes == 1
    assert not comparison.matches


def test_duplicate_features_are_detected():
    features = make_features(3)
    comparison = extraction.compare_to_reference(features + features[:1], features)
    assert comparison.duplicate_indexes == 1
    assert not comparison.matches
