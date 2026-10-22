from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from market_data_pipeline.reports import rating_rules as rr

# Setup states for the Summary tab.
AT_ENTRY = "AT_ENTRY"
WAIT_PULLBACK = "WAIT_PULLBACK"
EXTENDED = "EXTENDED"
NO_SETUP = "NO_SETUP"
RESISTANCE_ONLY = "RESISTANCE_ONLY"
NO_LEVELS = "NO_LEVELS"

# Target labels, in the order they are offered to select_target.
CE = "CE"
OTE = "OTE"
RANGE_HIGH = "RANGE_HIGH"
SMOOTH_HIGH = "SMOOTH_HIGH"
GEX_WALL = "GEX_WALL"
OI_CLUSTER = "OI_CLUSTER"


@dataclass(frozen=True)
class SizingConfig:
    account_size: float = 100_000.0
    risk_per_trade_pct: float = 1.0
    max_position_pct: float = 25.0


def sizing_config_from_rules(rules: dict) -> SizingConfig:
    """Account sizing, with ACCOUNT_SIZE and friends in .env winning over the tracked rules file."""
    from market_data_pipeline.config import account_settings

    resolved = account_settings(rules.get("sizing", {}))
    allowed = set(SizingConfig.__dataclass_fields__)
    return SizingConfig(**{k: v for k, v in resolved.items() if k in allowed})


