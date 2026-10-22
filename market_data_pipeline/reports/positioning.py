from __future__ import annotations

import math
from dataclasses import dataclass, field

LONG = "LONG"
SHORT = "SHORT"
NO_TREND = "NO TREND"

HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"

AGREE, DISAGREE, NEUTRAL = 1, -1, 0

UPTREND = "UPTREND"
DOWNTREND = "DOWNTREND"
ACCELERATING = "ACCELERATING"
DECELERATING = "DECELERATING"
POSITIVE_GAMMA = "POSITIVE_GAMMA"
NEGATIVE_GAMMA = "NEGATIVE_GAMMA"
NEAR_FLIP = "NEAR_FLIP"

# Opens used for the premium/discount read, in the order shown.
OPEN_LEVELS = (
    ("midnight_open", "Midnight open"),
    ("open_0830", "8:30 open"),
    ("open_0930", "9:30 open"),
    ("week_open", "Weekly open"),
    ("month_open", "Monthly open"),
)
SESSION_LEVELS = (
    ("asia_high", "Asia high"),
    ("asia_low", "Asia low"),
    ("london_high", "London high"),
    ("london_low", "London low"),
)
POOL_LEVELS = (("eqh", "Equal highs"), ("eql", "Equal lows"))
FLOW_LEVELS = (
    ("avwap_week", "Weekly VWAP"),
    ("avwap_month", "Monthly VWAP"),
    ("avwap_swing_low", "Swing-low VWAP"),
    ("avwap_swing_high", "Swing-high VWAP"),
    ("poc", "POC"),
    ("vah", "Value area high"),
    ("val", "Value area low"),
)
# Anchored VWAPs the vwap vote always reads; the swing anchor is picked by trade side.
VWAP_COMMON = ("avwap_week", "avwap_month")

BULLISH_DIV, BEARISH_DIV, CONFIRM_UP, CONFIRM_DOWN = "BULLISH_DIV", "BEARISH_DIV", "CONFIRM_UP", "CONFIRM_DOWN"


@dataclass(frozen=True)
class PositioningConfig:
    # --- screen
    min_abs_score: float = 0.0          # momentum score magnitude needed on top of the 4H structure
    # --- layers
    wall_room_pct: float = 0.03         # long: call wall must sit at least this far above spot to count as room
    opens_agree_share: float = 0.6      # share of available opens spot must be on the trade's side of
    cot_crowded_percentile: float = 0.9 # spec net percentile at/above this reads as crowded on the long side
    funding_crowded: float = 0.00004    # per-interval funding beyond which the paying side is crowded
    rvol_confirm: float = 1.2           # last completed day's relative volume that counts as participation
    # --- flow (shown beside conviction, not counted in it)
    cmf_threshold: float = 0.05         # |CMF| beyond this reads as accumulation / distribution
    mfi_with_trend: float = 55.0        # long: MFI at/above this is flow with the trend ...
    mfi_extreme: float = 80.0           # ... until it reaches this overbought level (mirrored for shorts)
    rs_threshold: float = 0.0           # relative-strength change vs the benchmark that counts as leading / lagging
    trade_size_rising: float = 1.15     # recent average trade size vs its baseline that counts as bigger prints
    vwap_agree_share: float = 0.6       # share of anchored VWAPs spot must be on the trade's side of
    # --- conviction
    high_net: int = 4                   # agree minus disagree
    medium_net: int = 2
    cot_markets: dict = field(default_factory=dict)  # ticker -> COT market_and_exchange_names


def positioning_config_from_rules(rules: dict) -> PositioningConfig:
    section = rules.get("positioning", {})
    allowed = set(PositioningConfig.__dataclass_fields__)
    return PositioningConfig(**{k: v for k, v in section.items() if k in allowed})


@dataclass(frozen=True)
class Screen:
    direction: str
    passes: bool
    reason: str


@dataclass(frozen=True)
class Vote:
    layer: str
    vote: int | None   # AGREE / DISAGREE / NEUTRAL, or None when the layer has no data for this name
    detail: str


