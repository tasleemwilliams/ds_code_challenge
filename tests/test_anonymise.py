import h3
import numpy as np
import pandas as pd
import pytest

from cct_pipeline import anonymise as an
from cct_pipeline.errors import DataValidationError
from cct_pipeline.wind import DIRECTION_COLUMN, SPEED_COLUMN, WIND_STATUS_COLUMN

CELL = h3.latlng_to_cell(-33.5512, 18.4881, 8)
OTHER_CELL = h3.latlng_to_cell(-33.5812, 18.4981, 8)
CODE = "Sewer: Blocked/Overflow"


def _records(count=5, cell=CELL, code=CODE, created="2020-01-01 08:15:30+02:00", cause=None):
    return pd.DataFrame(
        {
            "notification_number": range(1000, 1000 + count),
            "reference_number": ["9109228177"] * count,
            "creation_timestamp": [created] * count,
            "completion_timestamp": ["2020-01-02 13:45:00+02:00"] * count,
            "directorate": ["WATER AND SANITATION"] * count,
            "department": ["Distribution Services"] * count,
            "branch": ["Reticulation"] * count,
            "section": ["Reticulation WW Conveyance"] * count,
            "code_group": ["SEWER"] * count,
            "code": [code] * count,
            "cause_code_group": [None] * count,
            "cause_code": [cause] * count,
            "official_suburb": ["SAXONSEA"] * count,
            "latitude": [-33.5512] * count,
            "longitude": [18.4881] * count,
            "h3_level8_index": [cell] * count,
            DIRECTION_COLUMN: [90.0] * count,
            SPEED_COLUMN: [5.0] * count,
            WIND_STATUS_COLUMN: ["ok"] * count,
        }
    )


def _hourly():
    hours = pd.date_range("2020-01-01 06:00", periods=6, freq="h")
    return pd.DataFrame(
        {
            "hour": hours,
            DIRECTION_COLUMN: [10.0, 20.0, 30.0, 40.0, 50.0, np.nan],
            SPEED_COLUMN: [1.0, 2.0, 3.0, 4.0, 5.0, np.nan],
            WIND_STATUS_COLUMN: ["ok"] * 5 + ["Down"],
        }
    )


def test_direct_identifiers_and_precise_fields_are_removed(anonymisation_config):
    output = an.anonymise(_records(), _hourly(), anonymisation_config).anonymised
    for column in (
        "notification_number",
        "reference_number",
        "latitude",
        "longitude",
        "official_suburb",
        "creation_timestamp",
        "completion_timestamp",
        "branch",
        "section",
        DIRECTION_COLUMN,
        SPEED_COLUMN,
        WIND_STATUS_COLUMN,
    ):
        assert column not in output.columns


def test_unexpected_new_source_columns_cannot_reach_the_output(anonymisation_config):
    records = _records().assign(resident_phone="0210000000")
    output = an.anonymise(records, _hourly(), anonymisation_config).anonymised
    assert "resident_phone" not in output.columns


def test_times_are_floored_to_six_hour_local_blocks(anonymisation_config):
    output = an.anonymise(_records(), _hourly(), anonymisation_config).anonymised
    assert output[an.CREATED_BLOCK_COLUMN].iloc[0] == pd.Timestamp("2020-01-01 06:00:00+02:00")
    assert output[an.COMPLETED_BLOCK_COLUMN].iloc[0] == pd.Timestamp("2020-01-02 12:00:00+02:00")


def test_missing_completion_time_stays_missing():
    blocks = an.time_blocks(pd.Series(["2020-01-01 08:00:00+02:00", None]), 6)
    assert blocks.isna().tolist() == [False, True]


def test_location_stays_at_h3_level_8(anonymisation_config):
    output = an.anonymise(_records(), _hourly(), anonymisation_config).anonymised
    assert output["h3_level8_index"].map(h3.get_resolution).eq(8).all()


