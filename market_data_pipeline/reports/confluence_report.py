from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass, field
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
from market_data_pipeline.reports import backtest_read as br
from market_data_pipeline.reports import daily_read as dr
from market_data_pipeline.reports import funnel as fn
from market_data_pipeline.reports import positioning as pos
from market_data_pipeline.reports.note_templates import NoteContext, build_note
from market_data_pipeline.storage import latest_snapshot
from market_data_pipeline.reports.position_levels import (
    GEX_WALL,
    NO_LEVELS,
    NO_SETUP,
    OI_CLUSTER,
    RESISTANCE_ONLY,
    PositionPlan,
    build_plan,
    sizing_config_from_rules,
)
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
    "location_levels": (
        "location_levels.parquet",
        "entry/stop levels (uv run python -m market_data_pipeline.transforms.location)",
    ),
    "location_zones": (
        "location_zones.parquet",
        "FVG zones (uv run python -m market_data_pipeline.transforms.location)",
    ),
    "ict_levels": (
        "ict_levels.parquet",
        "opens, session highs/lows, equal highs/lows (uv run python -m market_data_pipeline.transforms.ict_levels)",
    ),
    "flow": (
        "flow_indicators.parquet",
        "money flow, relative strength, anchored VWAP, volume profile "
        "(uv run python -m market_data_pipeline.transforms.flow_indicators)",
    ),
    "flow_backtest": (
        "flow_backtest.parquet",
        "flow-indicator scorecard (uv run python -m market_data_pipeline.transforms.flow_backtest)",
    ),
    "backtest": (
        "backtest_summary.parquet",
        "signal scorecard (uv run python -m market_data_pipeline.transforms.backtest)",
    ),
    "backtest_target": (
        "backtest_target_summary.parquet",
        "ICT target-exit scorecard, what Rank is based on (uv run python -m market_data_pipeline.transforms.backtest)",
    ),
    "backtest_momentum": (
        "backtest_momentum.parquet",
        "momentum scorecard (uv run python -m market_data_pipeline.transforms.backtest)",
    ),
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
    levels: pd.DataFrame | None
    zones: pd.DataFrame | None
    backtest: pd.DataFrame | None
    backtest_target: pd.DataFrame | None
    backtest_momentum: pd.DataFrame | None
    missing_optional: list[str]
    dates: dict[str, date | None]
    ict: pd.DataFrame | None = None
    flow: pd.DataFrame | None = None
    flow_backtest: pd.DataFrame | None = None


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
    plan: PositionPlan | None = None
    funnel: fn.FunnelResult | None = None
    insider: dict | None = None
    rank: int | None = None
    edge: float | None = None
    edge_text: str = ""
    read: str = ""
    screen: pos.Screen | None = None
    votes: list[pos.Vote] = field(default_factory=list)
    conviction: pos.Conviction | None = None
    ict_table: list[dict] = field(default_factory=list)
    flow_votes: list[pos.Vote] = field(default_factory=list)
    flow_score: pos.FlowScore | None = None


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
    # The options files keep daily snapshots; open interest summed across days would be counted once per day.
    chain = pd.concat([latest_snapshot(f)[chain_columns] for f in chain_frames], ignore_index=True)
    gex = pd.concat(gex_frames, ignore_index=True)
    gex = gex.sort_values("date", kind="stable").drop_duplicates("symbol", keep="last")

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
        levels=optional["location_levels"],
        zones=optional["location_zones"],
        backtest=optional["backtest"],
        backtest_target=optional["backtest_target"],
        backtest_momentum=optional["backtest_momentum"],
        missing_optional=missing_optional,
        dates=dates,
        ict=optional["ict_levels"],
        flow=optional["flow"],
        flow_backtest=optional["flow_backtest"],
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
    funnel_config = fn.funnel_config_from_rules(rules)
    sizing_config = sizing_config_from_rules(rules)
    max_pullback = rules.get("location", {}).get("max_pullback_pct", 0.15)
    levels_by_symbol = inputs.levels.set_index("symbol") if inputs.levels is not None else None
    insider_flags = (
        insider_sell_flags(inputs.insider, as_of, insider_config) if inputs.insider is not None else {}
    )
    momentum = inputs.momentum.set_index("symbol")
    gex = inputs.gex.drop_duplicates("symbol", keep="last").set_index("symbol")
    funding = {}
    if inputs.perps is not None and not inputs.perps.empty:
        latest = inputs.perps.sort_values("timestamp").drop_duplicates("symbol", keep="last")
        funding = dict(zip(latest["symbol"], latest["funding_rate"]))

    pos_config = pos.positioning_config_from_rules(rules)
    ict_by_symbol = inputs.ict.set_index("symbol") if inputs.ict is not None else None
    flow_by_symbol = inputs.flow.set_index("symbol") if inputs.flow is not None else None
    cot_by_market = _latest_cot(inputs.cot)

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

        symbol_levels = None
        if levels_by_symbol is not None and symbol in levels_by_symbol.index:
            symbol_levels = levels_by_symbol.loc[symbol]
        gex_label = GEX_WALL if targets is not None and targets.wall_status == "ABOVE" else OI_CLUSTER
        plan = build_plan(
            rating=rating.rating,
            levels=symbol_levels,
            spot=spot,
            gex_target=targets.primary if targets else None,
            gex_label=gex_label,
            stretch=targets.stretch if targets else None,
            sizing=sizing_config,
            max_pullback_pct=max_pullback,
        )

        asset_class = str(mom["asset_class"]).capitalize()
        news_text, news_flag_text = news_context(inputs.news, symbol, rating.rating, news_config)
        insider = insider_flags.get(symbol)
        verdict = fn.evaluate(
            fn.FunnelInputs(
                delta_signal=mom["delta_signal"],
                score=_clean(mom["score"]),
                rating=rating.rating,
                regime=regime,
                setup=plan.status,
                zone_quality=plan.zone_quality,
                stop_atr=plan.stop_atr,
                rr=plan.rr,
                has_insider_flag=insider is not None,
                has_news_flag=news_flag_text is not None,
            ),
            funnel_config,
        )
        if verdict.multiplier != 1.0:
            # Re-derive so Size and the dollar columns carry the conviction cut, and a name stopped at a hard
            # gate shows no size at all rather than a full position it is not eligible for.
            plan = build_plan(
                rating=rating.rating, levels=symbol_levels, spot=spot,
                gex_target=targets.primary if targets else None, gex_label=gex_label,
                stretch=targets.stretch if targets else None, sizing=sizing_config,
                max_pullback_pct=max_pullback, multiplier=verdict.multiplier,
            )
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
        ict_row = ict_by_symbol.loc[symbol].to_dict() if ict_by_symbol is not None and symbol in ict_by_symbol.index else {}
        flow_row = (
            flow_by_symbol.loc[symbol].to_dict() if flow_by_symbol is not None and symbol in flow_by_symbol.index else {}
        )
        ict_table = pos.level_table(spot, _clean(ict_row.get("atr")), {**flow_row, **ict_row})
        screen = pos.screen(s4, s1, _clean(mom["score"]), pos_config)
        votes = _layer_votes(
            screen.direction, mom["delta_signal"], s1, regime if has_gex else None, spot, call_wall,
            cot_by_market.get(pos_config.cot_markets.get(symbol)), _clean(funding.get(symbol)), ict_row,
            insider is not None, news_flag_text is not None, pos_config,
        ) if screen.passes else []
        flow_votes = pos.flow_votes(screen.direction, spot, flow_row, pos_config) if screen.passes else []
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
                plan=plan,
                funnel=verdict,
                insider=insider,
                screen=screen,
                votes=votes,
                conviction=pos.conviction(votes, pos_config) if screen.passes else None,
                ict_table=ict_table,
                flow_votes=flow_votes,
                flow_score=pos.flow_score(flow_votes) if flow_votes else None,
            )
        )

    _annotate_reads(rows, inputs)
    rows.sort(key=_summary_sort_key)
    return rows, ladders


