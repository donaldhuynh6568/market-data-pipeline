import openpyxl
import pandas as pd
import pytest
import yaml

from market_data_pipeline.reports.confluence_report import generate_report, load_inputs, select_names

SNAPSHOT = "2026-09-19"


def _momentum() -> pd.DataFrame:
    def row(symbol, cls, score, signal, d5, d10, d20, cash_like=False, rank=None):
        return {
            "symbol": symbol, "asset_class": cls, "as_of": pd.Timestamp("2026-09-18", tz="UTC"), "close": 100.0,
            "bars_available": 90, "score": score, "delta_5d": d5, "delta_10d": d10, "delta_20d": d20,
            "delta_signal": signal, "above_200d_ma": None, "cash_like": cash_like, "rank": rank,
        }

    return pd.DataFrame([
        row("UP", "equity", 0.4, "ACCELERATING", 0.1, 0.2, 0.3, rank=1),
        row("FOC", "equity", 0.2, "MIXED", 0.1, -0.2, 0.3, rank=2),
        row("DOWN", "equity", -0.1, "DECELERATING", -0.1, -0.2, -0.3, rank=3),
        row("SOL/USD", "crypto", 0.1, "MIXED", 0.1, 0.1, -0.1, rank=4),
        row("SHV", "equity", 0.05, "MIXED", 0.0, 0.0, 0.0, cash_like=True),
        row("OTHER", "equity", 0.0, "MIXED", 0.0, 0.0, 0.0, rank=5),
    ])


def _gex() -> pd.DataFrame:
    def row(symbol, regime, spot, wall):
        return {
            "symbol": symbol, "date": SNAPSHOT, "total_gamma": 1.0, "call_gamma": 1.0, "put_gamma": 0.0,
            "max_positive_gamma_strike": wall, "max_negative_gamma_strike": wall - 10, "gamma_flip": wall - 5,
            "spot_price": spot, "gamma_regime": regime,
        }

    return pd.DataFrame([
        row("UP", "POSITIVE_GAMMA", 100.0, 110.0),
        row("FOC", "NEAR_FLIP", 100.0, 105.0),
        row("DOWN", "NEGATIVE_GAMMA", 100.0, 95.0),
    ])


def _chain() -> pd.DataFrame:
    rows = []
    for symbol in ("UP", "FOC", "DOWN"):
        for strike, oi in ((105.0, 500), (110.0, 900), (120.0, 300)):
            rows.append({"underlying_symbol": symbol, "type": "call", "strike_price": strike, "open_interest": oi})
    return pd.DataFrame(rows)


def _structure(value_4h: str) -> pd.DataFrame:
    return pd.DataFrame({"symbol": ["UP", "FOC", "DOWN", "SOL/USD"], "structure": [value_4h] * 4})


def _write_data(data_dir, skip: tuple[str, ...] = ()) -> None:
    frames = {
        "momentum.parquet": _momentum(),
        "gex_data.parquet": _gex(),
        "options_data.parquet": _chain(),
        "swing_structure_1h.parquet": _structure("UPTREND"),
        "swing_structure_4h.parquet": _structure("UPTREND"),
    }
    for filename, frame in frames.items():
        if filename not in skip:
            frame.to_parquet(data_dir / filename, index=False)


def _rules(tmp_path, focus=("FOC", "SOL/USD", "DOWN")):
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump({"focus": list(focus), "include_accelerating": True}), encoding="utf-8")
    return path


def test_missing_required_file_names_the_file_and_the_command(tmp_path):
    _write_data(tmp_path, skip=("gex_data.parquet",))

    with pytest.raises(SystemExit) as error:
        load_inputs(tmp_path)

    message = str(error.value)
    assert "gex_data.parquet" in message
    assert "transforms.gex" in message
    assert "momentum.parquet" not in message


def test_select_names_is_focus_plus_accelerating_and_skips_unknown_symbols():
    names = select_names(_momentum(), {"focus": ["FOC", "MISSING"], "include_accelerating": True})

    assert names == ["FOC", "UP"]


