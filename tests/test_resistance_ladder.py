import pandas as pd
import pytest

from market_data_pipeline.reports.resistance_ladder import (
    TargetConfig,
    call_oi_by_strike,
    ladder_table,
    select_targets,
)


def _series(oi_by_strike: dict[float, float]) -> pd.Series:
    return pd.Series(oi_by_strike, dtype=float).sort_index()


def test_call_oi_by_strike_sums_across_expiries_and_ignores_puts_and_other_symbols():
    chain = pd.DataFrame({
        "underlying_symbol": ["AMD", "AMD", "AMD", "AMD", "NVDA"],
        "type": ["call", "call", "put", "call", "call"],
        "strike_price": [600.0, 600.0, 600.0, 570.0, 600.0],
        "open_interest": [100, 50, 999, 30, 7],
    })

    result = call_oi_by_strike(chain, "AMD")

    assert result.to_dict() == {570.0: 30, 600.0: 150}


def test_call_wall_above_spot_is_the_primary_target():
    # AVGO / BTC style: wall untested, the bigger OI cluster further up must NOT replace it.
    by_strike = _series({360.0: 1000, 400.0: 5000})

    targets = select_targets(by_strike, spot=357.74, call_wall=360.0)

    assert targets.primary == 360.0
    assert targets.wall_status == "ABOVE"
    assert targets.wall_distance == pytest.approx(357.74 / 360 - 1)


def test_cleared_wall_uses_largest_oi_between_min_distance_and_near_band():
    # AMD style: wall 550 cleared; 570/580 minor, 600 is the real cluster; 650 is beyond the near band.
    by_strike = _series({560.0: 4012, 570.0: 5640, 580.0: 5058, 600.0: 9821, 650.0: 20000})

    targets = select_targets(by_strike, spot=557.32, call_wall=550.0)

    assert (targets.primary, targets.primary_oi) == (600.0, 9821.0)
    assert targets.wall_status == "CLEARED"
    assert targets.stretch == 650.0  # +16.6%: beyond the near band, inside the far band


def test_min_distance_skips_strikes_sitting_on_top_of_spot():
    # SMH style: 575 has more OI but is only 0.4% above spot, so 585 is the target.
    by_strike = _series({575.0: 3916, 585.0: 3265})

    targets = select_targets(by_strike, spot=572.67, call_wall=572.5)

    assert targets.primary == 585.0


def test_stretch_is_the_biggest_cluster_beyond_the_near_band():
    # ETH style: 2800 is the primary, 3000 (+14%) the stretch.
    by_strike = _series({2650.0: 5000, 2800.0: 31280, 3000.0: 48298})

    targets = select_targets(by_strike, spot=2625.2, call_wall=2600.0)

    assert (targets.primary, targets.stretch) == (2800.0, 3000.0)
    assert targets.stretch_oi == 48298.0


def test_bands_are_configurable():
    by_strike = _series({2800.0: 31280, 3000.0: 48298})

    wide = select_targets(by_strike, spot=2625.2, call_wall=2600.0, config=TargetConfig(near_band=0.15))

    assert wide.primary == 3000.0


def test_ties_resolve_to_the_strike_nearest_spot():
    by_strike = _series({350.0: 100, 360.0: 100})

    assert select_targets(by_strike, spot=340.0, call_wall=None).primary == 350.0


def test_no_chain_or_no_spot_gives_no_target():
    empty = pd.Series(dtype=float)

    assert select_targets(empty, spot=100.0, call_wall=None).primary is None
    assert select_targets(_series({110.0: 5}), spot=None, call_wall=105.0).primary is None
    no_wall = select_targets(empty, spot=100.0, call_wall=None)
    assert no_wall.wall_status == "NONE" and no_wall.wall_distance is None


def test_ladder_table_lists_top_strikes_above_spot_by_oi():
    by_strike = _series({330.0: 9999, 340.0: 139102, 345.0: 58395, 350.0: 42017})

    table = ladder_table(by_strike, spot=335.73, rows=2)

    assert table["strike"].tolist() == [340.0, 345.0]
    assert table["call_oi"].tolist() == [139102, 58395]


def test_stretch_is_dropped_when_its_oi_is_small_relative_to_the_primary():
    # SMH style: a 665 strike with a sliver of OI is not a meaningful stretch target.
    by_strike = _series({585.0: 3265, 665.0: 575})

    targets = select_targets(by_strike, spot=572.67, call_wall=572.5)

    assert targets.primary == 585.0
    assert targets.stretch is None
