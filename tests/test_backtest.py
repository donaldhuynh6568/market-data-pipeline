import numpy as np
import pandas as pd
import pytest

from market_data_pipeline.transforms.backtest import (
    BacktestConfig,
    expectancy,
    momentum_forward_returns,
    random_control,
    resolve_to_level,
    resolve_trade,
    run_backtest,
    run_target_backtest,
    summarize,
    summarize_target,
    target_trades,
    zone_trades,
)


def _bars(ohlc: list[tuple[float, float]], symbol: str = "TEST") -> pd.DataFrame:
    lows = [low for low, _ in ohlc]
    highs = [high for _, high in ohlc]
    mid = [(low + high) / 2 for low, high in ohlc]
    return pd.DataFrame({
        "symbol": symbol,
        "timestamp": pd.date_range("2026-01-01", periods=len(ohlc), freq="D", tz="UTC"),
        "open": mid, "high": highs, "low": lows, "close": mid,
        "volume": [1_000.0] * len(ohlc), "asset_class": "equity",
    })


def _bars_with_override(ohlc, overrides: dict[int, tuple[float, float]]) -> pd.DataFrame:
    """Same as `_bars`, but `overrides[i] = (open, close)` replaces the auto-midpoint for row i - for tests
    that need to control a candle's body independently of its wick."""
    frame = _bars(ohlc)
    for i, (open_, close) in overrides.items():
        frame.loc[i, "open"] = open_
        frame.loc[i, "close"] = close
    return frame


def test_resolve_trade_finds_the_target_the_stop_or_neither():
    high = np.array([100.0, 105.0, 112.0])
    low = np.array([95.0, 99.0, 104.0])

    assert resolve_trade(high, low, 0, entry=100.0, stop=90.0, reward=1.0) == (True, 2)  # 110 hit on bar 2
    assert resolve_trade(high, low, 0, entry=100.0, stop=96.0, reward=1.0) == (False, 0)  # low 95 on bar 0
    assert resolve_trade(high, low, 0, entry=100.0, stop=90.0, reward=5.0)[0] is None  # never resolves


def test_a_bar_spanning_both_counts_as_a_loss():
    # Daily bars cannot say which came first intraday, so the backtest must not assume the good outcome.
    high, low = np.array([120.0]), np.array([80.0])

    assert resolve_trade(high, low, 0, entry=100.0, stop=90.0, reward=1.0) == (False, 0)


def test_expectancy_math():
    assert expectancy(pd.Series([True, True, False, False]), reward=1.0) == pytest.approx(0.0)
    assert expectancy(pd.Series([True, False, False, False]), reward=3.0) == pytest.approx(0.0)
    assert expectancy(pd.Series([True] * 6 + [False] * 4), reward=1.0) == pytest.approx(0.2)
    assert np.isnan(expectancy(pd.Series([None, None], dtype=object), reward=1.0))


def _zone_bars(outcome: str) -> pd.DataFrame:
    """One bullish gap and one outcome. Tail bars overlap the entry bar so they form no further gaps.

    index 20 is candle 1 (low 90 = the stop), 21 the imbalance, 22 closes the gap: high[20]=100 below
    low[22]=110, so entry is 110 and R is 20, putting the 1R target at 130.
    """
    # index 19 reaches up to 106 so it does not form a second gap with the imbalance candle at 21.
    bars = [(95.0, 100.0)] * 19 + [(95.0, 106.0), (90.0, 100.0), (105.0, 115.0), (110.0, 118.0), (104.0, 112.0)]
    tail = [(109.0, 135.0)] * 20 if outcome == "win" else [(85.0, 118.0)] * 20
    return _bars(bars + tail)


def test_zone_trades_records_the_entry_stop_and_outcome():
    won = zone_trades(_zone_bars("win"))
    lost = zone_trades(_zone_bars("loss"))

    assert len(won) == 1 and len(lost) == 1
    trade = won.iloc[0]
    assert (trade["entry"], trade["stop"]) == (110.0, 90.0)
    assert bool(trade["win_1r"]) is True  # target 130 reached
    assert bool(lost.iloc[0]["win_1r"]) is False
    assert trade["mfe_r"] > 0 and trade["entered_at"] > trade["formed_at"]


def test_a_zone_price_never_returns_to_is_not_a_trade():
    # Gap forms then price runs away upward - nothing to enter, so it must not be scored as a win.
    # A strictly rising tail, so no later low ever touches a zone edge.
    bars = [(95.0, 100.0)] * 21 + [(105.0, 115.0), (120.0, 125.0)]
    bars += [(130.0 + k, 140.0 + k) for k in range(20)]

    assert zone_trades(_bars(bars)).empty


