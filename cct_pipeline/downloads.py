"""HTTP downloads with bounded retries for transient faults."""

from __future__ import annotations

import logging
import os
import random
import time
from pathlib import Path
from typing import Callable, TypeVar
from urllib.parse import urlparse

import requests

from cct_pipeline.config import DownloadConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")

RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
CHUNK_BYTES = 1024 * 1024


class DownloadError(Exception):
    """A download failed after any retries it was entitled to."""


class Downloader:
    """Fetches URLs, retrying only faults that are likely to clear.

    Timeouts, dropped connections, rate limits and server errors get retries with
    exponential backoff and jitter. Other 4xx responses, such as 404, will not change on
    a retry, so they fail immediately and the caller can move to its next source.
    """

    def __init__(
        self,
        config: DownloadConfig,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._session = session or requests.Session()
        self._sleep = sleep

    def get_bytes(self, url: str, params: dict[str, str] | None = None) -> bytes:
        """Return a response body held in memory only, for small payloads."""

        def attempt() -> bytes:
            with self._request(url, params) as response:
                return response.content

        return self._with_retries(url, attempt)

    def download(self, url: str, destination: Path) -> Path:
        """Stream a URL to a file, replacing the destination only once it is complete."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".part")

        def attempt() -> Path:
            with self._request(url) as response, partial.open("wb") as handle:
                for chunk in response.iter_content(CHUNK_BYTES):
                    handle.write(chunk)
            os.replace(partial, destination)
            return destination

        try:
            return self._with_retries(url, attempt)
        finally:
            partial.unlink(missing_ok=True)

    def download_cached(self, url: str, destination: Path) -> Path:
        """Download a URL unless a completed copy is already cached."""
        if destination.exists():
            logger.info("cache hit path=%s", destination.name)
            return destination
        return self.download(url, destination)

    def _request(self, url: str, params: dict[str, str] | None = None) -> requests.Response:
        timeout = (self._config.connect_timeout_seconds, self._config.read_timeout_seconds)
        response = self._session.get(url, params=params, stream=True, timeout=timeout)
        if response.status_code in RETRYABLE_STATUS_CODES:
            response.close()
            raise _RetryableStatus(f"HTTP {response.status_code}")
        if response.status_code != 200:
            response.close()
            raise DownloadError(f"HTTP {response.status_code} from {_host(url)}")
        return response

    def _with_retries(self, url: str, attempt: Callable[[], T]) -> T:
        for number in range(1, self._config.attempts + 1):
            try:
                return attempt()
            except (requests.RequestException, _RetryableStatus) as exc:
                reason = f"{type(exc).__name__}: {exc}"
                if number == self._config.attempts:
                    raise DownloadError(
                        f"{_host(url)} failed after {number} attempts ({reason})"
                    ) from exc
                delay = self._backoff(number)
                logger.warning(
                    "download retry host=%s attempt=%d/%d wait_seconds=%.1f reason=%s",
                    _host(url),
                    number,
                    self._config.attempts,
                    delay,
                    type(exc).__name__,
                )
                self._sleep(delay)
        raise DownloadError(f"{_host(url)}: no download attempts are configured")

    def _backoff(self, attempt_number: int) -> float:
        ceiling = self._config.backoff_seconds * 2 ** (attempt_number - 1)
        bounded = min(ceiling, self._config.max_backoff_seconds)
        return bounded * random.uniform(0.5, 1.0)


class _RetryableStatus(Exception):
    """The server returned a status that may clear on a later attempt."""


def _host(url: str) -> str:
    """The host alone, so logs stay short and never carry query parameters."""
    return urlparse(url).netloc
