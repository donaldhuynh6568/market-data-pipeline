from __future__ import annotations

import pandas as pd

# Below this a bucket's win rate swings on one or two trades, so it gets called out rather than read as a result.
MIN_TRUSTWORTHY_TRADES = 30
# Expectancy inside this band of zero is noise at these sample sizes, not an edge either way.
BREAKEVEN_BAND = 0.03
# A feature whose best and worst buckets differ by less than this is not separating anything.
MEANINGFUL_SPREAD = 0.05

CONTROL_VIEW = "Control @ 1R"
ALL_ZONES_PREFIX = "All zones @"
# The ICT target-exit backtest (transforms/backtest.py's run_target_backtest) reuses these functions with its
# own view name, since it has no "All zones @ XR" sweep to pick a takeaway from - there is exactly one exit,
# whatever the real level actually was.
TARGET_CONTROL_VIEW = "Control (to target)"

GLOSSARY = [
    ("R", "One unit of risk: the gap between the entry and the stop. Everything is measured in R so names at "
          "different prices can be compared. At a 1% risk budget on a 25,000 account, 1R is 250."),
    ("Trades", "How many historical setups this row is measured on. Under 30 and the number swings on one or two "
               "trades, so treat it as a hint, not a result."),
    ("Win rate", "Share of trades that reached the target before the stop. On its own it says nothing about "
                 "profit: a 60% win rate at a half-size target still loses money."),
    ("Expectancy (R)", "The number that matters. Average profit or loss per trade in units of risk. "
                       "+0.20R means a typical trade made a fifth of what it risked; -0.20R means it lost that. "
                       "Multiply by your risk per trade to get dollars."),
    ("Median best (R)", "How far the typical trade ran in your favour before it resolved. Well above 1R means "
                        "targets were being left on the table."),
    ("Median worst (R)", "How far the typical trade went against you before it resolved. Near 1R means most "
                         "trades came close to the stop even when they eventually won."),
    ("Control", "The same trade geometry entered on random days. It is the bar to clear: a signal that cannot "
                "beat random entry is not adding anything. Its stops are matched to the signal's own widths."),
    ("View", "One way of slicing the same trades. 'All zones @ 2R' asks what happens if you always target twice "
             "your risk; 'Momentum at entry' splits the same trades by how strong momentum was."),
    ("Median target (R)", "ICT target-exit scorecard only: how big the real level actually being held for was, "
                          "in units of risk, for a typical trade in that row. Varies trade to trade because the "
                          "target is a real price level (CE, OTE, a range high, a smooth swing high), not a "
                          "fixed multiple - this is what each trade's own reward was actually sized at."),
]


def control_expectancy(summary: pd.DataFrame, view: str = CONTROL_VIEW) -> float | None:
    """The random-entry benchmark, or None when the scorecard has no control row.

    `view` lets a second scorecard built on different trade geometry (the ICT target-exit backtest, which
    uses "Control (to target)") reuse this same lookup instead of a copy of it.
    """
    if summary is None or summary.empty:
        return None
    rows = summary[summary["view"] == view].dropna(subset=["expectancy_r"])
    return float(rows.iloc[0]["expectancy_r"]) if not rows.empty else None


def control_note(expectancy: float | None) -> str:
    """The control is the yardstick, so it gets an explanation rather than a verdict against itself."""
    if expectancy is None or pd.isna(expectancy):
        return "Benchmark row."
    return f"BENCHMARK - every row above has to beat {expectancy:+.3f}R to be worth anything."


def is_all_zones(view: object) -> bool:
    return str(view).startswith(ALL_ZONES_PREFIX)


def row_verdict(expectancy: float | None, trades: float | None, control: float | None = None) -> str:
    """Plain reading of one row: does it make money, and can the sample be trusted?"""
    if expectancy is None or pd.isna(expectancy):
        return ""
    small = trades is not None and not pd.isna(trades) and trades < MIN_TRUSTWORTHY_TRADES

    if abs(expectancy) <= BREAKEVEN_BAND:
        verdict = "About breakeven"
    elif expectancy > 0:
        verdict = "Makes money"
        if control is not None and expectancy > control:
            verdict += ", and beats random entry"
        elif control is not None:
            verdict += ", but still below random entry"
    else:
        verdict = "Loses money"

    if small:
        verdict += f" - only {int(trades)} trades, too few to rely on"
    return verdict


