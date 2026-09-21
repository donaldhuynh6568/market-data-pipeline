from __future__ import annotations

from dataclasses import dataclass, field

BUY = "BUY"
WATCH = "WATCH"
HOLD = "HOLD"
SELL = "SELL / AVOID"
UNCLASSIFIED = "HOLD (unclassified, review manually)"

# Reason codes explain WHY a rating was given; note_templates keys off them.
R_DECELERATING = "DECELERATING"
R_CONFIRMED = "CONFIRMED"
R_NEAR_WALL = "NEAR_WALL_UNCONFIRMED"
R_MIXED_DELTA = "MIXED_DELTA"
R_REGIME = "REGIME_UNSTABLE"
R_STRUCTURE_CONFLICT = "STRUCTURE_CONFLICT"
R_NO_GEX = "NO_GEX"
R_NO_MOMENTUM = "NO_MOMENTUM_HISTORY"
R_NO_STRUCTURE = "NO_STRUCTURE"

NO_DATA = "NO DATA"


@dataclass(frozen=True)
class RatingConfig:
    near_wall_threshold: float = 0.01
    buy_blocked_regimes: tuple[str, ...] = ("NEGATIVE_GAMMA", "NEAR_FLIP")


@dataclass(frozen=True)
class Rating:
    rating: str
    reason: str
    detail: dict = field(default_factory=dict)


def rate(
    delta_signal: str | None,
    gamma_regime: str | None,
    structure_4h: str | None,
    dist_to_wall: float | None,
    config: RatingConfig = RatingConfig(),
) -> Rating:
    """Rate one name from its delta signal, GEX regime, 4H structure and distance to the call wall.

    First match wins, top to bottom:
      1. no delta history                     -> HOLD (unclassified)
      2. DECELERATING                         -> SELL / AVOID
      3. no GEX regime (e.g. SOL)             -> HOLD (data gap, not unclassified)
      4. MIXED delta                          -> HOLD
      5. ACCELERATING:
           unstable/adverse regime            -> HOLD
           4H structure unknown               -> HOLD (unclassified)
           4H SIDEWAYS and at the call wall   -> WATCH   (live test, do not chase)
           4H DOWNTREND                       -> HOLD    (momentum/structure conflict)
           otherwise                          -> BUY
    `dist_to_wall` is spot / call_wall - 1 (None when there is no wall).
    """
    if delta_signal in (None, "UNKNOWN"):
        return Rating(UNCLASSIFIED, R_NO_MOMENTUM)
    if delta_signal == "DECELERATING":
        return Rating(SELL, R_DECELERATING)
    if gamma_regime in (None, NO_DATA):
        return Rating(HOLD, R_NO_GEX)
    if delta_signal != "ACCELERATING":  # MIXED
        return Rating(HOLD, R_MIXED_DELTA)

    if gamma_regime in config.buy_blocked_regimes:
        return Rating(HOLD, R_REGIME)
    if structure_4h in (None, "UNKNOWN"):
        return Rating(UNCLASSIFIED, R_NO_STRUCTURE)
    at_wall = dist_to_wall is not None and abs(dist_to_wall) <= config.near_wall_threshold
    if structure_4h == "SIDEWAYS" and at_wall:
        return Rating(WATCH, R_NEAR_WALL)
    if structure_4h == "DOWNTREND":
        return Rating(HOLD, R_STRUCTURE_CONFLICT)
    return Rating(BUY, R_CONFIRMED)
