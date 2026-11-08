import numpy as np
import pandas as pd
import pytest

from cct_pipeline import wind
from cct_pipeline.config import WindConfig, WindSource
from cct_pipeline.downloads import Downloader
from cct_pipeline.errors import DataValidationError, SourceUnavailableError
from tests.conftest import FakeResponse, FakeSession, wind_sheet, workbook_bytes

STATION = "Atlantis AQM Site"
PRIMARY = "https://primary.test/wind"
SECONDARY = "https://secondary.test/wind"
CONFIG = WindConfig(
    station=STATION,
    year=2020,
    fallback_file="wind_2020.xlsx",
    sources=(WindSource("primary", PRIMARY), WindSource("secondary", SECONDARY)),
    min_request_coverage=0.25,
)


def _parse(tmp_path, content, name="wind"):
    path = tmp_path / name
    path.write_bytes(content)
    return wind.parse_workbook(path, STATION, 2020)


def test_parses_the_station_columns_and_every_hour(tmp_path, wind_workbook):
    hourly = _parse(tmp_path, wind_workbook)
    assert len(hourly) == 8784  # 2020 was a leap year.
    first = hourly.iloc[0]
    assert first["hour"] == pd.Timestamp("2020-01-01 00:00")
    assert first[wind.DIRECTION_COLUMN] == 0.0
    assert first[wind.SPEED_COLUMN] == 1.0
    assert hourly[wind.DIRECTION_COLUMN].max() < 999  # The other station is never read.


def test_footer_summary_rows_are_excluded(tmp_path, wind_workbook):
    assert _parse(tmp_path, wind_workbook)["hour"].max() == pd.Timestamp("2020-12-31 23:00")


def test_reads_ods_as_well_as_xlsx(tmp_path):
    hourly = _parse(tmp_path, workbook_bytes(engine="odf"))
    assert len(hourly) == 8784


def test_true_date_cells_are_supported(tmp_path):
    sheet = wind_sheet()
    sheet.iloc[5:8789, 0] = pd.date_range("2020-01-01", periods=8784, freq="h").strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    assert _parse(tmp_path, workbook_bytes(sheet)).iloc[1]["hour"] == pd.Timestamp(
        "2020-01-01 01:00"
    )


def test_gap_reasons_are_preserved_and_values_left_empty(tmp_path):
    sheet = wind_sheet()
    sheet.iloc[5, [1, 2]] = "NoData"
    sheet.iloc[6, [1, 2]] = "Down"
    sheet.iloc[7, [1, 2]] = "<Samp"
    hourly = _parse(tmp_path, workbook_bytes(sheet))
    assert hourly[wind.WIND_STATUS_COLUMN].iloc[:3].tolist() == ["NoData", "Down", "<Samp"]
    assert hourly[wind.SPEED_COLUMN].iloc[:3].isna().all()


def test_impossible_readings_are_flagged_and_not_used(tmp_path):
    sheet = wind_sheet()
    sheet.iloc[5, 1] = 400.0
    hourly = _parse(tmp_path, workbook_bytes(sheet))
    assert hourly[wind.WIND_STATUS_COLUMN].iloc[0] == wind.STATUS_OUT_OF_RANGE
    assert np.isnan(hourly[wind.DIRECTION_COLUMN].iloc[0])


@pytest.mark.parametrize(
    "sheet",
    [wind_sheet(station="Elsewhere"), wind_sheet(year=2019), wind_sheet().iloc[:100]],
    ids=["station missing", "wrong year", "too few hours"],
)
def test_wrong_workbooks_are_rejected(tmp_path, sheet):
    with pytest.raises(wind.WindDataError):
        _parse(tmp_path, workbook_bytes(sheet))


def test_error_page_with_a_good_status_is_rejected(tmp_path):
    with pytest.raises(wind.WindDataError, match="not a spreadsheet"):
        _parse(tmp_path, b"<html>Service unavailable</html>")


def _acquire(routes, download_config, tmp_path, fallback=None):
    session = FakeSession(routes)
    downloader = Downloader(download_config, session=session, sleep=lambda _: None)
    fallback_dir = tmp_path / "fallback"
    fallback_dir.mkdir(exist_ok=True)
    if fallback:
        (fallback_dir / CONFIG.fallback_file).write_bytes(fallback)
    return wind.acquire_wind_data(CONFIG, downloader, tmp_path / "cache", fallback_dir), session