@dataclass(frozen=True)
class Conviction:
    level: str
    agree: int
    disagree: int
    neutral: int
    no_data: int
    reason: str


def _has(value) -> bool:
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def screen(structure_4h: str, structure_1h: str, score: float | None, config: PositioningConfig) -> Screen:
    """Gate: the 4H structure sets the direction and momentum has to agree with it.

    1H is not part of the gate - it is a layer, so a pullback inside a 4H trend is shown as disagreement
    rather than silently dropping the name.
    """
    if not _has(score):
        return Screen(NO_TREND, False, "no momentum score")
    if structure_4h == UPTREND:
        if score > config.min_abs_score:
            return Screen(LONG, True, "4H uptrend with positive momentum")
        return Screen(NO_TREND, False, "4H uptrend but momentum is not positive")
    if structure_4h == DOWNTREND:
        if score < -config.min_abs_score:
            return Screen(SHORT, True, "4H downtrend with negative momentum")
        return Screen(NO_TREND, False, "4H downtrend but momentum is not negative")
    return Screen(NO_TREND, False, f"4H structure is {structure_4h.lower() if structure_4h else 'unknown'}")


def _side(direction: str) -> int:
    return 1 if direction == LONG else -1


def _signed(direction: str, bullish: bool | None) -> int | None:
    """Map a bullish/bearish read to agree/disagree for the trade direction; None stays None."""
    if bullish is None:
        return None
    return AGREE if bullish == (direction == LONG) else DISAGREE


def structure_vote(direction: str, structure_1h: str) -> Vote:
    if structure_1h == UPTREND:
        return Vote("1H structure", _signed(direction, True), "1H uptrend")
    if structure_1h == DOWNTREND:
        return Vote("1H structure", _signed(direction, False), "1H downtrend")
    return Vote("1H structure", NEUTRAL if structure_1h else None, f"1H {(structure_1h or 'unknown').lower()}")


def delta_vote(direction: str, signal: str | None) -> Vote:
    """Is the momentum score still moving the trade's way? Accelerating is bullish; decelerating bearish."""
    if signal == ACCELERATING:
        return Vote("Momentum delta", _signed(direction, True), "score accelerating")
    if signal == DECELERATING:
        return Vote("Momentum delta", _signed(direction, False), "score decelerating")
    return Vote("Momentum delta", NEUTRAL if signal else None, f"score {(signal or 'unknown').lower()}")


def gamma_vote(direction: str, regime: str | None) -> Vote:
    """Dealer gamma. Positive gamma damps moves and supports a long; negative gamma extends a downside move."""
    if not regime or regime == "NO_DATA":
        return Vote("Gamma regime", None, "no options coverage")
    label = regime.replace("_", " ").lower()
    if direction == LONG:
        if regime == POSITIVE_GAMMA:
            return Vote("Gamma regime", AGREE, label)
        return Vote("Gamma regime", DISAGREE, label)
    if regime == NEGATIVE_GAMMA:
        return Vote("Gamma regime", AGREE, label)
    return Vote("Gamma regime", NEUTRAL, label)


def wall_vote(direction: str, spot: float | None, call_wall: float | None, config: PositioningConfig) -> Vote:
    """Long only: is there room before the call wall caps the move? Not meaningful for a short."""
    if direction != LONG or not _has(spot) or not _has(call_wall) or not spot:
        return Vote("Call-wall room", None, "n/a")
    room = call_wall / spot - 1
    if room < 0:
        return Vote("Call-wall room", NEUTRAL, f"spot is {-room * 100:.1f}% above the wall")
    return Vote(
        "Call-wall room", AGREE if room >= config.wall_room_pct else DISAGREE, f"wall {room * 100:.1f}% above spot"
    )