def test_report_ratings_targets_and_optional_gaps(tmp_path):
    _write_data(tmp_path)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    by_ticker = {row.ticker: row for row in result.rows}
    assert by_ticker["UP"].rating.rating == "BUY"
    assert by_ticker["UP"].targets.primary == 110.0
    assert by_ticker["FOC"].rating.rating == "HOLD"
    assert by_ticker["DOWN"].rating.rating == "SELL / AVOID"
    assert by_ticker["DOWN"].targets is None
    assert by_ticker["SOL/USD"].regime == "NO DATA"
    assert sorted(row.ticker for row in result.rows) == ["DOWN", "FOC", "SOL/USD", "UP"]
    assert any("Optional data file not found" in gap for gap in result.gaps)
    assert any("SOL/USD has no options/GEX coverage" in gap for gap in result.gaps)
    assert any("different days" in warning for warning in result.warnings)


def test_workbook_layout_formulas_and_excluded_cash_like(tmp_path):
    _write_data(tmp_path)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    workbook = openpyxl.load_workbook(result.path)
    assert workbook.sheetnames == [
        "Summary & Ratings", "Detail", "ICT Levels", "Momentum & Delta", "Resistance Ladder Detail", "Key Levels",
        "Backtest", "Methodology",
    ]
    detail = workbook["Detail"]
    up_detail = row_for(detail, "UP", DETAIL_HEADER_ROW)
    assert cell_by_header(detail, up_detail, "Rating", DETAIL_HEADER_ROW).value == "BUY"
    # a value, not an Excel formula
    assert cell_by_header(detail, up_detail, "Dist. to Target", DETAIL_HEADER_ROW).value == pytest.approx(0.1)
    sol_row = row_for(detail, "SOL/USD", DETAIL_HEADER_ROW)
    assert cell_by_header(detail, sol_row, "Primary Target", DETAIL_HEADER_ROW).value == "N/A"
    assert cell_by_header(detail, sol_row, "Dist. to Target", DETAIL_HEADER_ROW).value == "N/A"
    tickers = [c.value for c in workbook["Momentum & Delta"]["B"][4:] if c.value]
    assert "SHV" not in tickers and "UP" in tickers
    ladder_text = [c.value for c in workbook["Resistance Ladder Detail"]["A"] if c.value]
    assert any(str(v).startswith("UP   (spot: 100.00") for v in ladder_text)


def test_report_is_deterministic(tmp_path):
    _write_data(tmp_path)
    rules = _rules(tmp_path)

    first = generate_report(tmp_path, rules, tmp_path / "a.xlsx")
    second = generate_report(tmp_path, rules, tmp_path / "b.xlsx")

    def cells(path):
        workbook = openpyxl.load_workbook(path)
        return {ws.title: [[c.value for c in row] for row in ws.iter_rows()] for ws in workbook}

    assert cells(first.path) == cells(second.path)


SUMMARY_HEADER_ROW = 7  # row 5 is the backtest headline, row 6 the MOMENTUM / LEVELS / RISK banners
SUMMARY_BANNER_ROW = 6
DETAIL_HEADER_ROW = 5


def headers_of(ws, header_row):
    return [c.value for c in ws[header_row] if c.value]


def cell_by_header(ws, row, header, header_row=SUMMARY_HEADER_ROW):
    """Look a cell up by its column header, so the tests survive the sheet being reordered."""
    return ws.cell(row, headers_of(ws, header_row).index(header) + 1)


def row_for(ws, ticker, header_row=SUMMARY_HEADER_ROW):
    """Find a row by ticker, looking up the Ticker column rather than assuming it is column 1."""
    column = headers_of(ws, header_row).index("Ticker") + 1
    return next(r for r in range(header_row + 1, ws.max_row + 1) if ws.cell(r, column).value == ticker)


def _news_frame() -> pd.DataFrame:
    def row(id_, ticker, headline, created, relevance="HIGH"):
        return {
            "id": id_, "ticker": ticker, "headline": headline, "source": "benzinga", "url": f"https://x/{id_}",
            "created_at": pd.Timestamp(created, tz="UTC"), "relevance": relevance,
        }

    return pd.DataFrame([
        row("1", "UP", "UP Corp downgraded by a major bank", "2026-09-19 12:00"),
        row("2", "UP", "UP Corp launches product", "2026-09-19 08:00"),
        row("3", "FOC", "FOC quiet week", "2026-09-18 08:00"),
    ])