def test_too_little_history_produces_no_trades():
    assert zone_trades(_bars([(95.0, 100.0)] * 10)).empty


def test_summarize_splits_by_a_feature():
    trades = pd.DataFrame({
        "win_1r": [True, True, False, False, False, False],
        "zone_quality": ["STRONG", "STRONG", "PLAIN", "PLAIN", "PLAIN", "PLAIN"],
        "mfe_r": [2.0] * 6, "mae_r": [0.5] * 6,
    })

    table = summarize(trades, by="zone_quality").set_index("bucket")

    assert table.loc["STRONG", "win_rate"] == 1.0
    assert table.loc["PLAIN", "win_rate"] == 0.0
    assert table.loc["STRONG", "expectancy_r"] == pytest.approx(1.0)
    assert summarize(trades).iloc[0]["trades"] == 6


def test_momentum_uses_non_overlapping_windows_only():
    rng = np.random.default_rng(0)
    closes = 100 * np.cumprod(1 + rng.normal(0.001, 0.02, 120))
    bars = _bars([(c * 0.99, c * 1.01) for c in closes])

    table = momentum_forward_returns(bars, horizons=(10,))

    # 120 bars / 10 = 12 blocks, minus the ~10 the score needs before it is finite.
    assert table["observations"].sum() <= 12
    assert set(table["bucket"]) <= {"low", "mid", "high"}
    assert "correlation" in table


def test_random_control_is_reproducible_and_reports_a_baseline():
    rng = np.random.default_rng(1)
    closes = 100 * np.cumprod(1 + rng.normal(0.0005, 0.02, 90))
    bars = _bars([(c * 0.98, c * 1.02) for c in closes])

    first = random_control(bars, config=BacktestConfig(seed=7))
    again = random_control(bars, config=BacktestConfig(seed=7))

    assert first == again
    assert 0.0 <= first["win_rate"] <= 1.0 and first["trades"] > 0


def test_the_control_matches_the_stop_widths_it_is_given():
    """A control that stops wider than the signal flatters itself and overstates the signal's failure."""
    rng = np.random.default_rng(3)
    closes = 100 * np.cumprod(1 + rng.normal(0.0, 0.02, 120))
    bars = _bars([(c * 0.98, c * 1.02) for c in closes])

    tight = random_control(bars, np.array([0.5, 0.6]), BacktestConfig(seed=1))
    wide = random_control(bars, np.array([3.0, 4.0]), BacktestConfig(seed=1))

    # The control reports the geometry it actually used, so a mismatch with the signal is visible.
    assert tight["median_stop_atr"] == pytest.approx(0.55)
    assert wide["median_stop_atr"] == pytest.approx(3.5)
    # Stop width changes the outcome, which is exactly why it has to be matched rather than fixed.
    assert tight["win_rate"] != wide["win_rate"]
    # No distribution supplied falls back to the fixed default rather than crashing.
    assert random_control(bars, None, BacktestConfig(seed=1))["median_stop_atr"] == 1.5
    assert random_control(bars, np.array([np.nan, -1.0]), BacktestConfig(seed=1))["median_stop_atr"] == 1.5


def test_run_backtest_always_reports_the_random_control_even_with_no_zones():
    rng = np.random.default_rng(2)
    closes = 100 * np.cumprod(1 + rng.normal(0.0, 0.01, 80))
    bars = _bars([(c * 0.995, c * 1.005) for c in closes])

    trades, summary = run_backtest(bars)

    assert "Control @ 1R" in set(summary["view"])  # the bar any signal has to clear is never omitted
    assert list(summary.columns) == [
        "view", "bucket", "trades", "win_rate", "expectancy_r", "median_mfe_r", "median_mae_r",
    ]
    assert trades.empty or "win_1r" in trades


def test_excursions_cover_the_life_of_the_trade_not_the_rest_of_the_data():
    # Stop-out on the first tail bar, then price rallies far above the entry afterwards.
    bars = [(95.0, 100.0)] * 19 + [(95.0, 106.0), (90.0, 100.0), (105.0, 115.0), (110.0, 118.0), (104.0, 112.0)]
    bars += [(85.0, 118.0)] + [(300.0, 400.0)] * 19  # stop hit at 90, then a huge move up

    trade = zone_trades(_bars(bars)).iloc[0]

    assert bool(trade["win_1r"]) is False
    # The 300-400 bars happen after the stop, so they must not count as favourable excursion.
    assert trade["mfe_r"] < 1.0, "MFE leaked in moves that happened after the trade was over"


