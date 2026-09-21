from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from market_data_pipeline.config import DATA_DIR
from market_data_pipeline.reports import rating_rules as rr
from market_data_pipeline.reports.insider_flags import InsiderConfig, insider_sell_flags
from market_data_pipeline.picks import RULES_PATH, load_rules, select_names
from market_data_pipeline.reports.news_context import (
    NOT_COLLECTED_TEXT,
    news_config_from_rules,
    news_context,
    window_end,
)
from market_data_pipeline.reports.note_templates import NoteContext, build_note
from market_data_pipeline.reports.resistance_ladder import (
    TargetConfig,
    Targets,
    call_oi_by_strike,
    ladder_table,
    select_targets,
)

__all__ = ["RULES_PATH", "load_rules", "select_names"]  # re-exported from market_data_pipeline.picks

# key -> (parquet filename, command that produces it). This script never runs these; it only reads the files.
REQUIRED_FILES = {
    "momentum": ("momentum.parquet", "uv run python -m market_data_pipeline.transforms.momentum"),
    "gex": ("gex_data.parquet", "uv run python -m market_data_pipeline.transforms.gex"),
    "options": (
        "options_data.parquet",
        "uv run python -m market_data_pipeline.collectors.options --moneyness 0.20 --max-expiry-days 60",
    ),
    "swing_1h": (
        "swing_structure_1h.parquet",
        "uv run python -m market_data_pipeline.transforms.swing_structure --timeframe 1h",
    ),
    "swing_4h": (
        "swing_structure_4h.parquet",
        "uv run python -m market_data_pipeline.transforms.swing_structure --timeframe 4h",
    ),
}
OPTIONAL_FILES = {
    "gex_crypto": ("gex_crypto.parquet", "crypto GEX (uv run python -m market_data_pipeline.collectors.deribit_options)"),
    "deribit": ("deribit_options.parquet", "Deribit options (uv run python -m market_data_pipeline.collectors.deribit_options)"),
    "perps": ("perps_data.parquet", "perp funding/OI (uv run python -m market_data_pipeline.collectors.perps)"),
    "insider": ("insider_trades.parquet", "insider trades (uv run python -m market_data_pipeline.collectors.insider)"),
    "cot": ("cot_data.parquet", "COT positioning (uv run collect_cot_data.py)"),
    "news": ("news_data.parquet", "news (uv run python -m market_data_pipeline.collectors.news)"),
}

RATING_ORDER = {rr.BUY: 0, rr.WATCH: 1, rr.HOLD: 2, rr.UNCLASSIFIED: 2, rr.SELL: 3}
RATING_FILLS = {
    rr.BUY: "FFC6EFCE",
    rr.WATCH: "FFFFEB9C",
    rr.HOLD: "FFFFEB9C",
    rr.UNCLASSIFIED: "FFFFEB9C",
    rr.SELL: "FFFFC7CE",
}
HEADER_FILL = PatternFill("solid", fgColor="FF1F4E78")
HEADER_FONT = Font(bold=True, color="FFFFFFFF")
TITLE_FONT = Font(bold=True, size=14)
CRYPTO_COT_MARKERS = ("BITCOIN", "ETHER", "SOLANA")


@dataclass
class ReportInputs:
    momentum: pd.DataFrame
    gex: pd.DataFrame
    chain: pd.DataFrame
    structure_1h: dict[str, str]
    structure_4h: dict[str, str]
    perps: pd.DataFrame | None
    insider: pd.DataFrame | None
    cot: pd.DataFrame | None
    news: pd.DataFrame | None
    missing_optional: list[str]
    dates: dict[str, date | None]


@dataclass
class ReportRow:
    ticker: str
    asset_class: str
    rating: rr.Rating
    score: float | None
    signal: str | None
    regime: str
    structure_1h: str
    structure_4h: str
    spot: float | None
    targets: Targets | None
    note: str
    rule_note: str | None = None  # the deterministic note, kept when --llm-notes replaces `note`
    news: str = ""
    news_flag: str | None = None


def _to_date(value) -> date | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).date()