def _clean(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _quality(value) -> str | None:
    """Zone quality as a plain string; a missing value stays None rather than becoming the text 'nan'."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return str(value)


def select_target(floor: float, candidates: list[tuple[str, float | None]]) -> tuple[str | None, float | None]:
    """Nearest candidate strictly above `floor`, with the label of where it came from.

    The playbook's OTE and the options-derived wall answer the same question from different data, so the
    conservative read is whichever the trade would reach first. Callers pass the greater of entry and spot,
    so a level price has already traded through is never offered as a target.
    """
    above = [(label, value) for label, value in candidates if _clean(value) is not None and float(value) > floor]
    if not above:
        return None, None
    label, value = min(above, key=lambda item: float(item[1]))
    return label, float(value)


def position_dollars(entry: float | None, size: float | None) -> float | None:
    """Capital the position ties up."""
    if entry is None or size is None:
        return None
    return entry * size


def risk_dollars(entry: float | None, stop: float | None, size: float | None) -> float | None:
    """What a stop-out costs. Comes in under the risk budget whenever the position cap binds first."""
    if entry is None or stop is None or size is None or entry <= stop:
        return None
    return (entry - stop) * size


def _scaled(size: float | None, multiplier: float) -> float | None:
    """Apply the funnel's conviction cut, keeping whole units whole and fractional units fractional.

    A multiplier of zero means the funnel stopped the name at a hard gate, so there is no size to show -
    None rather than 0, so the Summary leaves it blank instead of implying a position of nothing.
    """
    if size is None or multiplier <= 0:
        return None
    scaled = size * multiplier
    if size < 1:
        return round(scaled, 4)
    # A quarter of 3 shares floors to 0, which would print "buy quarter size" next to nothing to buy.
    # One unit is the smallest real position; Risk $ still reports what that single unit actually risks.
    return max(1, math.floor(scaled))


def stop_distance_in_atr(entry: float | None, stop: float | None, atr: float | None) -> float | None:
    """How many average daily ranges the stop sits away from the entry.

    Risk % cannot be compared across names - 2% is a wide stop on a quiet ETF and noise on a volatile chip
    stock. Dividing by ATR puts every name on the same scale: below roughly 1.0 the stop sits inside a single
    normal day's movement, so ordinary churn takes it out before the idea has been tested.
    """
    entry, stop, atr = _clean(entry), _clean(stop), _clean(atr)
    if entry is None or stop is None or atr is None or atr <= 0:
        return None
    distance = entry - stop
    return distance / atr if distance > 0 else None


def risk_reward(entry: float | None, stop: float | None, target: float | None) -> float | None:
    """Reward-to-risk multiple. None when the stop is not below the entry (no definable risk)."""
    entry, stop, target = _clean(entry), _clean(stop), _clean(target)
    if entry is None or stop is None or target is None:
        return None
    risk = entry - stop
    if risk <= 0:
        return None
    return (target - entry) / risk


def position_size(entry: float | None, stop: float | None, config: SizingConfig) -> float | None:
    """Units to buy so a stop-out costs `risk_per_trade_pct` of the account, capped by `max_position_pct`.

    Whole units for anything priced below the risk budget, fractional (4dp) for high-priced assets like
    BTC where a whole unit is already more than the account would ever put at risk.
    """
    entry, stop = _clean(entry), _clean(stop)
    if entry is None or stop is None or entry <= 0:
        return None
    risk_per_unit = entry - stop
    if risk_per_unit <= 0:
        return None

    budget = config.account_size * config.risk_per_trade_pct / 100
    units = budget / risk_per_unit
    max_units = config.account_size * config.max_position_pct / 100 / entry
    units = min(units, max_units)
    return math.floor(units) if units >= 1 else round(units, 4)


def setup_status(
    rating: str,
    levels: pd.Series | None,
    spot: float | None,
    max_pullback_pct: float,
) -> str:
    """Where price stands relative to its entry zone — the difference between acting now and waiting."""
    if levels is None:
        return NO_LEVELS
    if rating == rr.SELL:
        return RESISTANCE_ONLY
    entry = _clean(levels.get("entry"))
    zone_low = _clean(levels.get("entry_zone_low"))
    if entry is None or spot is None:
        return NO_SETUP
    if zone_low is not None and zone_low <= spot <= entry:
        return AT_ENTRY
    if spot <= entry:  # price is already below the zone's near edge
        return AT_ENTRY
    return WAIT_PULLBACK if (spot - entry) / spot <= max_pullback_pct else EXTENDED


@dataclass(frozen=True)
class PositionPlan:
    status: str
    entry: float | None = None
    stop: float | None = None
    risk_pct: float | None = None
    stop_atr: float | None = None
    target: float | None = None
    target_source: str | None = None
    rr: float | None = None
    size: float | None = None
    position_usd: float | None = None
    risk_usd: float | None = None
    full_size: float | None = None
    full_risk_usd: float | None = None
    multiplier: float = 1.0
    has_displacement: bool = False
    zone_quality: str | None = None
    zone_status: str | None = None
    resistance_low: float | None = None
    resistance_high: float | None = None


def build_plan(
    rating: str,
    levels: pd.Series | None,
    spot: float | None,
    gex_target: float | None,
    gex_label: str,
    stretch: float | None,
    sizing: SizingConfig,
    max_pullback_pct: float,
    multiplier: float = 1.0,
) -> PositionPlan:
    """Entry, stop, target, R:R and size for one name. Never reads or changes the rating beyond SELL gating.

    `multiplier` is the funnel's conviction cut: `size` and the dollar figures are post-demerit, while
    `full_size` / `full_risk_usd` keep the untouched risk-budget figures for the Funnel tab.
    """
    status = setup_status(rating, levels, spot, max_pullback_pct)
    if levels is None:
        return PositionPlan(status=status)

    resistance = (_clean(levels.get("resistance_low")), _clean(levels.get("resistance_high")))
    if status == NO_SETUP:
        return PositionPlan(status=status, resistance_low=resistance[0], resistance_high=resistance[1])
    if status == RESISTANCE_ONLY:
        # A name rated SELL still has levels worth seeing - where support sits matters most on something
        # that is fading. Withhold the position, not the information: multiplier 0 blanks Shares/Cost/Risk.
        multiplier = 0.0

    entry = _clean(levels.get("entry"))
    stop = _clean(levels.get("stop"))
    atr = _clean(levels.get("atr"))
    if entry is None or stop is None:
        # Nothing to price a trade against, whatever the rating says. Keep the overhead-supply read.
        return PositionPlan(
            status=status,
            zone_quality=_quality(levels.get("entry_zone_quality")),
            zone_status=_quality(levels.get("entry_zone_status")),
            resistance_low=resistance[0],
            resistance_high=resistance[1],
        )
    full_size = position_size(entry, stop, sizing)
    size = _scaled(full_size, multiplier)
    # NOTE: reference/Location Confluence Playbook.md flags an unresolved conflict (source 5 vs. the rest of
    # the doc): it argues against a fixed target entirely, favoring a trailing stop with no predetermined
    # take-profit, on its own data that most winners kept running well past OTE. This function still resolves
    # to a fixed price. The doc leaves the choice to the user; it has not been made. If a trailing-stop mode is
    # ever built, it replaces this selection rather than blending with it.
    target_source, target = select_target(
        max(entry, spot) if spot is not None else entry,
        [
            # CE before OTE: the playbook treats the 50% midpoint as the conservative first target to partial
            # into and OTE (75%) as the completion reference, so whichever is nearer above price comes first.
            (CE, levels.get("range_ce")),
            (OTE, levels.get("range_ote")),
            (RANGE_HIGH, levels.get("range_high")),
            (SMOOTH_HIGH, levels.get("nearest_smooth_high")),
            (gex_label, gex_target),
            (OI_CLUSTER, stretch),
        ],
    )
    return PositionPlan(
        status=status,
        entry=entry,
        stop=stop,
        risk_pct=_clean(levels.get("risk_pct")),
        stop_atr=stop_distance_in_atr(entry, stop, atr),
        target=target,
        target_source=target_source,
        rr=risk_reward(entry, stop, target),
        size=size,
        position_usd=position_dollars(entry, size),
        risk_usd=risk_dollars(entry, stop, size),
        full_size=full_size,
        full_risk_usd=risk_dollars(entry, stop, full_size),
        multiplier=multiplier,
        has_displacement=bool(levels.get("entry_has_displacement", False)),
        zone_quality=_quality(levels.get("entry_zone_quality")),
        zone_status=_quality(levels.get("entry_zone_status")),
        resistance_low=resistance[0],
        resistance_high=resistance[1],
    )
