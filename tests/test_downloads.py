import pytest
import requests

from cct_pipeline.downloads import Downloader, DownloadError
from tests.conftest import FakeResponse, FakeSession

URL = "https://example.test/file"


def _downloader(config, routes):
    session = FakeSession(routes)
    return Downloader(config, session=session, sleep=lambda _: None), session


def test_404_is_not_retried(download_config):
    downloader, session = _downloader(download_config, {URL: FakeResponse(404)})
    with pytest.raises(DownloadError, match="404"):
        downloader.get_bytes(URL)
    assert len(session.calls) == 1


def test_server_error_is_retried_until_success(download_config):
    outcomes = [FakeResponse(503), FakeResponse(503), FakeResponse(200, b"ok")]
    downloader, session = _downloader(download_config, {URL: outcomes})
    assert downloader.get_bytes(URL) == b"ok"
    assert len(session.calls) == 3


def test_attempts_are_capped(download_config, connection_error):
    downloader, session = _downloader(download_config, {URL: connection_error})
    with pytest.raises(DownloadError, match="3 attempts"):
        downloader.get_bytes(URL)
    assert len(session.calls) == download_config.attempts


def test_timeouts_are_retried(download_config):
    outcomes = [requests.Timeout("slow"), FakeResponse(200, b"ok")]
    downloader, _ = _downloader(download_config, {URL: outcomes})
    assert downloader.get_bytes(URL) == b"ok"


def test_backoff_waits_grow_and_respect_the_ceiling(download_config):
    waits = []
    config = type(download_config)(
        attempts=5,
        backoff_seconds=2.0,
        max_backoff_seconds=5.0,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=1.0,
    )
    downloader = Downloader(
        config, session=FakeSession({URL: FakeResponse(503)}), sleep=waits.append
    )
    with pytest.raises(DownloadError):
        downloader.get_bytes(URL)
    assert len(waits) == 4
    assert all(wait <= 5.0 for wait in waits)
    assert 1.0 <= waits[0] <= 2.0


def test_failed_download_leaves_no_partial_file(download_config, tmp_path):
    downloader, _ = _downloader(download_config, {URL: FakeResponse(404)})
    destination = tmp_path / "file.bin"
    with pytest.raises(DownloadError):
        downloader.download(URL, destination)
    assert list(tmp_path.iterdir()) == []


def test_download_writes_the_complete_file(download_config, tmp_path):
    downloader, _ = _downloader(download_config, {URL: b"content"})
    destination = tmp_path / "nested" / "file.bin"
    assert downloader.download(URL, destination).read_bytes() == b"content"


def test_cached_file_avoids_the_network(download_config, tmp_path):
    downloader, session = _downloader(download_config, {URL: b"new"})
    destination = tmp_path / "file.bin"
    destination.write_bytes(b"old")
    assert downloader.download_cached(URL, destination).read_bytes() == b"old"
    assert session.calls == []