def test_news_column_flag_and_missing_ticker_gap(tmp_path):
    _write_data(tmp_path)
    _news_frame().to_parquet(tmp_path / "news_data.parquet", index=False)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    by_ticker = {row.ticker: row for row in result.rows}
    assert by_ticker["UP"].rating.rating == "BUY"
    assert by_ticker["UP"].news_flag == 'REVIEW NEWS: negative headline ("downgrade")'
    assert "09-19 Benzinga: UP Corp launches product" in by_ticker["UP"].news
    assert by_ticker["DOWN"].news.startswith("No news collected")
    assert any("No news collected for" in gap and "DOWN" in gap for gap in result.gaps)

    summary = openpyxl.load_workbook(result.path)["Summary & Ratings"]
    detail = openpyxl.load_workbook(result.path)["Detail"]
    assert cell_by_header(
        detail, row_for(detail, "UP", DETAIL_HEADER_ROW), "News", DETAIL_HEADER_ROW
    ).value.startswith("REVIEW NEWS")
    assert "News through 2026-09-19" in summary["A2"].value


def test_news_never_changes_ratings_or_targets(tmp_path):
    _write_data(tmp_path)
    without = generate_report(tmp_path, _rules(tmp_path), tmp_path / "a.xlsx")
    _news_frame().to_parquet(tmp_path / "news_data.parquet", index=False)

    with_news = generate_report(tmp_path, _rules(tmp_path), tmp_path / "b.xlsx")

    key = lambda result: [(r.ticker, r.rating.rating, r.targets and r.targets.primary) for r in result.rows]  # noqa: E731
    assert key(with_news) == key(without)


def test_report_without_news_file_has_no_news_column_and_lists_the_gap(tmp_path):
    _write_data(tmp_path)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    detail = openpyxl.load_workbook(result.path)["Detail"]
    assert "News" not in headers_of(detail, DETAIL_HEADER_ROW)
    assert any("news_data.parquet" in gap for gap in result.gaps)


def _write_backtest(data_dir) -> None:
    """An ICT target-exit scorecard whose buckets match what daily_read looks up, so ranking is exercised.

    Ranking reads backtest_target_summary.parquet, not backtest_summary.parquet - stop width plays no part.
    """
    pd.DataFrame([
        {"view": "Momentum at entry (to target)", "bucket": ">0.2", "trades": 40, "win_rate": 0.55,
         "expectancy_r": 0.10, "median_target_rr": 1.5},
        {"view": "Momentum at entry (to target)", "bucket": "0-0.2", "trades": 100, "win_rate": 0.45,
         "expectancy_r": -0.10, "median_target_rr": 1.0},
        {"view": "Zone quality (to target)", "bucket": "STRONG", "trades": 20, "win_rate": 0.55,
         "expectancy_r": 0.08, "median_target_rr": 1.4},
        {"view": "Weak close on entry (to target)", "bucket": "False", "trades": 50, "win_rate": 0.60,
         "expectancy_r": 0.05, "median_target_rr": 1.2},
        {"view": "Control (to target)", "bucket": "random entry, matched geometry", "trades": 600,
         "win_rate": 0.55, "expectancy_r": 0.02, "median_target_rr": None},
    ]).to_parquet(data_dir / "backtest_target_summary.parquet", index=False)