def test_wind_is_the_same_for_requests_at_different_hours_in_a_block(anonymisation_config):
    early = _records(5, created="2020-01-01 06:05:00+02:00")
    late = _records(5, created="2020-01-01 10:55:00+02:00").assign(
        **{DIRECTION_COLUMN: 50.0, SPEED_COLUMN: 5.0}
    )
    records = pd.concat([early, late], ignore_index=True)
    output = an.anonymise(records, _hourly(), anonymisation_config).anonymised
    assert output[an.BLOCK_SPEED_COLUMN].nunique() == 1
    assert output[an.BLOCK_SPEED_COLUMN].iloc[0] == 3.0
    assert output[an.BLOCK_DIRECTION_COLUMN].iloc[0] == pytest.approx(30.0)
    assert output[an.BLOCK_HOURS_COLUMN].iloc[0] == 5


def test_block_with_no_readings_has_empty_wind(anonymisation_config):
    records = _records(created="2020-03-01 08:00:00+02:00")
    output = an.anonymise(records, _hourly(), anonymisation_config).anonymised
    assert output[an.BLOCK_SPEED_COLUMN].isna().all()
    assert output[an.BLOCK_HOURS_COLUMN].eq(0).all()


def test_circular_mean_handles_the_north_wraparound():
    mean = an.circular_mean_degrees(pd.Series([350.0, 10.0]))
    assert min(mean, 360 - mean) == pytest.approx(0.0, abs=0.1)


@pytest.mark.parametrize(
    "code, reason",
    [
        ("Indigent Application", "sensitive:financial_status"),
        ("Customer : Reconnection Request", "sensitive:financial_status"),
        ("Suspect Tampering", "sensitive:alleged_wrongdoing"),
        ("Housing Tenant Transfers", "sensitive:property_or_tenancy"),
    ],
)
def test_sensitive_requests_go_to_review(anonymisation_config, code, reason):
    result = an.anonymise(_records(10, code=code), _hourly(), anonymisation_config)
    assert result.anonymised.empty
    assert set(result.review_queue[an.REVIEW_REASON_COLUMN]) == {reason}


def test_sensitive_cause_code_also_triggers_review(anonymisation_config):
    records = _records(10, code="Meter Replacement Project", cause="Tampering")
    assert an.anonymise(records, _hourly(), anonymisation_config).anonymised.empty


def test_maintenance_cause_is_not_mistaken_for_tenancy(anonymisation_config):
    # "Maintenance" contains "tenan"; the pattern must match the word, not the substring.
    records = _records(10, code="Meter Replacement Project", cause="Maintenance")
    assert len(an.anonymise(records, _hourly(), anonymisation_config).anonymised) == 10


def test_council_housing_code_group_goes_to_review_even_without_department(anonymisation_config):
    records = _records(10, code="Plumbing")
    records["code_group"] = "HOUSING - EXISTING"
    records["department"] = None
    result = an.anonymise(records, _hourly(), anonymisation_config)
    assert result.anonymised.empty
    assert set(result.review_queue[an.REVIEW_REASON_COLUMN]) == {"sensitive:property_or_tenancy"}


def test_small_groups_go_to_review(anonymisation_config):
    records = pd.concat([_records(5), _records(4, code="Burst Pipe")], ignore_index=True)
    result = an.anonymise(records, _hourly(), anonymisation_config)
    assert len(result.anonymised) == 5
    assert set(result.review_queue[an.REVIEW_REASON_COLUMN]) == {an.REASON_SMALL_GROUP}


def test_same_code_in_a_different_hexagon_is_a_separate_group(anonymisation_config):
    records = pd.concat([_records(5), _records(1, cell=OTHER_CELL)], ignore_index=True)
    assert len(an.anonymise(records, _hourly(), anonymisation_config).anonymised) == 5


