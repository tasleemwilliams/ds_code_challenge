"""Section 1: score features against the declarative schema in config/hex_schema.yaml."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import h3
import yaml

from cct_pipeline.errors import DataValidationError, PipelineError

logger = logging.getLogger(__name__)

STATUS_PASS = "PASS"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"

_MISSING = object()
MAX_EXAMPLES = 5
HEXAGON_RING_POINTS = 7  # Six corners, plus the first corner repeated to close the ring.


@dataclass(frozen=True)
class Rule:
    id: str
    path: str
    check: str
    weight: float
    params: Mapping[str, Any]


@dataclass(frozen=True)
class Schema:
    name: str
    version: int
    pass_threshold: float
    warn_threshold: float
    rules: Sequence[Rule]


@dataclass(frozen=True)
class ConformanceResult:
    score: float
    status: str
    feature_count: int
    pass_threshold: float
    warn_threshold: float
    rules: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 6),
            "status": self.status,
            "feature_count": self.feature_count,
            "pass_threshold": self.pass_threshold,
            "warn_threshold": self.warn_threshold,
            "rules": self.rules,
        }


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_equals(value: Any, params: Mapping[str, Any]) -> bool:
    return value == params["value"]


def _check_number_range(value: Any, params: Mapping[str, Any]) -> bool:
    return _is_number(value) and params["min"] <= value <= params["max"]


def _check_h3_cell(value: Any, params: Mapping[str, Any]) -> bool:
    return (
        isinstance(value, str)
        and h3.is_valid_cell(value)
        and h3.get_resolution(value) == params["resolution"]
    )


def _check_centroid_matches_h3(value: Any, params: Mapping[str, Any]) -> bool:
    if not isinstance(value, Mapping):
        return False
    cell, lat, lon = value.get("index"), value.get("centroid_lat"), value.get("centroid_lon")
    if not (isinstance(cell, str) and h3.is_valid_cell(cell) and _is_number(lat)):
        return False
    if not _is_number(lon):
        return False
    centre_lat, centre_lon = h3.cell_to_latlng(cell)
    tolerance = params["tolerance_degrees"]
    return abs(centre_lat - lat) <= tolerance and abs(centre_lon - lon) <= tolerance


def _check_hexagon_polygon(value: Any, params: Mapping[str, Any]) -> bool:
    if not isinstance(value, Mapping) or value.get("type") != "Polygon":
        return False
    rings = value.get("coordinates")
    if not isinstance(rings, list) or len(rings) != 1:
        return False
    ring = rings[0]
    if not isinstance(ring, list) or len(ring) != HEXAGON_RING_POINTS or ring[0] != ring[-1]:
        return False
    for point in ring:
        if not isinstance(point, list) or len(point) != 2:
            return False
        lon, lat = point
        if not (_is_number(lon) and _is_number(lat)):
            return False
        if not params["min_lon"] <= lon <= params["max_lon"]:
            return False
        if not params["min_lat"] <= lat <= params["max_lat"]:
            return False
    return True


# "unique" is absent because it needs the whole dataset. See _rule_outcomes.
_VALUE_CHECKS: dict[str, Callable[[Any, Mapping[str, Any]], bool]] = {
    "equals": _check_equals,
    "number_range": _check_number_range,
    "h3_cell": _check_h3_cell,
    "centroid_matches_h3": _check_centroid_matches_h3,
    "hexagon_polygon": _check_hexagon_polygon,
}
_KNOWN_CHECKS = {*_VALUE_CHECKS, "unique"}
_RULE_KEYS = {"id", "path", "check", "weight"}


def load_schema(path: Path) -> Schema:
    """Load and sanity-check the schema file."""
    with Path(path).open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    rules = [
        Rule(
            id=item["id"],
            path=item["path"],
            check=item["check"],
            weight=float(item["weight"]),
            params={key: value for key, value in item.items() if key not in _RULE_KEYS},
        )
        for item in raw["rules"]
    ]
    unknown = {rule.check for rule in rules} - _KNOWN_CHECKS
    if unknown:
        raise PipelineError(f"The schema uses unknown checks: {sorted(unknown)}")
    if not rules or any(rule.weight <= 0 for rule in rules):
        raise PipelineError("The schema needs at least one rule, and all weights must be positive")
    thresholds = raw["thresholds"]
    if not 0 <= thresholds["warn"] < thresholds["pass"] <= 1:
        raise PipelineError("The schema thresholds must satisfy 0 <= warn < pass <= 1")
    return Schema(
        name=raw["name"],
        version=raw["version"],
        pass_threshold=thresholds["pass"],
        warn_threshold=thresholds["warn"],
        rules=rules,
    )


def _value_at(feature: Mapping[str, Any], path: str) -> Any:
    value: Any = feature
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _rule_outcomes(rule: Rule, features: Sequence[Mapping[str, Any]]) -> list[bool]:
    """Whether each feature passes a rule. A missing value always fails."""
    values = [_value_at(feature, rule.path) for feature in features]
    if rule.check == "unique":
        counts = Counter(value for value in values if value is not _MISSING)
        return [value is not _MISSING and counts[value] == 1 for value in values]
    check = _VALUE_CHECKS[rule.check]
    return [value is not _MISSING and check(value, rule.params) for value in values]


def score_features(features: Sequence[Mapping[str, Any]], schema: Schema) -> ConformanceResult:
    """Compute the weighted share of passing checks and the resulting status."""
    total_weight = sum(rule.weight for rule in schema.rules)
    weighted_passes = 0.0
    rule_results = []
    for rule in schema.rules:
        outcomes = _rule_outcomes(rule, features)
        passed = sum(outcomes)
        weighted_passes += passed * rule.weight
        failures = [i for i, outcome in enumerate(outcomes) if not outcome]
        rule_results.append(
            {
                "id": rule.id,
                "weight": rule.weight,
                "passed": passed,
                "failed": len(failures),
                "first_failing_positions": failures[:MAX_EXAMPLES],
            }
        )

    score = weighted_passes / (total_weight * len(features)) if features else 0.0
    if score >= schema.pass_threshold:
        status = STATUS_PASS
    elif score >= schema.warn_threshold:
        status = STATUS_WARN
    else:
        status = STATUS_FAIL
    return ConformanceResult(
        score=score,
        status=status,
        feature_count=len(features),
        pass_threshold=schema.pass_threshold,
        warn_threshold=schema.warn_threshold,
        rules=rule_results,
    )


def enforce_conformance(result: ConformanceResult) -> None:
    """Log the outcome, and stop the pipeline only if the score is below the warn band."""
    logger.info(
        "schema conformance score=%.4f status=%s features=%d",
        result.score,
        result.status,
        result.feature_count,
    )
    if result.status == STATUS_PASS:
        return
    failing = [rule for rule in result.rules if rule["failed"]]
    if result.status == STATUS_WARN:
        logger.warning("schema conformance is below the pass band: %s", failing)
        return
    raise DataValidationError(
        f"Schema conformance {result.score:.4f} is below the minimum "
        f"{result.warn_threshold}: {failing}"
    )