def cot_vote(direction: str, percentile: float | None, change_1w: float | None, config: PositioningConfig) -> Vote:
    """Speculator net positioning: a rising net in the trade's direction, but not at a crowded extreme."""
    if not _has(percentile) or not _has(change_1w):
        return Vote("COT specs", None, "no COT for this name")
    pct_text = f"spec net {percentile * 100:.0f}th pct, {change_1w:+,.0f} w/w"
    crowded = percentile >= config.cot_crowded_percentile if direction == LONG else percentile <= 1 - config.cot_crowded_percentile
    if crowded:
        return Vote("COT specs", DISAGREE, pct_text + " (crowded)")
    if change_1w == 0:
        return Vote("COT specs", NEUTRAL, pct_text)
    return Vote("COT specs", _signed(direction, change_1w > 0), pct_text)


def funding_vote(direction: str, funding: float | None, config: PositioningConfig) -> Vote:
    """Perp funding (crypto): the side paying funding is the crowded one. One snapshot only, no history."""
    if not _has(funding):
        return Vote("Perp funding", None, "n/a")
    text = f"funding {funding:+.6f}"
    paying_longs = funding >= config.funding_crowded
    paying_shorts = funding <= -config.funding_crowded
    if direction == LONG:
        if paying_longs:
            return Vote("Perp funding", DISAGREE, text + " (longs crowded)")
        return Vote("Perp funding", AGREE if paying_shorts else NEUTRAL, text)
    if paying_shorts:
        return Vote("Perp funding", DISAGREE, text + " (shorts crowded)")
    return Vote("Perp funding", AGREE if paying_longs else NEUTRAL, text)


def volume_vote(direction: str, rvol: float | None, ret_1d: float | None, config: PositioningConfig) -> Vote:
    """Last completed day: heavy volume in the trade's direction is participation; heavy against it is distribution."""
    if not _has(rvol) or not _has(ret_1d):
        return Vote("Volume", None, "n/a")
    text = f"{rvol:.1f}x avg volume, {ret_1d * 100:+.1f}% day"
    if rvol < config.rvol_confirm or ret_1d == 0:
        return Vote("Volume", NEUTRAL, text)
    return Vote("Volume", _signed(direction, ret_1d > 0), text)


def _share_vote(
    layer: str, direction: str, spot: float | None, prices: list[float | None], agree_share: float, noun: str
) -> Vote:
    """Share of the available `prices` that spot sits on the trade's side of: above for a long, below for a short."""
    available = [price for price in prices if _has(price)]
    if not _has(spot) or not available:
        return Vote(layer, None, f"no {noun}")
    above = sum(1 for price in available if spot > price)
    share = above / len(available) if direction == LONG else (len(available) - above) / len(available)
    text = f"spot above {above}/{len(available)} {noun}"
    if share >= agree_share:
        return Vote(layer, AGREE, text)
    if share <= 1 - agree_share:
        return Vote(layer, DISAGREE, text)
    return Vote(layer, NEUTRAL, text)


def opens_vote(direction: str, spot: float | None, opens: dict[str, float], config: PositioningConfig) -> Vote:
    """Premium/discount to the opens: a long wants spot above most opens (buying from a discount is the reverse).

    Reads trend alignment, not entry quality: spot holding above the day/week/month opens is the bullish bias.
    """
    return _share_vote("Opens bias", direction, spot, list(opens.values()), config.opens_agree_share, "opens")


def flags_vote(has_insider_flag: bool, has_news_flag: bool) -> Vote:
    """Event risk. Only ever a disagreement: absence of a flag is not evidence for the trade."""
    reasons = [name for name, on in (("insider selling", has_insider_flag), ("news flag", has_news_flag)) if on]
    if reasons:
        return Vote("Flags", DISAGREE, ", ".join(reasons))
    return Vote("Flags", NEUTRAL, "none")


# --- flow layers: shown beside conviction, deliberately not part of it until they have been backtested -------------


def cmf_vote(direction: str, cmf: float | None, config: PositioningConfig) -> Vote:
    """Chaikin money flow: closes near the highs on volume is accumulation, near the lows is distribution."""
    if not _has(cmf):
        return Vote("CMF", None, "n/a")
    text = f"CMF {cmf:+.2f}"
    if abs(cmf) < config.cmf_threshold:
        return Vote("CMF", NEUTRAL, text)
    return Vote("CMF", _signed(direction, cmf > 0), text)


