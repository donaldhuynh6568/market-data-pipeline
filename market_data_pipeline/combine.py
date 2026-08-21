from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import DATA_DIR

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
}


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


def build_workbook(data_dir: Path | None = None, output_path: Path | None = None) -> Path:
    paths = get_data_paths(data_dir)
    if not paths["price"].exists() or not paths["options"].exists():
        raise FileNotFoundError(
            "Missing local data. Run the collectors first: "
            "uv run python -m market_data_pipeline.collectors.price --days 30 "
            "and uv run python -m market_data_pipeline.collectors.options --moneyness 0.20 --max-expiry-days 60."
        )

    price_df = _sanitize_for_excel(pd.read_parquet(paths["price"]))
    options_df = _sanitize_for_excel(pd.read_parquet(paths["options"]))
    flagged_df = options_df[options_df["unusual_activity"]].sort_values(
        "vol_oi_ratio", ascending=False
    )

    target_path = output_path or OUT_PATH
    with pd.ExcelWriter(target_path, engine="openpyxl") as writer:
        price_df.to_excel(writer, sheet_name="Price Data", index=False)
        options_df.to_excel(writer, sheet_name="Options Data", index=False)
        flagged_df.to_excel(writer, sheet_name="Unusual Activity", index=False)
        for key, (_, sheet_name) in OPTIONAL_SHEETS.items():
            path = paths[key]
            if path.exists():
                df = _sanitize_for_excel(pd.read_parquet(path))
                df.to_excel(writer, sheet_name=sheet_name, index=False)

    return target_path


def main() -> None:
    try:
        output_path = build_workbook()
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc

    paths = get_data_paths()
    price_df = _sanitize_for_excel(pd.read_parquet(paths["price"]))
    options_df = _sanitize_for_excel(pd.read_parquet(paths["options"]))
    flagged_df = options_df[options_df["unusual_activity"]].sort_values(
        "vol_oi_ratio", ascending=False
    )

    print(f"Wrote {output_path}")
    print(f"  Price Data: {len(price_df)} rows")
    print(f"  Options Data: {len(options_df)} rows")
    print(f"  Unusual Activity: {len(flagged_df)} rows")
    for key, (_, sheet_name) in OPTIONAL_SHEETS.items():
        path = paths[key]
        if path.exists():
            df = _sanitize_for_excel(pd.read_parquet(path))
            print(f"  {sheet_name}: {len(df)} rows")


if __name__ == "__main__":
    main()
