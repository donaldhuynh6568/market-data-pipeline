from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from market_data_pipeline.config import DATA_DIR
from market_data_pipeline.tickers import load_cash_like_tickers

RETURN_WINDOWS = (5, 20, 60)
SCORE_LOOKBACK = 10
DELTA_WINDOWS = (5, 10, 20)
# Floor on annualized vol (in %) in the score denominator so near-constant prices can't blow the score up.
MIN_ANN_VOL_PCT = 5.0
SMOOTHING_SPAN = 5
TREND_MA_WINDOW = 200
ANNUALIZATION = {"equity": 252, "crypto": 365}


def _bars_ago_return(series: pd.Series, bars: int, offset: int = 0) -> float:
    """Return over `bars` bars ending `offset` bars before the latest bar (NaN if not enough history)."""
    end_idx = len(series) - 1 - offset
    start_idx = end_idx - bars
    if start_idx < 0:
        return np.nan
    start_value = series.iloc[start_idx]
    if not start_value:
        return np.nan
    return series.iloc[end_idx] / start_value - 1


def compute_score_series(close: pd.Series, periods_per_year: int) -> pd.DataFrame:
    """Rotation-tracker score for every bar: 10-bar momentum % over 10-bar annualized vol %.

    Defined in the Methodology sheet of reference/Rotation_Positions_Tracker.xlsx. Uses simple
    (not log) daily returns and population std (ddof=0, which reproduces the scores already being
    read from the tracker); vol is floored at MIN_ANN_VOL_PCT.
    """
    momentum_pct = close.pct_change(SCORE_LOOKBACK) * 100
    vol_pct = close.pct_change().rolling(SCORE_LOOKBACK).std(ddof=0) * np.sqrt(periods_per_year) * 100
    score = momentum_pct / vol_pct.clip(lower=MIN_ANN_VOL_PCT)
    return pd.DataFrame({"momentum_pct": momentum_pct, "vol_pct": vol_pct, "score": score})


def classify_delta_signal(deltas: list[float]) -> str:
    if any(np.isnan(d) for d in deltas):
        return "UNKNOWN"
    if all(d > 0 for d in deltas):
        return "ACCELERATING"
    if all(d < 0 for d in deltas):
        return "DECELERATING"
    return "MIXED"


def compute_symbol_momentum(group: pd.DataFrame) -> dict:
    """Momentum metrics for one symbol's daily bars, sorted oldest to newest.

    Windows are counted in bars (trading days for equities, calendar days for
    crypto, which trades every day). Follows the "better momentum metrics" in
    reference/rotationstrategy.md: EMA of median price to smooth endpoints,
    acceleration, and volatility adjustment (Sharpe-style).
    """
    close = group["close"].astype(float).reset_index(drop=True)
    median_price = ((group["high"].astype(float) + group["low"].astype(float)) / 2).reset_index(drop=True)
    smoothed = median_price.ewm(span=SMOOTHING_SPAN, adjust=False).mean()
    asset_class = group["asset_class"].iloc[-1] if "asset_class" in group.columns else "equity"

    row: dict = {
        "symbol": group["symbol"].iloc[-1],
        "asset_class": asset_class,
        "as_of": group["timestamp"].iloc[-1],
        "close": close.iloc[-1],
        "bars_available": len(close),
    }
    for window in RETURN_WINDOWS:
        row[f"ret_{window}d"] = _bars_ago_return(close, window)
        row[f"smoothed_ret_{window}d"] = _bars_ago_return(smoothed, window)

    # Acceleration: latest 20-bar return minus the 20-bar return before it.
    prior_20d = _bars_ago_return(smoothed, 20, offset=20)
    row["acceleration_20d"] = row["smoothed_ret_20d"] - prior_20d

    daily_returns = np.log(close).diff().dropna()
    periods_per_year = ANNUALIZATION.get(asset_class, 252)
    vol_window = daily_returns.tail(60)
    daily_vol = vol_window.std() if len(vol_window) >= 20 else np.nan
    row["ann_vol"] = daily_vol * np.sqrt(periods_per_year)

    # Vol-adjusted score: 60-bar smoothed return over volatility scaled to the same horizon.
    horizon_vol = daily_vol * np.sqrt(60)
    row["vol_adj_score"] = row["smoothed_ret_60d"] / horizon_vol if horizon_vol else np.nan

    scores = compute_score_series(close, periods_per_year)
    row["momentum_10d_pct"] = scores["momentum_pct"].iloc[-1]
    row["vol_10d_pct"] = scores["vol_pct"].iloc[-1]
    row["score"] = scores["score"].iloc[-1]
    deltas = []
    for window in DELTA_WINDOWS:
        idx = len(scores) - 1 - window
        delta = scores["score"].iloc[-1] - scores["score"].iloc[idx] if idx >= 0 else np.nan
        row[f"delta_{window}d"] = delta
        deltas.append(delta)
    row["delta_signal"] = classify_delta_signal(deltas)

    if len(close) >= TREND_MA_WINDOW:
        row["above_200d_ma"] = bool(close.iloc[-1] > close.tail(TREND_MA_WINDOW).mean())
    else:
        row["above_200d_ma"] = None
    return row