def mfi_vote(direction: str, mfi: float | None, config: PositioningConfig) -> Vote:
    """Money flow index: flow leaning with the trade agrees, but a reading at the overbought/oversold extreme is
    exhaustion and reads against it, as does flow leaning the other way."""
    if not _has(mfi):
        return Vote("MFI", None, "n/a")
    text = f"MFI {mfi:.0f}"
    lean = mfi if direction == LONG else 100 - mfi          # mirror so one rule serves both sides
    if lean >= config.mfi_extreme:
        return Vote("MFI", DISAGREE, text + (" (overbought)" if direction == LONG else " (oversold)"))
    if lean >= config.mfi_with_trend:
        return Vote("MFI", AGREE, text)
    if lean < 100 - config.mfi_with_trend:
        return Vote("MFI", DISAGREE, text)
    return Vote("MFI", NEUTRAL, text)


def obv_vote(direction: str, signal: str | None) -> Vote:
    """On-balance volume against price: accumulation into weakness or a confirming rise agrees for a long."""
    labels = {
        BULLISH_DIV: (True, "bullish divergence"), CONFIRM_UP: (True, "OBV rising with price"),
        BEARISH_DIV: (False, "bearish divergence"), CONFIRM_DOWN: (False, "OBV falling with price"),
    }
    if not isinstance(signal, str):
        return Vote("OBV", None, "n/a")
    if signal not in labels:
        return Vote("OBV", NEUTRAL, "OBV and price disagree, no new extreme")
    bullish, text = labels[signal]
    return Vote("OBV", _signed(direction, bullish), text)


def rs_vote(direction: str, rs_change: float | None, benchmark: str | None, config: PositioningConfig) -> Vote:
    """Relative strength: a long wants the name leading its benchmark, a short wants it lagging."""
    if not _has(rs_change):
        return Vote("Rel. strength", None, "no benchmark")
    text = f"{rs_change * 100:+.1f}% vs {benchmark}"
    if abs(rs_change) <= config.rs_threshold:
        return Vote("Rel. strength", NEUTRAL, text)
    return Vote("Rel. strength", _signed(direction, rs_change > 0), text)


def trade_size_vote(
    direction: str, ratio: float | None, window_return: float | None, config: PositioningConfig
) -> Vote:
    """Bigger average prints are read as larger participants; which side they were on is the window's direction."""
    if not _has(ratio) or not _has(window_return):
        return Vote("Trade size", None, "n/a")
    text = f"avg trade {ratio:.2f}x baseline, {window_return * 100:+.1f}%"
    if ratio < config.trade_size_rising or window_return == 0:
        return Vote("Trade size", NEUTRAL, text)
    return Vote("Trade size", _signed(direction, window_return > 0), text)


def vwap_vote(direction: str, spot: float | None, levels: dict, config: PositioningConfig) -> Vote:
    """Spot against the anchored VWAPs (weekly, monthly and the swing anchor on the trade's own side)."""
    swing = "avwap_swing_low" if direction == LONG else "avwap_swing_high"
    prices = [levels.get(key) for key in (*VWAP_COMMON, swing)]
    return _share_vote("VWAP", direction, spot, prices, config.vwap_agree_share, "anchored VWAPs")


def profile_vote(direction: str, spot: float | None, levels: dict) -> Vote:
    """Acceptance against the volume profile: above the value area for a long, below it for a short."""
    vah, val = levels.get("vah"), levels.get("val")
    if not _has(spot) or not _has(vah) or not _has(val):
        return Vote("Profile", None, "n/a")
    if spot > vah:
        return Vote("Profile", _signed(direction, True), "above value area")
    if spot < val:
        return Vote("Profile", _signed(direction, False), "below value area")
    return Vote("Profile", NEUTRAL, "inside value area")