def _write_location(data_dir) -> None:
    """Location parquets for the synthetic universe: UP has a live zone, DOWN has overhead supply only."""
    levels = pd.DataFrame([
        {"symbol": "UP", "as_of": pd.Timestamp("2026-09-18", tz="UTC"), "spot": 100.0, "bars_available": 60,
         "atr": 4.0, "atr_pct": 0.04, "range_low": 80.0, "range_q1": 85.0, "range_ce": 90.0, "range_ote": 95.0,
         "range_high": 100.0, "range_height": 20.0, "spot_pos_in_range": 1.0, "entry": 96.0,
         "entry_zone_low": 94.0, "entry_zone_high": 96.0, "stop": 90.0, "risk_pct": 0.0625,
         "entry_formed_at": pd.Timestamp("2026-09-15", tz="UTC"), "entry_has_displacement": True,
         "entry_volume_ratio": 2.1, "entry_zone_quality": "STRONG", "entry_zone_status": "RESPECTED",
         "inside_day": None,
         "resistance_low": float("nan"), "resistance_high": float("nan"), "n_support_zones": 1,
         "n_resistance_zones": 0, "nearest_smooth_high": float("nan"), "n_smooth_highs_above": 0},
        {"symbol": "DOWN", "as_of": pd.Timestamp("2026-09-18", tz="UTC"), "spot": 100.0, "bars_available": 60,
         "atr": 2.0, "atr_pct": 0.02, "range_low": 95.0, "range_q1": 96.0, "range_ce": 97.0, "range_ote": 98.0,
         "range_high": 99.0, "range_height": 4.0, "spot_pos_in_range": 1.25, "entry": float("nan"),
         "entry_zone_low": float("nan"), "entry_zone_high": float("nan"), "stop": float("nan"),
         "risk_pct": float("nan"), "entry_formed_at": pd.NaT, "entry_has_displacement": False,
         "entry_volume_ratio": float("nan"), "entry_zone_quality": None, "entry_zone_status": None,
         "inside_day": "CLASSIC",
         "resistance_low": 105.0, "resistance_high": 108.0, "n_support_zones": 0, "n_resistance_zones": 1,
         "nearest_smooth_high": 110.0, "n_smooth_highs_above": 1},
    ])
    zones = pd.DataFrame([
        {"symbol": "UP", "kind": "bull", "formed_at": pd.Timestamp("2026-09-15", tz="UTC"), "zone_low": 94.0,
         "zone_high": 96.0, "candle1_stop": 90.0, "has_displacement": True, "filled": False, "spot": 100.0,
         "distance_pct": -0.04, "volume_ratio": 2.1, "has_volume_confirmation": True, "zone_quality": "STRONG",
         "zone_status": "RESPECTED"},
        {"symbol": "DOWN", "kind": "bear", "formed_at": pd.Timestamp("2026-09-15", tz="UTC"), "zone_low": 105.0,
         "zone_high": 108.0, "candle1_stop": 110.0, "has_displacement": False, "filled": False, "spot": 100.0,
         "distance_pct": 0.05, "volume_ratio": 0.8, "has_volume_confirmation": False, "zone_quality": "PLAIN",
         "zone_status": "UNTESTED"},
    ])
    levels.to_parquet(data_dir / "location_levels.parquet", index=False)
    zones.to_parquet(data_dir / "location_zones.parquet", index=False)


def test_level_columns_and_key_levels_tab_appear_when_location_data_exists(tmp_path):
    _write_data(tmp_path)
    _write_location(tmp_path)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    by_ticker = {row.ticker: row for row in result.rows}
    assert by_ticker["UP"].plan.status == "WAIT_PULLBACK"
    assert (by_ticker["UP"].plan.entry, by_ticker["UP"].plan.stop) == (96.0, 90.0)
    assert by_ticker["DOWN"].plan.status == "RESISTANCE_ONLY"  # rated SELL / AVOID
    assert by_ticker["SOL/USD"].plan.status == "NO_LEVELS"  # absent from the levels file

    workbook = openpyxl.load_workbook(result.path)
    detail = workbook["Detail"]
    assert {"Setup", "Zone", "Entry", "Stop", "Risk %", "Stop ATRs", "Level Target", "R:R", "Target From",
            "Size"} <= set(headers_of(detail, DETAIL_HEADER_ROW))
    up = row_for(detail, "UP", DETAIL_HEADER_ROW)
    get = lambda h: cell_by_header(detail, up, h, DETAIL_HEADER_ROW).value  # noqa: E731
    assert (get("Stop ATRs"), get("Zone")) == (pytest.approx(1.5), "STRONG")
    assert (get("Entry"), get("Stop")) == (96.0, 90.0)
    assert get("Risk %") == pytest.approx(0.0625)
    assert get("R:R") == pytest.approx((110.0 - 96.0) / (96.0 - 90.0))

    # the Summary carries the same levels in plain words
    summary = workbook["Summary & Ratings"]
    summary_up = row_for(summary, "UP")
    assert cell_by_header(summary, summary_up, "Buy at").value == 96.0
    assert cell_by_header(summary, summary_up, "Mom. trend").value == "Speeding up"

    key_levels = [c.value for c in openpyxl.load_workbook(result.path)["Key Levels"]["A"] if c.value]
    assert any(str(v).startswith("UP   (spot: 100.00") for v in key_levels)
    assert "Dealing range" in key_levels


def test_levels_never_change_ratings_or_targets(tmp_path):
    _write_data(tmp_path)
    without = generate_report(tmp_path, _rules(tmp_path), tmp_path / "a.xlsx")
    _write_location(tmp_path)

    with_levels = generate_report(tmp_path, _rules(tmp_path), tmp_path / "b.xlsx")

    def key(result):
        return [(r.ticker, r.rating.rating, r.targets and r.targets.primary) for r in result.rows]

    assert key(with_levels) == key(without)