CONVICTION_ORDER = {pos.HIGH: 0, pos.MEDIUM: 1, pos.LOW: 2}


def _summary_sort_key(row: ReportRow) -> tuple:
    """Names that pass the trend/momentum screen first, then conviction (net layer agreement), then the
    backtest-measured rank, then ticker so the order is deterministic."""
    passes = row.screen is not None and row.screen.passes
    c = row.conviction
    return (
        0 if passes else 1,
        CONVICTION_ORDER[c.level] if c else 3,
        -(c.agree - c.disagree) if c else 0,
        row.rank if row.rank is not None else 10_000,
        row.ticker,
    )


def _latest_cot(cot: pd.DataFrame | None) -> dict[str, pd.Series]:
    """Most recent COT row per market name, so a ticker can be matched through positioning.cot_markets."""
    if cot is None or cot.empty or "market_and_exchange_names" not in cot.columns:
        return {}
    latest = cot.sort_values("observation_date").drop_duplicates("market_and_exchange_names", keep="last")
    return {row["market_and_exchange_names"]: row for _, row in latest.iterrows()}


def _layer_votes(
    direction: str, signal, structure_1h: str, regime: str | None, spot, call_wall, cot_row, funding, ict_row: dict,
    has_insider_flag: bool, has_news_flag: bool, config: pos.PositioningConfig,
) -> list[pos.Vote]:
    """Every confluence layer's vote against the screened direction, in the order they appear on the Detail read."""
    opens = {key: _clean(ict_row.get(key)) for key, _ in pos.OPEN_LEVELS}
    return [
        pos.structure_vote(direction, structure_1h),
        pos.delta_vote(direction, signal),
        pos.gamma_vote(direction, regime),
        pos.wall_vote(direction, spot, call_wall, config),
        pos.opens_vote(direction, spot, opens, config),
        pos.volume_vote(direction, _clean(ict_row.get("rvol_1d")), _clean(ict_row.get("ret_1d")), config),
        pos.cot_vote(
            direction,
            _clean(cot_row["spec_net_percentile"]) if cot_row is not None else None,
            _clean(cot_row["spec_net_change_1w"]) if cot_row is not None else None,
            config,
        ),
        pos.funding_vote(direction, funding, config),
        pos.flags_vote(has_insider_flag, has_news_flag),
    ]


def _annotate_reads(rows: list[ReportRow], inputs: ReportInputs) -> None:
    """Attach the plain technical read, the measured edge for that setup profile, and the resulting rank."""
    # Ranking is read from the ICT target-exit backtest (resolved against a real level, not a fixed R-multiple)
    # - not inputs.backtest, which sweeps synthetic R-multiples and still exists for the Backtest tab's own
    # diagnostic views.
    lookup = dr.expectancy_lookup(inputs.backtest_target)
    for row in rows:
        plan = row.plan
        quality = fn.LEVEL_LABELS.get(plan.zone_quality) if plan and plan.zone_quality else None
        stop_atr = plan.stop_atr if plan else None
        row.read = dr.read_sentence(
            trend=fn.TREND_LABELS.get(row.signal),
            zone_quality=quality,
            stop_atr=stop_atr,
            setup=plan.status if plan else None,
            regime=row.regime,
            zone_status=plan.zone_status if plan else None,
        )
        tradeable = plan is not None and plan.entry is not None
        if tradeable:
            # Raw zone_quality (STRONG/CONFIRMED/PLAIN), not the plain-English label above - it has to match
            # the backtest's own bucket names.
            row.edge, row.edge_text = dr.historical_edge(
                lookup, row.score, zone_quality=plan.zone_quality, zone_status=plan.zone_status
            )
        else:
            row.edge, row.edge_text = None, ""
    ranks = dr.rank_reads([(row.ticker, row.edge) for row in rows])
    for row in rows:
        row.rank = ranks.get(row.ticker)


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
    if inputs.flow is None:
        gaps.append(
            "No flow_indicators.parquet, so the money-flow, relative-strength, VWAP and volume-profile columns read n/a. "
            "Run: uv run python -m market_data_pipeline.transforms.flow_indicators"
        )
    if inputs.levels is not None:
        no_setup = [row.ticker for row in rows if row.plan and row.plan.status in (NO_SETUP, RESISTANCE_ONLY)]
        if no_setup:
            gaps.append(
                f"No long entry zone within the lookback window for {', '.join(no_setup)} - either every gap has been "
                "filled or the name is rated SELL / AVOID."
            )
        thin = inputs.levels[inputs.levels["bars_available"] < 30]
        if not thin.empty:
            gaps.append(
                f"Thin daily history for {', '.join(sorted(thin['symbol']))} (under 30 bars), so the dealing range and "
                "zone scan rest on less data than intended."
            )
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


SETUP_FILLS = {
    "AT_ENTRY": "FFC6EFCE",
    "WAIT_PULLBACK": "FFFFEB9C",
    "EXTENDED": "FFFFEB9C",
    "NO_SETUP": "FFF2F2F2",
    "RESISTANCE_ONLY": "FFFFC7CE",
    "NO_LEVELS": "FFF2F2F2",
}


