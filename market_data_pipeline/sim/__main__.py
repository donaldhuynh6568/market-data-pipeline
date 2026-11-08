"""python -m market_data_pipeline.sim [--include-test]

Reads the dbt marts, adds the random baselines, simulates every variant net of costs and writes:
  mart_sim_trades     (variant, ticker, rebalance_date)   positions, turnover, gross/net contribution
  mart_sim_periods    (variant, rebalance_date)           portfolio return per holding period
  mart_equity_curve   (variant, rebalance_date)           cumulative net equity and drawdown
  mart_rank_ic        (rebalance_date)                    momentum score vs next-period return
  mart_performance    (variant, split)                    the keep-or-cut table

The test split is left out of every output unless --include-test is passed, so it stays unseen until the
strategy is frozen (analysis spec section 5).
"""

from __future__ import annotations

import argparse

import pandas as pd

from market_data_pipeline.settings import load_strategy
from market_data_pipeline.sim.data import read_mart, write_mart
from market_data_pipeline.sim.performance import equity_curves, performance_table, rank_ic
from market_data_pipeline.sim.random_signals import eligible_set, random_signals
from market_data_pipeline.sim.simulate import holding_returns, period_returns, simulate


def run(include_test: bool = False) -> pd.DataFrame:
    settings = load_strategy()
    splits = list(settings["report_splits"]) + (["test"] if include_test else [])
    splits = list(dict.fromkeys(splits))

    signals = read_mart("mart_signals")
    rebalances = read_mart("mart_rebalance_dates")
    prices = read_mart("mart_price_daily")
    for frame, cols in ((signals, ["rebalance_date"]), (rebalances, ["rebalance_date", "entry_date", "exit_date"]),
                        (prices, ["date"])):
        for col in cols:
            frame[col] = pd.to_datetime(frame[col])

    eligible = eligible_set(signals)
    randoms = pd.concat(
        [random_signals(eligible, n, settings["random_draws"], settings["random_seed"]) for n in settings["top_n_grid"]],
        ignore_index=True,
    )
    all_signals = pd.concat([signals, randoms], ignore_index=True)
    print(f"  [sim] {signals['variant'].nunique()} variants from dbt + {randoms['variant'].nunique()} random draws")

    returns = holding_returns(rebalances, prices)
    asset_class = prices.drop_duplicates("ticker").set_index("ticker")["asset_class"]
    trades = simulate(all_signals, rebalances, returns, asset_class, settings["cost_per_side"])
    periods = period_returns(trades)

    eligible_returns = eligible.merge(returns, on=["rebalance_date", "ticker"]).merge(
        rebalances[["rebalance_date", "split"]], on="rebalance_date"
    )
    ic = rank_ic(signals, eligible_returns)
    performance = performance_table(periods, trades, eligible_returns, ic, splits)

    visible = [*splits, "gap"]
    write_mart(trades[trades["split"].isin(visible)], "mart_sim_trades")
    write_mart(periods[periods["split"].isin(visible)], "mart_sim_periods")
    write_mart(equity_curves(periods, splits), "mart_equity_curve")
    write_mart(ic[ic["split"].isin(visible)], "mart_rank_ic")
    write_mart(performance, "mart_performance")
    return performance


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate Track A variants and baselines net of costs.")
    parser.add_argument("--include-test", action="store_true", help="Also report the held-out test split.")
    args = parser.parse_args()
    performance = run(args.include_test)
    cols = ["variant", "split", "n_rebalances", "net_cagr", "max_drawdown", "sharpe", "avg_turnover"]
    shown = performance[~performance["variant"].str.contains(r"_p(?:05|95)$")]
    with pd.option_context("display.width", 160, "display.max_rows", 200, "display.float_format", "{:.3f}".format):
        print(shown[cols].to_string(index=False))


if __name__ == "__main__":
    main()