def test_report_without_location_files_still_runs_and_lists_the_gap(tmp_path):
    _write_data(tmp_path)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    summary = openpyxl.load_workbook(result.path)["Summary & Ratings"]
    assert not {"Setup", "Zone", "Entry"} & {c.value for c in summary[5] if c.value}
    assert any("location_levels.parquet" in gap for gap in result.gaps)
    key_levels = [c.value for c in openpyxl.load_workbook(result.path)["Key Levels"]["A"] if c.value]
    assert any("No location data loaded" in str(v) for v in key_levels)


def test_report_with_levels_is_deterministic(tmp_path):
    _write_data(tmp_path)
    _write_location(tmp_path)
    rules = _rules(tmp_path)

    first = generate_report(tmp_path, rules, tmp_path / "a.xlsx")
    second = generate_report(tmp_path, rules, tmp_path / "b.xlsx")

    def cells(path):
        workbook = openpyxl.load_workbook(path)
        return {ws.title: [[c.value for c in row] for row in ws.iter_rows()] for ws in workbook}

    assert cells(first.path) == cells(second.path)


def test_summary_reads_as_screen_then_layers_then_conviction_then_levels_then_risk(tmp_path):
    _write_data(tmp_path)
    _write_location(tmp_path)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    summary = openpyxl.load_workbook(result.path)["Summary & Ratings"]
    headers = headers_of(summary, SUMMARY_HEADER_ROW)
    order = [
        headers.index(h)
        for h in ["Ticker", "Screen", "Score", "1H", "Flags", "Conviction", "vs Opens", "Buy at", "Shares", "Past edge"]
    ]
    assert order == sorted(order), f"columns out of reading order: {headers}"
    # Descriptive only: no instruction column, and the old per-name sentence is gone.
    assert not {"What to do", "Action", "Stage", "Read"} & set(headers)

    banners = [
        (summary.cell(SUMMARY_BANNER_ROW, c).value, c)
        for c in range(1, len(headers) + 1)
        if summary.cell(SUMMARY_BANNER_ROW, c).value
    ]
    assert [b[0] for b in banners] == [
        "SCREEN: TREND + MOMENTUM", "LAYERS (+ agrees, - against, 0 neutral)", "CONVICTION",
        "FLOW (shown, not in conviction)", "ICT LEVELS vs SPOT", "TRADE PLAN", "RISK", "EVIDENCE",
    ]
    assert [b[1] for b in banners] == [
        headers.index(h) + 1 for h in ["Screen", "1H", "Conviction", "CMF", "vs Opens", "Buy at", "Shares", "Rank"]
    ]


def test_screen_gates_layers_and_passing_names_sort_first(tmp_path):
    _write_data(tmp_path)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    summary = openpyxl.load_workbook(result.path)["Summary & Ratings"]
    screens = {
        summary.cell(r, 1).value: summary.cell(r, headers_of(summary, SUMMARY_HEADER_ROW).index("Screen") + 1).value
        for r in range(SUMMARY_HEADER_ROW + 1, summary.max_row + 1)
        if summary.cell(r, 1).value
    }
    # UP: 4H uptrend with a positive score passes. DOWN is in a 4H uptrend but its score is negative.
    assert screens["UP"] == "LONG"
    assert screens["DOWN"] == "NO TREND"
    tickers = list(screens)
    assert tickers.index("UP") < tickers.index("DOWN")

    up = row_for(summary, "UP")
    down = row_for(summary, "DOWN")
    layers = ["1H", "Delta", "Gamma", "Wall", "Opens", "Volume", "COT", "Funding", "Flags"]
    assert [cell_by_header(summary, up, h).value for h in layers[:4]] == ["+", "+", "+", "+"]
    assert cell_by_header(summary, up, "COT").value == "n/a"
    assert cell_by_header(summary, down, "1H").value in (None, "")  # layers are not run for a name that fails the screen
    assert cell_by_header(summary, down, "Conviction").value == "-"