def feature_takeaway(group: pd.DataFrame, control: float | None = None) -> str:
    """Does this way of slicing the trades actually separate good setups from bad ones?"""
    usable = group.dropna(subset=["expectancy_r"])
    usable = usable[usable["trades"] >= MIN_TRUSTWORTHY_TRADES]
    if len(usable) < 2:
        return "Not enough well-sampled buckets to tell."

    best = usable.loc[usable["expectancy_r"].idxmax()]
    worst = usable.loc[usable["expectancy_r"].idxmin()]
    spread = float(best["expectancy_r"] - worst["expectancy_r"])
    if spread < MEANINGFUL_SPREAD:
        return (
            f"No real effect: best and worst differ by only {spread:.3f}R. "
            "This tells you nothing useful - ignore it when choosing a trade."
        )

    takeaway = (
        f"Separates outcomes: {best['bucket']} returned {best['expectancy_r']:+.3f}R against "
        f"{worst['expectancy_r']:+.3f}R for {worst['bucket']} (a {spread:.2f}R spread). "
    )
    if control is not None and float(best["expectancy_r"]) > control:
        takeaway += f"The best bucket also clears random entry ({control:+.3f}R), so favouring it is justified."
    elif control is not None:
        takeaway += f"Even the best bucket stays under random entry ({control:+.3f}R), so this only ranks, it does not qualify."
    return takeaway


def target_takeaway(summary: pd.DataFrame) -> str:
    """Across the 'All zones @ XR' views, which reward target actually worked?"""
    rows = summary[summary["view"].astype(str).str.startswith(ALL_ZONES_PREFIX)].dropna(subset=["expectancy_r"])
    if rows.empty:
        return ""
    best = rows.loc[rows["expectancy_r"].idxmax()]
    target = str(best["view"]).replace(ALL_ZONES_PREFIX, "").strip()
    if float(best["expectancy_r"]) > BREAKEVEN_BAND:
        return (
            f"Targeting {target} was the best of these ({best['expectancy_r']:+.3f}R). Taking profit earlier did "
            "worse, so letting winners run further helped."
        )
    return (
        f"No reward target made money on average; the least bad was {target} at {best['expectancy_r']:+.3f}R. "
        "Entry quality is the problem, not where you take profit."
    )


def headline(summary: pd.DataFrame) -> str:
    """One line for the Summary header: does this system beat doing nothing at random?"""
    if summary is None or summary.empty:
        return ""
    control = control_expectancy(summary)
    overall = summary[summary["view"] == f"{ALL_ZONES_PREFIX} 1R"].dropna(subset=["expectancy_r"])
    if overall.empty or control is None:
        return ""
    zones = float(overall.iloc[0]["expectancy_r"])
    trades = int(overall.iloc[0]["trades"])
    if zones > control:
        return (
            f"Backtest: the average zone entry returned {zones:+.3f}R against {control:+.3f}R for random entry "
            f"({trades:,} trades), so the signal is adding something."
        )
    return (
        f"Backtest: the average zone entry returned {zones:+.3f}R against {control:+.3f}R for random entry "
        f"({trades:,} trades). The system has NOT beaten random overall, so treat Rank as relative order only, "
        "not as a reason to trade."
    )


def target_headline(summary: pd.DataFrame) -> str:
    """Same question as `headline`, for the ICT target-exit scorecard that Rank is actually based on."""
    if summary is None or summary.empty:
        return ""
    control = control_expectancy(summary, view=TARGET_CONTROL_VIEW)
    overall = summary[summary["view"] == "All zones (to target)"].dropna(subset=["expectancy_r"])
    if overall.empty or control is None:
        return ""
    zones = float(overall.iloc[0]["expectancy_r"])
    trades = int(overall.iloc[0]["trades"])
    if zones > control:
        return (
            f"Backtest (to target): resolved against the real ICT level, the average zone entry returned "
            f"{zones:+.3f}R against {control:+.3f}R for random entry ({trades:,} trades) - Rank is built on this."
        )
    return (
        f"Backtest (to target): resolved against the real ICT level, the average zone entry returned "
        f"{zones:+.3f}R against {control:+.3f}R for random entry ({trades:,} trades). This has NOT beaten "
        "random overall, so treat Rank as relative order only, not as a reason to trade."
    )
