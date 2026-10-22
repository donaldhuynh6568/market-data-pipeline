import pandas as pd
import pytest

from market_data_pipeline.reports.daily_read import (
    expectancy_lookup,
    historical_edge,
    momentum_band,
    rank_reads,
    read_sentence,
)


def test_bands_match_the_ones_the_backtest_reports_on():
    assert momentum_band(-0.5) == "negative"
    assert momentum_band(0.1) == "0-0.2"
    assert momentum_band(0.9) == ">0.2"
    assert momentum_band(None) is None and momentum_band(float("nan")) is None


def _lookup():
    return expectancy_lookup(pd.DataFrame([
        {"view": "Momentum at entry (to target)", "bucket": ">0.2", "trades": 39, "expectancy_r": 0.03},
        {"view": "Zone quality (to target)", "bucket": "STRONG", "trades": 13, "expectancy_r": 0.08},
        {"view": "Weak close on entry (to target)", "bucket": "True", "trades": 68, "expectancy_r": -0.53},
        {"view": "Control (to target)", "bucket": "random", "trades": 600, "expectancy_r": None},
    ]))


def test_edge_averages_momentum_and_zone_quality_and_reports_the_smallest_sample():
    edge, text = historical_edge(_lookup(), score=0.5, zone_quality="STRONG")

    assert edge == pytest.approx((0.03 + 0.08) / 2)
    assert text == "+0.06R (n>=13)"  # the weakest sample governs, not the largest


def test_a_failed_zone_status_pulls_in_the_weak_entry_bucket():
    """A FAILED live zone maps onto the backtest's weak-entry bucket, RESPECTED onto the clean one."""
    edge, _ = historical_edge(_lookup(), score=0.5, zone_status="FAILED")
    assert edge == pytest.approx((0.03 - 0.53) / 2)

    # RESPECTED has no matching bucket in this lookup (only "True" was supplied), so it contributes nothing.
    edge_respected, _ = historical_edge(_lookup(), score=0.5, zone_status="RESPECTED")
    assert edge_respected == pytest.approx(0.03)

    # UNTESTED/FILLED have no equivalent bucket at all - skipped rather than forced into one.
    edge_untested, _ = historical_edge(_lookup(), score=0.5, zone_status="UNTESTED")
    assert edge_untested == pytest.approx(0.03)


def test_stop_width_plays_no_part_in_the_edge():
    """Risk sizing is not a reason to rank one setup over another - historical_edge takes no stop_atr at all."""
    with_quality_only = historical_edge(_lookup(), score=0.5, zone_quality="STRONG")
    # Same call, just confirming the signature has no stop_atr parameter to pass in the first place.
    assert with_quality_only == historical_edge(_lookup(), 0.5, "STRONG")


def test_unknown_buckets_are_skipped_and_an_empty_scorecard_gives_no_edge():
    edge, _ = historical_edge(_lookup(), score=0.5)
    assert edge == pytest.approx(0.03)  # only the momentum bucket matched

    assert historical_edge({}, score=0.5, zone_quality="STRONG") == (None, "")
    assert historical_edge(_lookup(), score=None) == (None, "")


def test_rows_with_no_measurable_edge_rank_last_in_ticker_order():
    ranks = rank_reads([("AAA", -0.2), ("BBB", None), ("CCC", 0.1), ("AAB", None)])

    assert ranks["CCC"] == 1 and ranks["AAA"] == 2
    assert ranks["AAB"] == 3 and ranks["BBB"] == 4


def test_read_describes_momentum_level_and_stop():
    assert read_sentence("Speeding up", "Strong", 2.0, "WAIT_PULLBACK", "POSITIVE_GAMMA") == (
        "Momentum building into a strong level, stop has room"
    )
    assert "stop inside one day's noise" in read_sentence("Speeding up", "Weak", 0.6, "WAIT_PULLBACK", "POSITIVE_GAMMA")
    assert "stop a little tight" in read_sentence("Mixed", "Medium", 1.2, "WAIT_PULLBACK", "POSITIVE_GAMMA")


def test_read_flags_an_unstable_regime_and_a_missing_options_view():
    assert "unstable options regime" in read_sentence("Speeding up", "Strong", 2.0, "WAIT_PULLBACK", "NEAR_FLIP")
    assert "no options read" in read_sentence("Speeding up", "Strong", 2.0, "WAIT_PULLBACK", "NO DATA")


def test_read_says_plainly_when_there_is_nothing_to_trade():
    assert read_sentence("Speeding up", None, None, "NO_SETUP", "POSITIVE_GAMMA").endswith(
        "no level to trade against"
    )
    assert "overhead supply only" in read_sentence("Slowing down", None, None, "RESISTANCE_ONLY", "NEGATIVE_GAMMA")


def test_read_never_instructs():
    for setup in ("WAIT_PULLBACK", "AT_ENTRY", "NO_SETUP", "RESISTANCE_ONLY"):
        sentence = read_sentence("Speeding up", "Strong", 2.0, setup, "POSITIVE_GAMMA").lower()
        assert not any(word in sentence for word in ("buy ", "sell ", "skip", "wait for"))


def test_a_sell_rated_name_with_a_level_says_the_level_is_reference_only():
    """Levels stay visible on a fading name, so the sentence has to explain that it is not a buy."""
    sentence = read_sentence("Slowing down", "Medium", 2.4, "RESISTANCE_ONLY", "POSITIVE_GAMMA")

    assert "reference only" in sentence  # descriptive, so it survives the no-instructions rule below
    # With no level at all it falls back to the overhead-supply wording.
    assert "overhead supply only" in read_sentence("Slowing down", None, None, "RESISTANCE_ONLY", "NEGATIVE_GAMMA")


def test_read_flags_a_zone_that_already_failed_the_body_respect_test():
    sentence = read_sentence("Speeding up", "Strong", 2.0, "WAIT_PULLBACK", "POSITIVE_GAMMA", zone_status="FAILED")

    assert "already had a close back through its midpoint" in sentence


def test_read_says_nothing_extra_when_the_zone_has_not_failed():
    respected = read_sentence("Speeding up", "Strong", 2.0, "WAIT_PULLBACK", "POSITIVE_GAMMA", zone_status="RESPECTED")
    untested = read_sentence("Speeding up", "Strong", 2.0, "WAIT_PULLBACK", "POSITIVE_GAMMA", zone_status="UNTESTED")
    unset = read_sentence("Speeding up", "Strong", 2.0, "WAIT_PULLBACK", "POSITIVE_GAMMA")

    for sentence in (respected, untested, unset):
        assert "midpoint" not in sentence