def load_inputs(data_dir: Path = DATA_DIR) -> ReportInputs:
    """Read the parquet files the report needs. Never runs a collector; a missing required file is an error."""
    missing = [
        f"  {filename}  <- {command}"
        for filename, command in REQUIRED_FILES.values()
        if not (data_dir / filename).exists()
    ]
    if missing:
        raise SystemExit(
            "Missing required data files. Refresh them first (this report only analyzes existing parquets):\n"
            + "\n".join(missing)
        )

    def read(key: str, table: dict) -> pd.DataFrame:
        return pd.read_parquet(data_dir / table[key][0])

    momentum = read("momentum", REQUIRED_FILES)
    gex_frames = [read("gex", REQUIRED_FILES)]
    chain_frames = [read("options", REQUIRED_FILES)]
    optional: dict[str, pd.DataFrame | None] = {}
    missing_optional: list[str] = []
    for key, (filename, description) in OPTIONAL_FILES.items():
        path = data_dir / filename
        if path.exists():
            optional[key] = pd.read_parquet(path)
        else:
            optional[key] = None
            missing_optional.append(f"{filename} ({description})")
    if optional["gex_crypto"] is not None:
        gex_frames.append(optional["gex_crypto"])
    if optional["deribit"] is not None:
        chain_frames.append(optional["deribit"])

    chain_columns = ["underlying_symbol", "type", "strike_price", "open_interest"]
    chain = pd.concat([f[chain_columns] for f in chain_frames], ignore_index=True)
    gex = pd.concat(gex_frames, ignore_index=True)

    def structure_map(key: str) -> dict[str, str]:
        frame = read(key, REQUIRED_FILES)
        return dict(zip(frame["symbol"], frame["structure"]))

    equity_rows = momentum[momentum["asset_class"] == "equity"]
    momentum_dates = equity_rows["as_of"] if not equity_rows.empty else momentum["as_of"]
    perps = optional["perps"]
    insider = optional["insider"]
    dates = {
        "momentum": _to_date(momentum_dates.max()),
        "gex": _to_date(pd.to_datetime(gex["date"]).max()),
        "perps": _to_date(perps["timestamp"].max()) if perps is not None and not perps.empty else None,
        "insider": _to_date(pd.to_datetime(insider["transaction_date"]).max())
        if insider is not None and not insider.empty
        else None,
        "news": _to_date(window_end(optional["news"])),
    }
    return ReportInputs(
        momentum=momentum,
        gex=gex,
        chain=chain,
        structure_1h=structure_map("swing_1h"),
        structure_4h=structure_map("swing_4h"),
        perps=perps,
        insider=insider,
        cot=optional["cot"],
        news=optional["news"],
        missing_optional=missing_optional,
        dates=dates,
    )


def _clean(value):
    return None if value is None or pd.isna(value) else float(value)