def _detail_columns(rows: list[ReportRow], inputs: ReportInputs) -> list[dict]:
    """Every field, for the Detail tab. The Summary keeps only what a decision needs."""
    plan_value = lambda attr: lambda row, r: _na(getattr(row.plan, attr) if row.plan else None)  # noqa: E731
    columns = [
        {"title": "Ticker", "width": 10, "value": lambda row, r: row.ticker},
        {"title": "Asset Class", "width": 12, "value": lambda row, r: row.asset_class},
        {"title": "Delta Signal", "width": 16, "value": lambda row, r: _na(row.signal)},
        {"title": "Score", "width": 8, "value": lambda row, r: _na(None if row.score is None else round(row.score, 3))},
        {"title": "Rating", "width": 34, "value": lambda row, r: row.rating.rating},
        {"title": "GEX Regime", "width": 18, "value": lambda row, r: row.regime},
        {"title": "1H", "width": 11, "value": lambda row, r: row.structure_1h},
        {"title": "4H", "width": 11, "value": lambda row, r: row.structure_4h},
        {"title": "Setup", "width": 16, "value": lambda row, r: row.plan.status if row.plan else NO_LEVELS},
        {"title": "Zone", "width": 12, "value": plan_value("zone_quality")},
        {"title": "Entry", "width": 12, "format": "#,##0.##", "value": plan_value("entry")},
        {"title": "Stop", "width": 12, "format": "#,##0.##", "value": plan_value("stop")},
        {"title": "Stop ATRs", "width": 11, "format": '0.0"x"', "value": plan_value("stop_atr")},
        {"title": "Risk %", "width": 9, "format": "0.0%", "value": plan_value("risk_pct")},
        {"title": "Level Target", "width": 14, "format": "#,##0.##", "value": plan_value("target")},
        {"title": "R:R", "width": 8, "format": "0.00", "value": plan_value("rr")},
        {"title": "Target From", "width": 14, "value": plan_value("target_source")},
        {"title": "Size", "width": 10, "value": plan_value("size")},
        {"title": "Position $", "width": 13, "format": "#,##0", "value": plan_value("position_usd")},
        {"title": "Risk $", "width": 11, "format": "#,##0", "value": plan_value("risk_usd")},
        {
            "title": "Insider", "width": 18,
            "value": lambda row, r: (
                f"{row.insider['sells']} sells / {row.insider['owners']} insiders" if row.insider else ""
            ),
        },
        {"title": "Spot", "width": 12, "format": "#,##0.00", "value": lambda row, r: _na(row.spot)},
        {
            "title": "Primary Target", "width": 15, "format": "#,##0.##",
            "value": lambda row, r: _na(row.targets.primary if row.targets else None),
        },
        {
            "title": "Dist. to Target", "width": 14, "format": "0.0%",
            "value": lambda row, r: _na(
                row.targets.primary / row.spot - 1
                if row.targets and row.targets.primary is not None and row.spot
                else None
            ),
        },
    ]
    if inputs.news is not None:
        columns.append({"title": "News", "width": 60, "wrap": True, "value": lambda row, r: row.news})
    columns.append({"title": "Note", "width": 90, "wrap": True, "value": lambda row, r: row.note})
    if inputs.levels is None:
        columns = [c for c in columns if c["title"] not in LEVEL_COLUMN_TITLES]
    if any(row.rule_note for row in rows):
        columns.append(
            {"title": "Note (rule-based)", "width": 90, "wrap": True, "value": lambda row, r: row.rule_note or row.note}
        )
    return columns


# Banner groups on the Summary: the screen first, then the layers that confirm or contradict it, then the read
# on conviction, where price sits against the ICT levels, and what a trade would cost.
SUMMARY_BANNERS = [
    ("", 1), ("SCREEN: TREND + MOMENTUM", 5), ("LAYERS (+ agrees, - against, 0 neutral)", 9), ("CONVICTION", 3),
    ("FLOW (shown, not in conviction)", 8), ("ICT LEVELS vs SPOT", 5), ("TRADE PLAN", 4), ("RISK", 4), ("EVIDENCE", 2),
]

LAYER_TITLES = ["1H", "Delta", "Gamma", "Wall", "Opens", "Volume", "COT", "Funding", "Flags"]
FLOW_TITLES = ["CMF", "MFI", "OBV", "RS", "Size", "VWAP", "Profile"]
VOTE_SYMBOLS = {pos.AGREE: "+", pos.DISAGREE: "-", pos.NEUTRAL: "0", None: "n/a"}
VOTE_FILLS = {pos.AGREE: "FFC6EFCE", pos.DISAGREE: "FFFFC7CE"}
SCREEN_FILLS = {pos.LONG: "FFC6EFCE", pos.SHORT: "FFFFC7CE", pos.NO_TREND: "FFF2F2F2"}
CONVICTION_FILLS = {pos.HIGH: "FFC6EFCE", pos.MEDIUM: "FFFFEB9C", pos.LOW: "FFF2F2F2"}


def _vote_cell(index: int, attr: str = "votes") -> Callable:
    def value(row: ReportRow, r: int) -> str:
        votes = getattr(row, attr)
        return VOTE_SYMBOLS[votes[index].vote] if votes else ""

    return value


def _vote_fill(index: int, attr: str = "votes") -> Callable:
    def fill(row: ReportRow) -> str | None:
        votes = getattr(row, attr)
        return VOTE_FILLS.get(votes[index].vote) if votes else None

    return fill


def _level_text(level: dict | None) -> str:
    if level is None:
        return "-"
    text = f"{level['label']} {level['pct'] * 100:+.1f}%"
    return f"{text} ({level['status'].lower()})" if level["status"] else text


