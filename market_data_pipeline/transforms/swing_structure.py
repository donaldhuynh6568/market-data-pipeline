from __future__ import annotations

import argparse

import pandas as pd

from market_data_pipeline.config import DATA_DIR

INPUT_FILENAMES = {"1h": "price_data_1h.parquet", "4h": "price_data_4h.parquet"}
OUTPUT_FILENAMES = {"1h": "swing_structure_1h.parquet", "4h": "swing_structure_4h.parquet"}
DEFAULT_WINDOW = 3

SUMMARY_COLUMNS = [
    "symbol",
    "structure",
    "last_swing_high",
    "last_swing_high_time",
    "prior_swing_high",
    "prior_swing_high_time",
    "last_swing_low",
    "last_swing_low_time",
    "prior_swing_low",
    "prior_swing_low_time",
]


def find_swing_points(df: pd.DataFrame, window: int = DEFAULT_WINDOW) -> pd.DataFrame:
    """A bar is a swing high/low if its high/low is strictly the extreme within `window`
    bars on both sides. The most recent `window` bars can never be confirmed as swing
    points yet — you can't tell a swing formed until price has moved away from it."""
    if df.empty:
        return pd.DataFrame(columns=["symbol", "timestamp", "price", "kind"])

    rows = []
    for symbol, group in df.sort_values("timestamp").groupby("symbol"):
        group = group.reset_index(drop=True)
        highs = group["high"].to_numpy()
        lows = group["low"].to_numpy()
        timestamps = group["timestamp"].to_numpy()
        n = len(group)

        for i in range(window, n - window):
            left, right = slice(i - window, i), slice(i + 1, i + window + 1)
            if highs[i] > highs[left].max() and highs[i] > highs[right].max():
                rows.append(
                    {"symbol": symbol, "timestamp": timestamps[i], "price": highs[i], "kind": "swing_high"}
                )
            if lows[i] < lows[left].min() and lows[i] < lows[right].min():
                rows.append(
                    {"symbol": symbol, "timestamp": timestamps[i], "price": lows[i], "kind": "swing_low"}
                )

    return pd.DataFrame(rows, columns=["symbol", "timestamp", "price", "kind"])


def classify_symbol_structure(points: pd.DataFrame) -> dict:
    """Classify one symbol's structure from its swing points (already filtered to one symbol).

    UNKNOWN means fewer than 2 swing highs or lows have been found yet — a "not enough
    history" state, distinct from an actual SIDEWAYS read.
    """
    points = points.sort_values("timestamp")
    highs = points[points["kind"] == "swing_high"]
    lows = points[points["kind"] == "swing_low"]

    if len(highs) < 2 or len(lows) < 2:
        return dict.fromkeys(SUMMARY_COLUMNS[1:]) | {"structure": "UNKNOWN"}

    last_high, prior_high = highs.iloc[-1], highs.iloc[-2]
    last_low, prior_low = lows.iloc[-1], lows.iloc[-2]

    higher_high = last_high["price"] > prior_high["price"]
    higher_low = last_low["price"] > prior_low["price"]

    if higher_high and higher_low:
        structure = "UPTREND"
    elif not higher_high and not higher_low:
        structure = "DOWNTREND"
    else:
        structure = "SIDEWAYS"

    return {
        "structure": structure,
        "last_swing_high": last_high["price"],
        "last_swing_high_time": last_high["timestamp"],
        "prior_swing_high": prior_high["price"],
        "prior_swing_high_time": prior_high["timestamp"],
        "last_swing_low": last_low["price"],
        "last_swing_low_time": last_low["timestamp"],
        "prior_swing_low": prior_low["price"],
        "prior_swing_low_time": prior_low["timestamp"],
    }


def compute_swing_structure(bars_df: pd.DataFrame, window: int = DEFAULT_WINDOW) -> pd.DataFrame:
    swing_points = find_swing_points(bars_df, window=window)

    rows = []
    for symbol in bars_df["symbol"].unique():
        summary = classify_symbol_structure(swing_points[swing_points["symbol"] == symbol])
        summary["symbol"] = symbol
        rows.append(summary)

    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect swing-high/low structure from intraday bars.")
    parser.add_argument("--timeframe", choices=sorted(INPUT_FILENAMES), default="1h")
    parser.add_argument(
        "--window", type=int, default=DEFAULT_WINDOW, help="Bars on each side required to confirm a swing point."
    )
    args = parser.parse_args()

    input_path = DATA_DIR / INPUT_FILENAMES[args.timeframe]
    if not input_path.exists():
        cli_timeframe = "1Hour" if args.timeframe == "1h" else "4Hour"
        raise SystemExit(
            f"Missing {input_path}. Run the price collector first: "
            f"uv run python -m market_data_pipeline.collectors.price --timeframe {cli_timeframe}"
        )

    bars_df = pd.read_parquet(input_path)
    result = compute_swing_structure(bars_df, window=args.window)

    out_path = DATA_DIR / OUTPUT_FILENAMES[args.timeframe]
    result.to_parquet(out_path, index=False)
    print(f"Saved {len(result)} symbol structure rows to {out_path}")
    for _, row in result.iterrows():
        print(f"  [{row['symbol']}] {row['structure']}")


if __name__ == "__main__":
    main()
