"""Checks on committed files that the pipeline and the brief depend on."""

import json

import pytest

from cct_pipeline.config import PROJECT_ROOT, load_config
from cct_pipeline.subsample import _single_feature, compute_centroid
from cct_pipeline.wind import STATUS_OK, WIND_STATUS_COLUMN, parse_workbook


@pytest.fixture(scope="module")
def config():
    return load_config()


def test_committed_wind_fallback_is_usable(config):
    path = config.paths.fallback_dir / config.wind.fallback_file
    hourly = parse_workbook(path, config.wind.station, config.wind.year)
    assert len(hourly) == 8784
    assert (hourly[WIND_STATUS_COLUMN] == STATUS_OK).sum() == 2324


def test_committed_suburb_fallback_matches_the_configured_suburb(config):
    settings = config.subsample
    path = config.paths.fallback_dir / "suburb_saxonsea.geojson"
    feature = _single_feature(json.loads(path.read_text("utf-8")), settings)
    # compute_centroid raises if the centroid is outside the Atlantis area.
    assert compute_centroid(feature, settings.atlantis_area).lat < 0


def test_anonymisation_justification_is_under_500_words():
    text = (PROJECT_ROOT / "docs" / "ANONYMISATION.md").read_text("utf-8")
    # Counting on whitespace is deliberately strict: table markup counts as words too.
    assert len(text.split()) < 500


def test_outputs_and_downloads_are_never_committed():
    ignored = (PROJECT_ROOT / ".gitignore").read_text("utf-8").splitlines()
    assert "output/" in ignored
    assert "data/cache/" in ignored
