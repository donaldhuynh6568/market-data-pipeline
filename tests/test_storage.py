from datetime import date

import pandas as pd
import pytest

from market_data_pipeline import storage


def _bars(rows):
    return pd.DataFrame(rows, columns=["symbol", "timestamp", "close"]).assign(
        timestamp=lambda d: pd.to_datetime(d["timestamp"], utc=True)
    )


def test_merge_frames_new_row_wins_and_result_is_sorted_and_unique():
    existing = _bars([("A", "2026-10-01", 1.0), ("A", "2026-10-02", 2.0), ("B", "2026-10-01", 9.0)])
    new = _bars([("A", "2026-10-02", 2.5), ("A", "2026-10-03", 3.0)])

    merged = storage.merge_frames(existing, new, ["symbol", "timestamp"])

    assert merged[["symbol", "close"]].values.tolist() == [["A", 1.0], ["A", 2.5], ["A", 3.0], ["B", 9.0]]
    assert not merged.duplicated(["symbol", "timestamp"]).any()
    assert str(merged["timestamp"].dt.tz) == "UTC"


def test_merge_frames_handles_missing_or_empty_sides_and_new_columns():
    new = _bars([("A", "2026-10-01", 1.0)])
    assert storage.merge_frames(None, new, ["symbol", "timestamp"]).equals(new)
    assert len(storage.merge_frames(new, new.iloc[0:0], ["symbol", "timestamp"])) == 1

    wider = new.assign(vwap=5.0)
    merged = storage.merge_frames(new, wider.assign(timestamp=wider["timestamp"] + pd.Timedelta(days=1)), ["symbol", "timestamp"])
    assert list(merged.columns) == ["symbol", "timestamp", "close", "vwap"]
    assert merged["vwap"].isna().sum() == 1     # the old row has no value for the column it never had


def test_merge_parquet_reads_the_file_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    storage.save_parquet(_bars([("A", "2026-10-01", 1.0)]), "p.parquet")

    merged = storage.merge_parquet(_bars([("A", "2026-10-02", 2.0)]), "p.parquet", ["symbol", "timestamp"])
    assert len(merged) == 2
    first_run = storage.merge_parquet(_bars([("A", "2026-10-02", 2.0)]), "absent.parquet", ["symbol", "timestamp"])
    assert len(first_run) == 1


def test_save_parquet_is_atomic_and_leaves_no_temp_file(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    original = _bars([("A", "2026-10-01", 1.0)])
    storage.save_parquet(original, "p.parquet")

    class Boom(pd.DataFrame):
        def to_parquet(self, *args, **kwargs):
            raise OSError("disk full")

    with pytest.raises(OSError):
        storage.save_parquet(Boom(original), "p.parquet")

    assert pd.read_parquet(tmp_path / "p.parquet").equals(original)     # the old file survived
    assert [p.name for p in tmp_path.iterdir()] == ["p.parquet"]         # and no stray .tmp


def test_latest_snapshot_per_underlying_and_overall():
    df = pd.DataFrame({
        "underlying_symbol": ["A", "A", "B", "B"],
        "snapshot_date": ["2026-10-01", "2026-10-02", "2026-10-01", "2026-10-01"],
        "open_interest": [1, 2, 3, 4],
    })
    assert storage.latest_snapshot(df)["open_interest"].tolist() == [2, 3, 4]   # B's newest is still the 1st
    assert storage.latest_snapshot(df, by=None)["open_interest"].tolist() == [2]
    assert storage.latest_snapshot(df.drop(columns="snapshot_date")).equals(df.drop(columns="snapshot_date"))


def test_prune_old_snapshots_keeps_only_the_window():
    df = pd.DataFrame({"snapshot_date": ["2026-07-01", "2026-09-10", "2026-10-05"], "v": [1, 2, 3]})
    kept = storage.prune_old_snapshots(df, 30, today=date(2026, 10, 6))
    assert kept["v"].tolist() == [2, 3]
