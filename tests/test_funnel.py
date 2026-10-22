import pytest

from market_data_pipeline.reports import funnel as fn
from market_data_pipeline.reports import position_levels as pl
from market_data_pipeline.reports import rating_rules as rr


def _inputs(**overrides) -> fn.FunnelInputs:
    """A name that clears every gate, so each test only varies the one thing it is about."""
    base = dict(
        delta_signal="ACCELERATING",
        score=0.5,  # top momentum tier, so the baseline is full size
        rating=rr.BUY,
        regime="POSITIVE_GAMMA",
        setup=pl.WAIT_PULLBACK,
        zone_quality="STRONG",
        stop_atr=2.5,
        rr=2.0,
        has_insider_flag=False,
        has_news_flag=False,
    )
    base.update(overrides)
    return fn.FunnelInputs(**base)


def test_a_clean_name_clears_every_stage_at_full_size():
    result = fn.evaluate(_inputs())

    assert (result.stage, result.action, result.multiplier) == (fn.CLEARED, fn.FULL, 1.0)
    assert result.demerits == []
    assert set(result.gates.values()) == {fn.PASS}


# Each row is a real combination from the 2026-09-24 report.
@pytest.mark.parametrize(
    "name,overrides,stage,action",
    [
        ("XLE", {"delta_signal": "DECELERATING", "rating": rr.SELL}, fn.DELTA, fn.SKIP),
        ("XLE by rating alone", {"rating": rr.SELL}, fn.RATING, fn.SKIP),
        ("META (HOLD)", {"rating": rr.HOLD}, fn.RATING, fn.WATCH),
        ("MSFT (nothing to enter)", {"setup": pl.NO_SETUP}, fn.SETUP, fn.SKIP),
        ("a SELL with resistance only", {"setup": pl.RESISTANCE_ONLY}, fn.SETUP, fn.SKIP),
        ("no location data", {"setup": pl.NO_LEVELS}, fn.SETUP, fn.SKIP),
        ("too far below the zone", {"setup": pl.EXTENDED}, fn.SETUP, fn.WATCH),
    ],
)
def test_hard_gates_stop_a_name_and_record_where(name, overrides, stage, action):
    result = fn.evaluate(_inputs(**overrides))

    assert (result.stage, result.action) == (stage, action), name
    assert result.multiplier == 0.0
    assert result.gates[stage] == fn.FAIL


def test_a_hard_gate_leaves_later_stages_unevaluated():
    result = fn.evaluate(_inputs(rating=rr.HOLD))

    assert result.gates[fn.DELTA] == fn.PASS
    assert result.gates[fn.RATING] == fn.FAIL
    # Everything downstream is not applicable, not a pass - the name never got there.
    assert {result.gates[s] for s in (fn.REGIME, fn.SETUP, fn.ZONE, fn.STOP, fn.RR, fn.FLAGS)} == {fn.NA}


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"stop_atr": 0.8}, "stop 0.8 ATRs"),
        ({"rr": 0.5}, "R:R 0.50"),
        ({"has_insider_flag": True}, "insider selling"),
        ({"has_news_flag": True}, "news"),
    ],
)
def test_one_demerit_halves_the_size_without_stopping_the_name(overrides, reason):
    result = fn.evaluate(_inputs(**overrides))

    assert result.stage == fn.CLEARED  # demerits never override an earlier stage
    assert (result.action, result.multiplier) == (fn.HALF, 0.5)
    assert any(reason in d for d in result.demerits)


def test_zone_quality_no_longer_changes_size():
    """Crossed against momentum quintiles on 3,943 trades it does not order consistently, so it must not size."""
    strong = fn.evaluate(_inputs(zone_quality="STRONG"))
    plain = fn.evaluate(_inputs(zone_quality="PLAIN"))

    assert strong.multiplier == plain.multiplier == 1.0
    assert plain.demerits == []
    assert plain.gates[fn.ZONE] == fn.PASS  # still recorded, just not penalised


def test_momentum_sets_the_base_size_tier():
    # Measured: >0.2 returned +0.210R, 0-0.2 +0.028R, negative -0.194R.
    assert fn.evaluate(_inputs(score=0.5)).action == fn.FULL
    assert fn.evaluate(_inputs(score=0.1)).action == fn.HALF
    assert fn.evaluate(_inputs(score=-0.3)).action == fn.QUARTER
    assert fn.momentum_tier(None) == 0.5  # unknown is the middle tier, never the best


