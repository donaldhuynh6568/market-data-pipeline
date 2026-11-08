"""Strategy parameters from config/strategy.yaml: the one place they are defined, for dbt and the simulation alike."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .config import STRATEGY_PATH

# Keys dbt models read with var(). Everything else is simulation-only.
DBT_VAR_KEYS = (
    "execution_lag_days",
    "ffill_cap_days",
    "target_horizon",
    "split",
    "report_splits",
    "top_n_grid",
    "trend_filter",
    "fred_lag_days",
)


def load_strategy(path: str | Path = STRATEGY_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        settings = yaml.safe_load(handle) or {}
    split = settings.get("split", {})
    if split and abs(sum(split.values()) - 1.0) > 1e-9:
        raise ValueError(f"split fractions must sum to 1, got {split}")
    return settings


def dbt_vars(settings: dict[str, Any] | None = None, include_test: bool = False) -> dict[str, Any]:
    settings = settings or load_strategy()
    out = {k: settings[k] for k in DBT_VAR_KEYS if k in settings}
    if include_test:
        out["report_splits"] = list(dict.fromkeys([*out.get("report_splits", []), "test"]))
    return out


def dbt_vars_arg(settings: dict[str, Any] | None = None, include_test: bool = False) -> str:
    return json.dumps(dbt_vars(settings, include_test))
