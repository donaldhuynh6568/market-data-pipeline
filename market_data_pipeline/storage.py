from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from .config import DATA_DIR


def save_parquet(df: pd.DataFrame, filename: str) -> Path:
    path = DATA_DIR / filename
    df.to_parquet(path, index=False)
    return path


def read_parquet(filename: str) -> pd.DataFrame:
    return pd.read_parquet(DATA_DIR / filename)


def get_supabase_client():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key or "your_supabase" in url:
        return None

    from supabase import create_client

    return create_client(url, key)


def upsert_to_supabase(table: str, records: list[dict], on_conflict: str) -> None:
    client = get_supabase_client()
    if client is None:
        print(f"  [supabase] not configured, skipping write to '{table}'")
        return

    try:
        client.table(table).upsert(records, on_conflict=on_conflict).execute()
        print(f"  [supabase] upserted {len(records)} rows into '{table}'")
    except Exception as exc:  # pragma: no cover - best effort external dependency
        print(f"  [supabase] WARNING: upsert to '{table}' failed: {exc}")
