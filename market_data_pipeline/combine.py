from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .config import DATA_DIR
from .storage import latest_snapshot

OUT_PATH = DATA_DIR / "market_data.xlsx"

REQUIRED_FILES = {
    "price": "price_data.parquet",
    "options": "options_data.parquet",
}

OPTIONAL_SHEETS = {
    "cot": ("cot_data.parquet", "COT Data"),
    "gex": ("gex_data.parquet", "GEX Data"),
    "fred": ("fred_data.parquet", "FRED Data"),
    "sec": ("sec_filings.parquet", "SEC Filings"),
    "congress": ("congress_trades.parquet", "Congress Trades"),
    "insider": ("insider_trades.parquet", "Insider Trades"),
    "institutional": ("institutional_holdings.parquet", "13F Holdings"),
    "price_1h": ("price_data_1h.parquet", "Price 1H"),
    "price_4h": ("price_data_4h.parquet", "Price 4H"),
    "swing_1h": ("swing_structure_1h.parquet", "Swing Structure 1H"),
    "swing_4h": ("swing_structure_4h.parquet", "Swing Structure 4H"),
    "futures_volume": ("futures_volume_5m.parquet", "Futures Volume 5m"),
    "volume_floor": ("volume_floor_5m.parquet", "Volume Floor 5m"),
    "fundamentals": ("fundamentals_data.parquet", "Fundamentals"),
    "fundamentals_ratios": ("fundamentals_ratios.parquet", "Fundamentals Ratios"),
    "momentum": ("momentum.parquet", "Momentum"),
    "perps": ("perps_data.parquet", "Perps Data"),
    "deribit_options": ("deribit_options.parquet", "Deribit Options"),
    "gex_crypto": ("gex_crypto.parquet", "GEX Crypto"),
    "news": ("news_data.parquet", "News Data"),
    "location_levels": ("location_levels.parquet", "Location Levels"),
    "location_zones": ("location_zones.parquet", "Location Zones"),
    "backtest": ("backtest_summary.parquet", "Backtest Summary"),
}


# Excel allows 1,048,576 rows per sheet, one of them the header.
EXCEL_MAX_ROWS = 1_048_575

# By default the workbook holds the trailing 90 days of each high-frequency sheet; the parquets keep everything.
DEFAULT_DAYS = 90

# Sheets trimmed to the trailing window, and the column that dates them. The window ends at each sheet's own newest
# row, not at today's date, so a file that has gone stale still shows its last 90 days. Everything not listed
# (swing structure, momentum, COT, FRED, 13F, fundamentals, ...) is small or not a time series and is written whole.
TIME_COLUMNS = {
    "price": "timestamp",
    "price_1h": "timestamp",
    "price_4h": "timestamp",
    "futures_volume": "timestamp",
    "volume_floor": "timestamp",
    "perps": "timestamp",
    "news": "created_at",
}

# Files that keep a history of daily snapshots. The workbook shows the latest one: ~22k option rows are added per
# day, which would pass the sheet limit within about seven weeks, so a trailing window cannot rescue these.
SNAPSHOT_SHEETS = {"deribit_options"}


def _for_sheet(key: str, df: pd.DataFrame, days: int = DEFAULT_DAYS) -> pd.DataFrame:
    """The rows of one parquet that belong in its sheet: latest snapshot, or the trailing `days` window."""
    if key in SNAPSHOT_SHEETS:
        return latest_snapshot(df)
    column = TIME_COLUMNS.get(key)
    if not days or column is None or column not in df.columns or df.empty:
        return df
    times = pd.to_datetime(df[column], utc=True)
    return df[times >= times.max() - pd.Timedelta(days=days)]


def _fit_sheet(df: pd.DataFrame, name: str, note: list[str]) -> pd.DataFrame:
    """Never let a sheet exceed Excel's row limit: keep the newest rows (files are stored oldest first)."""
    if len(df) <= EXCEL_MAX_ROWS:
        return df
    note.append(f"{name}: capped at {EXCEL_MAX_ROWS:,} rows (had {len(df):,}); oldest rows dropped")
    return df.tail(EXCEL_MAX_ROWS)


def _sanitize_for_excel(df: pd.DataFrame) -> pd.DataFrame:
    cleaned = df.copy()
    for column in cleaned.columns:
        series = cleaned[column]
        if pd.api.types.is_datetime64_any_dtype(series):
            if getattr(series.dt, "tz", None) is not None:
                cleaned[column] = series.dt.tz_localize(None)
    return cleaned


def get_data_paths(data_dir: Path | None = None) -> dict[str, Path]:
    base_dir = data_dir or DATA_DIR
    paths = {key: base_dir / filename for key, filename in REQUIRED_FILES.items()}
    paths.update(
        {key: base_dir / filename for key, (filename, _) in OPTIONAL_SHEETS.items()}
    )
    return paths


def build_workbook(
    data_dir: Path | None = None,
    output_path: Path | None = None,
    days: int = DEFAULT_DAYS,
    summary: list[str] | None = None,
) -> Path:
    """Write the review workbook. `days` trims the high-frequency sheets (0 keeps everything); each sheet's line
    (rows written, rows left out) is appended to `summary` when one is passed."""
    paths = get_data_paths(data_dir)
    if not paths["price"].exists() or not paths["options"].exists():
        raise FileNotFoundError(
            "Missing local data. Run the collectors first: "
            "uv run python -m market_data_pipeline.collectors.price --days 30 "
            "and uv run python -m market_data_pipeline.collectors.options --moneyness 0.20 --max-expiry-days 60."
        )
    lines = summary if summary is not None else []

    def prepare(key: str, sheet: str, raw: pd.DataFrame) -> pd.DataFrame:
        df = _fit_sheet(_for_sheet(key, raw, days), sheet, lines)
        lines.append(f"  {sheet}: {len(df)} rows" + (f" ({len(raw) - len(df)} older/earlier left out)" if len(raw) > len(df) else ""))
        return _sanitize_for_excel(df)

    price_df = prepare("price", "Price Data", pd.read_parquet(paths["price"]))
    options_raw = pd.read_parquet(paths["options"])
    options_df = _sanitize_for_excel(latest_snapshot(options_raw))
    flagged_df = options_df[options_df["unusual_activity"]].sort_values("vol_oi_ratio", ascending=False)
    lines.append(f"  Options Data: {len(options_df)} rows (latest snapshot of {len(options_raw)} stored)")
    lines.append(f"  Unusual Activity: {len(flagged_df)} rows")

    target_path = output_path or OUT_PATH
    with pd.ExcelWriter(target_path, engine="openpyxl") as writer:
        price_df.to_excel(writer, sheet_name="Price Data", index=False)
        options_df.to_excel(writer, sheet_name="Options Data", index=False)
        flagged_df.to_excel(writer, sheet_name="Unusual Activity", index=False)
        for key, (_, sheet_name) in OPTIONAL_SHEETS.items():
            path = paths[key]
            if path.exists():
                prepare(key, sheet_name, pd.read_parquet(path)).to_excel(writer, sheet_name=sheet_name, index=False)

    return target_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Combine the parquet files into one review workbook (Excel).")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Trailing days kept on the high-frequency sheets (default {DEFAULT_DAYS}; 0 keeps everything, though "
        "Excel's row limit still applies). Options sheets always show the latest snapshot only.",
    )
    args = parser.parse_args()

    summary: list[str] = []
    try:
        output_path = build_workbook(days=args.days, summary=summary)
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Wrote {output_path}")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
