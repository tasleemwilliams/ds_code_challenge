"""Checks against the real data sources. Run them with: pytest -m live"""

import pytest

from cct_pipeline.config import load_config
from cct_pipeline.downloads import Downloader
from cct_pipeline.extraction import create_s3_client, select_features
from cct_pipeline.schema_validation import load_schema, score_features

pytestmark = pytest.mark.live


def test_real_s3_select_returns_conforming_hexagons():
    config = load_config()
    client = create_s3_client(config.extraction, config.sources, Downloader(config.download))
    features = select_features(client, config.extraction)
    assert len(features) == 3832
    result = score_features(features, load_schema(config.extraction.schema_file))
    assert result.score == 1.0


def test_real_wind_portal_serves_a_valid_workbook(tmp_path):
    from cct_pipeline.wind import parse_workbook

    config = load_config()
    portal = next(s for s in config.wind.sources if s.name == "open_data_portal")
    path = Downloader(config.download).download(portal.url, tmp_path / "wind")
    assert len(parse_workbook(path, config.wind.station, config.wind.year)) == 8784
