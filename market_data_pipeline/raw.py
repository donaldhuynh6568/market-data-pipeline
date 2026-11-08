"""Raw layer: append-only, hive-partitioned parquet plus a run manifest.

Collectors write here and nowhere else. A file is never read back and rewritten, so a bad run can only add rows,
never lose them; deduplication (latest `ingested_at` wins) is staging's job.

    data/raw/<source>/<dataset>/ingest_date=YYYY-MM-DD/part-<run_id>[-<n>].parquet
    data/raw/_manifest/ingest_date=YYYY-MM-DD/part-<run_id>.parquet
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from .config import DATA_DIR

RAW_DIR = DATA_DIR / "raw"
MANIFEST_DATASET = "_manifest"
RAW_METADATA_COLUMNS = ("source", "ingested_at", "run_id", "request_params")


def atomic_write_parquet(df: pd.DataFrame, path: Path) -> Path:
    """Write via a temp file and rename, so a crash never leaves a half-written parquet behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        df.to_parquet(tmp, index=False)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def dataset_dir(source: str, dataset: str) -> Path:
    return RAW_DIR / source / dataset


def dataset_glob(source: str, dataset: str) -> str:
    return (dataset_dir(source, dataset) / "*" / "*.parquet").as_posix()


@dataclass
class RawRun:
    source: str
    dataset: str
    request_params: dict[str, Any]
    date_col: str | None = None
    run_id: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    rows: int = 0
    min_date: str | None = None
    max_date: str | None = None
    paths: list[Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = f"{self.started_at:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"

    @property
    def ingest_date(self) -> str:
        return self.started_at.date().isoformat()

    def write(self, df: pd.DataFrame) -> Path | None:
        """Append one frame to this run's partition. Callable more than once per run (e.g. equities then crypto)."""
        if df.empty:
            return None
        if clash := set(RAW_METADATA_COLUMNS) & set(df.columns):
            raise ValueError(f"{self.source}/{self.dataset}: columns {sorted(clash)} are reserved for raw metadata")
        out = df.copy()
        out["source"] = self.source
        out["ingested_at"] = self.started_at
        out["run_id"] = self.run_id
        out["request_params"] = json.dumps(self.request_params, default=str, sort_keys=True)

        suffix = f"-{len(self.paths)}" if self.paths else ""
        path = dataset_dir(self.source, self.dataset) / f"ingest_date={self.ingest_date}" / f"part-{self.run_id}{suffix}.parquet"
        atomic_write_parquet(out, path)
        self.paths.append(path)
        self.rows += len(df)
        if self.date_col and self.date_col in df.columns:
            dates = df[self.date_col].dropna().astype(str)
            if not dates.empty:
                self.min_date = min(filter(None, [self.min_date, dates.min()]))
                self.max_date = max(filter(None, [self.max_date, dates.max()]))
        print(f"  [raw] wrote {len(df)} rows to {path}")
        return path


def _write_manifest(run: RawRun, status: str, error: str | None = None) -> Path:
    row = pd.DataFrame([{
        "run_id": run.run_id,
        "source": run.source,
        "dataset": run.dataset,
        "status": status,
        "row_count": run.rows,
        "file_count": len(run.paths),
        "min_date": run.min_date,
        "max_date": run.max_date,
        "request_params": json.dumps(run.request_params, default=str, sort_keys=True),
        "started_at": run.started_at,
        "finished_at": datetime.now(timezone.utc),
        "error": error,
    }])
    path = RAW_DIR / MANIFEST_DATASET / f"ingest_date={run.ingest_date}" / f"part-{run.run_id}.parquet"
    return atomic_write_parquet(row, path)


@contextmanager
def raw_run(
    source: str, dataset: str, request_params: dict[str, Any] | None = None, date_col: str | None = None
) -> Iterator[RawRun]:
    """One collector run: yields a RawRun to `write` frames into, and logs it to the manifest however it ends.

    A run that raises is logged as `failed` (and the error re-raised); one that wrote nothing is logged as `empty`.
    """
    run = RawRun(source, dataset, request_params or {}, date_col)
    try:
        yield run
    except BaseException as exc:
        _write_manifest(run, "failed", f"{type(exc).__name__}: {exc}")
        raise
    _write_manifest(run, "success" if run.rows else "empty")


def _duckdb():
    """An in-memory DuckDB pinned to UTC, so timestamps come back as UTC rather than the machine's local zone."""
    import duckdb

    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    return con


def read_raw(source: str, dataset: str, columns: list[str] | None = None) -> pd.DataFrame:
    """Every row ever written to a raw dataset (empty frame if none). For collectors and tests, not analysis."""
    if not any(dataset_dir(source, dataset).glob("*/*.parquet")):
        return pd.DataFrame(columns=columns or [])
    cols = ", ".join(f'"{c}"' for c in columns) if columns else "*"
    query = f"SELECT {cols} FROM read_parquet('{dataset_glob(source, dataset)}', hive_partitioning=true, union_by_name=true)"
    return _duckdb().sql(query).df()


def raw_max(source: str, dataset: str, key_col: str, ts_col: str) -> pd.DataFrame:
    """Latest `ts_col` per `key_col` across all raw files: what incremental collectors resume from."""
    if not any(dataset_dir(source, dataset).glob("*/*.parquet")):
        return pd.DataFrame(columns=[key_col, ts_col])
    query = (
        f'SELECT "{key_col}", max("{ts_col}") AS "{ts_col}" '
        f"FROM read_parquet('{dataset_glob(source, dataset)}', hive_partitioning=true, union_by_name=true) "
        f'GROUP BY 1 ORDER BY 1'
    )
    return _duckdb().sql(query).df()
