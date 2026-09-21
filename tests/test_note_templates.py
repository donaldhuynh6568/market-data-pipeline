from market_data_pipeline.reports import rating_rules as rr
from market_data_pipeline.reports.note_templates import NoteContext, build_note
from market_data_pipeline.reports.resistance_ladder import Targets


def _ctx(rating: rr.Rating, **overrides) -> NoteContext:
    base = dict(
        rating=rating,
        asset_class="Equity",
        signal="ACCELERATING",
        score=0.426,
        delta_5d=0.251,
        delta_10d=0.506,
        delta_20d=0.511,
        regime="POSITIVE_GAMMA",
        structure_1h="SIDEWAYS",
        structure_4h="SIDEWAYS",
        spot=557.32,
        targets=Targets(600.0, 9821.0, None, None, "CLEARED", 557.32 / 550 - 1, 550.0),
    )
    base.update(overrides)
    return NoteContext(**base)


def test_buy_note_is_built_from_the_inputs():
    note = build_note(_ctx(rr.Rating(rr.BUY, rr.R_CONFIRMED)))

    assert "Accelerating on all three windows (5d +0.25, 10d +0.51, 20d +0.51)" in note
    assert "Already 1.3% through reported wall (550)" in note
    assert "Primary target 600 (9,821 call OI)" in note
    assert note.endswith(".")


def test_same_inputs_give_the_same_note():
    ctx = _ctx(rr.Rating(rr.BUY, rr.R_CONFIRMED))

    assert build_note(ctx) == build_note(ctx)


def test_no_gex_note_states_the_data_gap():
    ctx = _ctx(rr.Rating(rr.HOLD, rr.R_NO_GEX), signal="MIXED", regime="NO DATA", targets=None)

    assert "DATA GAP" in build_note(ctx)


def test_sell_note_has_no_target_and_warns_against_re_entry():
    note = build_note(_ctx(rr.Rating(rr.SELL, rr.R_DECELERATING), regime="NEGATIVE_GAMMA", targets=None))

    assert "Decelerating" in note and "Negative gamma" in note
    assert "Primary target" not in note
    assert "re-entry" in note


def test_structure_relationship_wording():
    pullback = build_note(_ctx(rr.Rating(rr.BUY, rr.R_CONFIRMED), structure_1h="DOWNTREND", structure_4h="UPTREND"))
    bounce = build_note(_ctx(rr.Rating(rr.SELL, rr.R_DECELERATING), structure_1h="UPTREND", structure_4h="DOWNTREND"))

    assert "pullback inside the 4H uptrend" in pullback
    assert "bounce inside a 4H downtrend" in bounce


def test_wall_on_top_of_spot_is_described_as_sitting_on_it():
    targets = Targets(585.0, 3265.0, None, None, "CLEARED", 572.67 / 572.5 - 1, 572.5)

    note = build_note(_ctx(rr.Rating(rr.WATCH, rr.R_NEAR_WALL), targets=targets, spot=572.67))

    assert "sitting on the reported wall (572.5)" in note


def test_crypto_notes_include_funding_and_equity_notes_do_not():
    crypto = build_note(_ctx(rr.Rating(rr.BUY, rr.R_CONFIRMED), asset_class="Crypto", funding_rate=0.0000125))
    crowded = build_note(_ctx(rr.Rating(rr.BUY, rr.R_CONFIRMED), asset_class="Crypto", funding_rate=0.0005))
    equity = build_note(_ctx(rr.Rating(rr.BUY, rr.R_CONFIRMED), funding_rate=0.0000125))

    assert "Hyperliquid funding 0.00125% (neutral" in crypto
    assert "crowded long" in crowded
    assert "funding" not in equity


def test_insider_flag_is_appended():
    note = build_note(
        _ctx(rr.Rating(rr.BUY, rr.R_CONFIRMED), insider={"sells": 27, "owners": 4, "window_days": 90})
    )

    assert "Insider selling flag: 27 open-market sells by 4 insiders in the last 90 days" in note


def test_unclassified_note_asks_for_manual_review():
    note = build_note(_ctx(rr.Rating(rr.UNCLASSIFIED, rr.R_NO_STRUCTURE)))

    assert "Unclassified" in note and "review manually" in note
