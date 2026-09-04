import pandas as pd

from market_data_pipeline.transforms.swing_structure import (
    classify_symbol_structure,
    compute_swing_structure,
    find_swing_points,
)


def _bars(symbol, highs, lows):
    return pd.DataFrame(
        {
            "symbol": symbol,
            "timestamp": pd.date_range("2026-01-01", periods=len(highs), freq="h"),
            "high": highs,
            "low": lows,
        }
    )


def _points(rows):
    return pd.DataFrame(rows)


def test_find_swing_points_identifies_confirmed_highs_and_lows():
    # Hand-verified: with window=2, only index 3 (15), 6 (8), and 8 (16) qualify —
    # each is a strict extreme over 2 bars on both sides.
    highs = [10, 11, 12, 15, 12, 11, 10, 13, 16, 12, 11]
    lows = [9, 10, 11, 12, 10, 9, 8, 11, 14, 10, 9]
    df = _bars("TEST", highs, lows)

    points = find_swing_points(df, window=2)

    assert points[points["kind"] == "swing_high"]["price"].tolist() == [15, 16]
    assert points[points["kind"] == "swing_low"]["price"].tolist() == [8]


def test_find_swing_points_returns_empty_for_empty_input():
    df = pd.DataFrame(columns=["symbol", "timestamp", "high", "low"])

    assert find_swing_points(df).empty


def test_find_swing_points_confirms_nothing_within_window_of_the_edges():
    highs = [10, 20, 10]
    lows = [9, 5, 9]
    df = _bars("TEST", highs, lows)

    points = find_swing_points(df, window=2)

    assert points.empty


def test_classify_symbol_structure_uptrend_on_higher_highs_and_higher_lows():
    points = _points(
        [
            {"timestamp": "2026-01-01", "price": 100, "kind": "swing_low"},
            {"timestamp": "2026-01-02", "price": 110, "kind": "swing_high"},
            {"timestamp": "2026-01-03", "price": 105, "kind": "swing_low"},
            {"timestamp": "2026-01-04", "price": 115, "kind": "swing_high"},
        ]
    )

    result = classify_symbol_structure(points)

    assert result["structure"] == "UPTREND"
    assert result["last_swing_high"] == 115
    assert result["prior_swing_high"] == 110
    assert result["last_swing_low"] == 105
    assert result["prior_swing_low"] == 100


def test_classify_symbol_structure_downtrend_on_lower_highs_and_lower_lows():
    points = _points(
        [
            {"timestamp": "2026-01-01", "price": 110, "kind": "swing_high"},
            {"timestamp": "2026-01-02", "price": 100, "kind": "swing_low"},
            {"timestamp": "2026-01-03", "price": 105, "kind": "swing_high"},
            {"timestamp": "2026-01-04", "price": 95, "kind": "swing_low"},
        ]
    )

    result = classify_symbol_structure(points)

    assert result["structure"] == "DOWNTREND"


def test_classify_symbol_structure_sideways_on_mixed_signal():
    points = _points(
        [
            {"timestamp": "2026-01-01", "price": 100, "kind": "swing_low"},
            {"timestamp": "2026-01-02", "price": 110, "kind": "swing_high"},
            {"timestamp": "2026-01-03", "price": 105, "kind": "swing_low"},  # higher low
            {"timestamp": "2026-01-04", "price": 108, "kind": "swing_high"},  # lower high
        ]
    )

    result = classify_symbol_structure(points)

    assert result["structure"] == "SIDEWAYS"


def test_classify_symbol_structure_unknown_with_insufficient_swing_points():
    points = _points(
        [
            {"timestamp": "2026-01-01", "price": 100, "kind": "swing_low"},
            {"timestamp": "2026-01-02", "price": 110, "kind": "swing_high"},
        ]
    )

    result = classify_symbol_structure(points)

    assert result["structure"] == "UNKNOWN"
    assert result["last_swing_high"] is None


def test_compute_swing_structure_produces_one_row_per_symbol():
    highs = [10, 11, 12, 15, 12, 11, 10, 13, 16, 12, 11]
    lows = [9, 10, 11, 12, 10, 9, 8, 11, 14, 10, 9]
    df = pd.concat([_bars("AAA", highs, lows), _bars("BBB", highs, lows)], ignore_index=True)

    result = compute_swing_structure(df, window=2)

    assert sorted(result["symbol"].tolist()) == ["AAA", "BBB"]
    # Only 1 swing low is confirmed in this fixture (see test above), so
    # classification can't be determined yet — UNKNOWN is the correct, expected result.
    assert (result["structure"] == "UNKNOWN").all()
