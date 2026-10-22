from __future__ import annotations

import argparse

import pandas as pd

from market_data_pipeline.config import DATA_DIR

INPUT_FILENAME = "price_data_5m.parquet"
OUTPUT_FILENAME = "volume_floor_5m.parquet"

# Starting points, not fixed truths — tune per how noisy/quiet a symbol set turns out
# to be, the same spirit as the tunable unusual_activity threshold in options.py.
BASELINE_WINDOW_BARS = 20
DEFAULT_MULTIPLIER = 2.0


def filter_symbols(df: pd.DataFrame, symbols: list[str] | None) -> pd.DataFrame:
    """Restrict to `symbols`, or keep everything in the file when none are given.

    The default is the whole 5-minute universe rather than one ticker category, so the flagged bars line up
    with the names the rest of the pipeline reports on.
    """
    if not symbols:
        return df
    return df[df["symbol"].isin(symbols)]


def add_volume_baseline(df: pd.DataFrame, window: int = BASELINE_WINDOW_BARS) -> pd.DataFrame:
    """Add each bar's trailing per-symbol volume baseline (prior `window` bars, excluding
    the current one — otherwise a spike would partially absorb itself into its own baseline).
    NaN until a symbol has `window` bars of history; that's expected, not a bug.
    """
    df = df.sort_values(["symbol", "timestamp"]).copy()
    df["baseline_volume"] = df.groupby("symbol")["volume"].transform(
        lambda volume: volume.shift(1).rolling(window, min_periods=window).mean()
    )
    return df


def add_volume_ratio_flag(df: pd.DataFrame, multiplier: float = DEFAULT_MULTIPLIER) -> pd.DataFrame:
    df = df.copy()
    df["volume_ratio"] = df["volume"] / df["baseline_volume"]
    df["volume_floor_met"] = df["volume_ratio"] >= multiplier
    return df


def compute_volume_floor(
    df: pd.DataFrame, window: int = BASELINE_WINDOW_BARS, multiplier: float = DEFAULT_MULTIPLIER
) -> pd.DataFrame:
    return add_volume_ratio_flag(add_volume_baseline(df, window=window), multiplier=multiplier)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Flag 5-minute equity bars with volume well above that symbol's own trailing baseline."
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Symbols to include. Default: every symbol in price_data_5m.parquet, which covers the whole "
        "universe including ETFs and crypto. Cash-like names (SHV) have thin, noisy volume.",
    )
    parser.add_argument(
        "--window", type=int, default=BASELINE_WINDOW_BARS, help="Trailing bars used for the baseline."
    )
    parser.add_argument(
        "--multiplier", type=float, default=DEFAULT_MULTIPLIER, help="Volume-vs-baseline ratio to flag."
    )
    args = parser.parse_args()

    input_path = DATA_DIR / INPUT_FILENAME
    if not input_path.exists():
        raise SystemExit(
            f"Missing {input_path}. Run the price collector first: "
            "uv run python -m market_data_pipeline.collectors.price --timeframe 5Min"
        )

    df = filter_symbols(pd.read_parquet(input_path), args.symbols)
    if df.empty:
        print("No matching rows in price_data_5m.parquet for the requested symbols.")
        return

    result = compute_volume_floor(df, window=args.window, multiplier=args.multiplier)

    out_path = DATA_DIR / OUTPUT_FILENAME
    result.to_parquet(out_path, index=False)
    print(f"Saved {len(result)} rows to {out_path}")

    flagged = result[result["volume_floor_met"]]
    print(f"{len(flagged)} bars flagged (volume >= {args.multiplier}x trailing baseline)")
    for symbol, count in flagged["symbol"].value_counts().items():
        print(f"  [{symbol}] {count} flagged bars")


if __name__ == "__main__":
    main()