def _summary_columns(rows: list[ReportRow], inputs: ReportInputs) -> list[dict]:
    """The positioning screen: trend and momentum gate, layered confirmation, conviction, ICT levels from spot.

    Deliberately descriptive. Layers read the same direction the screen picked and never change the rating,
    the size or the rank-by-measured-edge underneath, because none of them has been backtested yet.
    """
    plan_value = lambda attr: lambda row, r: _na(getattr(row.plan, attr) if row.plan else None)  # noqa: E731
    structure = lambda attr: lambda row, r: getattr(row, attr).replace("UPTREND", "Up").replace("DOWNTREND", "Down").title()  # noqa: E731
    columns = [
        {"title": "Ticker", "width": 10, "value": lambda row, r: row.ticker},
        # --- SCREEN: is there a trend, and is momentum with it?
        {
            "title": "Screen", "width": 10, "value": lambda row, r: row.screen.direction if row.screen else "-",
            "fill": lambda row: SCREEN_FILLS[row.screen.direction] if row.screen else None,
        },
        {"title": "4H structure", "width": 12, "value": structure("structure_4h")},
        {"title": "1H structure", "width": 12, "value": structure("structure_1h")},
        {"title": "Score", "width": 8, "value": lambda row, r: _na(None if row.score is None else round(row.score, 3))},
        {"title": "Mom. trend", "width": 15, "value": lambda row, r: fn.TREND_LABELS.get(row.signal, _na(row.signal))},
    ]
    # --- LAYERS: does anything else agree?
    for index, title in enumerate(LAYER_TITLES):
        columns.append({"title": title, "width": 8, "value": _vote_cell(index), "fill": _vote_fill(index)})
    columns += [
        # --- CONVICTION
        {
            "title": "Conviction", "width": 11, "value": lambda row, r: row.conviction.level if row.conviction else "-",
            "fill": lambda row: CONVICTION_FILLS[row.conviction.level] if row.conviction else None,
        },
        {
            "title": "Agree/Against", "width": 14,
            "value": lambda row, r: f"{row.conviction.agree} / {row.conviction.disagree}" if row.conviction else "-",
        },
        {
            "title": "Why", "width": 52, "wrap": True,
            "value": lambda row, r: row.conviction.reason if row.conviction else (row.screen.reason if row.screen else ""),
        },
    ]
    # --- FLOW: money flow, relative strength and VWAP/profile, kept out of the conviction count until backtested
    for index, title in enumerate(FLOW_TITLES):
        columns.append(
            {"title": title, "width": 8, "value": _vote_cell(index, "flow_votes"), "fill": _vote_fill(index, "flow_votes")}
        )
    columns += [
        {
            "title": "Flow", "width": 8,
            "value": lambda row, r: row.flow_score.text if row.flow_score else "-",
        },
        # --- ICT LEVELS vs SPOT
        {"title": "vs Opens", "width": 15, "value": lambda row, r: pos.opens_summary(row.ict_table)},
        {"title": "Nearest above", "width": 30, "value": lambda row, r: _level_text(pos.nearest(row.ict_table, "ABOVE"))},
        {"title": "Nearest below", "width": 30, "value": lambda row, r: _level_text(pos.nearest(row.ict_table, "BELOW"))},
        {"title": "Equal highs", "width": 24, "value": lambda row, r: _level_text(_level_by_key(row, "eqh"))},
        {"title": "Equal lows", "width": 24, "value": lambda row, r: _level_text(_level_by_key(row, "eql"))},
        # --- TRADE PLAN (the existing location-based entry/stop/target)
        {"title": "Buy at", "width": 11, "format": "#,##0.##", "value": plan_value("entry")},
        {"title": "Stop", "width": 11, "format": "#,##0.##", "value": plan_value("stop")},
        {"title": "Target", "width": 11, "format": "#,##0.##", "value": plan_value("target")},
        {"title": "Stop room", "width": 11, "format": '0.0"x"', "value": plan_value("stop_atr")},
        # --- RISK
        {"title": "Shares", "width": 9, "value": plan_value("size")},
        {"title": "Cost $", "width": 11, "format": "#,##0", "value": plan_value("position_usd")},
        {"title": "Risk $", "width": 10, "format": "#,##0", "value": plan_value("risk_usd")},
        {"title": "Reward:Risk", "width": 12, "format": "0.00", "value": plan_value("rr")},
        # --- EVIDENCE: what has this profile actually been worth?
        {"title": "Rank", "width": 6, "value": lambda row, r: _na(row.rank)},
        {"title": "Past edge", "width": 16, "value": lambda row, r: row.edge_text or "-"},
    ]
    return columns


def _level_by_key(row: ReportRow, key: str) -> dict | None:
    return next((level for level in row.ict_table if level["key"] == key), None)


def _edge_fill(row: ReportRow) -> str | None:
    """Green where the measured expectancy is positive, red where it is not. Most of this sheet is red."""
    if row.edge is None:
        return None
    return "FFC6EFCE" if row.edge > 0 else "FFFFC7CE"


ACTION_FILLS = {
    fn.FULL: "FFC6EFCE",
    fn.HALF: "FFD9EAD3",
    fn.QUARTER: "FFFFEB9C",
    fn.MINIMAL: "FFFFEB9C",
    fn.WATCH: "FFF2F2F2",
    fn.SKIP: "FFFFC7CE",
}

LEVEL_COLUMN_TITLES = {
    "Setup", "Zone", "Entry", "Stop", "Risk %", "Stop ATRs", "Level Target", "R:R", "Target From", "Size",
    "Position $", "Risk $",
}


def _write_rows(ws, rows: list[ReportRow], columns: list[dict], header_row: int, rules: dict) -> None:
    """Write the header and data rows for a column spec, applying the shared conditional fills."""
    for col, spec in enumerate(columns, 1):
        ws.cell(row=header_row, column=col, value=spec["title"])
    _style_header(ws, header_row, len(columns))

    titles = [spec["title"] for spec in columns]
    index_of = lambda title: titles.index(title) + 1 if title in titles else None  # noqa: E731
    fills = {
        "rating": (index_of("Rating"), lambda row: RATING_FILLS[row.rating.rating]),
        "setup": (index_of("Setup"), lambda row: SETUP_FILLS[row.plan.status] if row.plan else None),
        "action": (index_of("Action"), lambda row: ACTION_FILLS[row.funnel.action] if row.funnel else None),
        "edge": (index_of("Past edge"), lambda row: _edge_fill(row)),
    }
    stop_col = index_of("Stop room") or index_of("Stop ATRs")
    news_col = index_of("News")
    min_stop_atr = rules.get("location", {}).get("min_stop_atr", 1.5)

    for offset, row in enumerate(rows):
        r = header_row + 1 + offset
        for col, spec in enumerate(columns, 1):
            cell = ws.cell(row=r, column=col, value=spec["value"](row, r))
            if spec.get("format"):
                cell.number_format = spec["format"]
            cell.alignment = Alignment(wrap_text=bool(spec.get("wrap")), vertical="top")
        for col, spec in enumerate(columns, 1):
            colour = spec["fill"](row) if spec.get("fill") else None
            if colour:
                ws.cell(row=r, column=col).fill = PatternFill("solid", fgColor=colour)
        for col, colour_for in fills.values():
            colour = colour_for(row) if col else None
            if colour:
                ws.cell(row=r, column=col).fill = PatternFill("solid", fgColor=colour)
        if stop_col and row.plan and row.plan.stop_atr is not None and row.plan.stop_atr < min_stop_atr:
            # A stop inside ~1.5 average days gets taken out by noise rather than by being wrong.
            ws.cell(row=r, column=stop_col).fill = PatternFill("solid", fgColor="FFFFC7CE")
        if news_col and row.news_flag:
            ws.cell(row=r, column=news_col).fill = PatternFill("solid", fgColor="FFFFC7CE")

    for col, spec in enumerate(columns, 1):
        ws.column_dimensions[get_column_letter(col)].width = spec["width"]
    ws.freeze_panes = f"A{header_row + 1}"


def _snapshot_line(dates: dict) -> str:
    gex_date, momentum_date = dates["gex"], dates["momentum"]
    if gex_date == momentum_date:
        line = f"Data snapshot: all layers as of {gex_date}"
    else:
        line = (
            f"Data snapshot: GEX/Options/Perps as of {gex_date}  |  Momentum/price data as of {momentum_date} close "
            "(one-layer vintage difference - see Methodology)"
        )
    if dates.get("news"):
        line += f"  |  News through {dates['news']} (UTC)"
    return line


