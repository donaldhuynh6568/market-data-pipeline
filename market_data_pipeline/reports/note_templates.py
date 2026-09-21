from __future__ import annotations

from dataclasses import dataclass

from market_data_pipeline.reports import rating_rules as rr
from market_data_pipeline.reports.resistance_ladder import Targets

# Perp funding within +/- this per-interval rate is described as neutral (Hyperliquid's baseline is 0.00125%).
NEUTRAL_FUNDING_RATE = 0.0001


@dataclass(frozen=True)
class NoteContext:
    rating: rr.Rating
    asset_class: str
    signal: str | None
    score: float | None
    delta_5d: float | None
    delta_10d: float | None
    delta_20d: float | None
    regime: str
    structure_1h: str | None
    structure_4h: str | None
    spot: float | None
    targets: Targets | None
    funding_rate: float | None = None
    funding_venue: str = "Hyperliquid"
    insider: dict | None = None


def _price(value: float) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _pct(value: float) -> str:
    return f"{abs(value) * 100:.1f}%"


def _deltas(ctx: NoteContext) -> str:
    return f"5d {ctx.delta_5d:+.2f}, 10d {ctx.delta_10d:+.2f}, 20d {ctx.delta_20d:+.2f}"


def _regime_line(regime: str) -> str:
    return {
        "POSITIVE_GAMMA": "Positive gamma regime (dealer hedging dampens moves)",
        "NEGATIVE_GAMMA": "Negative gamma regime (dealer hedging amplifies moves)",
        "NEAR_FLIP": "Near-flip gamma regime (unstable)",
    }.get(regime, f"{regime.title()} gamma data")


def _structure_line(s1: str | None, s4: str | None) -> str:
    s1 = (s1 or "UNKNOWN").upper()
    s4 = (s4 or "UNKNOWN").upper()
    if s4 == "UPTREND" and s1 == "DOWNTREND":
        return "1H downtrend reads as a pullback inside the 4H uptrend, not a reversal"
    if s4 == "DOWNTREND" and s1 == "UPTREND":
        return "1H uptrend is a bounce inside a 4H downtrend until 4H itself turns"
    return f"1H {s1.lower()} / 4H {s4.lower()}"


def _wall_line(targets: Targets | None) -> str | None:
    if targets is None or targets.call_wall is None or targets.wall_distance is None:
        return None
    if abs(targets.wall_distance) < 0.0005:
        return f"Spot is sitting on the reported wall ({_price(targets.call_wall)})"
    if targets.wall_status == "CLEARED":
        return f"Already {_pct(targets.wall_distance)} through reported wall ({_price(targets.call_wall)})"
    return f"{_pct(targets.wall_distance)} below wall ({_price(targets.call_wall)})"


def _target_line(targets: Targets | None) -> str | None:
    if targets is None or targets.primary is None:
        return None
    line = f"Primary target {_price(targets.primary)}"
    if targets.primary_oi:
        line += f" ({targets.primary_oi:,.0f} call OI)"
    if targets.stretch is not None:
        line += f"; stretch {_price(targets.stretch)}"
        if targets.stretch_oi:
            line += f" ({targets.stretch_oi:,.0f} call OI)"
    return line


def _funding_line(ctx: NoteContext) -> str | None:
    if ctx.asset_class.lower() != "crypto" or ctx.funding_rate is None:
        return None
    rate = ctx.funding_rate
    if abs(rate) <= NEUTRAL_FUNDING_RATE:
        stance = "neutral, no leverage stress"
    elif rate > 0:
        stance = "positive, longs paying (crowded long)"
    else:
        stance = "negative, shorts paying (crowded short)"
    return f"{ctx.funding_venue} funding {rate * 100:.5f}% ({stance})"


def _insider_line(insider: dict | None) -> str | None:
    if not insider:
        return None
    return (
        f"Insider selling flag: {insider['sells']} open-market sells by {insider['owners']} insiders "
        f"in the last {insider['window_days']} days - an independent bearish signal to weigh alongside this"
    )


def _core_sentences(ctx: NoteContext) -> list[str | None]:
    reason = ctx.rating.reason
    score = f"score {ctx.score:+.3f}" if ctx.score is not None else "score n/a"
    structure = _structure_line(ctx.structure_1h, ctx.structure_4h)
    regime = _regime_line(ctx.regime)
    wall = _wall_line(ctx.targets)
    target = _target_line(ctx.targets)

    if reason == rr.R_DECELERATING:
        return [
            f"Decelerating on all three windows ({_deltas(ctx)}); {score}",
            regime,
            structure,
            "Do not treat a bounce as a re-entry signal until delta turns positive",
        ]
    if reason == rr.R_CONFIRMED:
        return [
            f"Accelerating on all three windows ({_deltas(ctx)}); {score}",
            regime,
            structure,
            wall,
            target,
        ]
    if reason == rr.R_NEAR_WALL:
        return [
            f"Live test: accelerating ({_deltas(ctx)}) with spot right at the call wall, and 4H has not confirmed an uptrend",
            wall,
            structure,
            "Wait for a confirmed close through the wall; do not chase pre-break",
            target,
        ]
    if reason == rr.R_MIXED_DELTA:
        return [
            f"Delta is mixed ({_deltas(ctx)}); {score}",
            regime,
            structure,
            wall,
            "Wait for the near-term delta to align before treating the wall or target as live",
        ]
    if reason == rr.R_REGIME:
        return [
            f"Accelerating ({_deltas(ctx)}) but the gamma regime undermines confidence in nearby levels",
            regime,
            structure,
            wall,
            target,
        ]
    if reason == rr.R_STRUCTURE_CONFLICT:
        return [
            f"Momentum/structure conflict: delta is accelerating ({_deltas(ctx)}) but 4H structure is in a downtrend",
            regime,
            wall,
            "Reconsider as BUY once 4H confirms",
        ]
    if reason == rr.R_NO_GEX:
        signal = (ctx.signal or "unknown").lower()
        return [
            "DATA GAP: no options coverage, so no GEX regime, wall or target can be derived",
            f"This read is momentum ({signal}, {_deltas(ctx)}) and structure ({structure}) only",
        ]
    if reason == rr.R_NO_MOMENTUM:
        return ["Unclassified: not enough price history for the score and delta windows; review manually"]
    return [f"Unclassified: {reason.lower().replace('_', ' ')}; review manually"]


def build_note(ctx: NoteContext) -> str:
    """Deterministic note: same inputs always produce the same string."""
    sentences = _core_sentences(ctx) + [_funding_line(ctx), _insider_line(ctx.insider)]
    return ". ".join(s for s in sentences if s) + "."
