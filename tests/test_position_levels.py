import pandas as pd
import pytest

from market_data_pipeline.reports import rating_rules as rr
from market_data_pipeline.reports.position_levels import (
    AT_ENTRY,
    CE,
    EXTENDED,
    GEX_WALL,
    NO_LEVELS,
    NO_SETUP,
    OI_CLUSTER,
    OTE,
    RANGE_HIGH,
    RESISTANCE_ONLY,
    WAIT_PULLBACK,
    SizingConfig,
    build_plan,
    position_size,
    risk_reward,
    select_target,
    setup_status,
    sizing_config_from_rules,
)


def _levels(**overrides) -> pd.Series:
    base = {
        "entry": 100.0,
        "entry_zone_low": 96.0,
        "entry_zone_high": 100.0,
        "stop": 90.0,
        "risk_pct": 0.10,
        "atr": 5.0,
        "range_ce": 95.0,
        "range_ote": 105.0,
        "range_high": 110.0,
        "nearest_smooth_high": 118.0,
        "resistance_low": 130.0,
        "resistance_high": 135.0,
        "entry_has_displacement": True,
        "entry_zone_quality": "STRONG",
    }
    base.update(overrides)
    return pd.Series(base)


def test_target_is_the_nearest_reference_above_the_floor_and_says_where_it_came_from():
    candidates = [(OTE, 105.0), (RANGE_HIGH, 110.0), (GEX_WALL, 120.0), (OI_CLUSTER, None)]

    assert select_target(100.0, candidates) == (OTE, 105.0)
    assert select_target(106.0, candidates) == (RANGE_HIGH, 110.0)  # OTE already passed
    assert select_target(125.0, candidates) == (None, None)


def test_risk_reward_needs_a_stop_below_entry():
    assert risk_reward(100.0, 90.0, 130.0) == pytest.approx(3.0)
    assert risk_reward(100.0, 100.0, 130.0) is None
    assert risk_reward(100.0, 110.0, 130.0) is None
    assert risk_reward(100.0, 90.0, None) is None


def test_size_risks_the_configured_budget_between_entry_and_stop():
    config = SizingConfig(account_size=100_000, risk_per_trade_pct=1.0, max_position_pct=100)

    # 1% of 100k = $1,000 risk; $10 of risk per share -> 100 shares.
    assert position_size(100.0, 90.0, config) == 100


def test_size_is_capped_by_the_max_position_limit():
    config = SizingConfig(account_size=100_000, risk_per_trade_pct=1.0, max_position_pct=5)

    # Risk budget alone allows 1,000 shares at $1 of risk, but 5% of the account only buys 50.
    assert position_size(100.0, 99.0, config) == 50


def test_size_is_fractional_for_assets_priced_above_the_risk_budget():
    config = SizingConfig(account_size=100_000, risk_per_trade_pct=1.0, max_position_pct=100)

    size = position_size(85_000.0, 80_000.0, config)

    assert 0 < size < 1 and size == pytest.approx(0.2, abs=1e-4)


def test_size_refuses_a_stop_that_is_not_below_entry():
    config = SizingConfig()

    assert position_size(100.0, 100.0, config) is None
    assert position_size(100.0, 110.0, config) is None
    assert position_size(None, 90.0, config) is None


def test_status_matrix():
    levels = _levels()

    assert setup_status(rr.BUY, levels, spot=98.0, max_pullback_pct=0.15) == AT_ENTRY  # inside the zone
    assert setup_status(rr.BUY, levels, spot=105.0, max_pullback_pct=0.15) == WAIT_PULLBACK
    assert setup_status(rr.BUY, levels, spot=200.0, max_pullback_pct=0.15) == EXTENDED
    assert setup_status(rr.SELL, levels, spot=105.0, max_pullback_pct=0.15) == RESISTANCE_ONLY
    assert setup_status(rr.BUY, _levels(entry=None), spot=105.0, max_pullback_pct=0.15) == NO_SETUP
    assert setup_status(rr.BUY, None, spot=105.0, max_pullback_pct=0.15) == NO_LEVELS