def _write_summary(ws, rows: list[ReportRow], inputs: ReportInputs, universe_size: int, rules: dict) -> None:
    """The one sheet to read: what to do and why, then momentum, levels and money in that order."""
    ws["A1"] = "Confluence Report"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = _snapshot_line(inputs.dates)
    passing = sum(1 for row in rows if row.screen and row.screen.passes)
    ws["A3"] = (
        f"{len(rows)} names, {passing} pass the trend + momentum screen. Passing names are listed first, ordered by "
        "conviction (how many layers agree), then by measured edge. Read left to right: screen, layers, conviction, "
        f"where price sits against the ICT levels, then the plan and cost. Full {universe_size}-name universe is on "
        "Momentum & Delta; every ICT level is on ICT Levels."
    )
    sizing = sizing_config_from_rules(rules)
    ws["A4"] = (
        f"Shares/Cost/Risk assume an account of {sizing.account_size:,.0f} risking {sizing.risk_per_trade_pct:g}% "
        f"({sizing.account_size * sizing.risk_per_trade_pct / 100:,.0f}) per trade, already reduced for weak levels, "
        "tight stops and insider flags. Past edge is measured, not predicted. Set ACCOUNT_SIZE in .env."
    )

    verdict = br.target_headline(inputs.backtest_target) or br.headline(inputs.backtest)
    if verdict:
        ws["A5"] = verdict
        ws["A5"].font = Font(bold=True, color="FF9C0006")

    columns = _summary_columns(rows, inputs)
    col = 1
    for label, span in SUMMARY_BANNERS:
        if label:
            ws.merge_cells(start_row=6, start_column=col, end_row=6, end_column=col + span - 1)
            cell = ws.cell(row=6, column=col, value=label)
            cell.font = Font(bold=True, color="FF1F4E78")
            cell.alignment = Alignment(horizontal="center")
        col += span

    _write_rows(ws, rows, columns, header_row=7, rules=rules)