def test_primary_source_is_used_when_healthy(download_config, tmp_path, wind_workbook):
    acquired, _ = _acquire({PRIMARY: wind_workbook}, download_config, tmp_path)
    assert acquired.provenance["source"] == "primary"
    assert len(acquired.provenance["sha256"]) == 64


def test_404_moves_straight_to_the_next_source(download_config, tmp_path, wind_workbook):
    routes = {PRIMARY: FakeResponse(404), SECONDARY: wind_workbook}
    acquired, session = _acquire(routes, download_config, tmp_path)
    assert acquired.provenance["source"] == "secondary"
    assert session.calls.count(PRIMARY) == 1


def test_transient_fault_recovers_on_the_same_source(download_config, tmp_path, wind_workbook):
    routes = {PRIMARY: [FakeResponse(503), FakeResponse(200, wind_workbook)]}
    acquired, _ = _acquire(routes, download_config, tmp_path)
    assert acquired.provenance["source"] == "primary"


def test_invalid_content_moves_to_the_next_source(download_config, tmp_path, wind_workbook):
    routes = {PRIMARY: b"<html>maintenance</html>", SECONDARY: wind_workbook}
    acquired, _ = _acquire(routes, download_config, tmp_path)
    assert acquired.provenance["source"] == "secondary"
    assert not list((tmp_path / "cache").glob("*.candidate"))


def test_committed_copy_is_used_when_all_sources_fail(
    download_config, tmp_path, connection_error, wind_workbook
):
    routes = {PRIMARY: connection_error, SECONDARY: FakeResponse(500)}
    acquired, _ = _acquire(routes, download_config, tmp_path, fallback=wind_workbook)
    assert acquired.provenance["source"] == "committed_fallback"


def test_error_when_nothing_is_available(download_config, tmp_path, connection_error):
    with pytest.raises(SourceUnavailableError):
        _acquire({PRIMARY: connection_error}, download_config, tmp_path)


def test_second_run_uses_the_cache_without_network(download_config, tmp_path, wind_workbook):
    _acquire({PRIMARY: wind_workbook}, download_config, tmp_path)
    acquired, session = _acquire({}, download_config, tmp_path)
    assert acquired.provenance["source"] == "cache"
    assert session.calls == []


def _hourly():
    return pd.DataFrame(
        {
            "hour": pd.to_datetime(["2020-01-01 00:00", "2020-01-01 01:00"]),
            wind.DIRECTION_COLUMN: [90.0, np.nan],
            wind.SPEED_COLUMN: [5.0, np.nan],
            wind.WIND_STATUS_COLUMN: ["ok", "Down"],
        }
    )


def test_requests_get_the_wind_for_their_local_hour():
    requests = pd.DataFrame({"creation_timestamp": ["2020-01-01 00:59:59+02:00"]})
    joined = wind.join_wind(requests, _hourly())
    assert joined[wind.SPEED_COLUMN].tolist() == [5.0]


def test_timestamps_in_another_offset_are_converted_to_local_time():
    # 22:30 UTC on 31 December is 00:30 on 1 January in Cape Town.
    requests = pd.DataFrame({"creation_timestamp": ["2019-12-31 22:30:00+00:00"]})
    assert wind.join_wind(requests, _hourly())[wind.SPEED_COLUMN].tolist() == [5.0]


def test_requests_in_gaps_are_kept_with_the_reason():
    requests = pd.DataFrame(
        {"creation_timestamp": ["2020-01-01 01:10:00+02:00", "2021-06-01 10:00:00+02:00"]}
    )
    joined = wind.join_wind(requests, _hourly())
    assert len(joined) == 2
    assert joined[wind.WIND_STATUS_COLUMN].tolist() == ["Down", wind.STATUS_NO_RECORD]
    assert joined[wind.SPEED_COLUMN].isna().all()


def test_missing_creation_timestamp_is_rejected():
    requests = pd.DataFrame({"creation_timestamp": [None]})
    with pytest.raises(DataValidationError):
        wind.join_wind(requests, _hourly())


def test_low_coverage_stops_the_pipeline():
    augmented = pd.DataFrame({wind.WIND_STATUS_COLUMN: ["ok"] + ["Down"] * 9})
    coverage = wind.wind_coverage(augmented)
    assert coverage["request_coverage"] == pytest.approx(0.1)
    with pytest.raises(DataValidationError, match="wind data"):
        wind.enforce_wind_coverage(coverage, 0.25)