def flow_votes(direction: str, spot: float | None, flow: dict, config: PositioningConfig) -> list[Vote]:
    """The seven flow layers in column order. `flow` is one row of flow_indicators.parquet (empty when absent)."""
    return [
        cmf_vote(direction, flow.get("cmf"), config),
        mfi_vote(direction, flow.get("mfi"), config),
        obv_vote(direction, flow.get("obv_signal")),
        rs_vote(direction, flow.get("rs_short"), flow.get("benchmark"), config),
        trade_size_vote(direction, flow.get("trade_size_ratio"), flow.get("trade_size_ret"), config),
        vwap_vote(direction, spot, flow, config),
        profile_vote(direction, spot, flow),
    ]


@dataclass(frozen=True)
class FlowScore:
    agree: int
    disagree: int
    neutral: int
    no_data: int

    @property
    def text(self) -> str:
        return f"{self.agree} / {self.disagree}"


def flow_score(votes: list[Vote]) -> FlowScore:
    """Agree / against counts over the flow layers that have data. No HIGH/MEDIUM/LOW: this is not conviction."""
    count = lambda wanted: sum(1 for v in votes if v.vote == wanted)  # noqa: E731
    return FlowScore(count(AGREE), count(DISAGREE), count(NEUTRAL), count(None))


def conviction(votes: list[Vote], config: PositioningConfig) -> Conviction:
    """Net agreement across the layers that have data. Layers with no data are counted, never penalised."""
    agree = sum(1 for v in votes if v.vote == AGREE)
    disagree = sum(1 for v in votes if v.vote == DISAGREE)
    neutral = sum(1 for v in votes if v.vote == NEUTRAL)
    no_data = sum(1 for v in votes if v.vote is None)
    net = agree - disagree
    level = HIGH if net >= config.high_net else MEDIUM if net >= config.medium_net else LOW
    parts = []
    supports = [v.layer for v in votes if v.vote == AGREE]
    against = [v.layer for v in votes if v.vote == DISAGREE]
    if supports:
        parts.append("with: " + ", ".join(supports))
    if against:
        parts.append("against: " + ", ".join(against))
    return Conviction(level, agree, disagree, neutral, no_data, "; ".join(parts) or "no layer has a view")


def level_table(
    spot: float | None, atr: float | None, levels: dict, order: tuple[tuple[str, str], ...] | None = None
) -> list[dict]:
    """Every ICT reference level against spot: price, % from spot, ATR multiples and side. Missing levels are skipped.

    `levels` is one row of ict_levels.parquet. Status (SWEPT/UNTESTED) is attached for the session and
    equal-high/low levels, which are liquidity that either still rests there or has been taken.
    """
    if not _has(spot) or not spot:
        return []
    rows = []
    for key, label in order or OPEN_LEVELS + SESSION_LEVELS + POOL_LEVELS + FLOW_LEVELS:
        price = levels.get(key)
        if not _has(price):
            continue
        status = levels.get(f"{key}_status")
        if key in ("eqh", "eql"):
            count = levels.get(f"{key}_count")
            status = f"{int(count)}x, untouched" if _has(count) and count else None
        rows.append(
            {
                "key": key,
                "label": label,
                "price": float(price),
                "pct": float(price) / spot - 1,
                "atr": (float(price) - spot) / atr if _has(atr) and atr else None,
                "side": "ABOVE" if price > spot else "BELOW",
                "status": status if isinstance(status, str) else None,
            }
        )
    return rows


def nearest(table: list[dict], side: str) -> dict | None:
    """Closest level above (or below) spot."""
    candidates = [r for r in table if r["side"] == side]
    if not candidates:
        return None
    return min(candidates, key=lambda r: abs(r["pct"]))


def opens_summary(table: list[dict]) -> str:
    """'Above 3/5 opens' style read of where spot sits against the opens."""
    opens = [r for r in table if r["key"] in dict(OPEN_LEVELS)]
    if not opens:
        return "-"
    above = sum(1 for r in opens if r["side"] == "BELOW")  # an open below spot means spot is above it
    return f"above {above}/{len(opens)} opens"
