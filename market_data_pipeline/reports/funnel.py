from __future__ import annotations

from dataclasses import dataclass, field

from market_data_pipeline.reports import position_levels as pl
from market_data_pipeline.reports import rating_rules as rr

# Stages in the order a decision is actually made, mirroring the first-match-wins shape of rating_rules.rate().
DELTA = "DELTA"
RATING = "RATING"
REGIME = "REGIME"
SETUP = "SETUP"
ZONE = "ZONE"
STOP = "STOP"
RR = "RR"
FLAGS = "FLAGS"
STAGES = (DELTA, RATING, REGIME, SETUP, ZONE, STOP, RR, FLAGS)
CLEARED = "CLEARED"

# What to do with the name once the stages have run.
FULL = "FULL"
HALF = "HALF"
QUARTER = "QUARTER"
MINIMAL = "MINIMAL"
WATCH = "WATCH"
SKIP = "SKIP"

PASS = "PASS"
FAIL = "FAIL"
NA = "-"

UNSTABLE_REGIMES = ("NEGATIVE_GAMMA", "NEAR_FLIP", rr.NO_DATA)
# A rating with nowhere to enter is not a trade, however good the momentum looks.
NO_TRADE_SETUPS = (pl.NO_SETUP, pl.RESISTANCE_ONLY, pl.NO_LEVELS)


# Base size tier from the momentum score at entry. Measured on 3,943 zone trades over two years:
# score >0.2 returned +0.210R, 0-0.2 returned +0.028R, negative returned -0.194R. Momentum is the one
# feature that separated outcomes consistently, so it sets the tier rather than acting as one demerit.
MOMENTUM_TIERS = ((0.2, 1.0), (0.0, 0.5), (-float("inf"), 0.25))


@dataclass(frozen=True)
class FunnelConfig:
    min_rr: float = 1.0
    # 1.0, not 1.5: on the two-year sample only stops under 1 ATR were clearly bad (-0.114R, n=1350).
    # Between 1.0 and 2.5 ATR the effect is flat (-0.002R and -0.009R), so penalising 1.0-1.5 was unjustified.
    min_stop_atr: float = 1.0
    demerit_multiplier: float = 0.5
    min_multiplier: float = 0.125


def momentum_tier(score: float | None) -> float:
    """Base size multiplier from the momentum score. Unknown score is treated as the middle tier, not the best."""
    if score is None or not (score == score):  # NaN-safe
        return 0.5
    for floor, multiplier in MOMENTUM_TIERS:
        if score >= floor:
            return multiplier
    return MOMENTUM_TIERS[-1][1]


def funnel_config_from_rules(rules: dict) -> FunnelConfig:
    section = dict(rules.get("funnel", {}))
    section.setdefault("min_stop_atr", rules.get("location", {}).get("min_stop_atr", FunnelConfig.min_stop_atr))
    allowed = set(FunnelConfig.__dataclass_fields__)
    return FunnelConfig(**{k: v for k, v in section.items() if k in allowed})


@dataclass(frozen=True)
class FunnelInputs:
    delta_signal: str | None
    score: float | None
    rating: str
    regime: str
    setup: str
    zone_quality: str | None
    stop_atr: float | None
    rr: float | None
    has_insider_flag: bool = False
    has_news_flag: bool = False


@dataclass(frozen=True)
class FunnelResult:
    stage: str  # the first hard gate that stopped it, or CLEARED
    action: str
    multiplier: float
    demerits: list[str] = field(default_factory=list)
    gates: dict[str, str] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.action in (FULL, HALF, QUARTER, MINIMAL)


# Plain-English labels, so the sheet reads as instructions rather than as jargon.
ACTION_LABELS = {
    FULL: "Buy full",
    HALF: "Buy half",
    QUARTER: "Buy quarter",
    MINIMAL: "Buy tiny",
    WATCH: "Wait",
    SKIP: "Skip",
}
TREND_LABELS = {
    "ACCELERATING": "Speeding up",
    "MIXED": "Mixed",
    "DECELERATING": "Slowing down",
    "UNKNOWN": "Not enough history",
}
LEVEL_LABELS = {"STRONG": "Strong", "CONFIRMED": "Medium", "PLAIN": "Weak"}

# Why a name was stopped, keyed by the rating's own reason code where the stage alone is not specific enough.
_RATING_REASONS = {
    rr.R_MIXED_DELTA: "Momentum is mixed, not confirmed yet",
    rr.R_REGIME: "Options regime is unstable",
    rr.R_STRUCTURE_CONFLICT: "Momentum and the 4-hour trend disagree",
    rr.R_NO_GEX: "No options data for this name",
    rr.R_NO_MOMENTUM: "Not enough price history yet",
    rr.R_NO_STRUCTURE: "Not enough history to read the trend",
}
_DEMERIT_LABELS = {
    "insider selling": "Insiders selling",
    "news": "Negative news",
}


