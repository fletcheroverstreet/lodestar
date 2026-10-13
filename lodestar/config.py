"""Typed configuration loader for lodestar.

Every weight, threshold, window, and half-life used anywhere in the
scoring pipeline is declared in config.yaml and loaded here. Nothing
downstream should hardcode a number that belongs in that file — if a
bucket needs a new parameter, add it to config.yaml and read it through
BucketConfig.params, don't hardcode it in the metric function.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# The seven scoring buckets, in the order the project spec lists them.
# "news" has no per-metric weights (it's a single signal, not a blend of
# several), everything else does.
BUCKET_NAMES = (
    "quality", "value", "growth", "capital_allocation",
    "accounting_quality", "momentum", "news",
)


@dataclass(frozen=True)
class BucketConfig:
    """One factor bucket's settings.

    `metric_weights` maps metric name -> weight, and sums to 1.0 (enforced
    at load time) for every bucket except "news". `params` holds whatever
    else that bucket's section of config.yaml declares (windows, floors,
    thresholds) — read via BucketConfig.param(), not a hardcoded literal.
    """

    name: str
    metric_weights: dict[str, float]
    params: dict[str, Any]

    def metric_names(self) -> list[str]:
        return list(self.metric_weights)

    def param(self, key: str) -> Any:
        if key not in self.params:
            raise KeyError(
                f"config.yaml section '{self.name}' has no parameter '{key}'. "
                f"Available: {sorted(self.params)}"
            )
        return self.params[key]


@dataclass(frozen=True)
class Config:
    """The full, validated lodestar configuration, loaded once per run."""

    path: Path
    raw: dict[str, Any]

    universe_constituents_file: Path | None
    universe_min_history_quarters: int

    peer_min_group_size: int
    winsorize_low_pct: float
    winsorize_high_pct: float

    coverage_bucket_floor: float
    composite_min_weight_covered: float

    composite_weights: dict[str, float]
    buckets: dict[str, BucketConfig]

    @property
    def config_hash(self) -> str:
        """Stable hash of the entire config file's content.

        This is the other half of the determinism guarantee in the project
        spec: "same as-of date + same config hash = identical output".
        Sorting keys before hashing means key reordering in the YAML file
        (which changes nothing semantically) doesn't change the hash;
        changing any actual value does.
        """
        blob = json.dumps(self.raw, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:12]

    def bucket(self, name: str) -> BucketConfig:
        if name not in self.buckets:
            raise KeyError(f"no bucket config named {name!r}. "
                            f"Available: {sorted(self.buckets)}")
        return self.buckets[name]


def load_config(path: str | Path) -> Config:
    """Parse config.yaml and validate it. Raises ValueError with a clear
    message if any weight group doesn't sum to 1.0 — a silently wrong
    weight vector is worse than a startup crash."""
    path = Path(path)
    raw = yaml.safe_load(path.read_text())

    composite_weights = dict(raw["composite"]["weights"])
    _validate_weights(composite_weights, "composite.weights")
    if set(composite_weights) != set(BUCKET_NAMES):
        raise ValueError(
            f"composite.weights must have exactly the buckets {BUCKET_NAMES}, "
            f"got {sorted(composite_weights)}"
        )

    buckets: dict[str, BucketConfig] = {}
    for name in BUCKET_NAMES:
        section = dict(raw.get(name) or {})
        metric_weights = dict(section.pop("metrics", {}) or {})
        _validate_weights(metric_weights, f"{name}.metrics")
        buckets[name] = BucketConfig(
            name=name, metric_weights=metric_weights, params=section
        )

    constituents = raw["universe"].get("constituents_file")
    constituents_path = None
    if constituents:
        # RELATIVE TO THIS FILE, NOT TO WHEREVER THE COMMAND WAS RUN FROM.
        #
        # A bare `Path(constituents)` resolves against the process's working
        # directory, so the shipped `../finlake/universe/sp500_ndx.csv` pointed
        # at the right file only when lodestar happened to be launched from its
        # own folder (or, by coincidence, from finlake's). Run from anywhere
        # else - the parent directory, a scheduled task, the daemon started
        # from a different terminal - and it named a file that does not exist,
        # and the nightly re-score died on a FileNotFoundError.
        #
        # A path written inside a config file means "relative to the config
        # file"; that is how the value in config.yaml was always meant to be
        # read. Absolute paths are left exactly as given.
        constituents_path = Path(constituents)
        if not constituents_path.is_absolute():
            constituents_path = (path.parent / constituents_path).resolve()

    return Config(
        path=path,
        raw=raw,
        universe_constituents_file=constituents_path,
        universe_min_history_quarters=raw["universe"]["min_history_quarters"],
        peer_min_group_size=raw["peer_groups"]["min_group_size"],
        winsorize_low_pct=raw["peer_groups"]["winsorize_low_pct"],
        winsorize_high_pct=raw["peer_groups"]["winsorize_high_pct"],
        coverage_bucket_floor=raw["coverage"]["bucket_floor"],
        # Default 0.0 keeps an older config.yaml working unchanged (no
        # composite-level floor), rather than crashing on a missing key.
        composite_min_weight_covered=raw["coverage"].get(
            "composite_min_weight_covered", 0.0),
        composite_weights=composite_weights,
        buckets=buckets,
    )


def _validate_weights(weights: dict[str, float], label: str, tol: float = 1e-6) -> None:
    if not weights:
        return
    total = sum(weights.values())
    if abs(total - 1.0) > tol:
        raise ValueError(f"{label} must sum to 1.0, got {total:.6f}: {weights}")