def test_plan_ties_entry_stop_target_and_size_together():
    plan = build_plan(
        rating=rr.BUY,
        levels=_levels(),
        spot=101.0,
        gex_target=120.0,
        gex_label=GEX_WALL,
        stretch=140.0,
        sizing=SizingConfig(account_size=100_000, risk_per_trade_pct=1.0, max_position_pct=100),
        max_pullback_pct=0.15,
    )

    assert plan.status == WAIT_PULLBACK
    assert (plan.entry, plan.stop) == (100.0, 90.0)
    assert (plan.target_source, plan.target) == (OTE, 105.0)
    assert plan.rr == pytest.approx(0.5)
    assert plan.size == 100
    assert plan.has_displacement


def test_a_target_price_has_already_passed_is_not_offered():
    # Spot 112 is above OTE (105) and the range high (110), so both are skipped for the next level up.
    def plan_at_spot_112(**level_overrides):
        return build_plan(
            rating=rr.BUY,
            levels=_levels(**level_overrides),
            spot=112.0,
            gex_target=120.0,
            gex_label=GEX_WALL,
            stretch=None,
            sizing=SizingConfig(),
            max_pullback_pct=0.15,
        )

    assert plan_at_spot_112().target_source == "SMOOTH_HIGH"  # the untested high at 118
    assert plan_at_spot_112(nearest_smooth_high=None).target_source == GEX_WALL


def test_sell_names_keep_their_levels_but_get_no_position():
    """Where support sits matters most on a name that is fading, so withhold the size, not the information."""
    plan = build_plan(
        rating=rr.SELL,
        levels=_levels(),
        spot=101.0,
        gex_target=None,
        gex_label=OI_CLUSTER,
        stretch=None,
        sizing=SizingConfig(),
        max_pullback_pct=0.15,
    )

    assert plan.status == RESISTANCE_ONLY
    assert (plan.entry, plan.stop) == (100.0, 90.0)  # levels still visible
    assert plan.zone_quality == "STRONG" and plan.stop_atr is not None
    assert plan.size is None and plan.position_usd is None and plan.risk_usd is None  # but no position
    assert (plan.resistance_low, plan.resistance_high) == (130.0, 135.0)


def test_a_name_with_no_entry_at_all_still_reports_overhead_supply():
    plan = build_plan(
        rating=rr.SELL,
        levels=_levels(entry=None, stop=None),
        spot=101.0,
        gex_target=None,
        gex_label=OI_CLUSTER,
        stretch=None,
        sizing=SizingConfig(),
        max_pullback_pct=0.15,
    )

    assert plan.entry is None and plan.size is None
    assert (plan.resistance_low, plan.resistance_high) == (130.0, 135.0)


def test_missing_levels_yield_an_empty_plan():
    plan = build_plan(
        rating=rr.BUY,
        levels=None,
        spot=101.0,
        gex_target=120.0,
        gex_label=GEX_WALL,
        stretch=None,
        sizing=SizingConfig(),
        max_pullback_pct=0.15,
    )

    assert plan.status == NO_LEVELS and plan.entry is None


def test_names_without_any_target_still_produce_a_plan():
    plan = build_plan(
        rating=rr.HOLD,
        levels=_levels(range_ote=None, range_high=None, nearest_smooth_high=None),
        spot=101.0,
        gex_target=None,
        gex_label=OI_CLUSTER,
        stretch=None,
        sizing=SizingConfig(),
        max_pullback_pct=0.15,
    )

    assert plan.entry == 100.0 and plan.target is None and plan.rr is None
    assert plan.size is not None  # sizing only needs entry and stop


def test_sizing_config_reads_only_known_keys():
    config = sizing_config_from_rules({"sizing": {"account_size": 50_000, "unrelated": 1}})

    assert config.account_size == 50_000
    assert config.risk_per_trade_pct == SizingConfig().risk_per_trade_pct


def test_ce_is_offered_as_the_conservative_first_target_before_ote():
    # Playbook: CE (50%) is the interim target to partial into, OTE (75%) the completion reference.
    plan = build_plan(
        rating=rr.BUY,
        levels=_levels(entry=90.0, stop=85.0, range_ce=95.0, range_ote=105.0),
        spot=92.0,
        gex_target=120.0,
        gex_label=GEX_WALL,
        stretch=None,
        sizing=SizingConfig(),
        max_pullback_pct=0.15,
    )

    assert (plan.target_source, plan.target) == (CE, 95.0)


