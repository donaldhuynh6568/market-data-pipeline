import numpy as np
import pandas as pd
import pytest

from market_data_pipeline.transforms.location import (
    CONFIRMED,
    INSIDE_DAY_BODY_ONLY,
    INSIDE_DAY_CLASSIC,
    PLAIN,
    STRONG,
    ZONE_FAILED,
    ZONE_FILLED,
    ZONE_RESPECTED,
    ZONE_UNTESTED,
    LocationConfig,
    build_symbol_levels,
    classify_smooth_jagged,
    dealing_range,
    find_fvgs,
    location_config_from_rules,
    nearest_resistance,
    grade_zone,
    quadrants,
    inside_day_type,
    select_entry_zone,
    volume_ratio_at,
)
from market_data_pipeline.transforms.swing_structure import find_swing_points


def _bars(
    ohlc: list[tuple[float, float]],
    symbol: str = "TEST",
    bodies: float = 0.2,
    volumes: list[float] | None = None,
) -> pd.DataFrame:
    """Daily bars from (low, high) pairs. Bodies sit inside the range so nothing trips the displacement test,
    and volume is flat by default so nothing trips volume confirmation either."""
    lows = [low for low, _ in ohlc]
    highs = [high for _, high in ohlc]
    opens = [low + (1 - bodies) / 2 * (high - low) for low, high in ohlc]
    closes = [low + (1 + bodies) / 2 * (high - low) for low, high in ohlc]
    return pd.DataFrame(
        {
            "symbol": symbol,
            "timestamp": pd.date_range("2026-09-01", periods=len(ohlc), freq="D", tz="UTC"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes if volumes is not None else [1_000.0] * len(ohlc),
        }
    )


def test_bullish_gap_has_the_zone_between_candles_1_and_3_and_stops_at_candle_1():
    # candle 1 high 100 < candle 3 low 110, so the zone is 100-110 and the stop is candle 1's low.
    zones = find_fvgs(_bars([(95.0, 100.0), (102.0, 108.0), (110.0, 115.0), (111.0, 116.0)]))

    bull = zones[zones["kind"] == "bull"].iloc[0]
    assert (bull["zone_low"], bull["zone_high"]) == (100.0, 110.0)
    assert bull["candle1_stop"] == 95.0  # candle 1's low, not the zone's own boundary
    assert not bull["filled"]


def test_bearish_gap_is_the_mirror():
    zones = find_fvgs(_bars([(110.0, 115.0), (102.0, 108.0), (95.0, 100.0), (94.0, 99.0)]))

    bear = zones[zones["kind"] == "bear"].iloc[0]
    assert (bear["zone_low"], bear["zone_high"]) == (100.0, 110.0)
    assert bear["candle1_stop"] == 115.0  # candle 1's high


def test_overlapping_candles_produce_no_zone():
    zones = find_fvgs(_bars([(95.0, 105.0), (100.0, 110.0), (104.0, 112.0), (106.0, 114.0)]))

    assert zones.empty


def test_a_later_bar_trading_through_fills_the_zone_but_dipping_in_leaves_it_live():
    partial = _bars([(95.0, 100.0), (102.0, 108.0), (110.0, 115.0), (105.0, 112.0)])  # dips into 100-110
    through = _bars([(95.0, 100.0), (102.0, 108.0), (110.0, 115.0), (98.0, 112.0)])  # trades below 100

    assert not find_fvgs(partial).iloc[0]["filled"]
    assert find_fvgs(through).iloc[0]["filled"]


def test_displacement_needs_a_big_body_that_breaks_the_recent_extreme():
    # Six quiet bars, then a wide-bodied candle that gaps price higher.
    quiet = [(100.0, 101.0)] * 6
    run = quiet + [(101.0, 108.0), (109.0, 112.0), (110.0, 113.0)]
    config = LocationConfig(atr_window=3, displacement_atr_mult=0.8)

    wide = find_fvgs(_bars(run, bodies=0.9), config)
    narrow = find_fvgs(_bars(run, bodies=0.05), config)

    assert wide[wide["kind"] == "bull"]["has_displacement"].any()
    assert not narrow[narrow["kind"] == "bull"]["has_displacement"].any()


def test_quadrants_grade_the_range_into_ce_and_ote():
    grid = quadrants(100.0, 200.0)

    assert grid["range_ce"] == 150.0
    assert grid["range_ote"] == 175.0
    assert grid["range_q1"] == 125.0


def test_dealing_range_uses_the_last_confirmed_swings_and_needs_both():
    swings = pd.DataFrame(
        {
            "symbol": "TEST",
            "timestamp": pd.to_datetime(["2026-09-01", "2026-09-05", "2026-09-09"], utc=True),
            "price": [90.0, 150.0, 110.0],
            "kind": ["swing_low", "swing_high", "swing_low"],
        }
    )

    assert dealing_range(swings)["range_ce"] == 130.0  # last low 110, last high 150
    assert dealing_range(swings[swings["kind"] == "swing_low"]) is None


def test_a_swing_level_stays_smooth_until_a_later_bar_trades_through_it():
    bars = _bars([(95.0, 100.0), (90.0, 120.0), (96.0, 101.0), (97.0, 102.0), (98.0, 103.0)])
    swings = pd.DataFrame(
        {
            "symbol": "TEST",
            "timestamp": pd.to_datetime(["2026-09-01", "2026-09-05"], utc=True),
            "price": [100.0, 103.0],
            "kind": ["swing_high", "swing_high"],
        }
    )

    states = classify_smooth_jagged(swings, bars).set_index("price")["state"]

    assert states[100.0] == "JAGGED"  # a later bar reached 120
    assert states[103.0] == "SMOOTH"  # the last bar's high; nothing after it went higher


def test_entry_prefers_quality_only_among_zones_within_reach():
    spot = 100.0
    zones = pd.DataFrame(
        [
            {"kind": "bull", "zone_low": 96.0, "zone_high": 98.0, "filled": False, "has_displacement": False,
             "zone_quality": PLAIN, "distance_pct": -0.02, "formed_at": pd.Timestamp("2026-09-10")},
            {"kind": "bull", "zone_low": 70.0, "zone_high": 72.0, "filled": False, "has_displacement": True,
             "zone_quality": STRONG, "distance_pct": -0.28, "formed_at": pd.Timestamp("2026-09-05")},
        ]
    )

    # The textbook zone 28% down must not beat a workable one 2% down.
    assert select_entry_zone(zones, spot, LocationConfig())["zone_high"] == 98.0


def test_entry_falls_back_to_the_nearest_zone_when_none_are_in_band():
    zones = pd.DataFrame(
        [
            {"kind": "bull", "zone_low": 70.0, "zone_high": 72.0, "filled": False, "has_displacement": False,
             "distance_pct": -0.28, "formed_at": pd.Timestamp("2026-09-05")},
        ]
    )

    assert select_entry_zone(zones, 100.0, LocationConfig())["zone_high"] == 72.0


def test_filled_zones_and_zones_above_spot_are_never_entries():
    zones = pd.DataFrame(
        [
            {"kind": "bull", "zone_low": 96.0, "zone_high": 98.0, "filled": True, "has_displacement": False,
             "distance_pct": -0.02, "formed_at": pd.Timestamp("2026-09-10")},
            {"kind": "bull", "zone_low": 105.0, "zone_high": 108.0, "filled": False, "has_displacement": False,
             "distance_pct": 0.08, "formed_at": pd.Timestamp("2026-09-11")},
        ]
    )

    assert select_entry_zone(zones, 100.0, LocationConfig()) is None


def test_nearest_resistance_is_the_lowest_live_bearish_zone_above_spot():
    zones = pd.DataFrame(
        [
            {"kind": "bear", "zone_low": 120.0, "zone_high": 125.0, "filled": False},
            {"kind": "bear", "zone_low": 105.0, "zone_high": 108.0, "filled": False},
            {"kind": "bear", "zone_low": 102.0, "zone_high": 104.0, "filled": True},
        ]
    )

    assert nearest_resistance(zones, 100.0)["zone_low"] == 105.0
    assert nearest_resistance(zones, 200.0) is None


def _stacked_meta_bars() -> pd.DataFrame:
    """Bars that reproduce the three stacked META support zones in reference/Rotation_Positions_Tracker.xlsx.

    The tracker records META support as "617.46-671.91 (stacked, 3 zones)"; these bars yield exactly those
    three bullish gaps, which is the check that the detector matches the hand-built reference.
    """
    return _bars(
        [
            (605.27, 617.46),
            (609.74, 624.80),
            (638.56, 650.00),
            (642.14, 672.00),
            (649.22, 668.60),
            (670.00, 680.00),
            (671.91, 690.00),
            (675.00, 700.00),
            (690.00, 725.00),
        ],
        symbol="META",
    )


def test_reproduces_the_reference_trackers_stacked_meta_zones():
    zones = find_fvgs(_stacked_meta_bars())

    live = zones[(zones["kind"] == "bull") & (~zones["filled"])]
    spans = sorted(zip(live["zone_low"].round(2), live["zone_high"].round(2)))
    assert spans == [(617.46, 638.56), (624.80, 642.14), (668.60, 671.91)]
    assert min(live["zone_low"]) == 617.46 and max(live["zone_high"]) == 671.91


def test_levels_row_takes_entry_from_the_highest_live_zone():
    row, zones = build_symbol_levels(_stacked_meta_bars())

    assert row["entry"] == 671.91  # the near edge of the nearest support
    assert row["stop"] == 649.22  # candle 1 of that gap
    assert row["risk_pct"] == pytest.approx((671.91 - 649.22) / 671.91)
    assert row["n_support_zones"] == 3
    assert not zones.empty


def test_short_history_gives_no_setup_rather_than_an_error():
    row, zones = build_symbol_levels(_bars([(95.0, 100.0), (96.0, 101.0)]))

    assert zones.empty
    assert row["entry"] is None or np.isnan(row["entry"])
    assert row["bars_available"] == 2


def test_swing_helper_is_reused_unchanged():
    bars = _stacked_meta_bars()

    assert not find_swing_points(bars, window=1).empty


def test_config_reads_only_known_keys_from_the_rules_file():
    config = location_config_from_rules({"location": {"lookback_days": 40, "unrelated": "ignored"}})

    assert config.lookback_days == 40
    assert config.swing_window == LocationConfig().swing_window


def test_volume_ratio_uses_a_trailing_baseline_that_excludes_the_candle_itself():
    volume = np.array([100.0] * 5 + [300.0])

    # Baseline is the prior 5 bars only; including the 300 would drag the average up and understate the spike.
    assert volume_ratio_at(volume, 5, window=5) == pytest.approx(3.0)
    assert np.isnan(volume_ratio_at(volume, 3, window=5))  # not enough history yet
    assert np.isnan(volume_ratio_at(None, 5, window=5))  # bars carry no volume at all


def test_grade_zone_matrix():
    assert grade_zone(True, True) == STRONG
    assert grade_zone(True, False) == CONFIRMED
    assert grade_zone(False, True) == CONFIRMED
    assert grade_zone(False, False) == PLAIN


# One bullish gap at index 3 (high[2]=100 below low[4]=110); the leading bars overlap so they add no others.
_VOLUME_PRICES = [(90.0, 103.0), (90.0, 103.0), (95.0, 100.0), (102.0, 108.0), (110.0, 115.0), (107.0, 116.0)]


def test_a_gap_on_heavy_volume_is_confirmed_and_a_quiet_one_is_plain():
    config = LocationConfig(volume_baseline_window=2, volume_confirm_mult=1.5)

    heavy = find_fvgs(_bars(_VOLUME_PRICES, volumes=[100.0, 100.0, 100.0, 400.0, 100.0, 100.0]), config)
    quiet = find_fvgs(_bars(_VOLUME_PRICES, volumes=[100.0] * 6), config)

    heavy_zone = heavy.iloc[0]
    assert heavy_zone["volume_ratio"] == pytest.approx(4.0)  # 400 against the prior two bars, not itself
    assert heavy_zone["has_volume_confirmation"]
    assert heavy_zone["zone_quality"] == CONFIRMED  # volume only, no price displacement
    assert quiet.iloc[0]["zone_quality"] == PLAIN


def test_zones_without_enough_volume_history_grade_plain_rather_than_raising():
    zones = find_fvgs(_bars(_VOLUME_PRICES))

    assert np.isnan(zones.iloc[0]["volume_ratio"])  # default 20-bar window, only 6 bars
    assert zones.iloc[0]["zone_quality"] == PLAIN


def test_bars_without_a_volume_column_still_produce_zones():
    bars = _bars(_VOLUME_PRICES).drop(columns="volume")

    zones = find_fvgs(bars)

    assert len(zones) == 1
    assert np.isnan(zones.iloc[0]["volume_ratio"]) and zones.iloc[0]["zone_quality"] == PLAIN


def _zone(zone_high, quality, distance_pct):
    return {
        "kind": "bull", "zone_low": zone_high - 2.0, "zone_high": zone_high, "filled": False,
        "has_displacement": quality == STRONG, "zone_quality": quality, "distance_pct": distance_pct,
        "formed_at": pd.Timestamp("2026-09-10"),
    }


def test_entry_prefers_the_best_quality_zone_in_band_over_a_nearer_weaker_one():
    zones = pd.DataFrame([_zone(98.0, PLAIN, -0.02), _zone(94.0, CONFIRMED, -0.06), _zone(90.0, STRONG, -0.10)])

    assert select_entry_zone(zones, 100.0, LocationConfig())["zone_high"] == 90.0  # STRONG wins

    without_strong = pd.DataFrame([_zone(98.0, PLAIN, -0.02), _zone(94.0, CONFIRMED, -0.06)])
    assert select_entry_zone(without_strong, 100.0, LocationConfig())["zone_high"] == 94.0

    only_plain = pd.DataFrame([_zone(98.0, PLAIN, -0.02), _zone(94.0, PLAIN, -0.06)])
    assert select_entry_zone(only_plain, 100.0, LocationConfig())["zone_high"] == 98.0  # nearest


def test_quality_is_ignored_for_zones_out_of_reach():
    # Everything is beyond max_pullback_pct, so proximity decides and the STRONG zone far below does not win.
    zones = pd.DataFrame([_zone(70.0, PLAIN, -0.30), _zone(50.0, STRONG, -0.50)])

    assert select_entry_zone(zones, 100.0, LocationConfig())["zone_high"] == 70.0


def test_levels_row_carries_the_entry_zone_quality():
    row, _ = build_symbol_levels(_stacked_meta_bars())

    assert row["entry_zone_quality"] in {STRONG, CONFIRMED, PLAIN}


def _ohlc(rows: list[tuple[float, float, float, float]], symbol: str = "TEST") -> pd.DataFrame:
    """Rows of (open, high, low, close), for tests that need independent control over the body vs. the wick."""
    return pd.DataFrame(
        {
            "symbol": symbol,
            "timestamp": pd.date_range("2026-09-01", periods=len(rows), freq="D", tz="UTC"),
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [1_000.0] * len(rows),
        }
    )


def test_zone_status_is_untested_when_price_never_trades_back_into_it():
    # Same bars as the plain bullish-gap test: only one bar follows formation, and it stays above the zone.
    zones = find_fvgs(_bars([(95.0, 100.0), (102.0, 108.0), (110.0, 115.0), (111.0, 116.0)]))

    assert zones[zones["kind"] == "bull"].iloc[0]["zone_status"] == ZONE_UNTESTED


def test_zone_status_is_respected_when_a_later_body_holds_above_the_midpoint():
    # Zone is 100 (=high of bar0) to 110 (=low of bar2), midpoint 105. The extra bar wicks down to a low of
    # 104 - into the zone, below the midpoint - but its BODY (open 109, close 106) stays above the midpoint,
    # which is exactly the "wick may pierce, the body must hold" test.
    bars = _ohlc([
        (96.0, 100.0, 95.0, 99.0),    # bar0: pre-imbalance candle; its high (100) is the zone's low edge
        (103.0, 108.0, 102.0, 106.0),  # bar1: the imbalance candle
        (112.0, 115.0, 110.0, 113.0),  # bar2: closes the gap; its low (110) is the zone's high edge
        (109.0, 112.0, 104.0, 106.0),  # bar3: wicks into the zone, body stays above midpoint (105)
    ])

    zone = find_fvgs(bars)
    bull = zone[zone["kind"] == "bull"].iloc[0]
    assert (bull["zone_low"], bull["zone_high"]) == (100.0, 110.0)
    assert not bull["filled"]  # low 104 never reached zone_low (100)
    assert bull["zone_status"] == ZONE_RESPECTED


def test_zone_status_is_failed_when_a_later_body_closes_through_the_midpoint():
    # Same zone (100-110, midpoint 105): the extra bar's low (100.5) still does not fill it (never reaches
    # zone_low=100), but its close (103.8, below the 105 midpoint) shows the body already breaching the
    # midpoint - a softer, earlier failure signal than `filled`.
    bars = _ohlc([
        (96.0, 100.0, 95.0, 99.0),
        (103.0, 108.0, 102.0, 106.0),
        (112.0, 115.0, 110.0, 113.0),
        (102.5, 106.0, 100.5, 103.8),
    ])

    zone = find_fvgs(bars)
    bull = zone[zone["kind"] == "bull"].iloc[0]
    assert not bull["filled"]
    assert bull["zone_status"] == ZONE_FAILED


def test_zone_status_is_filled_once_the_far_edge_is_fully_cleared():
    through = _bars([(95.0, 100.0), (102.0, 108.0), (110.0, 115.0), (98.0, 112.0)])

    assert find_fvgs(through).iloc[0]["zone_status"] == ZONE_FILLED


def test_inside_day_classic_when_the_whole_bar_sits_inside_the_prior_one():
    bars = _ohlc([(105.0, 110.0, 100.0, 107.0), (103.0, 108.0, 102.0, 105.0)])

    assert inside_day_type(bars) == INSIDE_DAY_CLASSIC


def test_inside_day_body_only_when_only_the_body_is_contained():
    # Today's wicks (98-112) poke outside yesterday's range (100-110), but the body (104-106) sits inside it.
    bars = _ohlc([(105.0, 110.0, 100.0, 107.0), (104.0, 112.0, 98.0, 106.0)])

    assert inside_day_type(bars) == INSIDE_DAY_BODY_ONLY


def test_inside_day_none_when_neither_the_range_nor_the_body_is_contained():
    bars = _ohlc([(105.0, 110.0, 100.0, 107.0), (111.0, 116.0, 109.0, 115.0)])

    assert inside_day_type(bars) is None


def test_inside_day_needs_at_least_two_bars():
    assert inside_day_type(_ohlc([(105.0, 110.0, 100.0, 107.0)])) is None


def test_levels_row_carries_inside_day():
    row, _ = build_symbol_levels(_stacked_meta_bars())

    assert row["inside_day"] in (None, INSIDE_DAY_CLASSIC, INSIDE_DAY_BODY_ONLY)
