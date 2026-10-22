from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .config import DATA_DIR


def save_parquet(df: pd.DataFrame, filename: str) -> Path:
    """Write via a temp file and rename, so a crash or a locked target never leaves a half-written parquet.

    The large price files are 100+ MB; writing them in place meant an interrupted run corrupted the only copy.
    """
    path = DATA_DIR / filename
    tmp = path.with_name(path.name + ".tmp")
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def read_parquet(filename: str) -> pd.DataFrame:
    return pd.read_parquet(DATA_DIR / filename)


def merge_frames(
    existing: pd.DataFrame | None, new: pd.DataFrame, keys: list[str], sort_by: list[str] | None = None
) -> pd.DataFrame:
    """Append `new` to `existing`, keeping one row per `keys` with the new row winning.

    New wins so a bar fetched while it was still forming gets replaced by its final values on the next run.
    Existing column order is kept, columns only the new frame has are added at the end.
    """
    if existing is None or existing.empty:
        merged = new.copy()
    elif new.empty:
        merged = existing.copy()
    else:
        columns = list(existing.columns) + [c for c in new.columns if c not in existing.columns]
        merged = pd.concat([existing.reindex(columns=columns), new.reindex(columns=columns)], ignore_index=True)
    merged = merged.drop_duplicates(keys, keep="last")
    return merged.sort_values(sort_by or keys, kind="stable").reset_index(drop=True)


def merge_parquet(
    new: pd.DataFrame, filename: str, keys: list[str], sort_by: list[str] | None = None
) -> pd.DataFrame:
    """`merge_frames` against the parquet already on disk (a missing file is a first run)."""
    path = DATA_DIR / filename
    existing = pd.read_parquet(path) if path.exists() else None
    return merge_frames(existing, new, keys, sort_by)


def prune_old_snapshots(df: pd.DataFrame, keep_days: int, today: date | None = None) -> pd.DataFrame:
    """Keep the last `keep_days` of `snapshot_date` values (ISO date strings)."""
    if df.empty:
        return df
    cutoff = ((today or date.today()) - timedelta(days=keep_days)).isoformat()
    return df[df["snapshot_date"].astype(str) >= cutoff].reset_index(drop=True)


def latest_snapshot(df: pd.DataFrame, by: str | None = "underlying_symbol", column: str = "snapshot_date") -> pd.DataFrame:
    """Rows from the most recent `column` value, per `by` group (or overall when `by` is None).

    The options and Deribit files keep a history of daily snapshots; anything that sums open interest or reads
    "the chain" must look at one snapshot, or it counts every stored day. A frame without the column is returned
    unchanged (older files, hand-built test frames).
    """
    if column not in df.columns or df.empty:
        return df
    newest = df.groupby(by)[column].transform("max") if by else df[column].max()
    return df[df[column] == newest]


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
