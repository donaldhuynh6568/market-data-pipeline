"""Holding-period returns and costs per (variant, ticker, rebalance), vectorized.

Phase 1 sizing (analysis spec section 7): target weights are reset at every rebalance, no compounding inside a
variant's book. Turnover is |target weight - previous target weight| per name, so drift between rebalances is
ignored: a slight understatement of real turnover, the same for every variant. Cash earns zero.
"""

from __future__ import annotations

import pandas as pd

TRADE_COLUMNS = [
    "variant", "ticker", "rebalance_date", "entry_date", "exit_date", "split",
    "weight", "prev_weight", "turnover", "gross_ret", "contribution", "cost", "net_contribution",
]


def holding_returns(periods: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Close-to-close return from entry_date to exit_date for every ticker with a price.

    Each leg takes the last non-null close on or before its date, so a name whose bars stop mid-period (halt,
    delisting) is marked at its last traded price instead of vanishing from the book.
    """
    closes = prices.dropna(subset=["close"])[["ticker", "date", "close"]].sort_values("date")
    periods = periods.dropna(subset=["exit_date"])
    legs = []
    for leg in ("entry_date", "exit_date"):
        dates = periods[["rebalance_date", leg]].rename(columns={leg: "date"})
        grid = dates.merge(pd.DataFrame({"ticker": closes["ticker"].unique()}), how="cross").sort_values("date")
        priced = pd.merge_asof(grid, closes, on="date", by="ticker", direction="backward")
        legs.append(priced.rename(columns={"close": f"close_{leg}"}).drop(columns="date"))
    out = legs[0].merge(legs[1], on=["rebalance_date", "ticker"])
    out["gross_ret"] = out["close_exit_date"] / out["close_entry_date"] - 1
    return out[["rebalance_date", "ticker", "gross_ret"]].dropna()


def simulate(
    signals: pd.DataFrame,
    periods: pd.DataFrame,
    returns: pd.DataFrame,
    asset_class: pd.Series,
    cost_per_side: dict[str, float],
) -> pd.DataFrame:
    """One row per (variant, ticker, rebalance) that is held or traded, over periods that have an exit."""
    closed = periods.dropna(subset=["exit_date"]).sort_values("rebalance_date")
    held = signals.loc[signals["weight"] > 0, ["variant", "ticker", "rebalance_date", "weight"]]
    held = held[held["rebalance_date"].isin(closed["rebalance_date"])]

    wide = held.pivot_table(
        index=["variant", "rebalance_date"], columns="ticker", values="weight", aggfunc="sum", fill_value=0.0
    ).sort_index()
    prev = wide.groupby(level="variant").shift(1).fillna(0.0)      # before a variant's first rebalance: all cash
    stacked = pd.DataFrame({"weight": wide.stack(), "prev_weight": prev.stack()})
    stacked = stacked[(stacked["weight"] > 0) | (stacked["prev_weight"] > 0)].reset_index()
    stacked["turnover"] = (stacked["weight"] - stacked["prev_weight"]).abs()

    trades = stacked.merge(closed, on="rebalance_date").merge(returns, on=["rebalance_date", "ticker"], how="left")
    # A held name with no entry price cannot have been bought: its slot sits in cash for the period.
    trades["gross_ret"] = trades["gross_ret"].where(trades["weight"] > 0, 0.0).fillna(0.0)
    rates = trades["ticker"].map(asset_class).map(cost_per_side)
    if rates.isna().any():
        missing = sorted(trades.loc[rates.isna(), "ticker"].unique())
        raise ValueError(f"no cost_per_side rate for the asset class of {missing}")
    trades["contribution"] = trades["weight"] * trades["gross_ret"]
    trades["cost"] = trades["turnover"] * rates
    trades["net_contribution"] = trades["contribution"] - trades["cost"]
    return trades[TRADE_COLUMNS].sort_values(["variant", "rebalance_date", "ticker"]).reset_index(drop=True)


def period_returns(trades: pd.DataFrame) -> pd.DataFrame:
    """Portfolio return per (variant, rebalance): the sum of its names' contributions, cash at zero."""
    held = trades.assign(is_held=trades["weight"] > 0, is_trade=trades["turnover"] > 1e-12)
    return (
        held.groupby(["variant", "rebalance_date", "entry_date", "exit_date", "split"], as_index=False)
        .agg(
            gross_ret=("contribution", "sum"),
            cost=("cost", "sum"),
            net_ret=("net_contribution", "sum"),
            turnover=("turnover", "sum"),
            n_positions=("is_held", "sum"),
            n_trades=("is_trade", "sum"),
        )
        .sort_values(["variant", "rebalance_date"])
        .reset_index(drop=True)
    )
