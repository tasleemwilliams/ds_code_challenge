"""Command-line entry point: python -m cct_pipeline."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from cct_pipeline.config import DEFAULT_CONFIG_PATH, load_config
from cct_pipeline.errors import PipelineError
from cct_pipeline.pipeline import run_pipeline

logger = logging.getLogger("cct_pipeline")


def _configure_logging(level: str, log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file, mode="w")],
    )
    # Libraries log every request at lower levels, which would bury the pipeline's own logs.
    for noisy in ("botocore", "boto3", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the data engineering challenge pipeline.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING"])
    arguments = parser.parse_args(argv)

    config = load_config(arguments.config)
    _configure_logging(arguments.log_level, config.paths.output_dir / "pipeline.log")
    try:
        report = run_pipeline(config)
    except PipelineError as exc:
        logger.error("pipeline stopped: %s", exc)
        return 1
    total = sum(step["seconds"] for step in report.steps)
    logger.info("pipeline finished seconds=%.1f outputs=%s", total, config.paths.output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