def test_ict_levels_tab_and_summary_columns_use_ict_file(tmp_path):
    _write_data(tmp_path)
    pd.DataFrame([{
        "symbol": "UP", "as_of": pd.Timestamp("2026-09-18", tz="UTC"), "spot": 100.0, "atr": 4.0, "rvol_1d": 2.0,
        "ret_1d": 0.01, "midnight_open": 99.0, "open_0830": 98.0, "open_0930": 97.0, "week_open": 96.0,
        "month_open": 95.0, "asia_high": 101.0, "asia_low": 98.5, "asia_high_status": "UNTESTED",
        "asia_low_status": "SWEPT", "london_high": float("nan"), "london_low": float("nan"),
        "london_high_status": None, "london_low_status": None, "eqh": 103.0, "eqh_count": 2, "eql": float("nan"),
        "eql_count": 0,
    }]).to_parquet(tmp_path / "ict_levels.parquet", index=False)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    workbook = openpyxl.load_workbook(result.path)
    summary = workbook["Summary & Ratings"]
    up = row_for(summary, "UP")
    assert cell_by_header(summary, up, "vs Opens").value == "above 5/5 opens"
    assert cell_by_header(summary, up, "Nearest above").value == "Asia high +1.0% (untested)"
    assert cell_by_header(summary, up, "Nearest below").value == "Midnight open -1.0%"
    assert cell_by_header(summary, up, "Equal highs").value == "Equal highs +3.0% (2x, untouched)"
    assert cell_by_header(summary, up, "Opens").value == "+"
    assert cell_by_header(summary, up, "Volume").value == "+"

    ict = workbook["ICT Levels"]
    labels = [ict.cell(r, 3).value for r in range(6, ict.max_row + 1)]
    assert labels[0] == "Equal highs" and "Monthly open" in labels


def test_missing_ict_file_is_reported_and_report_still_builds(tmp_path):
    _write_data(tmp_path)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    assert any("ict_levels.parquet" in gap for gap in result.gaps)
    summary = openpyxl.load_workbook(result.path)["Summary & Ratings"]
    assert cell_by_header(summary, row_for(summary, "UP"), "vs Opens").value == "-"
    assert "No ICT level data" in openpyxl.load_workbook(result.path)["ICT Levels"]["A3"].value


def test_detail_tab_keeps_the_technicals_the_daily_read_drops(tmp_path):
    _write_data(tmp_path)
    _write_location(tmp_path)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    detail = headers_of(openpyxl.load_workbook(result.path)["Detail"], DETAIL_HEADER_ROW)
    assert {"Rating", "GEX Regime", "1H", "4H", "Setup", "Risk %", "Target From", "Spot"} <= set(detail)


def test_names_are_ranked_by_measured_edge_and_the_untradeable_rank_last(tmp_path):
    _write_data(tmp_path)
    _write_location(tmp_path)
    _write_backtest(tmp_path)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    by_ticker = {row.ticker: row for row in result.rows}
    # UP has a 0.4 score, STRONG zone quality, and a RESPECTED entry zone (the clean/"False" weak-entry
    # bucket). Stop width plays no part in the edge at all.
    assert by_ticker["UP"].edge == pytest.approx((0.10 + 0.08 + 0.05) / 3)
    assert by_ticker["UP"].rank == 1
    assert "+0.08R" in by_ticker["UP"].edge_text  # smallest matched sample (n=20) governs, not the largest

    # SOL/USD has no levels at all, so it cannot be ranked on measured edge and falls to the back.
    assert by_ticker["SOL/USD"].edge is None
    assert by_ticker["SOL/USD"].rank > by_ticker["UP"].rank
    assert "no level to trade against" in by_ticker["SOL/USD"].read


def test_the_read_describes_the_technicals_without_instructing(tmp_path):
    _write_data(tmp_path)
    _write_location(tmp_path)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    reads = {row.ticker: row.read for row in result.rows}
    assert "Momentum building into a strong level" in reads["UP"]
    assert "Momentum fading" in reads["DOWN"]
    for read in reads.values():
        assert not any(word in read.lower() for word in ("buy ", "sell ", "skip")), read


def test_a_hard_gated_name_carries_no_size_on_the_summary(tmp_path):
    _write_data(tmp_path)
    _write_location(tmp_path)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")
    summary = openpyxl.load_workbook(result.path)["Summary & Ratings"]

    down_row = row_for(summary, "DOWN")
    for header in ("Shares", "Cost $", "Risk $"):
        assert cell_by_header(summary, down_row, header).value == "N/A"
    assert cell_by_header(summary, down_row, "Past edge").value == "-"


