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