def _write_ict_levels(ws, rows: list[ReportRow], inputs: ReportInputs) -> None:
    """Every ICT level for every shown name against spot, nearest first, so the Summary's two nearest can be checked."""
    ws["A1"] = "ICT Levels vs Spot"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = _snapshot_line(inputs.dates)
    if inputs.ict is None:
        ws["A3"] = "No ICT level data. Run: uv run python -m market_data_pipeline.transforms.ict_levels"
        return
    ws["A3"] = (
        "% = level / spot - 1 (positive means the level is above spot). ATRs = distance in average daily ranges. "
        "SWEPT = price has traded through it since it formed; UNTESTED = the liquidity still rests there."
    )
    headers = ["Ticker", "Spot", "Level", "Price", "% from spot", "ATRs", "Side", "Status"]
    for col, title in enumerate(headers, 1):
        ws.cell(row=5, column=col, value=title)
    _style_header(ws, 5, len(headers))
    r = 6
    for row in rows:
        for level in sorted(row.ict_table, key=lambda x: -x["pct"]):
            values = [
                row.ticker, _na(row.spot), level["label"], level["price"], level["pct"], _na(level["atr"]),
                level["side"].title(), level["status"] or "",
            ]
            for col, value in enumerate(values, 1):
                ws.cell(row=r, column=col, value=value)
            ws.cell(row=r, column=2).number_format = "#,##0.00"
            ws.cell(row=r, column=4).number_format = "#,##0.00"
            ws.cell(row=r, column=5).number_format = "+0.0%;-0.0%"
            ws.cell(row=r, column=6).number_format = '+0.0"x";-0.0"x"'
            r += 1
    for col, width in enumerate([10, 12, 16, 12, 12, 9, 9, 18], 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A6"


def _write_flow_backtest(ws, table: pd.DataFrame) -> None:
    """Forward return after each flow vote, so the Flow columns can be judged before they are trusted."""
    from market_data_pipeline.transforms.flow_backtest import HORIZONS, headline

    ws["A1"] = "Flow Backtest - Did The Flow Votes Predict Anything?"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = (
        "Each daily flow vote as it would have read on every past date (data up to that date only), against the move that "
        "followed. Returns are direction-adjusted: positive means the candidate direction (long when momentum was "
        "positive, short when negative) made money. Windows do not overlap within a name, but names move together and this "
        "is one two-year stretch of mostly rising markets with no costs, so treat gaps under about 0.5% as noise. "
        "VWAP and the volume profile are not tested (latest intraday bars only)."
    )
    ws["A3"] = "10-day headline: " + " | ".join(headline(table))
    ws["A3"].alignment = Alignment(wrap_text=True, vertical="top")
    headers = ["Layer", "Direction", "Horizon (days)", "Vote", "Observations", "Mean return", "Win rate"]
    for col, title in enumerate(headers, 1):
        ws.cell(row=5, column=col, value=title)
    _style_header(ws, 5, len(headers))
    order = {"ALL": 0, "CMF": 1, "MFI": 2, "OBV": 3, "Rel. strength": 4, "Trade size": 5}
    vote_order = {"all": 0, "agree": 1, "neutral": 2, "against": 3}
    ranked = table[table["horizon"].isin(HORIZONS)].assign(
        _l=table["layer"].map(order), _v=table["vote"].map(vote_order)
    ).sort_values(["horizon", "direction", "_l", "_v"], ascending=[True, False, True, True])
    for r, (_, row) in enumerate(ranked.iterrows(), 6):
        values = [row["layer"], row["direction"], int(row["horizon"]), row["vote"], int(row["observations"]),
                  row["mean_return"], row["win_rate"]]
        for col, value in enumerate(values, 1):
            ws.cell(row=r, column=col, value=value)
        ws.cell(row=r, column=6).number_format = "+0.00%;-0.00%"
        ws.cell(row=r, column=7).number_format = "0%"
    for col, width in enumerate([16, 11, 14, 10, 13, 13, 10], 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A6"


def _write_detail(ws, rows: list[ReportRow], inputs: ReportInputs, rules: dict) -> None:
    """Everything the Summary leaves out, for when you want to check the working."""
    ws["A1"] = "Detail - The Technicals Behind Each Read"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = _snapshot_line(inputs.dates)
    ws["A3"] = (
        "The raw technicals behind each row of the Daily Read: the rating waterfall, gamma regime, 1H/4H structure, "
        "the setup state and the exact prices. Use it to check why a read says what it says."
    )
    _write_rows(ws, rows, _detail_columns(rows, inputs), header_row=5, rules=rules)


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


def _write_scorecard(
    ws, summary: pd.DataFrame, start_row: int, control_view: str, extra_columns: list[tuple[int, str]],
    has_reward_sweep: bool,
) -> int:
    """Write one backtest scorecard's rows (view/bucket/trades/win rate/expectancy/extras/meaning).

    Returns the next free row. `extra_columns` is [(excel_column, dataframe_key), ...] for whatever sits
    between Expectancy and the meaning column - the R-multiple scorecard has two (median best/worst R), the
    ICT target-exit one has one (the median target size actually in play). `has_reward_sweep` is True only
    for a scorecard with an "All zones @ XR" family to compare a reward-sweep takeaway across; the target-exit
    scorecard has exactly one "ALL" row and gets no such takeaway.
    """
    meaning_col = 6 + len(extra_columns)
    control = br.control_expectancy(summary, view=control_view)
    all_zone_views = [v for v in summary["view"].unique() if br.is_all_zones(v)] if has_reward_sweep else []
    last_all_zone_view = all_zone_views[-1] if all_zone_views else None
    r = start_row
    for view, group in summary.groupby("view", sort=False):
        for _, row in group.iterrows():
            if pd.isna(row["win_rate"]):
                continue
            meaning = (
                br.control_note(control)
                if str(row["view"]) == control_view
                else br.row_verdict(row["expectancy_r"], row["trades"], control)
            )
            ws.cell(row=r, column=meaning_col, value=meaning).alignment = Alignment(wrap_text=True, vertical="top")
            ws.cell(row=r, column=1, value=str(row["view"]))
            ws.cell(row=r, column=2, value=str(row["bucket"]))
            ws.cell(row=r, column=3, value=int(row["trades"]))
            ws.cell(row=r, column=4, value=float(row["win_rate"])).number_format = "0.0%"
            cell = ws.cell(row=r, column=5, value=float(row["expectancy_r"]))
            cell.number_format = "+0.000;-0.000"
            cell.fill = PatternFill("solid", fgColor="FFC6EFCE" if row["expectancy_r"] > 0 else "FFFFC7CE")
            for col, key in extra_columns:
                value = row.get(key)
                ws.cell(row=r, column=col, value=None if pd.isna(value) else float(value)).number_format = "0.00"
            if "Control" in str(row["view"]):
                for col in range(1, meaning_col + 1):
                    ws.cell(row=r, column=col).font = Font(bold=True)
            r += 1
        # One line saying whether this way of slicing the trades is worth anything at all.
        if has_reward_sweep and br.is_all_zones(view):
            takeaway = br.target_takeaway(summary) if view == last_all_zone_view else ""
        elif str(view) in (control_view, "All zones (to target)"):
            takeaway = ""  # the control explains itself above; a single ALL row has nothing to compare across
        else:
            takeaway = br.feature_takeaway(group, control)
        if takeaway:
            cell = ws.cell(row=r, column=2, value=takeaway)
            cell.font = Font(italic=True)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            r += 1
        r += 1
    return r


def _write_backtest(ws, inputs: ReportInputs) -> None:
    """How these signals have actually performed on the bars collected so far - the reason to trust a read, or not."""
    ws["A1"] = "Backtest - Has Any Of This Worked?"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = (
        "Every historical zone entry scored on the rule the report uses: buy the near edge, stop at candle 1. "
        "Expectancy is R per trade, so positive makes money and negative loses it. The random-entry control is the "
        "bar any signal has to clear - a signal worse than random is worse than no signal."
    )
    ws["A3"] = (
        "The control's stop widths are sampled from the ones the zones actually used, because a fixed width is not a "
        "like-for-like comparison - tighter stops get hit more often whatever the entry. Momentum at entry is the one "
        "feature that has ordered outcomes consistently, so it drives position size; zone quality does not order once "
        "momentum is held fixed (see the crossed view), which is why it no longer changes size. Still a sample and not "
        "a verdict: trades cluster by name and date and there are no transaction costs. Re-run with "
        "uv run python -m market_data_pipeline.transforms.backtest"
    )
    if (inputs.backtest is None or inputs.backtest.empty) and (inputs.backtest_target is None or inputs.backtest_target.empty):
        ws["A5"] = "No backtest data. Run: uv run python -m market_data_pipeline.transforms.backtest"
        ws.column_dimensions["A"].width = 120
        return

    r = 5
    if inputs.backtest is not None and not inputs.backtest.empty:
        headers = ["View", "Bucket", "Trades", "Win rate", "Expectancy (R)", "Median best (R)", "Median worst (R)",
                   "What it means"]
        for col, title in enumerate(headers, 1):
            ws.cell(row=r, column=col, value=title)
        _style_header(ws, r, len(headers))
        r += 1
        r = _write_scorecard(
            ws, inputs.backtest, r, br.CONTROL_VIEW,
            [(6, "median_mfe_r"), (7, "median_mae_r")], has_reward_sweep=True,
        )
        r += 1

    ws.cell(row=r, column=1, value="ICT target-exit backtest - resolved against the real level, not a fixed R-multiple").font = Font(bold=True, size=12)
    r += 1
    ws.cell(
        row=r, column=1,
        value="Same zones, but held for CE / OTE / the dealing-range high / the nearest smooth swing high - "
        "whichever sat nearest above price at entry - instead of a fixed reward. This, not the scorecard above, "
        "is what Rank on the Summary tab is built on: momentum and the ICT read, with no stop-width factor.",
    ).alignment = Alignment(wrap_text=True, vertical="top")
    r += 2
    if inputs.backtest_target is not None and not inputs.backtest_target.empty:
        headers = ["View", "Bucket", "Trades", "Win rate", "Expectancy (R)", "Median target (R)", "What it means"]
        for col, title in enumerate(headers, 1):
            ws.cell(row=r, column=col, value=title)
        _style_header(ws, r, len(headers))
        r += 1
        r = _write_scorecard(
            ws, inputs.backtest_target, r, br.TARGET_CONTROL_VIEW, [(6, "median_target_rr")], has_reward_sweep=False,
        )
    else:
        ws.cell(row=r, column=1, value="No target-exit backtest data yet. Run: uv run python -m market_data_pipeline.transforms.backtest")
        r += 1
    r += 1

    if inputs.backtest_momentum is not None and not inputs.backtest_momentum.empty:
        ws.cell(row=r, column=1, value="Momentum score vs forward return (non-overlapping windows only)").font = Font(bold=True)
        r += 1
        for col, title in enumerate(["Horizon", "Score third", "Observations", "Mean return", "Win rate"], 1):
            ws.cell(row=r, column=col, value=title)
        _style_header(ws, r, 5)
        r += 1
        for _, row in inputs.backtest_momentum.iterrows():
            ws.cell(row=r, column=1, value=f"{int(row['horizon'])}d")
            ws.cell(row=r, column=2, value=str(row["bucket"]))
            ws.cell(row=r, column=3, value=int(row["observations"]))
            ws.cell(row=r, column=4, value=float(row["mean_return"])).number_format = "+0.00%;-0.00%"
            ws.cell(row=r, column=5, value=float(row["win_rate"])).number_format = "0.0%"
            r += 1

    r += 1
    ws.cell(row=r, column=1, value="What the columns mean").font = Font(bold=True, size=12)
    r += 1
    for term, meaning in br.GLOSSARY:
        ws.cell(row=r, column=1, value=term).font = Font(bold=True)
        cell = ws.cell(row=r, column=2, value=meaning)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        r += 1

    for col, width in enumerate([26, 62, 9, 11, 16, 16, 16, 52], 1):
        ws.column_dimensions[get_column_letter(col)].width = width


def _write_key_levels(ws, rows: list[ReportRow], inputs: ReportInputs) -> None:
    """Per name: the dealing range with its quadrant grid, then every live zone and the untested swing levels."""
    ws["A1"] = "Key Levels - Dealing Range, Zones and Untested Liquidity"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = (
        "Derived from daily bars only (transforms/location.py). A zone is a three-candle fair value gap; entry is its "
        "near edge and the stop is the pre-imbalance candle's extreme, not the zone boundary. Filled zones are dropped. "
        "Quality grades each zone on two independent qualifiers: a fast, directional break of the prior high/low "
        "(price displacement) and volume well above the imbalance candle's own trailing baseline. STRONG clears both, "
        "CONFIRMED clears one, PLAIN clears neither. Entries prefer the best available within reach. Status is the "
        "body-vs-wick respect test: RESPECTED means price has traded back into the zone and every candle body since "
        "has stayed on the entry side of its midpoint; FAILED means a body has already closed past the midpoint, an "
        "earlier and softer warning than a zone being fully filled; UNTESTED means price has not traded back in yet."
    )
    if inputs.levels is None:
        ws["A4"] = "No location data loaded. Run: uv run python -m market_data_pipeline.transforms.location"
        ws.column_dimensions["A"].width = 120
        return

    levels = inputs.levels.set_index("symbol")
    zones = inputs.zones if inputs.zones is not None else pd.DataFrame()
    r = 4
    for row in rows:
        if row.ticker not in levels.index:
            continue
        level = levels.loc[row.ticker]
        spot = _clean(level.get("spot"))
        header = f"{row.ticker}   (spot: {spot:,.2f} | {row.rating.rating})"
        inside_day = level.get("inside_day")
        if isinstance(inside_day, str):
            header += f"  |  Inside day ({inside_day.replace('_', ' ').title()}) - compression, watch for expansion"
        ws.cell(row=r, column=1, value=header).font = Font(bold=True)
        r += 1

        position = _clean(level.get("spot_pos_in_range"))
        note = "" if position is None else f"spot sits at {position * 100:.0f}% of the range"
        if position is not None and position > 1:
            note += " - broken out above it, so OTE/CE sit below spot"
        ws.cell(row=r, column=1, value="Dealing range").font = Font(bold=True)
        ws.cell(row=r, column=4, value=note)
        r += 1
        for label, key in [
            ("Range high", "range_high"),
            ("OTE (75%)", "range_ote"),
            ("CE (50%)", "range_ce"),
            ("Range low", "range_low"),
        ]:
            ws.cell(row=r, column=1, value=label)
            ws.cell(row=r, column=2, value=_na(_clean(level.get(key)))).number_format = "#,##0.##"
            r += 1

        live = pd.DataFrame()
        if not zones.empty:
            live = zones[(zones["symbol"] == row.ticker) & (~zones["filled"])]
        if live.empty:
            ws.cell(row=r, column=1, value="No live zones in the lookback window")
            r += 1
        else:
            for col, title in enumerate(
                ["Zone", "Low", "High", "Candle-1 stop", "Quality", "Status", "Volume", "Formed", "vs Spot"], 1
            ):
                ws.cell(row=r, column=col, value=title)
            _style_header(ws, r, 9)
            r += 1
            entry = row.plan.entry if row.plan else None
            for _, zone in live.sort_values("zone_high", ascending=False).iterrows():
                ws.cell(row=r, column=1, value="support" if zone["kind"] == "bull" else "resistance")
                ws.cell(row=r, column=2, value=float(zone["zone_low"])).number_format = "#,##0.##"
                ws.cell(row=r, column=3, value=float(zone["zone_high"])).number_format = "#,##0.##"
                ws.cell(row=r, column=4, value=float(zone["candle1_stop"])).number_format = "#,##0.##"
                ws.cell(row=r, column=5, value=str(zone["zone_quality"]))
                status = str(zone.get("zone_status") or "")
                status_cell = ws.cell(row=r, column=6, value=status)
                if status == "FAILED":
                    status_cell.fill = PatternFill("solid", fgColor="FFFFC7CE")
                ratio = _clean(zone.get("volume_ratio"))
                ws.cell(row=r, column=7, value=_na(ratio)).number_format = '0.00"x"'
                ws.cell(row=r, column=8, value=pd.Timestamp(zone["formed_at"]).strftime("%Y-%m-%d"))
                ws.cell(row=r, column=9, value=float(zone["distance_pct"])).number_format = "0.0%"
                if entry is not None and abs(float(zone["zone_high"]) - entry) < 1e-9:
                    for col in range(1, 10):
                        if col != 6 or status != "FAILED":
                            ws.cell(row=r, column=col).fill = PatternFill("solid", fgColor="FFC6EFCE")
                r += 1

        smooth = _clean(level.get("nearest_smooth_high"))
        summary = f"Untested (smooth) highs above spot: {int(level.get('n_smooth_highs_above', 0))}"
        if smooth is not None:
            summary += f", nearest {smooth:,.2f}"
        ws.cell(row=r, column=1, value=summary)
        r += 2

    for col, width in enumerate([16, 14, 14, 16, 12, 11, 10, 14, 12], 1):
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
        ("heading", "0. The Positioning Screen (Summary tab)"),
        ("text", "Step 1, the screen: the 4H swing structure sets the direction and the momentum score has to agree with it "
                 "(4H uptrend + positive score = LONG, 4H downtrend + negative score = SHORT, anything else = NO TREND and "
                 "the layers are not run)."),
        ("text", "Step 2, the layers: each one votes + (agrees), - (against) or 0 (neutral) relative to the screened direction, "
                 "or n/a when there is no data. 1H structure; momentum delta; gamma regime; room to the call wall (long only); "
                 "spot against the opens; the last completed day's volume and direction; speculator COT (metals only); perp "
                 "funding (crypto only, one snapshot); insider/news flags (only ever a vote against)."),
        ("text", "Step 3, conviction: net = agree minus against, over the layers that have data. "
                 f"HIGH at net >= {rules.get('positioning', {}).get('high_net', 4)}, "
                 f"MEDIUM at net >= {rules.get('positioning', {}).get('medium_net', 2)}, otherwise LOW. A layer with no data "
                 "is never counted against a name."),
        ("text", "ICT levels vs spot: opens (midnight, 8:30, 9:30 New York, weekly, monthly), the last completed Asia and London "
                 "session highs/lows with whether they have since been swept, and the nearest untouched equal highs/lows "
                 "(1H swing points within a fraction of 1H ATR). Equities do not trade overnight, so their midnight open and "
                 "Asia/London ranges are blank. The full table is on the ICT Levels tab."),
        ("text", "None of the layers or the conviction read has been backtested. They do not change the rating, the size or "
                 "the rank by measured edge; the sort order is screen pass, then conviction, then that measured rank."),
        ("heading", "0b. Flow Indicators (Summary tab, not part of conviction)"),
        ("text", "Seven extra columns vote + / - / 0 / n/a against the screened direction, with a Flow cell counting agree / "
                 "against. They are shown beside conviction, not inside it, because none has been backtested; "
                 "transforms/flow_backtest.py tests the daily ones."),
        ("text", "CMF (Chaikin money flow, 20 daily bars): volume weighted by where each bar closed in its range; positive is "
                 "accumulation. MFI (14): volume-weighted RSI; flow with the trade agrees, but an overbought (long) or "
                 "oversold (short) extreme reads against. OBV: on-balance volume against price over 20 bars, a bearish divergence "
                 "(price at a high, OBV not) is against a long. RS: change in close / benchmark over 20 bars (SPY, or BTC for "
                 "ETH and SOL). Size: average trade size (volume / trades) over 5 days vs 60, read with the 5-day direction."),
        ("text", "VWAP: spot against the anchored VWAPs from the weekly open, monthly open and the last swing low (long) or high "
                 "(short), from 1H bars. Profile: spot against the value area (70% of volume) of the last 20 sessions of 5m "
                 "bars. These and the POC / value-area edges are listed on the ICT Levels tab."),
        ("text", "Caveats: CMF, MFI and OBV all come from the same OHLCV and move together, so they are one family, not three "
                 "independent confirmations. Average trade size is distorted by order slicing and means something different for "
                 "crypto. Equity VWAP and profile include extended-hours bars, and the latest daily bar is the day in progress."),
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
        ("heading", "9. Location: Entry, Stop and Size"),
        ("text", "Levels come from daily bars via transforms/location.py, following reference/Location Confluence Playbook.md. "
                 "The dealing range is the last confirmed swing low to swing high (3 bars each side), graded into quadrants: "
                 "CE is the 50% midpoint, OTE the 3/4 completion reference. A zone is a three-candle fair value gap - candle 1's "
                 "high below candle 3's low for support - and stays live until a later bar trades fully through it."),
        ("text", "Each zone is graded on two independent qualifiers. Price displacement is the playbook's own test: the "
                 "imbalance candle's body exceeds a multiple of ATR and breaks the prior bars' extreme. Volume confirmation "
                 "is an addition, not an ICT concept: the same candle traded above a multiple of its own trailing volume "
                 "baseline, measured excluding itself. STRONG clears both, CONFIRMED clears one, PLAIN clears neither, and the "
                 "Zone column reports which the chosen entry rests on. They overlap only partly, so a CONFIRMED zone backed by "
                 "volume alone is real but weaker evidence than a STRONG one."),
        ("text", "Entry is the zone's near edge (the playbook's 'enter at the edge, not the midpoint'). The stop is candle 1's "
                 "extreme, the bar before the imbalance, not the zone's own boundary - which is why Risk % is often wider than "
                 "an ATR stop would be - Stop ATRs reports that distance in average daily ranges, and anything under the "
                 "configured minimum is highlighted because a stop inside normal daily movement gets taken out by noise "
                 "rather than by the idea failing. Risk %% cannot be compared across names; Stop ATRs can. "
                 "The target is whichever reference sits nearest above entry (OTE, the range high, an "
                 "untested swing high, the gamma wall or the OI cluster) and Target From names which one. Size risks the "
                 "configured percentage of the account between entry and stop, capped by the per-position limit."),
        ("text", "Setup states: AT_ENTRY (price is in the zone), WAIT_PULLBACK (the zone is below spot but within reach), "
                 "EXTENDED (the nearest zone is further below than the pullback limit - structure exists, but chasing here is "
                 "not the plan), NO_SETUP (no live support zone), RESISTANCE_ONLY (a SELL / AVOID name, shown with overhead "
                 "supply only). Levels are candidates for names the ranking already selected: they never change a rating, and "
                 "the pipeline does not track real open positions."),
        ("heading", "10. The Funnel: From Universe to Position Size"),
        ("text", "The Funnel tab walks each name through the stages in the order a decision is actually made, and the "
                 "Summary tab is ordered the same way so reading left to right follows the same path. Stages: DELTA "
                 "(momentum), RATING (the waterfall above), REGIME, SETUP, ZONE, STOP, RR, FLAGS."),
        ("text", "Hard gates stop a name outright and set the Stage column: a DECELERATING delta or a SELL rating is "
                 "SKIP; a rating with no live level (NO_SETUP, RESISTANCE_ONLY) is SKIP because there is nothing to "
                 "enter on; an EXTENDED setup or any non-BUY rating is WATCH. Everything else is a demerit, which "
                 "halves the suggested size rather than overriding an earlier stage - a plain zone, a stop inside the "
                 "minimum ATRs, an R:R below the minimum, an unstable regime, an insider-selling cluster or a news "
                 "flag. R:R ranks and never rejects."),
        ("text", "Action maps from what is left: FULL, HALF, QUARTER, then MINIMAL. Size, Position $ and Risk $ on the "
                 "Summary already carry that cut; the Funnel tab shows the full-risk figures beside them. Risk $ "
                 "landing under the per-trade budget means the position cap bound first, not an error. These are "
                 "mechanical gates on daily bars, and the pipeline still tracks no open positions, so nothing here "
                 "knows what you already hold."),
        ("heading", "11. Known Gaps in This Snapshot"),
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
    _write_detail(workbook.create_sheet("Detail"), rows, inputs, rules)
    _write_ict_levels(workbook.create_sheet("ICT Levels"), rows, inputs)
    _write_momentum(workbook.create_sheet("Momentum & Delta"), inputs.momentum)
    _write_ladders(workbook.create_sheet("Resistance Ladder Detail"), rows, ladders, TargetConfig(**rules.get("targets", {})))
    _write_key_levels(workbook.create_sheet("Key Levels"), rows, inputs)
    _write_backtest(workbook.create_sheet("Backtest"), inputs)
    if inputs.flow_backtest is not None and not inputs.flow_backtest.empty:
        _write_flow_backtest(workbook.create_sheet("Flow Backtest"), inputs.flow_backtest)
    _write_methodology(workbook.create_sheet("Methodology"), _methodology_lines(rules, gaps, inputs.dates))

    target = output_path or (data_dir / f"Confluence_Report_{report_date.isoformat()}.xlsx")
    try:
        workbook.save(target)
    except PermissionError as exc:
        # Excel holds an exclusive lock on an open workbook, which is easy to hit and baffling as a traceback.
        raise SystemExit(
            f"Could not write {target.name} - it is open in Excel. Close it and run this again, or pass "
            f"--output to write somewhere else."
        ) from exc
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