def plain_reason(result: FunnelResult, rating_reason: str | None = None, setup: str | None = None) -> str:
    """One sentence saying why, in words rather than stage names."""
    if result.stage == DELTA:
        return "Momentum is slowing down"
    if result.stage == RATING:
        if result.action == SKIP:
            return "Rated sell, avoid"
        return _RATING_REASONS.get(rating_reason, "Not a clean buy yet")
    if result.stage == SETUP:
        if setup == pl.EXTENDED:
            return "Price is too far above the buy level"
        return "No level to buy at right now"
    if not result.demerits:
        return "Everything lines up"

    parts = []
    for demerit in result.demerits:
        if demerit in _DEMERIT_LABELS:
            parts.append(_DEMERIT_LABELS[demerit])
        elif demerit.startswith("stop "):
            parts.append("Stop is too tight")
        elif demerit.startswith("R:R"):
            parts.append("Thin reward for the risk")
        else:
            parts.append(demerit.capitalize())
    return "Smaller size: " + ", ".join(parts).lower().capitalize()


def action_for(multiplier: float) -> str:
    if multiplier >= 1.0:
        return FULL
    if multiplier >= 0.5:
        return HALF
    if multiplier >= 0.25:
        return QUARTER
    return MINIMAL


def evaluate(inputs: FunnelInputs, config: FunnelConfig = FunnelConfig()) -> FunnelResult:
    """Walk the stages in order: hard gates stop a name outright, demerits only cut its size.

    A demerit never overrides an earlier stage - it scales the position down. Nothing is rejected for a thin
    R:R alone, since a low ratio on an otherwise clean setup is a smaller trade, not a forbidden one.
    """
    gates = dict.fromkeys(STAGES, NA)

    # Stage 1 - momentum. A fading name is out regardless of everything downstream.
    if inputs.delta_signal == "DECELERATING":
        gates[DELTA] = FAIL
        return FunnelResult(stage=DELTA, action=SKIP, multiplier=0.0, gates=gates)
    gates[DELTA] = PASS

    # Stage 2 - the rating already resolved momentum, regime and 4H structure into one word.
    if inputs.rating == rr.SELL:
        gates[RATING] = FAIL
        return FunnelResult(stage=RATING, action=SKIP, multiplier=0.0, gates=gates)
    if inputs.rating != rr.BUY:
        gates[RATING] = FAIL
        return FunnelResult(stage=RATING, action=WATCH, multiplier=0.0, gates=gates)
    gates[RATING] = PASS

    # Momentum sets the base tier; everything below only cuts it further.
    multiplier = momentum_tier(inputs.score)
    demerits: list[str] = []
    if multiplier < 1.0:
        demerits.append(f"momentum {inputs.score:.2f}" if inputs.score is not None else "momentum unknown")

    # Stage 3 - regime governs how hard to lean in, not whether to enter. Unreachable for a BUY today, since
    # rating_rules already blocks unstable regimes from reaching BUY; kept so the funnel stays correct if
    # those rules change.
    if inputs.regime in UNSTABLE_REGIMES:
        gates[REGIME] = FAIL
        multiplier *= config.demerit_multiplier
        demerits.append(f"{inputs.regime.lower().replace('_', ' ')} regime")
    else:
        gates[REGIME] = PASS

    # Stage 4 - is there a level to act on at all?
    if inputs.setup in NO_TRADE_SETUPS:
        gates[SETUP] = FAIL
        return FunnelResult(stage=SETUP, action=SKIP, multiplier=0.0, demerits=demerits, gates=gates)
    if inputs.setup == pl.EXTENDED:
        gates[SETUP] = FAIL
        return FunnelResult(stage=SETUP, action=WATCH, multiplier=0.0, demerits=demerits, gates=gates)
    gates[SETUP] = PASS

    # Stage 5 - zone quality is recorded but no longer changes size. Crossed against momentum quintiles on the
    # two-year sample it does not order consistently: in the top quintile PLAIN (+0.225R) beat CONFIRMED (+0.108R),
    # and in Q3 PLAIN beat CONFIRMED again. Its marginal edge was riding momentum, so sizing on it is unjustified.
    gates[ZONE] = NA if inputs.zone_quality is None else PASS

    # Stage 6 - a stop inside daily noise gets hit by chance rather than by being wrong.
    if inputs.stop_atr is None:
        gates[STOP] = NA
    elif inputs.stop_atr < config.min_stop_atr:
        gates[STOP] = FAIL
        multiplier *= config.demerit_multiplier
        demerits.append(f"stop {inputs.stop_atr:.1f} ATRs (inside noise)")
    else:
        gates[STOP] = PASS

    # Stage 7 - ranks, never rejects.
    if inputs.rr is None:
        gates[RR] = NA
    elif inputs.rr < config.min_rr:
        gates[RR] = FAIL
        multiplier *= config.demerit_multiplier
        demerits.append(f"R:R {inputs.rr:.2f}")
    else:
        gates[RR] = PASS

    # Stage 8 - independent signals, last so they check rather than trigger.
    flags = []
    if inputs.has_insider_flag:
        flags.append("insider selling")
    if inputs.has_news_flag:
        flags.append("news")
    if flags:
        gates[FLAGS] = FAIL
        multiplier *= config.demerit_multiplier ** len(flags)
        demerits.extend(flags)
    else:
        gates[FLAGS] = PASS

    multiplier = max(multiplier, config.min_multiplier)
    return FunnelResult(
        stage=CLEARED, action=action_for(multiplier), multiplier=multiplier, demerits=demerits, gates=gates
    )
