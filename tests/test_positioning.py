import pytest

from market_data_pipeline.reports import positioning as pos

CFG = pos.PositioningConfig()


def test_screen_needs_4h_structure_and_agreeing_momentum():
    assert pos.screen("UPTREND", "DOWNTREND", 0.3, CFG) == pos.Screen(pos.LONG, True, "4H uptrend with positive momentum")
    assert pos.screen("DOWNTREND", "UPTREND", -0.3, CFG).direction == pos.SHORT
    assert not pos.screen("UPTREND", "UPTREND", -0.1, CFG).passes      # momentum disagrees with structure
    assert not pos.screen("DOWNTREND", "DOWNTREND", 0.1, CFG).passes
    assert pos.screen("SIDEWAYS", "UPTREND", 0.5, CFG).direction == pos.NO_TREND
    assert not pos.screen("UPTREND", "UPTREND", None, CFG).passes


def test_screen_respects_min_abs_score():
    strict = pos.PositioningConfig(min_abs_score=0.2)
    assert not pos.screen("UPTREND", "UPTREND", 0.1, strict).passes
    assert pos.screen("UPTREND", "UPTREND", 0.25, strict).passes


def test_structure_and_delta_votes_flip_for_a_short():
    assert pos.structure_vote(pos.LONG, "UPTREND").vote == pos.AGREE
    assert pos.structure_vote(pos.SHORT, "UPTREND").vote == pos.DISAGREE
    assert pos.structure_vote(pos.LONG, "SIDEWAYS").vote == pos.NEUTRAL
    assert pos.delta_vote(pos.LONG, "ACCELERATING").vote == pos.AGREE
    assert pos.delta_vote(pos.SHORT, "DECELERATING").vote == pos.AGREE
    assert pos.delta_vote(pos.LONG, "MIXED").vote == pos.NEUTRAL
    assert pos.delta_vote(pos.LONG, None).vote is None


def test_gamma_and_wall_votes():
    assert pos.gamma_vote(pos.LONG, "POSITIVE_GAMMA").vote == pos.AGREE
    assert pos.gamma_vote(pos.LONG, "NEAR_FLIP").vote == pos.DISAGREE
    assert pos.gamma_vote(pos.SHORT, "NEGATIVE_GAMMA").vote == pos.AGREE
    assert pos.gamma_vote(pos.SHORT, "POSITIVE_GAMMA").vote == pos.NEUTRAL
    assert pos.gamma_vote(pos.LONG, "NO_DATA").vote is None
    assert pos.wall_vote(pos.LONG, 100.0, 110.0, CFG).vote == pos.AGREE
    assert pos.wall_vote(pos.LONG, 100.0, 101.0, CFG).vote == pos.DISAGREE
    assert pos.wall_vote(pos.LONG, 100.0, 95.0, CFG).vote == pos.NEUTRAL   # wall already cleared
    assert pos.wall_vote(pos.SHORT, 100.0, 110.0, CFG).vote is None


def test_opens_vote_counts_only_available_opens():
    opens = {"a": 90.0, "b": 95.0, "c": float("nan"), "d": 99.0, "e": 98.0}
    assert pos.opens_vote(pos.LONG, 100.0, opens, CFG).vote == pos.AGREE
    assert pos.opens_vote(pos.SHORT, 100.0, opens, CFG).vote == pos.DISAGREE
    mixed = {"a": 90.0, "b": 110.0}
    assert pos.opens_vote(pos.LONG, 100.0, mixed, CFG).vote == pos.NEUTRAL
    assert pos.opens_vote(pos.LONG, 100.0, {"a": float("nan")}, CFG).vote is None


def test_cot_vote_penalises_crowding_and_reads_direction():
    assert pos.cot_vote(pos.LONG, 0.5, 1000.0, CFG).vote == pos.AGREE
    assert pos.cot_vote(pos.LONG, 0.5, -1000.0, CFG).vote == pos.DISAGREE
    assert pos.cot_vote(pos.LONG, 0.95, 1000.0, CFG).vote == pos.DISAGREE
    assert "crowded" in pos.cot_vote(pos.LONG, 0.95, 1000.0, CFG).detail
    assert pos.cot_vote(pos.SHORT, 0.05, -1000.0, CFG).vote == pos.DISAGREE
    assert pos.cot_vote(pos.LONG, None, None, CFG).vote is None