def test_once_price_is_through_ce_the_target_steps_up_to_ote():
    plan = build_plan(
        rating=rr.BUY,
        levels=_levels(entry=90.0, stop=85.0, range_ce=95.0, range_ote=105.0),
        spot=97.0,
        gex_target=120.0,
        gex_label=GEX_WALL,
        stretch=None,
        sizing=SizingConfig(),
        max_pullback_pct=0.15,
    )

    assert (plan.target_source, plan.target) == (OTE, 105.0)


def test_zone_quality_flows_onto_the_plan():
    plan = build_plan(
        rating=rr.BUY, levels=_levels(entry_zone_quality="STRONG"), spot=101.0, gex_target=120.0,
        gex_label=GEX_WALL, stretch=None, sizing=SizingConfig(), max_pullback_pct=0.15,
    )

    assert plan.zone_quality == "STRONG"


def test_a_missing_zone_quality_stays_none_rather_than_the_text_nan():
    plan = build_plan(
        rating=rr.BUY, levels=_levels(entry_zone_quality=float("nan")), spot=101.0, gex_target=120.0,
        gex_label=GEX_WALL, stretch=None, sizing=SizingConfig(), max_pullback_pct=0.15,
    )

    assert plan.zone_quality is None


def test_stop_distance_in_atr_normalises_risk_across_names():
    from market_data_pipeline.reports.position_levels import stop_distance_in_atr

    # A $10 stop is 2 average days on a quiet name and half a day on a volatile one.
    assert stop_distance_in_atr(100.0, 90.0, 5.0) == pytest.approx(2.0)
    assert stop_distance_in_atr(100.0, 90.0, 20.0) == pytest.approx(0.5)
    assert stop_distance_in_atr(100.0, 90.0, None) is None
    assert stop_distance_in_atr(100.0, 90.0, 0.0) is None  # no ATR, no scale
    assert stop_distance_in_atr(100.0, 110.0, 5.0) is None  # stop above entry is not a distance


def test_plan_reports_the_stop_in_atrs():
    plan = build_plan(
        rating=rr.BUY, levels=_levels(atr=4.0), spot=101.0, gex_target=120.0, gex_label=GEX_WALL,
        stretch=None, sizing=SizingConfig(), max_pullback_pct=0.15,
    )

    assert plan.stop_atr == pytest.approx((100.0 - 90.0) / 4.0)


def test_dollars_derive_from_the_adjusted_size_and_keep_the_full_risk_baseline():
    sizing = SizingConfig(account_size=100_000, risk_per_trade_pct=1.0, max_position_pct=100)
    full = build_plan(
        rating=rr.BUY, levels=_levels(), spot=101.0, gex_target=120.0, gex_label=GEX_WALL, stretch=None,
        sizing=sizing, max_pullback_pct=0.15,
    )
    half = build_plan(
        rating=rr.BUY, levels=_levels(), spot=101.0, gex_target=120.0, gex_label=GEX_WALL, stretch=None,
        sizing=sizing, max_pullback_pct=0.15, multiplier=0.5,
    )

    assert (full.size, full.risk_usd) == (100, 1000.0)
    assert (half.size, half.risk_usd) == (50, 500.0)
    assert half.position_usd == 50 * 100.0
    assert half.full_size == 100 and half.full_risk_usd == 1000.0  # baseline survives for the Funnel tab


def test_a_hard_gated_name_shows_no_size_rather_than_a_position_of_zero():
    stopped = build_plan(
        rating=rr.BUY, levels=_levels(), spot=101.0, gex_target=120.0, gex_label=GEX_WALL, stretch=None,
        sizing=SizingConfig(), max_pullback_pct=0.15, multiplier=0.0,
    )

    assert stopped.size is None and stopped.position_usd is None and stopped.risk_usd is None
    assert stopped.full_size is not None  # what it would have been, for the Funnel tab