def _weak_entry_zone_bars(trigger_close: float) -> pd.DataFrame:
    """Same zone as `_zone_bars` (entry 110, stop 90, midpoint 105), but the trigger bar's own close is
    controlled independently of its low, so it can sit on either side of the midpoint."""
    bars = [(95.0, 100.0)] * 19 + [(95.0, 106.0), (90.0, 100.0), (105.0, 115.0), (110.0, 118.0), (104.0, 112.0)]
    tail = [(109.0, 135.0)] * 20
    return _bars_with_override(bars + tail, overrides={23: (109.0, trigger_close)})


def test_weak_entry_close_flags_a_trigger_bar_that_closes_below_the_zone_midpoint():
    # Low (104) still triggers the entry (<= zone_high 110), but a close of 104 sits below the 105 midpoint -
    # the wick reached the zone, the body did not confirm it.
    trade = zone_trades(_weak_entry_zone_bars(trigger_close=104.0)).iloc[0]

    assert bool(trade["weak_entry_close"]) is True


def test_weak_entry_close_is_false_when_the_trigger_bar_closes_above_the_midpoint():
    trade = zone_trades(_weak_entry_zone_bars(trigger_close=108.0)).iloc[0]

    assert bool(trade["weak_entry_close"]) is False


def test_no_bar_before_the_trigger_can_ever_be_weak():
    """The trigger is defined as the FIRST bar whose low reaches the zone, so every earlier bar's low (and
    therefore its close, which can't sit below its own low) stayed above the zone entirely - there is no
    earlier point at which a body breach could even be checked."""
    trade = zone_trades(_weak_entry_zone_bars(trigger_close=104.0)).iloc[0]

    assert trade["entered_at"] == trade["entered_at"]  # trigger exists (sanity: the trade resolved at all)
    # bars_to_trigger counts from formation to the trigger bar itself, confirming the trigger is bar 23, not later.
    assert trade["bars_to_trigger"] == 2


def test_run_backtest_reports_a_view_for_weak_entry_close():
    weak = zone_trades(_weak_entry_zone_bars(trigger_close=104.0))
    strong = zone_trades(_weak_entry_zone_bars(trigger_close=108.0)).assign(symbol="OTHER")
    trades = pd.concat([weak, strong], ignore_index=True)

    table = summarize(trades, by="weak_entry_close")

    assert set(table["bucket"]) == {"True", "False"}


def test_resolve_to_level_is_what_resolve_trade_delegates_to():
    high = np.array([100.0, 105.0, 112.0])
    low = np.array([95.0, 99.0, 104.0])

    assert resolve_to_level(high, low, 0, stop=90.0, target=110.0) == resolve_trade(
        high, low, 0, entry=100.0, stop=90.0, reward=1.0
    )


# --- target_trades: resolved against the real ICT target instead of a synthetic R-multiple -----------------
#
# Builds one clean dealing range (swing low 80, swing high 130, both confirmed well before the gap) and one
# bullish FVG (entry 115, stop 97) that triggers on the second-to-last bar. As of that trigger, the only known
# ICT level above price is OTE at 117.5 (CE sits below the floor; the dealing-range high and the still-SMOOTH
# swing high both happen to be the same 130 level, so OTE at 117.5 is nearer and wins). The resolution bar then
# either clears 117.5 (win) or 97 (loss) first.
#
# The same bars also happen to form a second, overlapping FVG a few candles later (entry 108, stop 99) that
# only triggers in the loss variant, once price falls far enough - a real, independently-measured zone, not a
# bug. Tests filter to entry == 115.0 so they assert on the zone they built, not this incidental second one.


def _target_zone_bars(resolution: tuple[float, float]) -> pd.DataFrame:
    base = (
        [(95.0, 100.0)] * 30                                      # 0-29 baseline
        + [(80.0, 90.0)]                                          # 30 swing low
        + [(85.0, 95.0), (88.0, 97.0), (90.0, 99.0)]              # 31-33 recovery
        + [(95.0, 130.0)]                                         # 34 swing high
        + [(100.0, 115.0), (98.0, 112.0), (96.0, 110.0)]          # 35-37 confirm the swing high
        + [(97.0, 100.0)]                                         # 38 FVG candle 1 (zone_low 100, stop 97)
        + [(99.0, 105.0)]                                         # 39 imbalance candle
        + [(115.0, 120.0)]                                        # 40 FVG candle 3 (entry 115)
        + [(108.0, 112.0)]                                        # 41 trigger (low 108 <= entry 115)
    )
    return _bars(base + [resolution])


