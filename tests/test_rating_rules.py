import pytest

from market_data_pipeline.reports.rating_rules import (
    BUY,
    HOLD,
    R_CONFIRMED,
    R_DECELERATING,
    R_MIXED_DELTA,
    R_NEAR_WALL,
    R_NO_GEX,
    R_NO_MOMENTUM,
    R_NO_STRUCTURE,
    R_REGIME,
    R_STRUCTURE_CONFLICT,
    SELL,
    UNCLASSIFIED,
    WATCH,
    rate,
)

# (name, delta_signal, regime, 4H structure, distance to call wall, rating, reason)
# Each row of the reference report's ratings (reference/Confluence_Report.xlsx, 2026-09-19 snapshot).
REFERENCE_CASES = [
    ("AMD", "ACCELERATING", "POSITIVE_GAMMA", "SIDEWAYS", 557.32 / 550 - 1, BUY, R_CONFIRMED),
    ("ETH", "ACCELERATING", "POSITIVE_GAMMA", "UPTREND", 2625.2 / 2600 - 1, BUY, R_CONFIRMED),
    ("PANW", "ACCELERATING", "POSITIVE_GAMMA", "UPTREND", 363.57 / 400 - 1, BUY, R_CONFIRMED),
    ("SMH", "ACCELERATING", "POSITIVE_GAMMA", "SIDEWAYS", 572.67 / 572.5 - 1, WATCH, R_NEAR_WALL),
    ("AAPL", "MIXED", "NEAR_FLIP", "UPTREND", 335.73 / 340 - 1, HOLD, R_MIXED_DELTA),
    ("GOOGL", "ACCELERATING", "NEAR_FLIP", "DOWNTREND", 349.23 / 375 - 1, HOLD, R_REGIME),
    ("AVGO", "ACCELERATING", "POSITIVE_GAMMA", "DOWNTREND", 357.74 / 360 - 1, HOLD, R_STRUCTURE_CONFLICT),
    ("META", "MIXED", "NEAR_FLIP", "UPTREND", 666.74 / 700 - 1, HOLD, R_MIXED_DELTA),
    ("BTC", "MIXED", "POSITIVE_GAMMA", "UPTREND", 81040.22 / 82000 - 1, HOLD, R_MIXED_DELTA),
    ("SOL", "MIXED", "NO DATA", "UPTREND", None, HOLD, R_NO_GEX),
    ("XLE", "DECELERATING", "NEGATIVE_GAMMA", "DOWNTREND", None, SELL, R_DECELERATING),
]


@pytest.mark.parametrize("name,signal,regime,structure,dist,expected,reason", REFERENCE_CASES)
def test_reproduces_reference_ratings(name, signal, regime, structure, dist, expected, reason):
    result = rate(signal, regime, structure, dist)

    assert result.rating == expected
    assert result.reason == reason


def test_watch_takes_precedence_over_buy_at_the_wall():
    assert rate("ACCELERATING", "POSITIVE_GAMMA", "SIDEWAYS", 0.005).rating == WATCH
    assert rate("ACCELERATING", "POSITIVE_GAMMA", "SIDEWAYS", 0.02).rating == BUY


def test_watch_needs_sideways_4h_so_uptrend_at_the_wall_is_buy():
    assert rate("ACCELERATING", "POSITIVE_GAMMA", "UPTREND", 0.005).rating == BUY


def test_near_wall_threshold_is_configurable():
    from market_data_pipeline.reports.rating_rules import RatingConfig

    tight = RatingConfig(near_wall_threshold=0.001)

    assert rate("ACCELERATING", "POSITIVE_GAMMA", "SIDEWAYS", 0.005, tight).rating == BUY


def test_negative_gamma_blocks_buy_but_not_sell():
    assert rate("ACCELERATING", "NEGATIVE_GAMMA", "UPTREND", 0.1).rating == HOLD
    assert rate("DECELERATING", "POSITIVE_GAMMA", "UPTREND", 0.1).rating == SELL


def test_missing_inputs_are_unclassified_not_guessed():
    assert rate(None, "POSITIVE_GAMMA", "UPTREND", 0.1) == rate("UNKNOWN", "POSITIVE_GAMMA", "UPTREND", 0.1)
    assert rate("UNKNOWN", "POSITIVE_GAMMA", "UPTREND", 0.1).rating == UNCLASSIFIED
    assert rate("UNKNOWN", "POSITIVE_GAMMA", "UPTREND", 0.1).reason == R_NO_MOMENTUM
    assert rate("ACCELERATING", "POSITIVE_GAMMA", "UNKNOWN", 0.1).rating == UNCLASSIFIED
    assert rate("ACCELERATING", "POSITIVE_GAMMA", None, 0.1).reason == R_NO_STRUCTURE
