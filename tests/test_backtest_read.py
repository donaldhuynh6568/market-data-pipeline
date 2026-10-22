import pandas as pd
import pytest

from market_data_pipeline.reports.backtest_read import (
    GLOSSARY,
    TARGET_CONTROL_VIEW,
    control_note,
    is_all_zones,
    control_expectancy,
    feature_takeaway,
    headline,
    row_verdict,
    target_headline,
    target_takeaway,
)


def _summary(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["view", "bucket", "trades", "win_rate", "expectancy_r"])


def test_row_verdict_says_whether_it_makes_money():
    assert row_verdict(0.21, 752) == "Makes money"
    assert row_verdict(-0.19, 1804) == "Loses money"
    assert row_verdict(0.01, 1387) == "About breakeven"
    assert row_verdict(None, 100) == ""


def test_row_verdict_compares_against_the_control_when_given():
    assert row_verdict(0.21, 752, control=0.14) == "Makes money, and beats random entry"
    assert row_verdict(0.07, 195, control=0.14) == "Makes money, but still below random entry"


def test_row_verdict_calls_out_a_sample_too_small_to_rely_on():
    verdict = row_verdict(0.33, 12)

    assert "only 12 trades" in verdict and "too few" in verdict


def test_feature_takeaway_reports_the_spread_when_a_feature_separates():
    group = _summary([
        ("Momentum at entry @ 1R", ">0.2", 752, 0.605, 0.210),
        ("Momentum at entry @ 1R", "0-0.2", 1387, 0.514, 0.028),
        ("Momentum at entry @ 1R", "negative", 1804, 0.403, -0.194),
    ])

    takeaway = feature_takeaway(group, control=0.141)

    assert "Separates outcomes" in takeaway
    assert ">0.2" in takeaway and "negative" in takeaway
    assert "0.40R spread" in takeaway
    assert "clears random entry" in takeaway  # 0.210 beats 0.141


def test_feature_takeaway_says_plainly_when_a_feature_does_nothing():
    group = _summary([
        ("Displacement @ 1R", "True", 664, 0.482, -0.036),
        ("Displacement @ 1R", "False", 3279, 0.480, -0.039),
    ])

    takeaway = feature_takeaway(group)

    assert "No real effect" in takeaway and "ignore it" in takeaway


def test_feature_takeaway_notes_when_even_the_best_bucket_trails_random():
    group = _summary([
        ("Stop width (ATRs) @ 1R", ">2.5", 195, 0.533, 0.067),
        ("Stop width (ATRs) @ 1R", "<1.0", 1350, 0.443, -0.114),
    ])

    takeaway = feature_takeaway(group, control=0.141)

    assert "only ranks" in takeaway  # 0.067 does not clear 0.141


def test_feature_takeaway_ignores_buckets_too_small_to_trust():
    group = _summary([
        ("Zone quality @ 1R", "STRONG", 13, 0.67, 0.40),  # dropped: under 30 trades
        ("Zone quality @ 1R", "PLAIN", 2918, 0.472, -0.056),
    ])

    assert "Not enough well-sampled buckets" in feature_takeaway(group)


def test_target_takeaway_picks_the_best_reward_multiple():
    summary = _summary([
        ("All zones @ 1R", "ALL", 3943, 0.481, -0.039),
        ("All zones @ 2R", "ALL", 3904, 0.335, 0.004),
        ("All zones @ 3R", "ALL", 3877, 0.263, 0.052),
    ])

    takeaway = target_takeaway(summary)

    assert "3R" in takeaway and "letting winners run" in takeaway


def test_target_takeaway_says_when_no_target_worked():
    summary = _summary([
        ("All zones @ 1R", "ALL", 299, 0.388, -0.224),
        ("All zones @ 2R", "ALL", 271, 0.210, -0.369),
    ])

    takeaway = target_takeaway(summary)

    assert "No reward target made money" in takeaway
    assert "Entry quality is the problem" in takeaway


def test_headline_warns_when_the_system_has_not_beaten_random():
    summary = _summary([
        ("All zones @ 1R", "ALL", 3943, 0.481, -0.039),
        ("Control @ 1R", "random entry", 666, 0.571, 0.141),
    ])

    line = headline(summary)

    assert "NOT beaten random" in line
    assert "relative order only" in line
    assert "-0.039R" in line and "+0.141R" in line and "3,943" in line


def test_headline_says_so_when_the_system_does_beat_random():
    summary = _summary([
        ("All zones @ 1R", "ALL", 4000, 0.55, 0.200),
        ("Control @ 1R", "random entry", 666, 0.571, 0.141),
    ])

    assert "adding something" in headline(summary)


def test_control_expectancy_reads_a_different_view_name_when_given_one():
    summary = _summary([(TARGET_CONTROL_VIEW, "random entry", 626, 0.65, 0.027)])

    assert control_expectancy(summary) is None  # default view name doesn't match
    assert control_expectancy(summary, view=TARGET_CONTROL_VIEW) == pytest.approx(0.027)


def test_target_headline_describes_the_scorecard_rank_is_built_on():
    beats = _summary([
        ("All zones (to target)", "ALL", 259, 0.552, 0.148),
        (TARGET_CONTROL_VIEW, "random entry", 626, 0.65, 0.027),
    ])
    line = target_headline(beats)

    assert "to target" in line and "Rank is built on this" in line
    assert "+0.148R" in line and "+0.027R" in line


def test_target_headline_warns_when_it_has_not_beaten_random():
    loses = _summary([
        ("All zones (to target)", "ALL", 259, 0.552, -0.148),
        (TARGET_CONTROL_VIEW, "random entry", 626, 0.65, 0.027),
    ])
    line = target_headline(loses)

    assert "NOT beaten" in line and "relative order only" in line


def test_missing_pieces_degrade_quietly():
    assert control_expectancy(pd.DataFrame()) is None
    assert headline(pd.DataFrame()) == ""
    assert headline(_summary([("All zones @ 1R", "ALL", 10, 0.5, 0.0)])) == ""  # no control row
    assert target_takeaway(_summary([])) == ""


def test_the_glossary_covers_every_metric_on_the_tab():
    terms = {term for term, _ in GLOSSARY}

    assert {"R", "Trades", "Win rate", "Expectancy (R)", "Control"} <= terms
    for _, meaning in GLOSSARY:
        assert len(meaning) > 40  # a definition, not a restatement of the label


def test_the_control_row_explains_itself_instead_of_grading_itself():
    note = control_note(0.141)

    assert "BENCHMARK" in note and "+0.141R" in note
    assert "below random entry" not in note  # it IS random entry
    assert control_note(None) == "Benchmark row."


def test_is_all_zones_identifies_the_reward_target_family():
    assert is_all_zones("All zones @ 2R")
    assert not is_all_zones("Momentum at entry @ 1R")
    assert not is_all_zones("Control @ 1R")