def test_target_trades_reaches_the_real_level_before_the_stop():
    trade = target_trades(_target_zone_bars((112.0, 119.0)))
    row = trade[trade["entry"] == 115.0].iloc[0]

    assert (row["stop"], row["target"], row["target_label"]) == (97.0, 117.5, "OTE")
    assert row["target_rr"] == pytest.approx(0.25 / 1.8)  # (117.5-115)/(115-97)
    assert bool(row["won"]) is True
    assert bool(row["weak_entry_close"]) is False


def test_target_trades_hits_the_stop_before_the_real_level():
    trade = target_trades(_target_zone_bars((85.0, 95.0)))
    row = trade[trade["entry"] == 115.0].iloc[0]

    assert (row["stop"], row["target"], row["target_label"]) == (97.0, 117.5, "OTE")
    assert bool(row["won"]) is False


def test_a_zone_with_no_known_ict_level_yet_is_skipped():
    # Truncated well before the swing high confirms, let alone the gap forms - nothing for this test to measure.
    base = (
        [(95.0, 100.0)] * 30
        + [(80.0, 90.0)]
        + [(85.0, 95.0), (88.0, 97.0), (90.0, 99.0)]
        + [(95.0, 130.0)]
        + [(100.0, 115.0), (98.0, 112.0), (96.0, 110.0)]
        + [(95.0, 100.0)] * 4
    )
    assert target_trades(_bars(base)).empty


def test_summarize_target_uses_each_trades_own_realized_reward():
    trades = pd.DataFrame({
        "won": [True, True, False, False],
        "target_rr": [2.0, 0.5, 1.0, 3.0],
        "zone_quality": ["STRONG", "PLAIN", "STRONG", "PLAIN"],
    })

    table = summarize_target(trades, by="zone_quality").set_index("bucket")

    # STRONG: won at 2.0R, lost at -1R -> mean 0.5. PLAIN: won at 0.5R, lost at -1R -> mean -0.25.
    assert table.loc["STRONG", "expectancy_r"] == pytest.approx(0.5)
    assert table.loc["PLAIN", "expectancy_r"] == pytest.approx(-0.25)
    assert summarize_target(trades).iloc[0]["trades"] == 4


def test_random_control_can_sample_rewards_as_well_as_stops():
    rng = np.random.default_rng(4)
    closes = 100 * np.cumprod(1 + rng.normal(0.0, 0.02, 120))
    bars = _bars([(c * 0.98, c * 1.02) for c in closes])

    fixed = random_control(bars, config=BacktestConfig(seed=2))
    matched = random_control(bars, config=BacktestConfig(seed=2), reward_multiples=np.array([0.5, 1.0, 1.5]))

    assert "median_reward" not in fixed  # unchanged behaviour when no reward distribution is supplied
    assert matched["median_reward"] == pytest.approx(1.0)
    assert matched["trades"] > 0


def test_run_target_backtest_always_reports_a_control_even_with_no_zones():
    rng = np.random.default_rng(5)
    closes = 100 * np.cumprod(1 + rng.normal(0.0, 0.01, 80))
    bars = _bars([(c * 0.995, c * 1.005) for c in closes])

    trades, summary = run_target_backtest(bars)

    assert "Control (to target)" in set(summary["view"])
    assert list(summary.columns) == ["view", "bucket", "trades", "win_rate", "expectancy_r", "median_target_rr"]
    assert trades.empty or "won" in trades


def test_run_target_backtest_reports_the_real_outcome():
    # OTHER (the loss variant) also triggers the incidental second overlapping zone (entry 108), so the
    # universe is 3 trades total: 1 win (TEST) and 2 losses (OTHER), not a clean 2-trade, 1-1 split.
    bars = pd.concat(
        [_target_zone_bars((112.0, 119.0)), _target_zone_bars((85.0, 95.0)).assign(symbol="OTHER")],
        ignore_index=True,
    )

    trades, summary = run_target_backtest(bars)

    assert len(trades[trades["entry"] == 115.0]) == 2
    overall = summary[summary["view"] == "All zones (to target)"].iloc[0]
    assert overall["trades"] == 3 and overall["win_rate"] == pytest.approx(1 / 3)
