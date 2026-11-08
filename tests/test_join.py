import h3
import numpy as np
import pandas as pd
import pytest

from cct_pipeline import join
from cct_pipeline.config import JoinConfig
from cct_pipeline.errors import DataValidationError

LAT, LON = -33.5512, 18.4881
CITY_CELL = h3.latlng_to_cell(LAT, LON, 8)
CONFIG = JoinConfig(max_failure_rate=0.001, max_missing_coordinate_rate=0.35)


def _requests(latitudes, longitudes):
    return pd.DataFrame(
        {
            "notification_number": range(1, len(latitudes) + 1),
            "latitude": latitudes,
            "longitude": longitudes,
        }
    )


def _stats(matched=1000, no_coordinates=0, invalid=0, outside=0):
    total = matched + no_coordinates + invalid + outside
    return join.JoinStats(total, matched, no_coordinates, invalid, outside)


def test_request_in_a_city_hexagon_is_matched():
    joined, stats = join.assign_hexes(_requests([LAT], [LON]), {CITY_CELL})
    assert joined[join.HEX_COLUMN].tolist() == [CITY_CELL]
    assert joined[join.STATUS_COLUMN].tolist() == [join.STATUS_MATCHED]
    assert stats.matched == 1


def test_empty_coordinates_get_index_zero_and_are_not_failures():
    frame = _requests([np.nan, LAT, np.nan], [np.nan, np.nan, LON])
    joined, stats = join.assign_hexes(frame, {CITY_CELL})
    assert joined[join.HEX_COLUMN].tolist() == ["0", "0", "0"]
    assert stats.no_coordinates == 3
    assert stats.failed == 0


def test_impossible_coordinates_get_index_zero_and_are_failures():
    joined, stats = join.assign_hexes(_requests([95.0], [LON]), {CITY_CELL})
    assert joined[join.HEX_COLUMN].tolist() == ["0"]
    assert joined[join.STATUS_COLUMN].tolist() == [join.STATUS_INVALID_COORDINATES]
    assert stats.failed == 1


def test_request_outside_the_city_keeps_its_index_and_is_a_failure():
    joined, stats = join.assign_hexes(_requests([-34.044257], [18.774378]), {CITY_CELL})
    assert joined[join.HEX_COLUMN].tolist() == [h3.latlng_to_cell(-34.044257, 18.774378, 8)]
    assert joined[join.STATUS_COLUMN].tolist() == [join.STATUS_OUTSIDE_CITY]
    assert stats.outside_city_hexes == 1


def test_each_request_gets_exactly_one_status():
    frame = _requests([LAT, np.nan, 95.0, -34.0], [LON, np.nan, LON, 18.8])
    _, stats = join.assign_hexes(frame, {CITY_CELL})
    assert stats.matched + stats.no_coordinates + stats.failed == stats.total


def test_failure_rate_excludes_requests_without_coordinates():
    stats = _stats(matched=90, no_coordinates=100, outside=10)
    assert stats.failure_rate == pytest.approx(0.1)


def test_failure_rate_is_zero_when_no_request_has_coordinates():
    assert _stats(matched=0, no_coordinates=10).failure_rate == 0.0


def test_failures_at_the_threshold_are_accepted():
    join.enforce_join_thresholds(_stats(matched=999, outside=1), CONFIG)


def test_failures_above_the_threshold_stop_the_pipeline():
    with pytest.raises(DataValidationError, match="failed to join"):
        join.enforce_join_thresholds(_stats(matched=998, outside=2), CONFIG)


def test_too_many_missing_coordinates_stop_the_pipeline():
    with pytest.raises(DataValidationError, match="no coordinates"):
        join.enforce_join_thresholds(_stats(matched=60, no_coordinates=40), CONFIG)


def test_join_logs_failure_count(caplog):
    caplog.set_level("INFO")
    join.enforce_join_thresholds(_stats(matched=9999, outside=1), CONFIG)
    assert "failed=1" in caplog.text


def _hex_frame(numbers, cells):
    return pd.DataFrame({"notification_number": numbers, join.HEX_COLUMN: cells})


def test_matching_reference_reports_no_differences():
    frame = _hex_frame([1, 2], [CITY_CELL, "0"])
    comparison = join.compare_to_reference(frame, frame.iloc[::-1])
    assert comparison["index_mismatches"] == 0
    join.require_reference_match(comparison)


def test_reference_mismatch_stops_the_pipeline():
    comparison = join.compare_to_reference(
        _hex_frame([1, 2], [CITY_CELL, "0"]), _hex_frame([1, 2], [CITY_CELL, CITY_CELL])
    )
    assert comparison["index_mismatches"] == 1
    with pytest.raises(DataValidationError):
        join.require_reference_match(comparison)


def test_rows_missing_from_either_side_are_counted():
    comparison = join.compare_to_reference(
        _hex_frame([1, 2], ["0", "0"]), _hex_frame([2, 3], ["0", "0"])
    )
    assert comparison["missing_from_reference"] == 1
    assert comparison["missing_from_joined"] == 1


def test_duplicate_notification_numbers_are_rejected():
    frame = _hex_frame([1, 1], ["0", "0"])
    with pytest.raises(DataValidationError, match="not unique"):
        join.compare_to_reference(frame, frame)
