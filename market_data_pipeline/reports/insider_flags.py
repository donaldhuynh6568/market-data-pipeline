from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class InsiderConfig:
    window_days: int = 90
    min_sells: int = 20
    min_owners: int = 3


def insider_sell_flags(
    trades: pd.DataFrame, as_of: date, config: InsiderConfig = InsiderConfig()
) -> dict[str, dict]:
    """Names with clustered open-market insider selling in the window ending `as_of`.

    Counts Form 4 rows with transaction code S (open-market sale) and disposed direction. A name is flagged
    only when it reaches BOTH the sell-count and distinct-insider thresholds, so one insider's routine
    scheduled sales do not trip it. Returns {ticker: {"sells": int, "owners": int, "window_days": int}}.
    """
    if trades is None or trades.empty:
        return {}

    dates = pd.to_datetime(trades["transaction_date"], errors="coerce")
    start = pd.Timestamp(as_of) - pd.Timedelta(days=config.window_days)
    is_sale = (trades["transaction_code"] == "S") & (trades["acquired_disposed_code"] == "D")
    recent = trades[is_sale & (dates > start) & (dates <= pd.Timestamp(as_of))]
    if recent.empty:
        return {}

    grouped = recent.groupby("ticker").agg(sells=("ticker", "size"), owners=("reporting_owner", "nunique"))
    flagged = grouped[(grouped["sells"] >= config.min_sells) & (grouped["owners"] >= config.min_owners)]
    return {
        ticker: {"sells": int(row["sells"]), "owners": int(row["owners"]), "window_days": config.window_days}
        for ticker, row in flagged.iterrows()
    }
