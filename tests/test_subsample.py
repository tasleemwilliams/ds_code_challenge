import json
import math

import numpy as np
import pandas as pd
import pytest

from cct_pipeline import subsample
from cct_pipeline.config import AreaConfig, SubsampleConfig
from cct_pipeline.downloads import Downloader
from cct_pipeline.errors import DataValidationError, SourceUnavailableError
from tests.conftest import FakeSession

AREA = AreaConfig(min_lat=-33.62, max_lat=-33.50, min_lon=18.44, max_lon=18.55)
SERVICE_URL = "https://example.test/suburbs"
CONFIG = SubsampleConfig(
    suburb="SAXONSEA",
    radius_arcminutes=1.0,
    suburbs_query_url=SERVICE_URL,
    suburb_name_field="NAME",
    atlantis_area=AREA,
)


def _square(lat, lon, half=0.01, name="SAXONSEA"):
    ring = [
        [lon - half, lat - half],
        [lon + half, lat - half],
        [lon + half, lat + half],
        [lon - half, lat + half],
        [lon - half, lat - half],
    ]
    return {
        "type": "Feature",
        "properties": {"NAME": name},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


def _collection(*features):
    return json.dumps({"type": "FeatureCollection", "features": list(features)}).encode()


def test_one_arcminute_of_latitude_measures_one():
    distance = subsample.great_circle_arcminutes(
        -33.0, 18.0, np.array([-33.0 + 1 / 60]), np.array([18.0])
    )
    assert distance[0] == pytest.approx(1.0, abs=1e-6)


def test_a_minute_of_longitude_is_shorter_away_from_the_equator():
    distance = subsample.great_circle_arcminutes(
        -33.0, 18.0, np.array([-33.0]), np.array([18.0 + 1 / 60])
    )
    assert distance[0] == pytest.approx(math.cos(math.radians(33.0)), abs=1e-4)


def test_selects_only_requests_inside_the_radius():
    centre = subsample.Centroid(lat=-33.55, lon=18.49)
    frame = pd.DataFrame(
        {
            "latitude": [-33.55, -33.55 + 0.99 / 60, -33.55 + 1.01 / 60, np.nan],
            "longitude": [18.49, 18.49, 18.49, 18.49],
        }
    )
    selected = subsample.select_within_radius(frame, centre, 1.0)
    assert selected.index.tolist() == [0, 1]


def test_centroid_is_computed_from_the_polygon():
    centroid = subsample.compute_centroid(_square(-33.55, 18.49), AREA)
    assert centroid.lat == pytest.approx(-33.55)
    assert centroid.lon == pytest.approx(18.49)


def test_centroid_away_from_atlantis_is_rejected():
    # Beacon Valley is a real case: it sounds nearby but is in Mitchells Plain.
    with pytest.raises(DataValidationError, match="not near Atlantis"):
        subsample.compute_centroid(_square(-34.04, 18.63), AREA)


def test_invalid_polygon_is_rejected():
    feature = _square(-33.55, 18.49)
    feature["geometry"]["coordinates"] = [
        [[18.48, -33.55], [18.50, -33.54], [18.50, -33.55], [18.48, -33.54], [18.48, -33.55]]
    ]
    with pytest.raises(DataValidationError, match="valid polygon"):
        subsample.compute_centroid(feature, AREA)


def _fetch(routes, download_config, cache, fallback):
    session = FakeSession(routes)
    downloader = Downloader(download_config, session=session, sleep=lambda _: None)
    return subsample.fetch_suburb_feature(CONFIG, downloader, cache, fallback), session


def test_boundary_comes_from_the_service_and_is_then_cached(download_config, tmp_path):
    routes = {SERVICE_URL: _collection(_square(-33.55, 18.49))}
    (_, source), _ = _fetch(routes, download_config, tmp_path, tmp_path / "none")
    assert source == "city_service"
    (_, source), session = _fetch({}, download_config, tmp_path, tmp_path / "none")
    assert source == "cache"
    assert session.calls == []


def test_boundary_falls_back_when_the_service_is_down(download_config, tmp_path, connection_error):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    (fallback / "suburb_saxonsea.geojson").write_bytes(_collection(_square(-33.55, 18.49)))
    routes = {SERVICE_URL: connection_error}
    (_, source), _ = _fetch(routes, download_config, tmp_path / "cache", fallback)
    assert source == "fallback"


def test_wrong_suburb_in_the_response_is_not_accepted(download_config, tmp_path):
    routes = {SERVICE_URL: _collection(_square(-33.55, 18.49, name="OTHER"))}
    with pytest.raises(SourceUnavailableError):
        _fetch(routes, download_config, tmp_path / "cache", tmp_path / "none")


def test_apostrophes_in_suburb_names_are_escaped():
    config = SubsampleConfig(**{**CONFIG.__dict__, "suburb": "GORDON'S BAY"})
    assert "GORDON''S BAY" in subsample._query_parameters(config)["where"]