def build_rows(inputs: ReportInputs, rules: dict, as_of: date) -> tuple[list[ReportRow], dict[str, pd.Series]]:
    rating_config = rr.RatingConfig(
        near_wall_threshold=rules.get("rating", {}).get("near_wall_threshold", 0.01),
        buy_blocked_regimes=tuple(rules.get("rating", {}).get("buy_blocked_regimes", ["NEGATIVE_GAMMA", "NEAR_FLIP"])),
    )
    target_config = TargetConfig(**rules.get("targets", {}))
    insider_config = InsiderConfig(**rules.get("insider", {}))
    news_config = news_config_from_rules(rules)
    insider_flags = (
        insider_sell_flags(inputs.insider, as_of, insider_config) if inputs.insider is not None else {}
    )
    momentum = inputs.momentum.set_index("symbol")
    gex = inputs.gex.drop_duplicates("symbol", keep="last").set_index("symbol")
    funding = {}
    if inputs.perps is not None and not inputs.perps.empty:
        latest = inputs.perps.sort_values("timestamp").drop_duplicates("symbol", keep="last")
        funding = dict(zip(latest["symbol"], latest["funding_rate"]))

    rows: list[ReportRow] = []
    ladders: dict[str, pd.Series] = {}
    for symbol in select_names(inputs.momentum, rules):
        mom = momentum.loc[symbol]
        has_gex = symbol in gex.index
        regime = str(gex.loc[symbol, "gamma_regime"]) if has_gex else rr.NO_DATA
        spot = _clean(gex.loc[symbol, "spot_price"]) if has_gex else _clean(mom["close"])
        call_wall = _clean(gex.loc[symbol, "max_positive_gamma_strike"]) if has_gex else None
        s1 = inputs.structure_1h.get(symbol, "UNKNOWN")
        s4 = inputs.structure_4h.get(symbol, "UNKNOWN")

        wall_distance = spot / call_wall - 1 if spot is not None and call_wall else None
        rating = rr.rate(mom["delta_signal"], regime if has_gex else None, s4, wall_distance, rating_config)

        targets = None
        if has_gex and rating.rating != rr.SELL:
            by_strike = call_oi_by_strike(inputs.chain, symbol)
            targets = select_targets(by_strike, spot, call_wall, target_config)
            if not by_strike.empty:
                ladders[symbol] = by_strike

        asset_class = str(mom["asset_class"]).capitalize()
        news_text, news_flag_text = news_context(inputs.news, symbol, rating.rating, news_config)
        note = build_note(
            NoteContext(
                rating=rating,
                asset_class=asset_class,
                signal=mom["delta_signal"],
                score=_clean(mom["score"]),
                delta_5d=_clean(mom["delta_5d"]),
                delta_10d=_clean(mom["delta_10d"]),
                delta_20d=_clean(mom["delta_20d"]),
                regime=regime,
                structure_1h=s1,
                structure_4h=s4,
                spot=spot,
                targets=targets,
                funding_rate=_clean(funding.get(symbol)),
                insider=insider_flags.get(symbol),
            )
        )
        rows.append(
            ReportRow(
                ticker=symbol,
                asset_class=asset_class,
                rating=rating,
                score=_clean(mom["score"]),
                signal=mom["delta_signal"],
                regime=regime,
                structure_1h=s1,
                structure_4h=s4,
                spot=spot,
                targets=targets,
                note=note,
                news=news_text,
                news_flag=news_flag_text,
            )
        )

    rows.sort(key=lambda r: (RATING_ORDER[r.rating.rating], -(r.score if r.score is not None else float("-inf"))))
    return rows, ladders


def known_gaps(inputs: ReportInputs, rows: list[ReportRow], rules: dict) -> list[str]:
    """Data gaps read from the data itself, so the list is always accurate for this run."""
    gaps: list[str] = []
    for row in rows:
        if row.regime == rr.NO_DATA:
            gaps.append(
                f"{row.ticker} has no options/GEX coverage, so it carries no GEX regime, wall or target in this report "
                "- structure-only read."
            )
    momentum_date, gex_date = inputs.dates["momentum"], inputs.dates["gex"]
    if momentum_date and gex_date and momentum_date != gex_date:
        gaps.append(
            f"Momentum/price data ({momentum_date}) and GEX/options data ({gex_date}) are from different days "
            "- a small vintage mismatch across layers, not a data error."
        )
    stale_after = rules.get("staleness", {}).get("stale_after_days", 3)
    layer_dates = {k: v for k, v in inputs.dates.items() if v and k in ("momentum", "gex", "perps")}
    if layer_dates:
        newest = max(layer_dates.values())
        for layer, layer_date in layer_dates.items():
            if (newest - layer_date).days > stale_after:
                gaps.append(f"The {layer} layer is {(newest - layer_date).days} days older than the newest layer ({layer_date}).")
    if inputs.cot is None or not _has_crypto_cot(inputs.cot):
        gaps.append("No COT (managed-money/speculator futures positioning) rows are loaded for BTC, ETH or SOL.")
    if inputs.momentum["above_200d_ma"].isna().all():
        max_bars = int(inputs.momentum["bars_available"].max())
        gaps.append(
            f"No 200-day moving average is available for any name (max history collected is {max_bars} bars), so the "
            "rotation strategy's defensive trend-filter trigger cannot run mechanically yet."
        )
    if inputs.news is not None:
        uncollected = [row.ticker for row in rows if row.news.startswith(NOT_COLLECTED_TEXT)]
        if uncollected:
            gaps.append(
                f"No news collected for {', '.join(uncollected)}. Run: "
                f"uv run python -m market_data_pipeline.collectors.news --symbols {' '.join(uncollected)}"
            )
        newest = max(d for d in inputs.dates.values() if d)
        if inputs.dates["news"] and (newest - inputs.dates["news"]).days > stale_after:
            gaps.append(f"News data is {(newest - inputs.dates['news']).days} days older than the newest layer ({inputs.dates['news']}).")
    if inputs.dates["insider"]:
        gaps.append(f"Insider-selling flags use Form 4 data through {inputs.dates['insider']}.")
    cash_like = inputs.momentum.loc[inputs.momentum["cash_like"], "symbol"].tolist()
    if cash_like:
        gaps.append(
            f"Cash-like instruments ({', '.join(cash_like)}) are excluded from scoring/ranking - near-zero volatility "
            "otherwise distorts the score formula."
        )
    for item in inputs.missing_optional:
        gaps.append(f"Optional data file not found: {item}.")
    gaps.append(
        "This is a single, fairly short observation window. Patterns here are descriptive, not statistically "
        "validated edges."
    )
    return gaps


