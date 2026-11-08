from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

from market_data_pipeline.collectors.fred import fetch_series_observations, parse_fred_observations
from market_data_pipeline.config import CONFIG_DIR, require_fred_api_key
from market_data_pipeline.raw import raw_run

FOMC_DATES_PATH = CONFIG_DIR / "fomc_meeting_dates.yaml"

# Target rate range set directly by FOMC decisions (since Dec 2008). Unlike DFF
# (market-driven daily effective rate), these only change on days the FOMC
# actually moves the target range, so a diff around each meeting date gives a
# clean hike/cut/hold read.
TARGET_RATE_SERIES = ["DFEDTARU", "DFEDTARL"]


def load_meeting_dates(path: str | Path = FOMC_DATES_PATH) -> list[pd.Timestamp]:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return [pd.Timestamp(m["date"]) for m in raw.get("meetings", [])]


def classify_decision(before: float | None, after: float | None) -> tuple[str, float | None]:
    if before is None or after is None:
        return "unknown", None
    change_bps = round((after - before) * 100)
    if change_bps > 0:
        return "hike", change_bps
    if change_bps < 0:
        return "cut", change_bps
    return "hold", 0


def build_fomc_decisions(
    meeting_dates: list[pd.Timestamp], target_rate_upper: pd.DataFrame
) -> pd.DataFrame:
    series = target_rate_upper.dropna(subset=["value"]).sort_values("date")
    series["date"] = pd.to_datetime(series["date"])

    rows = []
    for meeting_date in sorted(meeting_dates):
        before_rows = series[series["date"] < meeting_date]
        # Decisions typically take effect the day after the announcement, so look
        # a few days past the meeting date for the new rate to show up.
        after_rows = series[series["date"] <= meeting_date + pd.Timedelta(days=3)]

        before = before_rows["value"].iloc[-1] if not before_rows.empty else None
        after = after_rows["value"].iloc[-1] if not after_rows.empty else None

        decision, change_bps = classify_decision(before, after)
        rows.append(
            {
                "date": meeting_date.date().isoformat(),
                "target_upper_before": before,
                "target_upper_after": after,
                "decision": decision,
                "change_bps": change_bps,
            }
        )

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect FOMC meeting dates and rate decisions (hike/cut/hold)."
    )
    parser.add_argument(
        "--dates-file", default=str(FOMC_DATES_PATH), help="Path to the FOMC meeting dates YAML."
    )
    args = parser.parse_args()

    meeting_dates = load_meeting_dates(args.dates_file)
    if not meeting_dates:
        print("No FOMC meeting dates configured, nothing to do.")
        return

    api_key = require_fred_api_key()

    with raw_run("fred", "fomc_decisions", {"dates_file": args.dates_file}, date_col="date") as run:
        print("Fetching DFEDTARU...")
        payload = fetch_series_observations("DFEDTARU", api_key)
        target_rate_upper = parse_fred_observations(payload, "DFEDTARU")

        decisions = build_fomc_decisions(meeting_dates, target_rate_upper)
        if decisions.empty:
            print("No FOMC decisions built.")
            return
        run.write(decisions)


if __name__ == "__main__":
    main()
