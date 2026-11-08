"""Loading the service request files with memory-efficient column types."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# Repeated text values take far less memory as categories. Identifiers stay as text so
# that long numbers are never rounded or reformatted.
_COLUMN_TYPES = {
    "notification_number": "int64",
    "reference_number": "string",
    "creation_timestamp": "string",
    "completion_timestamp": "string",
    "directorate": "category",
    "department": "category",
    "branch": "category",
    "section": "category",
    "code_group": "category",
    "code": "category",
    "cause_code_group": "category",
    "cause_code": "category",
    "official_suburb": "category",
    "latitude": "float64",
    "longitude": "float64",
    "h3_level8_index": "string",
}


def load_service_requests(path: Path) -> pd.DataFrame:
    """Load a service request CSV, ignoring the unnamed row number column in sr.csv.gz."""
    return pd.read_csv(
        path,
        dtype=_COLUMN_TYPES,
        usecols=lambda column: column in _COLUMN_TYPES,
    )
