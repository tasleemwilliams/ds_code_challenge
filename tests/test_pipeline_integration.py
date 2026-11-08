"""Runs the whole pipeline on small generated data, with the network faked at its edges."""

import gzip
import json

import h3
import pandas as pd
import pytest
import yaml

from cct_pipeline import anonymise as an
from cct_pipeline.anonymise import main as anonymise_main
from cct_pipeline.downloads import Downloader
from cct_pipeline.errors import DataValidationError
from cct_pipeline.pipeline import run_pipeline
from tests.conftest import FakeResponse, FakeSession, build_config, make_features

CENTRE_LAT, CENTRE_LON = -33.5512, 18.4881
BASE_URL = "https://data.test"
SUBURB_URL = "https://suburbs.test/query"
WIND_URL = "https://wind.test/file"
DEAD_WIND_URL = "https://dead.test/file"


class FakeS3Client:
    def __init__(self, features):
        self.features = features

    def select_object_content(self, **_):
        body = "\n".join(json.dumps(feature) for feature in self.features).encode()
        events = [{"Records": {"Payload": body}}, {"Stats": {"Details": {}}}, {"End": {}}]
        return {"Payload": events}


def _service_requests(extra_rows=None):
    """Thirty requests in one Atlantis hexagon, plus some without coordinates."""
    rows = []
    for number in range(30):
        code = "Indigent Application" if number < 6 else "Sewer: Blocked/Overflow"
        rows.append(
            {
                "notification_number": 1000 + number,
                "reference_number": "9100000000",
                "creation_timestamp": f"2020-01-01 {number % 24:02d}:15:00+02:00",
                "completion_timestamp": "2020-01-03 10:00:00+02:00",
                "directorate": "WATER AND SANITATION",
                "department": "Distribution Services",
                "branch": "Reticulation",
                "section": "Conveyance",
                "code_group": "SEWER",
                "code": code,
                "cause_code_group": None,
                "cause_code": None,
                "official_suburb": "SAXONSEA",
                "latitude": CENTRE_LAT,
                "longitude": CENTRE_LON,
            }
        )
    for number in range(5):
        rows.append(
            {
                **rows[-1],
                "notification_number": 2000 + number,
                "latitude": None,
                "longitude": None,
                "official_suburb": None,
            }
        )
    rows.extend(extra_rows or [])
    return pd.DataFrame(rows)


def _gzip_csv(frame):
    return gzip.compress(frame.to_csv(index=False).encode())


def _suburb_geojson():
    half = 0.01
    ring = [
        [CENTRE_LON - half, CENTRE_LAT - half],
        [CENTRE_LON + half, CENTRE_LAT - half],
        [CENTRE_LON + half, CENTRE_LAT + half],
        [CENTRE_LON - half, CENTRE_LAT + half],
        [CENTRE_LON - half, CENTRE_LAT - half],
    ]
    feature = {
        "type": "Feature",
        "properties": {"OFC_SBRB_NAME": "SAXONSEA"},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }
    return json.dumps({"type": "FeatureCollection", "features": [feature]}).encode()


