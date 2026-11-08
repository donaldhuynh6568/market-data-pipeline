import numpy as np
import pandas as pd
import pytest

from market_data_pipeline.sim.performance import performance_table, rank_ic, summarize
from market_data_pipeline.sim.random_signals import eligible_set, random_signals
from market_data_pipeline.sim.simulate import holding_returns, period_returns, simulate

D = pd.Timestamp


def _prices(table: dict[str, list[float]], dates: list[str]) -> pd.DataFrame:
    rows = [
        {"ticker": t, "date": D(d), "close": c, "asset_class": "crypto" if "/" in t else "equity"}
        for t, closes in table.items() for d, c in zip(dates, closes)
    ]
    return pd.DataFrame(rows)


# Three monthly periods: signal at a month end, entry the next day, exit at the next entry.
DATES = ["2026-01-30", "2026-02-02", "2026-02-27", "2026-03-02", "2026-03-31", "2026-04-01"]
PERIODS = pd.DataFrame({
    "rebalance_date": [D("2026-01-30"), D("2026-02-27"), D("2026-03-31")],
    "entry_date": [D("2026-02-02"), D("2026-03-02"), D("2026-04-01")],
    "exit_date": [D("2026-03-02"), D("2026-04-01"), pd.NaT],
    "split": ["train", "train", "validation"],
})
PRICES = _prices({
    "SPY": [100, 100, 105, 110, 115, 121],
    "A":   [10, 10, 11, 12, 12, 9],
    "B":   [20, 20, 20, 30, 30, 30],
}, DATES)
ASSET_CLASS = PRICES.drop_duplicates("ticker").set_index("ticker")["asset_class"]


def _signals(variant: str, weights: dict[str, dict[str, float]]) -> pd.DataFrame:
    return pd.DataFrame([
        {"variant": variant, "ticker": t, "rebalance_date": D(date), "weight": w, "hold": w > 0, "score": None}
        for date, names in weights.items() for t, w in names.items()
    ])


def _run(signals, cost=0.0):
    returns = holding_returns(PERIODS, PRICES)
    trades = simulate(signals, PERIODS, returns, ASSET_CLASS, {"equity": cost, "crypto": cost})
    return trades, period_returns(trades)


def test_holding_return_is_entry_close_to_exit_close():
    returns = holding_returns(PERIODS, PRICES).set_index(["rebalance_date", "ticker"])["gross_ret"]

    assert returns[(D("2026-01-30"), "SPY")] == pytest.approx(110 / 100 - 1)
    assert returns[(D("2026-02-27"), "A")] == pytest.approx(9 / 12 - 1)
    assert (D("2026-03-31"), "SPY") not in returns.index                  # open period: no exit yet


def test_spy_buy_and_hold_matches_spy_price_return():
    signals = _signals("spy_bh", {d: {"SPY": 1.0} for d in ["2026-01-30", "2026-02-27", "2026-03-31"]})

    trades, periods = _run(signals)

    assert periods["net_ret"].tolist() == pytest.approx([0.10, 0.10])
    assert (1 + periods["net_ret"]).prod() == pytest.approx(121 / 100)    # entry 100 -> last exit 121
    assert trades["turnover"].tolist() == pytest.approx([1.0, 0.0])       # bought once, then held


def test_equal_weight_zero_cost_matches_hand_calculation():
    weights = {d: {"SPY": 1 / 3, "A": 1 / 3, "B": 1 / 3} for d in ["2026-01-30", "2026-02-27"]}

    _, periods = _run(_signals("equal_weight", weights))

    first = (110 / 100 - 1 + 12 / 10 - 1 + 30 / 20 - 1) / 3
    second = (121 / 110 - 1 + 9 / 12 - 1 + 30 / 30 - 1) / 3
    assert periods["net_ret"].tolist() == pytest.approx([first, second])


def test_costs_reduce_net_by_exactly_turnover_times_rate():
    weights = {"2026-01-30": {"A": 1.0}, "2026-02-27": {"B": 1.0}}   # full switch A -> B
    rate = 0.001

    trades, periods = _run(_signals("momentum_top1", weights), cost=rate)

    assert periods["turnover"].tolist() == pytest.approx([1.0, 2.0])      # buy A; sell A + buy B
    assert periods["cost"].tolist() == pytest.approx([1.0 * rate, 2.0 * rate])
    assert (periods["gross_ret"] - periods["net_ret"]).tolist() == pytest.approx(periods["cost"].tolist())
    sold = trades[(trades["ticker"] == "A") & (trades["rebalance_date"] == D("2026-02-27"))].iloc[0]
    assert (sold["weight"], sold["prev_weight"], sold["contribution"]) == (0.0, 1.0, 0.0)