def test_a_weak_momentum_name_says_so_in_its_demerits():
    result = fn.evaluate(_inputs(score=-0.3))

    assert any("momentum -0.30" in d for d in result.demerits)


def test_amd_today_carries_a_thin_ratio_and_an_insider_cluster():
    # Top-tier momentum, stop 2.4 ATRs, but R:R 0.52 and 75 insider sells.
    result = fn.evaluate(_inputs(score=0.5, zone_quality="STRONG", stop_atr=2.4, rr=0.52, has_insider_flag=True))

    assert result.stage == fn.CLEARED
    assert (result.action, result.multiplier) == (fn.QUARTER, 0.25)
    assert result.demerits == ["R:R 0.52", "insider selling"]


def test_demerits_compound_and_stop_at_the_floor():
    result = fn.evaluate(
        _inputs(score=-0.3, stop_atr=0.5, rr=0.1, has_insider_flag=True, has_news_flag=True)
    )

    assert result.multiplier == pytest.approx(0.125)  # floored, not driven to zero
    assert result.action == fn.MINIMAL
    assert len(result.demerits) == 5  # weak momentum, tight stop, thin R:R, insider, news


def test_rr_ranks_but_never_rejects():
    # The whole point: a thin ratio on an otherwise clean setup is a smaller trade, not a forbidden one.
    result = fn.evaluate(_inputs(rr=0.01))

    assert result.action == fn.HALF and result.is_actionable


def test_missing_measurements_are_not_counted_against_a_name():
    result = fn.evaluate(_inputs(zone_quality=None, stop_atr=None, rr=None))

    assert result.multiplier == 1.0 and result.action == fn.FULL
    assert {result.gates[s] for s in (fn.ZONE, fn.STOP, fn.RR)} == {fn.NA}


def test_unstable_regime_is_a_demerit_not_a_gate():
    result = fn.evaluate(_inputs(regime="NEAR_FLIP"))

    assert result.stage == fn.CLEARED and result.action == fn.HALF
    assert result.gates[fn.REGIME] == fn.FAIL


def test_action_tiers():
    assert fn.action_for(1.0) == fn.FULL
    assert fn.action_for(0.5) == fn.HALF
    assert fn.action_for(0.25) == fn.QUARTER
    assert fn.action_for(0.125) == fn.MINIMAL


def test_thresholds_are_configurable():
    lenient = fn.FunnelConfig(min_rr=0.5, min_stop_atr=0.5)

    assert fn.evaluate(_inputs(rr=0.6, stop_atr=0.6), lenient).action == fn.FULL
    assert fn.evaluate(_inputs(rr=0.6, stop_atr=0.6)).action == fn.QUARTER  # defaults fail both


def test_the_stop_threshold_defaults_to_one_atr():
    """1.0-2.5 ATR measured flat, so only sub-1-ATR stops are penalised."""
    assert fn.FunnelConfig().min_stop_atr == 1.0
    assert fn.evaluate(_inputs(stop_atr=1.2)).demerits == []
    assert fn.evaluate(_inputs(stop_atr=0.9)).demerits != []


def test_config_falls_back_to_the_location_stop_threshold():
    config = fn.funnel_config_from_rules({"location": {"min_stop_atr": 2.0}})

    assert config.min_stop_atr == 2.0  # one threshold, not two that can drift apart
    assert (
        fn.funnel_config_from_rules({"funnel": {"min_stop_atr": 3.0}, "location": {"min_stop_atr": 2.0}}).min_stop_atr
        == 3.0
    )


def test_dollar_helpers():
    assert pl.position_dollars(100.0, 25) == 2500.0
    assert pl.risk_dollars(100.0, 90.0, 25) == 250.0
    assert pl.position_dollars(85_000.0, 0.2) == pytest.approx(17_000.0)  # fractional crypto units
    assert pl.risk_dollars(100.0, 110.0, 25) is None  # stop above entry is not a risk
    assert pl.position_dollars(None, 25) is None and pl.risk_dollars(100.0, 90.0, None) is None
