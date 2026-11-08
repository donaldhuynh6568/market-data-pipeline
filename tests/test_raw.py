import json

import pandas as pd
import pytest

from market_data_pipeline import raw


@pytest.fixture(autouse=True)
def raw_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(raw, "RAW_DIR", tmp_path)
    return tmp_path


def _bars(*rows):
    return pd.DataFrame(rows, columns=["symbol", "timestamp", "close"]).assign(
        timestamp=lambda d: pd.to_datetime(d["timestamp"], utc=True)
    )


def _manifest():
    files = sorted((raw.RAW_DIR / raw.MANIFEST_DATASET).glob("*/*.parquet"))
    return pd.concat([pd.read_parquet(p) for p in files], ignore_index=True)


def test_two_runs_write_two_files_and_lose_nothing():
    with raw.raw_run("alpaca", "bars_1d", {"days": 5}, date_col="timestamp") as run:
        run.write(_bars(("A", "2026-10-01", 1.0), ("A", "2026-10-02", 2.0)))
    with raw.raw_run("alpaca", "bars_1d", {"days": 5}, date_col="timestamp") as run:
        run.write(_bars(("A", "2026-10-02", 2.5)))      # a re-fetched bar: kept alongside, never overwritten

    files = list(raw.dataset_dir("alpaca", "bars_1d").glob("ingest_date=*/part-*.parquet"))
    stored = raw.read_raw("alpaca", "bars_1d")

    assert len(files) == 2
    assert len(stored) == 3 and stored["run_id"].nunique() == 2
    assert sorted(stored.loc[stored["timestamp"] == pd.Timestamp("2026-10-02", tz="UTC"), "close"]) == [2.0, 2.5]


def test_metadata_columns_are_added():
    with raw.raw_run("fred", "series", {"series": ["VIXCLS"]}) as run:
        run.write(pd.DataFrame({"series_id": ["VIXCLS"], "date": ["2026-10-01"], "value": [15.0]}))

    stored = raw.read_raw("fred", "series")

    assert set(raw.RAW_METADATA_COLUMNS) <= set(stored.columns)
    assert stored["source"].iloc[0] == "fred"
    assert json.loads(stored["request_params"].iloc[0]) == {"series": ["VIXCLS"]}
    assert str(stored["ingested_at"].dtype).startswith("datetime64")
    assert str(stored["ingest_date"].iloc[0])[:10] == run.ingest_date   # hive partition column comes back too


def test_reserved_column_names_are_refused():
    with pytest.raises(ValueError, match="reserved"):
        with raw.raw_run("alpaca", "news") as run:
            run.write(pd.DataFrame({"id": [1], "source": ["benzinga"]}))


def test_manifest_logs_success_empty_and_failed_runs():
    with raw.raw_run("alpaca", "bars_1d", date_col="timestamp") as run:
        run.write(_bars(("A", "2026-10-01", 1.0)))
        run.write(_bars(("B", "2026-10-03", 1.0)))     # second frame in the same run gets its own file
    with raw.raw_run("alpaca", "bars_1d"):
        pass
    with pytest.raises(RuntimeError):
        with raw.raw_run("alpaca", "bars_1d"):
            raise RuntimeError("api down")

    manifest = _manifest().sort_values("started_at")

    assert manifest["status"].tolist() == ["success", "empty", "failed"]
    first = manifest.iloc[0]
    assert (first["row_count"], first["file_count"]) == (2, 2)
    assert first["min_date"].startswith("2026-10-01") and first["max_date"].startswith("2026-10-03")
    assert "api down" in manifest.iloc[2]["error"]


def test_raw_max_is_the_latest_timestamp_per_key_across_runs():
    assert raw.raw_max("alpaca", "bars_1d", "symbol", "timestamp").empty
    with raw.raw_run("alpaca", "bars_1d") as run:
        run.write(_bars(("A", "2026-10-01", 1.0), ("B", "2026-10-05", 1.0)))
    with raw.raw_run("alpaca", "bars_1d") as run:
        run.write(_bars(("A", "2026-10-04", 1.0)))

    latest = raw.raw_max("alpaca", "bars_1d", "symbol", "timestamp").set_index("symbol")["timestamp"]

    assert latest["A"] == pd.Timestamp("2026-10-04", tz="UTC")
    assert latest["B"] == pd.Timestamp("2026-10-05", tz="UTC")


def test_atomic_write_leaves_no_temp_file_on_failure(tmp_path):
    class Boom(pd.DataFrame):
        def to_parquet(self, *args, **kwargs):
            raise OSError("disk full")

    with pytest.raises(OSError):
        raw.atomic_write_parquet(Boom({"a": [1]}), tmp_path / "x" / "p.parquet")

    assert list((tmp_path / "x").iterdir()) == []