def compute_momentum(price_df: pd.DataFrame, cash_like: list[str] | None = None) -> pd.DataFrame:
    """One row per symbol with momentum metrics, ranked by tracker score (best first).

    Symbols in `cash_like` keep their metrics but get no rank, so a near-zero-vol
    instrument like SHV can't top the ranking.
    """
    if price_df.empty:
        return pd.DataFrame()

    ordered = price_df.sort_values(["symbol", "timestamp"]).drop_duplicates(
        ["symbol", "timestamp"], keep="last"
    )
    rows = [compute_symbol_momentum(group) for _, group in ordered.groupby("symbol", sort=True)]
    result = pd.DataFrame(rows)
    result["cash_like"] = result["symbol"].isin(cash_like or [])
    rankable = result["score"].where(~result["cash_like"])
    result["rank"] = rankable.rank(ascending=False, method="min")
    return result.sort_values("rank", na_position="last").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute momentum score, deltas and ranking from daily bars.")
    parser.add_argument(
        "--as-of",
        default=None,
        help="YYYY-MM-DD: ignore bars after this date (reproduce a past snapshot).",
    )
    args = parser.parse_args()

    price_path = DATA_DIR / "price_data.parquet"
    if not price_path.exists():
        raise SystemExit(
            f"Missing {price_path}. Run the price collector first: "
            "uv run collect_price_data.py --days 90"
        )

    price_df = pd.read_parquet(price_path)
    if args.as_of:
        cutoff = pd.Timestamp(args.as_of, tz="UTC") + pd.Timedelta(days=1)
        timestamps = pd.to_datetime(price_df["timestamp"], utc=True)
        price_df = price_df[timestamps < cutoff]

    result = compute_momentum(price_df, cash_like=load_cash_like_tickers())
    if result["smoothed_ret_60d"].isna().all():
        print(
            "  [momentum] not enough history for 60-bar metrics; "
            "re-run the price collector with --days 90 or more"
        )

    out_path = DATA_DIR / "momentum.parquet"
    result.to_parquet(out_path, index=False)
    print(f"Saved {len(result)} momentum rows to {out_path}")
    for _, row in result.head(10).iterrows():
        print(
            f"  #{row['rank']:.0f} {row['symbol']}: score={row['score']:.3f} "
            f"deltas(5/10/20)={row['delta_5d']:+.3f}/{row['delta_10d']:+.3f}/{row['delta_20d']:+.3f} "
            f"{row['delta_signal']}"
        )
    for signal in ("ACCELERATING", "DECELERATING"):
        names = result.loc[result["delta_signal"] == signal, "symbol"].tolist()
        print(f"  {signal} on all three windows ({len(names)}): {', '.join(names)}")


if __name__ == "__main__":
    main()