@pytest.fixture
def environment(tmp_path, base_config_raw, wind_workbook, download_config):
    def build(requests=None):
        requests = _service_requests() if requests is None else requests
        cell = h3.latlng_to_cell(CENTRE_LAT, CENTRE_LON, 8)
        features = make_features(7, resolution=8)
        cells = {feature["properties"]["index"] for feature in features}
        assert cell in cells

        with_hex = requests.copy()
        with_hex["h3_level8_index"] = [
            "0" if pd.isna(lat) else h3.latlng_to_cell(lat, lon, 8)
            for lat, lon in zip(requests["latitude"], requests["longitude"])
        ]
        reference = [
            {**f, "properties": {k: v for k, v in f["properties"].items() if k != "resolution"}}
            for f in features
        ]

        raw = dict(base_config_raw)
        raw["sources"] = {**raw["sources"], "base_url": BASE_URL}
        raw["subsample"] = {**raw["subsample"], "suburbs_query_url": SUBURB_URL}
        raw["wind"] = {
            **raw["wind"],
            "sources": [
                {"name": "dead", "url": DEAD_WIND_URL},
                {"name": "portal", "url": WIND_URL},
            ],
        }
        config = build_config(raw, tmp_path)
        session = FakeSession(
            {
                f"{BASE_URL}/city-hex-polygons-8.geojson": json.dumps(
                    {"features": reference}
                ).encode(),
                f"{BASE_URL}/sr.csv.gz": _gzip_csv(requests.reset_index()),
                f"{BASE_URL}/sr_hex.csv.gz": _gzip_csv(with_hex),
                SUBURB_URL: _suburb_geojson(),
                WIND_URL: wind_workbook,
                DEAD_WIND_URL: FakeResponse(404),
            }
        )
        downloader = Downloader(download_config, session=session, sleep=lambda _: None)
        return config, downloader, FakeS3Client(features)

    return build


def test_full_pipeline_produces_every_output(environment):
    config, downloader, client = environment()
    run_pipeline(config, downloader, client)
    output = config.paths.output_dir

    report = json.loads((output / "run_report.json").read_text())
    assert all(step["status"] == "ok" for step in report["steps"])
    assert report["metrics"]["schema_conformance"]["status"] == "PASS"
    assert report["metrics"]["join"]["matched"] == 30
    assert report["metrics"]["join"]["no_coordinates"] == 5
    assert report["metrics"]["wind_source"]["source"] == "portal"
    assert report["metrics"]["subsample"]["records"] == 30

    assert (output / "city_hex_level8.geojson").exists()
    joined = pd.read_csv(
        output / "restricted" / "service_requests_with_hex.csv.gz", dtype={"h3_level8_index": str}
    )
    assert (joined["h3_level8_index"] == "0").sum() == 5

    anonymised = pd.read_csv(output / "anonymised" / an.ANONYMISED_FILENAME)
    review = pd.read_csv(output / "restricted" / an.REVIEW_QUEUE_FILENAME)
    assert len(anonymised) == 24
    assert len(review) == 6
    assert "notification_number" not in anonymised.columns
    assert "latitude" not in anonymised.columns


def test_report_holds_no_resident_level_data(environment):
    config, downloader, client = environment()
    run_pipeline(config, downloader, client)
    report = (config.paths.output_dir / "run_report.json").read_text()
    assert "9100000000" not in report
    assert "Indigent Application" not in report
    assert "creation_timestamp" not in report


def test_join_failures_above_the_threshold_stop_the_run_and_are_reported(environment):
    outside = {
        **_service_requests().iloc[0].to_dict(),
        "notification_number": 3000,
        "latitude": -34.0443,
        "longitude": 18.7744,
    }
    config, downloader, client = environment(_service_requests([outside]))
    with pytest.raises(DataValidationError, match="failed to join"):
        run_pipeline(config, downloader, client)
    report = json.loads((config.paths.output_dir / "run_report.json").read_text())
    assert report["steps"][-1]["step"] == "2_assign_hexes"
    assert report["steps"][-1]["status"] == "failed"
    assert not (config.paths.output_dir / "anonymised").exists()


def test_anonymisation_can_run_alone_from_pipeline_files(environment, tmp_path, base_config_raw):
    config, downloader, client = environment()
    run_pipeline(config, downloader, client)
    public = config.paths.output_dir / "anonymised" / an.ANONYMISED_FILENAME
    first = public.read_text()
    public.unlink()

    raw = dict(base_config_raw)
    raw["paths"] = {key: str(tmp_path / value) for key, value in raw["paths"].items()}
    raw["extraction"] = {**raw["extraction"], "schema_file": str(config.extraction.schema_file)}
    config_path = tmp_path / "standalone.yaml"
    config_path.write_text(yaml.safe_dump(raw))
    assert anonymise_main(["--config", str(config_path)]) == 0
    assert public.read_text() == first
