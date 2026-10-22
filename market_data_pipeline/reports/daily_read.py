from __future__ import annotations

import numpy as np
import pandas as pd

# Bands must match the ones transforms/backtest.py's run_target_backtest reports on, so a row can be looked
# up in its own scorecard.
MOMENTUM_BANDS = [(-np.inf, 0.0, "negative"), (0.0, 0.2, "0-0.2"), (0.2, np.inf, ">0.2")]

# Ranking rests on the ICT target-exit backtest (transforms/backtest.py's run_target_backtest), which resolves
# each trade against the real level a live plan would hold for instead of a fixed R-multiple. Stop width is a
# risk-sizing input - how big a position to take - not a reason one setup should rank above another, so it is
# deliberately not one of these views.
MOMENTUM_VIEW = "Momentum at entry (to target)"
QUALITY_VIEW = "Zone quality (to target)"
WEAK_ENTRY_VIEW = "Weak close on entry (to target)"
# The live zone_status read (location.py) can reflect more than one test since the zone formed; the backtest's
# weak/clean split only measures the trigger bar itself (the first contact). FAILED/RESPECTED map onto it as
# the closest honest match; UNTESTED/FILLED have no equivalent bucket and contribute nothing rather than being
# forced into one.
ZONE_STATUS_TO_WEAK_ENTRY_BUCKET = {"RESPECTED": "False", "FAILED": "True"}


def _band(value: float | None, bands) -> str | None:
    if value is None or not np.isfinite(value):
        return None
    for low, high, label in bands:
        if low <= value < high:
            return label
    return None


def momentum_band(score: float | None) -> str | None:
    return _band(score, MOMENTUM_BANDS)


def expectancy_lookup(backtest: pd.DataFrame | None) -> dict[tuple[str, str], tuple[float, int]]:
    """{(view, bucket): (expectancy_r, trades)} from the backtest scorecard."""
    if backtest is None or backtest.empty:
        return {}
    table = {}
    for _, row in backtest.iterrows():
        if pd.isna(row.get("expectancy_r")):
            continue
        table[(str(row["view"]), str(row["bucket"]))] = (float(row["expectancy_r"]), int(row["trades"]))
    return table


def historical_edge(
    lookup: dict, score: float | None, zone_quality: str | None = None, zone_status: str | None = None
) -> tuple[float | None, str]:
    """Average measured expectancy of the buckets this setup falls into, plus a short description.

    Ranks on momentum plays and the ICT read - zone quality, and whether the chosen entry zone has already
    shown a body-close failure - because those are the things actually measured against a trade's real exit
    (transforms/backtest.py's target_trades). Stop width (risk sizing) plays no part: it decides how big a
    position to take, not whether one setup deserves to rank above another.

    This is not a prediction. It is what setups sharing these characteristics have been worth on the bars
    collected so far, which is the only honest basis for ranking one read above another.
    """
    if not lookup:
        return None, ""
    weak_bucket = ZONE_STATUS_TO_WEAK_ENTRY_BUCKET.get(zone_status)
    parts = []
    for view, bucket in (
        (MOMENTUM_VIEW, momentum_band(score)),
        (QUALITY_VIEW, zone_quality),
        (WEAK_ENTRY_VIEW, weak_bucket),
    ):
        if bucket and (view, bucket) in lookup:
            parts.append(lookup[(view, bucket)])
    if not parts:
        return None, ""
    mean = sum(value for value, _ in parts) / len(parts)
    trades = min(count for _, count in parts)
    return mean, f"{mean:+.2f}R (n>={trades})"


def read_sentence(
    trend: str | None,
    zone_quality: str | None,
    stop_atr: float | None,
    setup: str | None,
    regime: str | None,
    zone_status: str | None = None,
) -> str:
    """Describe the technical picture. States what is there; it does not tell you to trade it."""
    trend_text = {
        "Speeding up": "Momentum building",
        "Mixed": "Momentum mixed",
        "Slowing down": "Momentum fading",
    }.get(trend, "Momentum unclear")

    if setup in (None, "NO_SETUP", "NO_LEVELS"):
        return f"{trend_text}, but no level to trade against"
    if setup == "RESISTANCE_ONLY":
        # Rated sell, so no position - but the level is still shown, and the sentence should say why.
        if zone_quality:
            return f"{trend_text}, level below is reference only"
        return f"{trend_text}, overhead supply only"

    level_text = {
        "Strong": "into a strong level",
        "Medium": "into a fair level",
        "Weak": "into a weak level",
    }.get(zone_quality, "with no graded level")

    if stop_atr is None or not np.isfinite(stop_atr):
        stop_text = "stop distance unknown"
    elif stop_atr < 1.0:
        stop_text = "stop inside one day's noise"
    elif stop_atr < 1.5:
        stop_text = "stop a little tight"
    else:
        stop_text = "stop has room"

    sentence = f"{trend_text} {level_text}, {stop_text}"
    if regime in ("NEGATIVE_GAMMA", "NEAR_FLIP"):
        sentence += "; unstable options regime"
    elif regime in (None, "NO DATA"):
        sentence += "; no options read"
    if zone_status == "FAILED":
        # select_entry_zone only excludes a zone once it is fully FILLED - a zone can still be the chosen
        # entry while already having had a candle body close past its midpoint (location.py's zone_status).
        # That is worth saying plainly: the level is nominally live but has already shown a crack.
        sentence += "; this level already had a close back through its midpoint"
    if setup == "AT_ENTRY":
        sentence += "; price is at the level now"
    elif setup == "EXTENDED":
        sentence += "; price far above the level"
    return sentence


def rank_reads(keys: list[tuple[str, float | None]]) -> dict[str, int]:
    """Rank names by measured edge, best first. Names with nothing to trade rank last, in ticker order."""
    scored = [(ticker, edge) for ticker, edge in keys if edge is not None]
    unscored = sorted(ticker for ticker, edge in keys if edge is None)
    scored.sort(key=lambda item: (-item[1], item[0]))
    return {ticker: i + 1 for i, (ticker, _) in enumerate(scored + [(t, None) for t in unscored])}
