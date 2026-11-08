import copy

import pytest

from cct_pipeline import schema_validation as sv
from cct_pipeline.config import PROJECT_ROOT
from cct_pipeline.errors import DataValidationError, PipelineError
from tests.conftest import make_features


@pytest.fixture(scope="module")
def schema():
    return sv.load_schema(PROJECT_ROOT / "config" / "hex_schema.yaml")


def _rule(result, rule_id):
    return next(rule for rule in result.rules if rule["id"] == rule_id)


def test_valid_features_score_one_and_pass(schema):
    result = sv.score_features(make_features(), schema)
    assert result.score == 1.0
    assert result.status == sv.STATUS_PASS
    sv.enforce_conformance(result)


def test_score_is_graded_not_all_or_nothing(schema):
    features = make_features(20)
    features[0]["properties"]["centroid_lat"] = 0.0
    result = sv.score_features(features, schema)
    assert 0.99 < result.score < 1.0
    assert _rule(result, "centroid_lat_in_city_bounds")["failed"] == 1


def test_score_in_warn_band_continues(schema, caplog):
    features = make_features(7)
    features[0]["geometry"]["coordinates"] = []
    result = sv.score_features(features, schema)
    assert result.status == sv.STATUS_WARN
    sv.enforce_conformance(result)
    assert "below the pass band" in caplog.text


def test_score_below_warn_band_stops_the_pipeline(schema):
    features = [{"type": "Feature"} for _ in range(5)]
    result = sv.score_features(features, schema)
    assert result.status == sv.STATUS_FAIL
    with pytest.raises(DataValidationError):
        sv.enforce_conformance(result)


def test_missing_values_fail_rules_without_raising(schema):
    result = sv.score_features([{}], schema)
    assert result.score == 0.0


def test_no_features_scores_zero(schema):
    assert sv.score_features([], schema).status == sv.STATUS_FAIL


def test_duplicate_indexes_fail_the_uniqueness_rule(schema):
    features = make_features(3)
    result = sv.score_features(features + copy.deepcopy(features[:1]), schema)
    assert _rule(result, "index_is_unique")["failed"] == 2


def test_wrong_resolution_cell_fails(schema):
    features = make_features(1)
    features[0]["properties"]["index"] = "89ad3670e7fffff"
    result = sv.score_features(features, schema)
    assert _rule(result, "index_is_resolution_8_h3_cell")["failed"] == 1


def test_unclosed_ring_fails(schema):
    features = make_features(1)
    features[0]["geometry"]["coordinates"][0][-1] = [18.5, -33.5]
    result = sv.score_features(features, schema)
    assert _rule(result, "geometry_is_closed_hexagon_in_city_bounds")["failed"] == 1


def test_centroid_far_from_cell_centre_fails(schema):
    features = make_features(1)
    features[0]["properties"]["centroid_lat"] += 0.01
    result = sv.score_features(features, schema)
    assert _rule(result, "centroid_matches_h3_centre")["failed"] == 1


def test_unknown_check_in_schema_is_rejected(tmp_path):
    path = tmp_path / "schema.yaml"
    path.write_text(
        "name: x\nversion: 1\nthresholds: {pass: 0.9, warn: 0.8}\n"
        "rules:\n  - {id: a, path: b, check: nonsense, weight: 1}\n"
    )
    with pytest.raises(PipelineError, match="unknown checks"):
        sv.load_schema(path)


def test_inverted_thresholds_are_rejected(tmp_path):
    path = tmp_path / "schema.yaml"
    path.write_text(
        "name: x\nversion: 1\nthresholds: {pass: 0.8, warn: 0.9}\n"
        "rules:\n  - {id: a, path: b, check: unique, weight: 1}\n"
    )
    with pytest.raises(PipelineError, match="thresholds"):
        sv.load_schema(path)
