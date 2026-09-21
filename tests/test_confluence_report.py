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
    assert [row.ticker for row in result.rows] == ["UP", "FOC", "SOL/USD", "DOWN"]
    assert any("Optional data file not found" in gap for gap in result.gaps)
    assert any("SOL/USD has no options/GEX coverage" in gap for gap in result.gaps)
    assert any("different days" in warning for warning in result.warnings)


def test_workbook_layout_formulas_and_excluded_cash_like(tmp_path):
    _write_data(tmp_path)

    result = generate_report(tmp_path, _rules(tmp_path), tmp_path / "out.xlsx")

    workbook = openpyxl.load_workbook(result.path)
    assert workbook.sheetnames == ["Summary & Ratings", "Momentum & Delta", "Resistance Ladder Detail", "Methodology"]
    summary = workbook["Summary & Ratings"]
    assert summary["A6"].value == "UP" and summary["C6"].value == "BUY"
    assert summary["K6"].value == "=J6/I6-1"
    sol_row = next(r for r in range(6, 12) if summary.cell(r, 1).value == "SOL/USD")
    assert summary.cell(sol_row, 10).value == "N/A" and summary.cell(sol_row, 11).value == "N/A"
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
    assert summary["M5"].value == "News"
    assert summary["M6"].value.startswith("REVIEW NEWS")
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

    summary = openpyxl.load_workbook(result.path)["Summary & Ratings"]
    assert summary["M5"].value is None
    assert any("news_data.parquet" in gap for gap in result.gaps)
