from __future__ import annotations

from pathlib import Path

import pandas as pd

from market_data_pipeline.config import DATA_DIR
from market_data_pipeline.raw import atomic_write_parquet

MARTS_DIR = DATA_DIR / "marts"


def read_mart(name: str, marts_dir: Path | None = None) -> pd.DataFrame:
    path = (marts_dir or MARTS_DIR) / f"{name}.parquet"
    if not path.exists():
        raise SystemExit(f"{path} not found. Run `uv run python -m market_data_pipeline.build` first.")
    return pd.read_parquet(path)


def write_mart(df: pd.DataFrame, name: str, marts_dir: Path | None = None) -> Path:
    path = atomic_write_parquet(df, (marts_dir or MARTS_DIR) / f"{name}.parquet")
    print(f"  [sim] wrote {len(df)} rows to {path}")
    return path