def test_insider_flag_gets_its_own_column_instead_of_hiding_in_the_note(tmp_path):
    _write_data(tmp_path)
    _write_location(tmp_path)
    trades = pd.DataFrame([
        {"ticker": "UP", "reporting_owner": f"insider{i % 4}", "transaction_date": "2026-09-10",
         "transaction_code": "S", "acquired_disposed_code": "D"}
        for i in range(24)
    ])
    trades.to_parquet(tmp_path / "insider_trades.parquet", index=False)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    detail = openpyxl.load_workbook(result.path)["Detail"]
    up_row = row_for(detail, "UP", DETAIL_HEADER_ROW)
    assert cell_by_header(detail, up_row, "Insider", DETAIL_HEADER_ROW).value == "24 sells / 4 insiders"
    # and the flag costs the name size rather than being decoration
    assert "insider selling" in next(row for row in result.rows if row.ticker == "UP").funnel.demerits


def test_a_workbook_open_in_excel_explains_itself(tmp_path, monkeypatch):
    _write_data(tmp_path)
    _write_location(tmp_path)

    def locked(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("openpyxl.workbook.workbook.Workbook.save", locked)
    with pytest.raises(SystemExit) as error:
        generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    message = str(error.value)
    assert "open in Excel" in message and "--output" in message


def test_key_levels_tab_shows_zone_status_and_inside_day(tmp_path):
    _write_data(tmp_path)
    _write_location(tmp_path)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    key_levels = openpyxl.load_workbook(result.path)["Key Levels"]
    rows = [[c.value for c in r] for r in key_levels.iter_rows()]
    header_row = next(r for r in rows if r[0] == "Zone")
    assert header_row[5] == "Status"
    status_col = [r[5] for r in rows if r and r[0] == "support"]
    assert "RESPECTED" in status_col

    # DOWN's levels row carries inside_day="CLASSIC"; its ticker header line should say so.
    down_header = next(v for v in (r[0] for r in rows) if isinstance(v, str) and v.startswith("DOWN"))
    assert "Inside day" in down_header and "Classic" in down_header
    # UP has no inside_day set, so its header must not mention it.
    up_header = next(v for v in (r[0] for r in rows) if isinstance(v, str) and v.startswith("UP"))
    assert "Inside day" not in up_header


def test_a_failed_zone_status_is_highlighted_and_mentioned_in_the_read(tmp_path):
    _write_data(tmp_path)
    _write_location(tmp_path)
    levels = pd.read_parquet(tmp_path / "location_levels.parquet")
    levels.loc[levels["symbol"] == "UP", "entry_zone_status"] = "FAILED"
    levels.to_parquet(tmp_path / "location_levels.parquet", index=False)
    zones = pd.read_parquet(tmp_path / "location_zones.parquet")
    zones.loc[zones["symbol"] == "UP", "zone_status"] = "FAILED"
    zones.to_parquet(tmp_path / "location_zones.parquet", index=False)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    up_row = next(row for row in result.rows if row.ticker == "UP")
    assert up_row.plan.zone_status == "FAILED"
    assert "already had a close back through its midpoint" in up_row.read
    # Ratings and the chosen entry/target are unaffected - this is informational, not a gate.
    assert up_row.rating.rating == "BUY" and up_row.plan.entry == 96.0

    key_levels = openpyxl.load_workbook(result.path)["Key Levels"]
    status_cells = [cell for row in key_levels.iter_rows() for cell in row if cell.value == "FAILED"]
    assert status_cells
    assert status_cells[0].fill.fgColor.rgb == "FFFFC7CE"  # the red highlight applied to a FAILED status cell


def test_stored_option_history_is_read_as_one_snapshot_not_summed(tmp_path):
    """The options files now keep daily snapshots; OI summed across them would be counted once per stored day."""
    _write_data(tmp_path)
    old = _chain().assign(snapshot_date="2026-09-18")
    latest = _chain().assign(snapshot_date="2026-09-19")
    pd.concat([old, latest], ignore_index=True).to_parquet(tmp_path / "options_data.parquet", index=False)
    stale_gex = _gex().assign(date="2026-09-18", max_positive_gamma_strike=999.0)
    pd.concat([stale_gex, _gex()], ignore_index=True).to_parquet(tmp_path / "gex_data.parquet", index=False)

    inputs = load_inputs(tmp_path)

    calls = inputs.chain[(inputs.chain["underlying_symbol"] == "UP") & (inputs.chain["type"] == "call")]
    assert calls["open_interest"].sum() == 500 + 900 + 300         # one snapshot, not two
    assert inputs.gex.set_index("symbol").loc["UP", "max_positive_gamma_strike"] == 110.0   # newest date wins


def _flow_row(symbol, **overrides):
    row = {
        "symbol": symbol, "as_of": pd.Timestamp("2026-09-18", tz="UTC"), "cmf": 0.2, "mfi": 65.0,
        "obv_signal": "CONFIRM_UP", "benchmark": "SPY", "rs_short": 0.03, "rs_long": 0.05, "trade_size_ratio": 1.3,
        "trade_size_ret": 0.02, "avwap_week": 95.0, "avwap_month": 94.0, "avwap_swing_low": 93.0,
        "avwap_swing_high": 120.0, "poc": 97.0, "vah": 99.0, "val": 96.0,
    }
    return row | overrides


def test_flow_columns_vote_but_never_change_conviction_or_ratings(tmp_path):
    _write_data(tmp_path)
    without = generate_report(tmp_path, _rules(tmp_path), tmp_path / "without.xlsx")
    pd.DataFrame([_flow_row("UP")]).to_parquet(tmp_path / "flow_indicators.parquet", index=False)

    with_flow = generate_report(tmp_path, _rules(tmp_path), tmp_path / "with.xlsx")

    book = openpyxl.load_workbook(with_flow.path)["Summary & Ratings"]
    up = row_for(book, "UP")
    flow_cells = [cell_by_header(book, up, h).value for h in ["CMF", "MFI", "OBV", "RS", "Size", "VWAP", "Profile"]]
    assert flow_cells == ["+", "+", "+", "+", "+", "+", "+"]       # spot 100 sits above the value area (99) too
    assert cell_by_header(book, up, "Flow").value == "7 / 0"

    old = openpyxl.load_workbook(without.path)["Summary & Ratings"]
    same = lambda h: cell_by_header(old, row_for(old, "UP"), h).value == cell_by_header(book, up, h).value  # noqa: E731
    assert all(same(h) for h in ["Conviction", "Agree/Against", "Why", "Rank", "Buy at", "Shares"])
    assert [r.rating.rating for r in with_flow.rows] == [r.rating.rating for r in without.rows]
    assert [r.conviction for r in with_flow.rows] == [r.conviction for r in without.rows]


def test_flow_levels_join_the_ict_levels_tab_and_missing_flow_file_degrades_to_na(tmp_path):
    _write_data(tmp_path)
    missing = generate_report(tmp_path, _rules(tmp_path), tmp_path / "missing.xlsx")
    assert any("flow_indicators.parquet" in gap for gap in missing.gaps)
    book = openpyxl.load_workbook(missing.path)["Summary & Ratings"]
    assert cell_by_header(book, row_for(book, "UP"), "CMF").value == "n/a"
    assert cell_by_header(book, row_for(book, "UP"), "Flow").value == "0 / 0"

    pd.DataFrame([_flow_row("UP")]).to_parquet(tmp_path / "flow_indicators.parquet", index=False)
    pd.DataFrame([{"symbol": "UP", "atr": 4.0}]).to_parquet(tmp_path / "ict_levels.parquet", index=False)
    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    ict = openpyxl.load_workbook(result.path)["ICT Levels"]
    labels = {ict.cell(r, 3).value for r in range(6, ict.max_row + 1)}
    assert {"POC", "Value area high", "Weekly VWAP", "Swing-low VWAP"} <= labels


def test_flow_backtest_tab_appears_only_when_that_file_exists(tmp_path):
    _write_data(tmp_path)
    absent = generate_report(tmp_path, _rules(tmp_path), tmp_path / "a.xlsx")
    assert "Flow Backtest" not in openpyxl.load_workbook(absent.path).sheetnames

    rows = [
        ("ALL", "LONG", 10, "all", 500, 0.01, 0.55),
        ("CMF", "LONG", 10, "agree", 300, 0.02, 0.6),
        ("CMF", "LONG", 10, "against", 100, -0.01, 0.4),
    ]
    columns = ["layer", "direction", "horizon", "vote", "observations", "mean_return", "win_rate"]
    pd.DataFrame(rows, columns=columns).to_parquet(tmp_path / "flow_backtest.parquet", index=False)

    present = generate_report(tmp_path, _rules(tmp_path), tmp_path / "b.xlsx")

    sheet = openpyxl.load_workbook(present.path)["Flow Backtest"]
    assert "agree +2.00% (n=300) vs against -1.00% (n=100)" in sheet["A3"].value
    assert [sheet.cell(r, 4).value for r in (6, 7, 8)] == ["all", "agree", "against"]