def _has_crypto_cot(cot: pd.DataFrame) -> bool:
    for column in ("market_and_exchange_names", "commodity_name"):
        if column in cot.columns:
            text = cot[column].fillna("").astype(str).str.upper()
            if any(text.str.contains(marker).any() for marker in CRYPTO_COT_MARKERS):
                return True
    return False


# --- workbook writing -------------------------------------------------------------------------------------------


def _style_header(ws, row: int, columns: int) -> None:
    for col in range(1, columns + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _na(value):
    return "N/A" if value is None else value


def _write_summary(ws, rows: list[ReportRow], inputs: ReportInputs, universe_size: int, rules: dict) -> None:
    dates = inputs.dates
    gex_date, momentum_date = dates["gex"], dates["momentum"]
    if gex_date == momentum_date:
        snapshot = f"Data snapshot: all layers as of {gex_date}"
    else:
        snapshot = (
            f"Data snapshot: GEX/Options/Perps as of {gex_date}  |  Momentum/price data as of {momentum_date} close "
            "(one-layer vintage difference - see Methodology)"
        )
    if dates.get("news"):
        snapshot += f"  |  News through {dates['news']} (UTC)"
    ws["A1"] = "Confluence Report"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = snapshot
    ws["A3"] = (
        f"Includes the focus list plus every ACCELERATING name ({len(rows)} names). "
        f"Full {universe_size}-name ranked universe is on the Momentum & Delta tab."
    )
    headers = [
        "Ticker", "Asset Class", "Rating", "Score", "Delta Signal", "GEX Regime", "1H", "4H", "Spot",
        "Primary Target", "Dist. to Target", "Note",
    ]
    with_news = inputs.news is not None
    with_rule_note = any(row.rule_note for row in rows)
    if with_news:
        headers.append("News")
    if with_rule_note:
        headers.append("Note (rule-based)")
    extra_col = 13
    for col, title in enumerate(headers, 1):
        ws.cell(row=5, column=col, value=title)
    _style_header(ws, 5, len(headers))

    for offset, row in enumerate(rows):
        r = 6 + offset
        target = row.targets.primary if row.targets else None
        values = [
            row.ticker, row.asset_class, row.rating.rating, _na(None if row.score is None else round(row.score, 3)),
            _na(row.signal), row.regime, row.structure_1h, row.structure_4h, _na(row.spot), _na(target),
            f"=J{r}/I{r}-1" if target is not None and row.spot else "N/A", row.note,
        ]
        for col, value in enumerate(values, 1):
            ws.cell(row=r, column=col, value=value)
        ws.cell(row=r, column=3).fill = PatternFill("solid", fgColor=RATING_FILLS[row.rating.rating])
        ws.cell(row=r, column=3).font = Font(bold=True)
        ws.cell(row=r, column=9).number_format = "#,##0.00"
        ws.cell(row=r, column=10).number_format = "#,##0.##"
        ws.cell(row=r, column=11).number_format = "0.0%"
        ws.cell(row=r, column=12).alignment = Alignment(wrap_text=True, vertical="top")
        if with_news:
            news_cell = ws.cell(row=r, column=extra_col, value=row.news)
            news_cell.alignment = Alignment(wrap_text=True, vertical="top")
            if row.news_flag:
                news_cell.fill = PatternFill("solid", fgColor="FFFFC7CE")
        if with_rule_note:
            ws.cell(row=r, column=extra_col + int(with_news), value=row.rule_note or row.note).alignment = Alignment(
                wrap_text=True, vertical="top"
            )
        for col in range(1, 12):
            ws.cell(row=r, column=col).alignment = Alignment(vertical="top")

    widths = [10, 12, 34, 8, 16, 18, 11, 11, 12, 15, 14, 90] + ([70] if with_news else []) + ([90] if with_rule_note else [])
    for col, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A6"


def _write_momentum(ws, momentum: pd.DataFrame) -> None:
    ranked = momentum[~momentum["cash_like"]].sort_values("rank")
    ws["A1"] = "Momentum & Delta - Full Ranked Universe"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = (
        "Score = 10-day-lookback momentum % / annualized volatility %.  Delta_Nd = score today minus score N trading "
        "sessions ago (5/10/20). Cash-like instruments (e.g. SHV) excluded from ranking."
    )
    headers = ["Rank", "Ticker", "Class", "Score", "Delta 5d", "Delta 10d", "Delta 20d", "Signal"]
    for col, title in enumerate(headers, 1):
        ws.cell(row=4, column=col, value=title)
    _style_header(ws, 4, len(headers))
    for offset, (_, row) in enumerate(ranked.iterrows()):
        r = 5 + offset
        values = [
            int(row["rank"]), row["symbol"], row["asset_class"], round(row["score"], 3),
            round(row["delta_5d"], 3), round(row["delta_10d"], 3), round(row["delta_20d"], 3), row["delta_signal"],
        ]
        for col, value in enumerate(values, 1):
            ws.cell(row=r, column=col, value=None if pd.isna(value) else value)
    for col, width in enumerate([7, 11, 10, 9, 10, 10, 10, 16], 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A5"


def _write_ladders(ws, rows: list[ReportRow], ladders: dict[str, pd.Series], target_config: TargetConfig) -> None:
    ws["A1"] = "Resistance Ladder Detail"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = (
        "GEX Data reports only the single largest-gamma strike on each side, so once that wall is cleared it shows no "
        "further level. These ladders are rebuilt from the raw options chain (total call OI by strike above spot) to "
        "find the next real resistance concentration. This is an OI-concentration proxy, not a re-run gamma calculation."
    )
    r = 4
    for row in rows:
        if row.ticker not in ladders or row.targets is None or row.spot is None:
            continue
        table = ladder_table(ladders[row.ticker], row.spot, target_config.ladder_rows)
        if table.empty:
            continue
        wall = row.targets.call_wall
        wall_text = ""
        if wall is not None:
            status = " - ALREADY CLEARED" if row.targets.wall_status == "CLEARED" else ""
            wall_text = f" | reported wall: {wall:,.2f}{status}"
        ws.cell(row=r, column=1, value=f"{row.ticker}   (spot: {row.spot:,.2f}{wall_text})").font = Font(bold=True)
        header_row = r + 1
        for col, title in enumerate(["Strike", "Total Call OI", "Distance from Spot"], 1):
            ws.cell(row=header_row, column=col, value=title)
        ws.cell(row=header_row, column=4, value=row.spot)
        _style_header(ws, header_row, 4)
        for i, (_, level) in enumerate(table.iterrows(), 1):
            line = header_row + i
            ws.cell(row=line, column=1, value=float(level["strike"]))
            ws.cell(row=line, column=2, value=int(level["call_oi"])).number_format = "#,##0"
            ws.cell(row=line, column=3, value=f"=A{line}/$D${header_row}-1").number_format = "0.0%"
        r = header_row + len(table) + 2
    for col, width in enumerate([12, 16, 20, 12], 1):
        ws.column_dimensions[get_column_letter(col)].width = width


def _methodology_lines(rules: dict, gaps: list[str], dates: dict[str, date | None]) -> list[tuple[str, str]]:
    rating = rules.get("rating", {})
    targets = rules.get("targets", {})
    insider = rules.get("insider", {})
    wall_pct = rating.get("near_wall_threshold", 0.01) * 100
    blocked = ", ".join(rating.get("buy_blocked_regimes", ["NEGATIVE_GAMMA", "NEAR_FLIP"]))
    return [
        ("title", "Confluence Report - Methodology"),
        ("text", f"Generated by market_data_pipeline/reports/confluence_report.py from the parquet files in data/. "
                 f"Data as of {dates['gex']} (GEX / options / perps) and {dates['momentum']} (price / momentum close). "
                 "Thresholds live in confluence_report_rules.yaml; ratings, targets and notes are rule-driven and reproducible."),
        ("heading", "1. The Momentum Score"),
        ("text", "score = momentum_% / annualized_volatility_%, over a 10-session lookback (simple daily returns, population "
                 "standard deviation, 252 periods per year for equities and 365 for crypto, volatility floored at 5%). "
                 "A risk-adjusted measure, structurally similar to a Sharpe ratio."),
        ("heading", "2. The Delta (rate of change of the score)"),
        ("text", "delta_Nd = score today - score N sessions ago, for N = 5, 10, 20. ACCELERATING = all three deltas positive. "
                 "DECELERATING = all three negative. MIXED = the windows disagree. Checking all three separates a real "
                 "reversal from short-term noise."),
        ("heading", "3. GEX Regime and Walls"),
        ("text", "Dealer hedging makes the strike with the most upside gamma the 'call wall'. POSITIVE_GAMMA dampens moves, "
                 "NEGATIVE_GAMMA amplifies them, NEAR_FLIP means spot sits near the gamma flip and the regime is unstable. "
                 "GEX reports only the single largest strike per side, so once a wall is cleared the Resistance Ladder tab "
                 "rebuilds the next level from total call open interest by strike."),
        ("heading", "4. Swing Structure (1H / 4H)"),
        ("text", "Independently derived trend read on two timeframes. A 1H pullback inside a 4H uptrend is a healthy pause; "
                 "a 1H bounce inside a 4H downtrend is a bounce to fade until 4H turns."),
        ("heading", "5. Rating Rules (first match wins)"),
        ("text", "SELL / AVOID: delta DECELERATING."),
        ("text", "HOLD (data gap): no GEX regime available for the name."),
        ("text", "HOLD: delta MIXED."),
        ("text", f"HOLD: ACCELERATING but the regime is one of [{blocked}], or 4H structure is a DOWNTREND (momentum/structure conflict)."),
        ("text", f"WATCH: ACCELERATING, 4H SIDEWAYS, and spot within {wall_pct:g}% of the call wall - a live test; do not chase pre-break."),
        ("text", "BUY: ACCELERATING with none of the blockers above."),
        ("text", "HOLD (unclassified, review manually): missing momentum history or 4H structure."),
        ("heading", "6. Targets"),
        ("text", "Primary target = the reported call wall while it is above spot. Once spot is at or through the wall, it is the "
                 f"strike with the most total call OI between {targets.get('min_distance', 0.01) * 100:g}% and "
                 f"{targets.get('near_band', 0.12) * 100:g}% above spot. The stretch target is the largest OI cluster beyond "
                 f"that, up to {targets.get('far_band', 0.20) * 100:g}%, shown only when its OI is at least "
                 f"{targets.get('stretch_min_oi_ratio', 0.75) * 100:g}% of the primary target's. SELL / AVOID names carry no upside target."),
        ("heading", "7. Inclusion and Insider Flag"),
        ("text", "The Summary tab shows the focus list in the rules file plus every ACCELERATING name. An insider-selling flag "
                 f"is added to a note when open-market sells reach {insider.get('min_sells', 20)} across at least "
                 f"{insider.get('min_owners', 3)} insiders in the last {insider.get('window_days', 90)} days."),
        ("heading", "8. News Context"),
        ("text", "News comes from the Alpaca news feed (Benzinga) via collectors/news.py; the report only reads news_data.parquet. "
                 f"The News column shows up to {rules.get('news', {}).get('headlines_per_name', 3)} newest headlines from the last "
                 f"{rules.get('news', {}).get('lookback_days', 7)} days, headlines that name the asset first, then articles tagged to it and only a few others "
                 "(multi-ticker roundups are ignored). A REVIEW NEWS flag appears when a BUY/WATCH has a headline naming the asset that matches a negative "
                 "keyword, or a SELL / AVOID has one matching a positive keyword. This is a keyword screen, not sentiment analysis, and "
                 "it never changes a rating or target."),
        ("heading", "9. Known Gaps in This Snapshot"),
        *[("text", f"- {gap}") for gap in gaps],
    ]


def _write_methodology(ws, lines: list[tuple[str, str]]) -> None:
    for r, (kind, text) in enumerate(lines, 1):
        cell = ws.cell(row=r, column=1, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if kind == "title":
            cell.font = TITLE_FONT
        elif kind == "heading":
            cell.font = Font(bold=True, size=12)
    ws.column_dimensions["A"].width = 140


@dataclass
class ReportResult:
    path: Path
    rows: list[ReportRow]
    gaps: list[str]
    warnings: list[str]


def generate_report(
    data_dir: Path = DATA_DIR,
    rules_path: Path = RULES_PATH,
    output_path: Path | None = None,
    note_rewriter: Callable[[list[ReportRow]], dict[str, str]] | None = None,
) -> ReportResult:
    """Build the workbook from existing parquets. `note_rewriter` (opt-in) may replace note text only."""
    rules = load_rules(rules_path)
    inputs = load_inputs(data_dir)
    report_date = max(d for d in (inputs.dates["gex"], inputs.dates["momentum"], inputs.dates["perps"]) if d)
    rows, ladders = build_rows(inputs, rules, report_date)
    gaps = known_gaps(inputs, rows, rules)
    if note_rewriter is not None:
        for ticker, text in note_rewriter(rows).items():
            row = next(r for r in rows if r.ticker == ticker)
            row.rule_note, row.note = row.note, text  # ratings and targets are never touched

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary & Ratings"
    universe = int((~inputs.momentum["cash_like"]).sum())
    _write_summary(summary, rows, inputs, universe, rules)
    _write_momentum(workbook.create_sheet("Momentum & Delta"), inputs.momentum)
    _write_ladders(workbook.create_sheet("Resistance Ladder Detail"), rows, ladders, TargetConfig(**rules.get("targets", {})))
    _write_methodology(workbook.create_sheet("Methodology"), _methodology_lines(rules, gaps, inputs.dates))

    target = output_path or (data_dir / f"Confluence_Report_{report_date.isoformat()}.xlsx")
    workbook.save(target)
    warnings = [gap for gap in gaps if "older than the newest layer" in gap or "different days" in gap]
    return ReportResult(path=target, rows=rows, gaps=gaps, warnings=warnings)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the confluence report from existing parquet files (does not run any collectors)."
    )
    parser.add_argument("--output", default=None, help="Output .xlsx path (default: data/Confluence_Report_<date>.xlsx)")
    parser.add_argument("--rules", default=str(RULES_PATH), help="Path to the rules YAML")
    parser.add_argument(
        "--llm-notes",
        action="store_true",
        help="Opt in: have Claude rewrite the Note column (ratings/targets unchanged, rule-based note kept in an extra "
        "column). Needs the anthropic package and credentials; not reproducible run to run.",
    )
    parser.add_argument("--llm-model", default=None, help="Model for --llm-notes (default: claude-opus-5)")
    args = parser.parse_args()

    rewriter = None
    if args.llm_notes:
        from market_data_pipeline.reports import llm_notes

        model = args.llm_model or llm_notes.DEFAULT_MODEL
        client = llm_notes._make_client()
        rewriter = lambda rows: llm_notes.rewrite_notes(rows, model=model, client=client)  # noqa: E731

    result = generate_report(
        rules_path=Path(args.rules),
        output_path=Path(args.output) if args.output else None,
        note_rewriter=rewriter,
    )
    print(f"Wrote {result.path}")
    for row in result.rows:
        target = "N/A" if row.targets is None or row.targets.primary is None else f"{row.targets.primary:g}"
        print(f"  {row.ticker:<8} {row.rating.rating:<38} target={target}")
    for warning in result.warnings:
        print(f"  WARNING: {warning}")


if __name__ == "__main__":
    main()
