from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from market_data_pipeline.config import DATA_DIR, ROOT_DIR

RULES_PATH = ROOT_DIR / "confluence_report_rules.yaml"


def load_rules(path: Path = RULES_PATH) -> dict:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def select_names(momentum: pd.DataFrame, rules: dict) -> list[str]:
    """Focus list (in the order given) plus, optionally, every ACCELERATING name, restricted to symbols with data."""
    available = set(momentum["symbol"])
    names = [s for s in rules.get("focus", []) if s in available]
    if rules.get("include_accelerating", True):
        accelerating = momentum[(momentum["delta_signal"] == "ACCELERATING") & (~momentum["cash_like"])]
        names += [s for s in accelerating["symbol"] if s not in names]
    return names


def load_picks(data_dir: Path = DATA_DIR, rules_path: Path = RULES_PATH) -> list[str]:
    """The tickers the confluence report shows: the rules-file focus list plus ACCELERATING names in momentum.parquet."""
    momentum_path = data_dir / "momentum.parquet"
    if not momentum_path.exists():
        raise SystemExit(
            f"Missing {momentum_path.name}, needed to work out your picks. Run "
            "`uv run python -m market_data_pipeline.transforms.momentum` first, or pass --symbols explicitly."
        )
    return select_names(pd.read_parquet(momentum_path), load_rules(rules_path))