def test_crypto_and_equity_use_their_own_cost_rate():
    prices = _prices({"BTC/USD": [1, 1, 1, 1, 1, 1], "A": [1, 1, 1, 1, 1, 1]}, DATES)
    signals = _signals("x", {"2026-01-30": {"BTC/USD": 0.5, "A": 0.5}})
    returns = holding_returns(PERIODS, prices)
    asset_class = prices.drop_duplicates("ticker").set_index("ticker")["asset_class"]

    trades = simulate(signals, PERIODS, returns, asset_class, {"equity": 0.001, "crypto": 0.0025})

    assert trades.set_index("ticker")["cost"].to_dict() == pytest.approx({"BTC/USD": 0.00125, "A": 0.0005})


def test_random_draws_are_reproducible_and_drawn_from_the_eligible_set():
    eligible = eligible_set(_signals("equal_weight", {
        "2026-01-30": {"SPY": 1 / 3, "A": 1 / 3, "B": 1 / 3}, "2026-02-27": {"SPY": 0.5, "A": 0.5},
    }))

    first = random_signals(eligible, top_n=2, draws=5, seed=7)
    again = random_signals(eligible, top_n=2, draws=5, seed=7)
    other = random_signals(eligible, top_n=2, draws=5, seed=8)

    pd.testing.assert_frame_equal(first, again)
    assert not first.equals(other)
    assert set(first["variant"]) == {f"random_top2_d{i:03d}" for i in range(5)}
    feb = first[first["rebalance_date"] == D("2026-02-27")]
    assert set(feb["ticker"]) <= {"SPY", "A"}
    assert first.groupby(["variant", "rebalance_date"])["weight"].sum().tolist() == pytest.approx([1.0] * 10)


def test_summarize_cagr_drawdown_and_sharpe():
    periods = pd.DataFrame({
        "rebalance_date": [D("2025-01-31"), D("2025-07-31")],
        "entry_date": [D("2025-02-03"), D("2025-08-01")],
        "exit_date": [D("2025-08-01"), D("2026-02-03")],
        "net_ret": [0.10, -0.05], "gross_ret": [0.10, -0.05], "cost": [0.0, 0.0],
        "turnover": [1.0, 0.0], "n_trades": [1, 0], "n_positions": [1, 1],
    })

    stats = summarize(periods)

    years = (D("2026-02-03") - D("2025-02-03")).days / 365.25
    assert stats["net_cagr"] == pytest.approx((1.10 * 0.95) ** (1 / years) - 1)
    assert stats["max_drawdown"] == pytest.approx(-0.05)
    expected_sharpe = np.mean([0.10, -0.05]) / np.std([0.10, -0.05], ddof=1) * np.sqrt(2 / years)
    assert stats["sharpe"] == pytest.approx(expected_sharpe)


def test_performance_table_reports_only_the_requested_splits_and_collapses_random_draws():
    weights = {d: {"SPY": 1.0} for d in ["2026-01-30", "2026-02-27"]}
    signals = pd.concat([
        _signals("spy_bh", weights),
        _signals("random_top1_d000", {d: {"A": 1.0} for d in ["2026-01-30", "2026-02-27"]}),
        _signals("random_top1_d001", {d: {"B": 1.0} for d in ["2026-01-30", "2026-02-27"]}),
    ])
    periods_df = PERIODS.assign(split=["train", "test", "validation"])
    returns = holding_returns(periods_df, PRICES)
    trades = simulate(signals, periods_df, returns, ASSET_CLASS, {"equity": 0.0, "crypto": 0.0})
    eligible_returns = returns.merge(periods_df[["rebalance_date", "split"]], on="rebalance_date")
    ic = rank_ic(signals.assign(variant="equal_weight"), eligible_returns)

    table = performance_table(period_returns(trades), trades, eligible_returns, ic, splits=["train"])

    assert set(table["split"]) == {"train"}                                # the test period never shows up
    assert pd.isna(table.set_index("variant").loc["spy_bh", "random_pctile"])   # only momentum is placed
    assert set(table["variant"]) == {"spy_bh", "random_top1_p05", "random_top1_p50", "random_top1_p95"}
    p50 = table.set_index("variant").loc["random_top1_p50"]
    assert p50["n_draws"] == 2
    assert p50["total_net_return"] == pytest.approx(np.median([12 / 10 - 1, 30 / 20 - 1]))


def test_random_pctile_places_momentum_within_its_random_family():
    dates = ["2026-01-30", "2026-02-27"]
    signals = pd.concat([
        _signals("momentum_top1", {d: {"B": 1.0} for d in dates}),          # +50% then 0%
        _signals("random_top1_d000", {d: {"A": 1.0} for d in dates}),       # +20% then -25%
        _signals("random_top1_d001", {d: {"SPY": 1.0} for d in dates}),     # +10% then +10%
    ])
    returns = holding_returns(PERIODS, PRICES)
    trades = simulate(signals, PERIODS, returns, ASSET_CLASS, {"equity": 0.0, "crypto": 0.0})
    eligible_returns = returns.merge(PERIODS[["rebalance_date", "split"]], on="rebalance_date")
    ic = rank_ic(signals.assign(variant="equal_weight"), eligible_returns)

    table = performance_table(period_returns(trades), trades, eligible_returns, ic, splits=["train"])

    assert table.set_index("variant").loc["momentum_top1", "random_pctile"] == 1.0   # beat both draws