def test_funding_vote_flags_the_crowded_side():
    assert pos.funding_vote(pos.LONG, 0.0001, CFG).vote == pos.DISAGREE
    assert pos.funding_vote(pos.LONG, -0.0001, CFG).vote == pos.AGREE
    assert pos.funding_vote(pos.LONG, 0.00001, CFG).vote == pos.NEUTRAL
    assert pos.funding_vote(pos.SHORT, -0.0001, CFG).vote == pos.DISAGREE
    assert pos.funding_vote(pos.LONG, None, CFG).vote is None


def test_volume_vote_needs_heavy_volume_and_follows_the_day_direction():
    assert pos.volume_vote(pos.LONG, 2.0, 0.01, CFG).vote == pos.AGREE
    assert pos.volume_vote(pos.LONG, 2.0, -0.01, CFG).vote == pos.DISAGREE
    assert pos.volume_vote(pos.SHORT, 2.0, -0.01, CFG).vote == pos.AGREE
    assert pos.volume_vote(pos.LONG, 0.8, 0.05, CFG).vote == pos.NEUTRAL
    assert pos.volume_vote(pos.LONG, None, 0.05, CFG).vote is None


def test_flags_only_ever_vote_against():
    assert pos.flags_vote(False, False).vote == pos.NEUTRAL
    assert pos.flags_vote(True, False).vote == pos.DISAGREE
    assert "news flag" in pos.flags_vote(True, True).detail


def test_conviction_counts_available_layers_and_ignores_gaps():
    votes = [pos.Vote(f"l{i}", v, "") for i, v in enumerate([1, 1, 1, 1, 0, None, None, -1])]
    result = pos.conviction(votes, CFG)
    assert (result.agree, result.disagree, result.neutral, result.no_data) == (4, 1, 1, 2)
    assert result.level == pos.MEDIUM   # net 3: at least medium (2) but under high (4)
    assert pos.conviction(votes[:4], CFG).level == pos.HIGH
    assert pos.conviction([pos.Vote("x", None, "")] * 3, CFG).level == pos.LOW


def test_level_table_distances_sides_and_status():
    levels = {
        "midnight_open": 99.0, "week_open": float("nan"), "asia_high": 102.0, "asia_high_status": "SWEPT",
        "eqh": 105.0, "eqh_count": 3,
    }
    table = pos.level_table(100.0, 2.0, levels)
    by_key = {r["key"]: r for r in table}
    assert set(by_key) == {"midnight_open", "asia_high", "eqh"}      # NaN levels are skipped
    assert by_key["midnight_open"]["pct"] == pytest.approx(-0.01)
    assert by_key["midnight_open"]["side"] == "BELOW"
    assert by_key["asia_high"]["atr"] == pytest.approx(1.0)
    assert by_key["asia_high"]["status"] == "SWEPT"
    assert by_key["eqh"]["status"] == "3x, untouched"
    assert pos.nearest(table, "ABOVE")["key"] == "asia_high"
    assert pos.nearest(table, "BELOW")["key"] == "midnight_open"
    assert pos.opens_summary(table) == "above 1/1 opens"
    assert pos.level_table(None, 2.0, levels) == []


