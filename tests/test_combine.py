from pathlib import Path

import pandas as pd
import pytest

from market_data_pipeline.combine import _sanitize_for_excel, build_workbook


def test_sanitize_for_excel_removes_timezone_from_datetime_columns():
    df = pd.DataFrame({
        "ts": [
            pd.Timestamp("2024-01-01 12:00:00", tz="UTC"),
            pd.Timestamp("2024-01-02 12:00:00", tz="UTC"),
        ]
    })

    cleaned = _sanitize_for_excel(df)

    assert cleaned["ts"].dt.tz is None
    assert str(cleaned["ts"].iloc[0]) == "2024-01-01 12:00:00"


def test_build_workbook_raises_clear_error_when_data_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="Run the collectors first"):
        build_workbook(data_dir=tmp_path)


def test_workbook_options_sheet_shows_only_the_latest_snapshot(tmp_path: Path):
    pd.DataFrame({"symbol": ["A"], "timestamp": [pd.Timestamp("2026-10-01", tz="UTC")], "close": [1.0]}).to_parquet(
        tmp_path / "price_data.parquet"
    )
    options = pd.DataFrame({
        "underlying_symbol": ["A", "A"],
        "snapshot_date": ["2026-10-01", "2026-10-02"],
        "open_interest": [10, 20],
        "unusual_activity": [False, True],
        "vol_oi_ratio": [0.1, 2.0],
    })
    options.to_parquet(tmp_path / "options_data.parquet")

    target = build_workbook(data_dir=tmp_path, output_path=tmp_path / "out.xlsx")

    import openpyxl

    sheet = openpyxl.load_workbook(target)["Options Data"]
    assert sheet.max_row == 2                          # header + the 2026-10-02 snapshot only
    assert [c.value for c in sheet[2]][2] == 20


def _minimal_data(tmp_path: Path) -> None:
    pd.DataFrame({"symbol": ["A"], "timestamp": [pd.Timestamp("2026-10-01", tz="UTC")], "close": [1.0]}).to_parquet(
        tmp_path / "price_data.parquet"
    )
    pd.DataFrame({
        "underlying_symbol": ["A"], "snapshot_date": ["2026-10-01"], "unusual_activity": [False], "vol_oi_ratio": [0.1],
    }).to_parquet(tmp_path / "options_data.parquet")


def test_time_series_sheets_keep_the_trailing_window_from_their_own_newest_row():
    from market_data_pipeline.combine import _for_sheet

    times = pd.date_range("2026-01-01", periods=200, freq="D", tz="UTC")
    df = pd.DataFrame({"timestamp": times, "v": range(200)})

    kept = _for_sheet("volume_floor", df, days=90)
    assert kept["timestamp"].min() == times[-1] - pd.Timedelta(days=90)
    assert len(kept) == 91
    assert len(_for_sheet("volume_floor", df, days=0)) == 200                 # 0 disables trimming
    assert len(_for_sheet("momentum", df, days=90)) == 200                    # not a time-series sheet: untouched
    assert len(_for_sheet("volume_floor", df.rename(columns={"timestamp": "t"}), days=90)) == 200   # no date column


def test_fit_sheet_caps_at_the_excel_limit_keeping_newest_rows_and_says_so(monkeypatch):
    from market_data_pipeline import combine

    monkeypatch.setattr(combine, "EXCEL_MAX_ROWS", 3)
    notes: list[str] = []

    fitted = combine._fit_sheet(pd.DataFrame({"v": [1, 2, 3, 4, 5]}), "Big", notes)

    assert fitted["v"].tolist() == [3, 4, 5]
    assert "Big: capped at 3 rows" in notes[0]
    assert combine._fit_sheet(pd.DataFrame({"v": [1]}), "Small", notes)["v"].tolist() == [1] and len(notes) == 1


def test_workbook_trims_big_sheets_and_reports_what_it_left_out(tmp_path: Path):
    import openpyxl

    _minimal_data(tmp_path)
    times = pd.date_range("2026-01-01", periods=200, freq="D", tz="UTC")
    pd.DataFrame({"symbol": "A", "timestamp": times, "x": 1.0}).to_parquet(tmp_path / "volume_floor_5m.parquet")
    summary: list[str] = []

    target = build_workbook(data_dir=tmp_path, output_path=tmp_path / "out.xlsx", days=30, summary=summary)

    sheet = openpyxl.load_workbook(target)["Volume Floor 5m"]
    assert sheet.max_row == 1 + 31                                           # header + 31 days (both ends inclusive)
    assert any("Volume Floor 5m: 31 rows (169 older/earlier left out)" in line for line in summary)