def test_group_sizes_are_counted_after_sensitive_records_are_removed(anonymisation_config):
    plain = _records(4, cause=None)
    flagged = _records(3, cause="Tampering")
    groups = dict(anonymisation_config.__dict__)
    groups["quasi_identifiers"] = ("h3_level8_index", "code")
    config = type(anonymisation_config)(**groups)
    # Seven records share the group, but only four are publishable, which is below five.
    result = an.anonymise(pd.concat([plain, flagged], ignore_index=True), _hourly(), config)
    assert result.anonymised.empty
    assert result.stats["review_reasons"] == {
        an.REASON_SMALL_GROUP: 4,
        "sensitive:alleged_wrongdoing": 3,
    }


@pytest.mark.parametrize("bad_cell", ["0", None, "not-a-cell"])
def test_records_without_a_valid_hexagon_go_to_review(anonymisation_config, bad_cell):
    records = pd.concat([_records(5), _records(1, cell=bad_cell)], ignore_index=True)
    result = an.anonymise(records, _hourly(), anonymisation_config)
    assert len(result.anonymised) == 5
    assert result.review_queue[an.REVIEW_REASON_COLUMN].tolist() == [an.REASON_INVALID_LOCATION]


def test_every_record_is_either_published_or_queued(anonymisation_config):
    records = pd.concat(
        [_records(5), _records(2, code="Burst Pipe"), _records(6, code="Indigent Application")],
        ignore_index=True,
    )
    result = an.anonymise(records, _hourly(), anonymisation_config)
    assert len(result.anonymised) + len(result.review_queue) == len(records)


def test_review_queue_keeps_originals_and_has_reviewer_columns(anonymisation_config):
    result = an.anonymise(_records(1), _hourly(), anonymisation_config)
    queue = result.review_queue
    assert queue["notification_number"].tolist() == [1000]
    assert {"reviewer_decision", "reviewer_notes"} <= set(queue.columns)


def test_output_order_does_not_follow_the_original_order(anonymisation_config):
    late = _records(5, created="2020-06-01 12:00:00+02:00")
    early = _records(5, created="2020-01-01 12:00:00+02:00")
    records = pd.concat([late, early], ignore_index=True)
    output = an.anonymise(records, _hourly(), anonymisation_config).anonymised
    assert output[an.CREATED_BLOCK_COLUMN].is_monotonic_increasing


def _published(anonymisation_config):
    return an.anonymise(_records(), _hourly(), anonymisation_config).anonymised


def test_verify_catches_an_extra_column(anonymisation_config):
    output = _published(anonymisation_config).assign(latitude=-33.5)
    with pytest.raises(DataValidationError, match="unexpected columns"):
        an.verify_anonymised(output, anonymisation_config)


def test_verify_catches_a_finer_location(anonymisation_config):
    output = _published(anonymisation_config)
    output["h3_level8_index"] = h3.latlng_to_cell(-33.5512, 18.4881, 9)
    with pytest.raises(DataValidationError, match="not H3 level 8"):
        an.verify_anonymised(output, anonymisation_config)


def test_verify_catches_a_finer_time(anonymisation_config):
    output = _published(anonymisation_config)
    output[an.CREATED_BLOCK_COLUMN] = pd.Timestamp("2020-01-01 07:00:00+02:00")
    with pytest.raises(DataValidationError, match="6-hour block"):
        an.verify_anonymised(output, anonymisation_config)


def test_verify_catches_a_small_group(anonymisation_config):
    output = _published(anonymisation_config).iloc[:4]
    with pytest.raises(DataValidationError, match="minimum size"):
        an.verify_anonymised(output, anonymisation_config)


def test_verify_catches_sensitive_content(anonymisation_config):
    output = _published(anonymisation_config).assign(code="Indigent Application")
    with pytest.raises(DataValidationError, match="sensitive"):
        an.verify_anonymised(output, anonymisation_config)


def test_outputs_are_written_to_separate_folders(anonymisation_config, tmp_path):
    result = an.anonymise(_records(5), _hourly(), anonymisation_config)
    paths = an.write_outputs(result, tmp_path)
    assert paths["anonymised"].parent.name == "anonymised"
    assert paths["review_queue"].parent.name == "restricted"
