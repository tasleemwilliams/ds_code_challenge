"""Step timing, resource logging and the machine-readable run report."""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:  # Not available on Windows.
    resource = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def peak_memory_mb() -> float | None:
    """Peak resident memory of this process in MB, where the platform reports it."""
    if resource is None:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes and Linux reports kilobytes.
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(peak / divisor, 1)


@dataclass
class RunReport:
    """Collects timings and metrics. It must never hold resident-level data."""

    steps: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def add_metrics(self, section: str, values: dict[str, Any]) -> None:
        self.metrics.setdefault(section, {}).update(values)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = {
            "total_seconds": round(sum(step["seconds"] for step in self.steps), 3),
            "peak_memory_mb": peak_memory_mb(),
            "steps": self.steps,
            "metrics": self.metrics,
        }
        path.write_text(json.dumps(content, indent=2, default=str) + "\n", encoding="utf-8")


@contextmanager
def timed(step: str, report: RunReport) -> Iterator[None]:
    """Log and record how long a step takes, including when it fails."""
    start = time.perf_counter()
    status = "failed"
    try:
        yield
        status = "ok"
    finally:
        seconds = round(time.perf_counter() - start, 3)
        report.steps.append({"step": step, "seconds": seconds, "status": status})
        logger.info(
            "timing step=%s seconds=%.3f status=%s peak_memory_mb=%s",
            step,
            seconds,
            status,
            peak_memory_mb(),
        )