def test_cmf_and_mfi_votes_mirror_for_shorts_and_flag_exhaustion():
    assert pos.cmf_vote(pos.LONG, 0.2, CFG).vote == pos.AGREE
    assert pos.cmf_vote(pos.LONG, -0.2, CFG).vote == pos.DISAGREE
    assert pos.cmf_vote(pos.SHORT, -0.2, CFG).vote == pos.AGREE
    assert pos.cmf_vote(pos.LONG, 0.01, CFG).vote == pos.NEUTRAL
    assert pos.cmf_vote(pos.LONG, None, CFG).vote is None

    assert pos.mfi_vote(pos.LONG, 65, CFG).vote == pos.AGREE
    overbought = pos.mfi_vote(pos.LONG, 85, CFG)
    assert overbought.vote == pos.DISAGREE and "overbought" in overbought.detail
    assert pos.mfi_vote(pos.LONG, 30, CFG).vote == pos.DISAGREE
    assert pos.mfi_vote(pos.LONG, 50, CFG).vote == pos.NEUTRAL
    assert pos.mfi_vote(pos.SHORT, 35, CFG).vote == pos.AGREE
    assert "oversold" in pos.mfi_vote(pos.SHORT, 15, CFG).detail
    assert pos.mfi_vote(pos.SHORT, 70, CFG).vote == pos.DISAGREE


def test_obv_rs_and_trade_size_votes():
    assert pos.obv_vote(pos.LONG, "BULLISH_DIV").vote == pos.AGREE
    assert pos.obv_vote(pos.LONG, "BEARISH_DIV").vote == pos.DISAGREE
    assert pos.obv_vote(pos.SHORT, "CONFIRM_DOWN").vote == pos.AGREE
    assert pos.obv_vote(pos.LONG, "NONE").vote == pos.NEUTRAL
    assert pos.obv_vote(pos.LONG, None).vote is None

    assert pos.rs_vote(pos.LONG, 0.04, "SPY", CFG).vote == pos.AGREE
    assert pos.rs_vote(pos.LONG, -0.04, "SPY", CFG).vote == pos.DISAGREE
    assert pos.rs_vote(pos.SHORT, -0.04, "SPY", CFG).vote == pos.AGREE
    assert pos.rs_vote(pos.LONG, float("nan"), None, CFG).vote is None

    assert pos.trade_size_vote(pos.LONG, 1.4, 0.03, CFG).vote == pos.AGREE      # bigger prints on a rising window
    assert pos.trade_size_vote(pos.LONG, 1.4, -0.03, CFG).vote == pos.DISAGREE
    assert pos.trade_size_vote(pos.LONG, 1.0, 0.03, CFG).vote == pos.NEUTRAL    # size not rising
    assert pos.trade_size_vote(pos.LONG, None, 0.03, CFG).vote is None


def test_vwap_and_profile_votes_use_the_trades_own_swing_anchor():
    levels = {"avwap_week": 95.0, "avwap_month": 98.0, "avwap_swing_low": 90.0, "avwap_swing_high": 120.0,
              "vah": 99.0, "val": 96.0}
    assert pos.vwap_vote(pos.LONG, 100.0, levels, CFG).vote == pos.AGREE         # above all three
    assert pos.vwap_vote(pos.SHORT, 100.0, levels, CFG).vote == pos.DISAGREE     # above 2 of 3, a short wants below
    assert pos.vwap_vote(pos.LONG, 100.0, {}, CFG).vote is None
    assert pos.profile_vote(pos.LONG, 100.0, levels).vote == pos.AGREE
    assert pos.profile_vote(pos.LONG, 97.0, levels).vote == pos.NEUTRAL
    assert pos.profile_vote(pos.SHORT, 95.0, levels).vote == pos.AGREE
    assert pos.profile_vote(pos.LONG, 95.0, levels).vote == pos.DISAGREE
    assert pos.profile_vote(pos.LONG, 100.0, {}).vote is None


def test_flow_score_counts_and_does_not_touch_conviction():
    flow = {"cmf": 0.2, "mfi": 65.0, "obv_signal": "BEARISH_DIV", "rs_short": float("nan")}
    votes = pos.flow_votes(pos.LONG, 100.0, flow, CFG)
    assert [v.layer for v in votes] == ["CMF", "MFI", "OBV", "Rel. strength", "Trade size", "VWAP", "Profile"]
    score = pos.flow_score(votes)
    assert (score.agree, score.disagree, score.no_data) == (2, 1, 4)
    assert score.text == "2 / 1"
    assert pos.conviction([], CFG).level == pos.LOW                              # conviction is built from its own votes
    assert pos.level_table(100.0, 2.0, {"poc": 101.0, "vah": 103.0})[0]["label"] == "POC"
