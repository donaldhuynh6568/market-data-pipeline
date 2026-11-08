"""Random-selection baseline: N names drawn uniformly from the same eligible set momentum ranks, many times."""

from __future__ import annotations

import numpy as np
import pandas as pd


def eligible_set(signals: pd.DataFrame) -> pd.DataFrame:
    """(rebalance_date, ticker) pairs eligible at each rebalance: exactly the equal_weight variant's holdings."""
    return signals.loc[signals["variant"] == "equal_weight", ["rebalance_date", "ticker"]].reset_index(drop=True)


def random_signals(eligible: pd.DataFrame, top_n: int, draws: int, seed: int) -> pd.DataFrame:
    """`draws` independent portfolios of `top_n` equal-weighted names per rebalance, as mart_signals rows.

    Seeded per (top_n, draw) so adding draws or N values never changes the existing ones.
    """
    eligible = eligible.sort_values(["rebalance_date", "ticker"]).reset_index(drop=True)
    groups = [(date, g["ticker"].to_numpy()) for date, g in eligible.groupby("rebalance_date", sort=True)]
    frames = []
    for draw in range(draws):
        rng = np.random.default_rng([seed, top_n, draw])
        picks_dates, picks_tickers = [], []
        for date, tickers in groups:
            n = min(top_n, len(tickers))
            chosen = rng.choice(tickers, size=n, replace=False)
            picks_dates.extend([date] * n)
            picks_tickers.extend(chosen)
        frame = pd.DataFrame({"rebalance_date": picks_dates, "ticker": picks_tickers})
        frame["weight"] = 1.0 / frame.groupby("rebalance_date")["ticker"].transform("count")
        frame["variant"] = f"random_top{top_n}_d{draw:03d}"
        frames.append(frame)
    out = pd.concat(frames, ignore_index=True)
    out["hold"] = True
    return out[["variant", "ticker", "rebalance_date", "hold", "weight"]]
