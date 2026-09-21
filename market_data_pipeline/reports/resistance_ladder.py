from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TargetConfig:
    min_distance: float = 0.01
    near_band: float = 0.12
    far_band: float = 0.20
    ladder_rows: int = 6
    # A stretch target is only reported when its OI is at least this fraction of the primary target's OI
    stretch_min_oi_ratio: float = 0.75


@dataclass(frozen=True)
class Targets:
    primary: float | None
    primary_oi: float | None
    stretch: float | None
    stretch_oi: float | None
    wall_status: str  # ABOVE (call wall above spot), CLEARED (spot at/through it), NONE (no wall reported)
    wall_distance: float | None  # spot / call_wall - 1
    call_wall: float | None = None


def call_oi_by_strike(chain: pd.DataFrame, symbol: str) -> pd.Series:
    """Total call open interest per strike across all expiries in the chain (an OI-concentration proxy, not gamma)."""
    calls = chain[(chain["underlying_symbol"] == symbol) & (chain["type"] == "call")]
    if calls.empty:
        return pd.Series(dtype=float)
    by_strike = calls.groupby("strike_price")["open_interest"].sum()
    return by_strike[by_strike > 0].sort_index()


def _argmax_in_band(
    by_strike: pd.Series, spot: float, low: float, high: float, low_inclusive: bool
) -> tuple[float | None, float | None]:
    distance = by_strike.index.to_series().div(spot).sub(1)
    in_band = (distance >= low if low_inclusive else distance > low) & (distance <= high)
    window = by_strike[in_band.to_numpy()]
    if window.empty:
        return None, None
    # Ties resolve to the strike nearest spot so results are deterministic.
    best = window[window == window.max()].index.min()
    return float(best), float(window.loc[best])


def _keep_meaningful_stretch(
    stretch: float | None, stretch_oi: float | None, primary_oi: float | None, config: TargetConfig
) -> tuple[float | None, float | None]:
    if stretch is None or not primary_oi:
        return stretch, stretch_oi
    if stretch_oi < config.stretch_min_oi_ratio * primary_oi:
        return None, None
    return stretch, stretch_oi


def select_targets(
    by_strike: pd.Series,
    spot: float | None,
    call_wall: float | None,
    config: TargetConfig = TargetConfig(),
) -> Targets:
    """Primary/stretch price targets from the reported call wall and the call-OI ladder.

    While the reported call wall is above spot it IS the primary target. Once spot is at or through it,
    GEX shows no further level, so the target becomes the strike with the most call OI between
    `min_distance` and `near_band` above spot (`min_distance` skips strikes sitting right on top of spot).
    The stretch target is the strike with the most call OI beyond `near_band` and within `far_band`.
    """
    if spot is None or pd.isna(spot):
        return Targets(None, None, None, None, "NONE", None)

    has_wall = call_wall is not None and not pd.isna(call_wall)
    wall_distance = float(spot / call_wall - 1) if has_wall else None
    wall_status = "NONE" if not has_wall else ("ABOVE" if call_wall > spot else "CLEARED")

    stretch, stretch_oi = None, None
    if not by_strike.empty:
        stretch, stretch_oi = _argmax_in_band(by_strike, spot, config.near_band, config.far_band, low_inclusive=False)

    if wall_status == "ABOVE":
        oi = float(by_strike.get(call_wall)) if call_wall in by_strike.index else None
        stretch, stretch_oi = _keep_meaningful_stretch(stretch, stretch_oi, oi, config)
        return Targets(float(call_wall), oi, stretch, stretch_oi, wall_status, wall_distance, float(call_wall))

    primary, primary_oi = None, None
    if not by_strike.empty:
        primary, primary_oi = _argmax_in_band(
            by_strike, spot, config.min_distance, config.near_band, low_inclusive=True
        )
    stretch, stretch_oi = _keep_meaningful_stretch(stretch, stretch_oi, primary_oi, config)
    return Targets(
        primary, primary_oi, stretch, stretch_oi, wall_status, wall_distance, float(call_wall) if has_wall else None
    )


def ladder_table(by_strike: pd.Series, spot: float, rows: int = 6) -> pd.DataFrame:
    """Top `rows` strikes above spot by total call OI (highest first), as shown on the ladder tab."""
    above = by_strike[by_strike.index > spot]
    top = above.sort_values(ascending=False, kind="stable").head(rows)
    return pd.DataFrame({"strike": top.index.astype(float), "call_oi": top.to_numpy()})
