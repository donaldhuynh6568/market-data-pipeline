from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import DATA_DIR

OUT_PATH = DATA_DIR / "market_data.xlsx"


def _sanitize_for_excel(df: pd.DataFrame) -> pd.DataFrame:
    cleaned = df.copy()
    for column in cleaned.columns:
        series = cleaned[column]
        if pd.api.types.is_datetime64_any_dtype(series):
            if getattr(series.dt, "tz", None) is not None:
                cleaned[column] = series.dt.tz_localize(None)
    return cleaned


def get_data_paths(data_dir: Path | None = None) -> tuple[Path, Path]:
    base_dir = data_dir or DATA_DIR
    return base_dir / "price_data.parquet", base_dir / "options_data.parquet"


def build_workbook(data_dir: Path | None = None, output_path: Path | None = None) -> Path:
    price_path, options_path = get_data_paths(data_dir)
    if not price_path.exists() or not options_path.exists():
        raise FileNotFoundError(
            "Missing local data. Run the collectors first: "
            "uv run python -m market_data_pipeline.collectors.price --days 30 "
            "and uv run python -m market_data_pipeline.collectors.options --moneyness 0.20 --max-expiry-days 60."
        )

    price_df = _sanitize_for_excel(pd.read_parquet(price_path))
    options_df = _sanitize_for_excel(pd.read_parquet(options_path))
    flagged_df = options_df[options_df["unusual_activity"]].sort_values(
        "vol_oi_ratio", ascending=False
    )

    target_path = output_path or OUT_PATH
    with pd.ExcelWriter(target_path, engine="openpyxl") as writer:
        price_df.to_excel(writer, sheet_name="Price Data", index=False)
        options_df.to_excel(writer, sheet_name="Options Data", index=False)
        flagged_df.to_excel(writer, sheet_name="Unusual Activity", index=False)

    return target_path


def main() -> None:
    try:
        output_path = build_workbook()
    except FileNotFoundError as exc:
        raise SystemExit(str(exc)) from exc

    price_path, options_path = get_data_paths()
    price_df = _sanitize_for_excel(pd.read_parquet(price_path))
    options_df = _sanitize_for_excel(pd.read_parquet(options_path))
    flagged_df = options_df[options_df["unusual_activity"]].sort_values(
        "vol_oi_ratio", ascending=False
    )

    print(f"Wrote {output_path}")
    print(f"  Price Data: {len(price_df)} rows")
    print(f"  Options Data: {len(options_df)} rows")
    print(f"  Unusual Activity: {len(flagged_df)} rows")


if __name__ == "__main__":
    main()
