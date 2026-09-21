from __future__ import annotations

import argparse
import io
import json
import os
import re
import ssl
import zipfile
from collections import defaultdict
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

from market_data_pipeline.config import DATA_DIR


def normalize_column_name(column_name: str) -> str:
    cleaned = str(column_name).strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "_", cleaned)
    return cleaned.strip("_")


def normalize_cot_columns(df: pd.DataFrame) -> pd.DataFrame:
    renamed = df.copy()
    counts: defaultdict[str, int] = defaultdict(int)
    new_columns: list[str] = []

    for column in renamed.columns:
        normalized = normalize_column_name(column)
        counts[normalized] += 1
        if counts[normalized] == 1:
            new_columns.append(normalized)
        else:
            new_columns.append(f"{normalized}_{counts[normalized]}")

    renamed.columns = new_columns
    return renamed


# Metals match on substring. Crypto targets are "^"-anchored against market_and_exchange_names so the
# standard CME contracts don't also pull in the Micro/Nano/perp-style variants (different contract sizes).
# SOL has no CME contract; its only CFTC-reportable futures are Coinbase Derivatives' Nano Solana.
DEFAULT_TARGETS = [
    "gold",
    "silver",
    "gld",
    "slv",
    "^bitcoin - chicago mercantile",
    "^ether cash settled - chicago mercantile",
    "^nano solana - coinbase",
]

_TEXT_COLUMN_CANDIDATES = [
    "commodity_name",
    "commodity",
    "market_and_exchange_names",
    "market_and_exchange_name",
    "name",
    "commodity_name_2",
]


def _target_pattern(targets: list[str]) -> str:
    """Regex alternation of escaped targets; a leading '^' anchors that target to the start of the text."""
    parts = []
    for target in targets:
        if target.startswith("^"):
            parts.append("^" + re.escape(target[1:]))
        else:
            parts.append(re.escape(target))
    return "|".join(parts)


def filter_target_commodities(df: pd.DataFrame, targets: list[str] | None = None) -> pd.DataFrame:
    if df.empty:
        return df

    targets = [t.lower() for t in (targets or DEFAULT_TARGETS)]
    text_cols = [name for name in _TEXT_COLUMN_CANDIDATES if name in df.columns]
    if not text_cols:
        return df

    pattern = _target_pattern(targets)
    mask = pd.Series(False, index=df.index)
    for column in text_cols:
        mask |= df[column].fillna("").astype(str).str.lower().str.contains(pattern, na=False)
    return df[mask]


def _find_column(columns: list[str], *patterns: str) -> str | None:
    for pattern in patterns:
        for column in columns:
            if re.fullmatch(pattern, column):
                return column
    return None


SPEC_CATEGORIES: list[tuple[str, str, str]] = [
    ("managed_money", r"m_money_positions_long_all", r"m_money_positions_short_all"),
    ("noncommercial", r"noncommercial_positions_long_all", r"noncommercial_positions_short_all"),
    # The CFTC API's JSON field names abbreviate the same Legacy columns.
    ("noncommercial", r"noncomm_positions_long_all", r"noncomm_positions_short_all"),
]

COMMODITY_COLUMN_CANDIDATES = [
    "commodity_name",
    "commodity",
    "market_and_exchange_names",
    "market_and_exchange_name",
    "name",
]

DATE_COLUMN_CANDIDATES = [
    "report_date_as_yyyy_mm_dd",
    "as_of_date_in_form_yyyy_mm_dd",
]


def add_positioning_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add net large-speculator positioning, week-over-week change, and percentile rank.

    Prefers the Disaggregated report's Managed Money category; falls back to the
    Legacy report's Noncommercial category when Managed Money columns aren't present
    (the two are not equivalent groupings, so which one was used is recorded).
    """
    if df.empty:
        return df

    columns = list(df.columns)
    category_name, long_col, short_col = None, None, None
    for name, long_pattern, short_pattern in SPEC_CATEGORIES:
        long_col = _find_column(columns, long_pattern)
        short_col = _find_column(columns, short_pattern)
        if long_col and short_col:
            category_name = name
            break

    if not category_name:
        print("  [cot] no recognized long/short positioning columns found, skipping derived metrics")
        return df

    commodity_col = next((c for c in COMMODITY_COLUMN_CANDIDATES if c in columns), None)
    date_col = next((c for c in DATE_COLUMN_CANDIDATES if c in columns), None)

    result = df.copy()
    result["spec_category"] = category_name
    result["spec_long"] = pd.to_numeric(result[long_col], errors="coerce")
    result["spec_short"] = pd.to_numeric(result[short_col], errors="coerce")
    result["spec_net"] = result["spec_long"] - result["spec_short"]

    if commodity_col is None or date_col is None:
        print("  [cot] no commodity/date column found, skipping change/percentile calculations")
        return result

    sort_dates = pd.to_datetime(result[date_col], errors="coerce")
    result = result.assign(_sort_date=sort_dates).sort_values([commodity_col, "_sort_date"])
    grouped = result.groupby(commodity_col)["spec_net"]
    result["spec_net_change_1w"] = grouped.diff()
    result["spec_net_percentile"] = grouped.rank(pct=True)
    result = result.drop(columns="_sort_date").sort_index()
    return result


def add_release_date(df: pd.DataFrame, date_column: str | None = None) -> pd.DataFrame:
    """Add observation_date/release_date to prevent look-ahead bias in downstream joins.

    CFTC COT reports reflect Tuesday positions and are published the following Friday;
    approximated here as a fixed +3 calendar-day offset since a full CFTC holiday
    calendar is out of scope. Backtests should only use a row after its release_date.
    """
    if df.empty:
        return df

    resolved_date_column = date_column or next(
        (c for c in DATE_COLUMN_CANDIDATES if c in df.columns), None
    )
    if resolved_date_column is None:
        print("  [cot] no recognized date column found, skipping observation/release dates")
        return df

    result = df.copy()
    result["observation_date"] = pd.to_datetime(result[resolved_date_column], errors="coerce")
    result["release_date"] = result["observation_date"] + pd.Timedelta(days=3)
    return result


def _is_supported_report_file(filename: str) -> bool:
    return filename.lower().endswith((".csv", ".txt", ".tsv", ".dat"))


def read_cot_bytes(source: str) -> bytes:
    if source.startswith("http://") or source.startswith("https://"):
        request = Request(source, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=60, context=ssl._create_unverified_context()) as response:
            payload = response.read()
    else:
        payload = Path(source).read_bytes()

    if source.lower().endswith(".zip") or payload[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            report_name = next((name for name in archive.namelist() if _is_supported_report_file(name)), None)
            if report_name is None:
                raise ValueError("ZIP archive did not contain a supported report file (.csv/.txt/.tsv/.dat).")
            return archive.read(report_name)
    return payload


def _parse_cot_payload(payload: bytes) -> pd.DataFrame:
    text = payload.decode("utf-8", errors="replace").lstrip()
    if text.startswith("[") or text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("COT payload was not valid JSON.") from exc

        if isinstance(parsed, list):
            records = parsed
        elif isinstance(parsed, dict):
            for key in ("data", "results", "rows"):
                if isinstance(parsed.get(key), list):
                    records = parsed[key]
                    break
            else:
                raise ValueError("JSON payload did not contain a list of rows.")
        else:
            raise ValueError("Unsupported JSON COT payload format.")

        return pd.DataFrame(records)

    return pd.read_csv(io.BytesIO(payload), low_memory=False)


def load_cot_data(
    source: str | None = None,
    csv_path: str | None = None,
    targets: list[str] | None = None,
) -> pd.DataFrame:
    resolved_source = source or csv_path or os.getenv("CFTC_COT_URL")
    if not resolved_source:
        raise SystemExit(
            "No CFTC COT source provided. Set CFTC_COT_URL or pass --source-url/--csv-path."
        )

    payload = read_cot_bytes(resolved_source)
    df = _parse_cot_payload(payload)
    df = normalize_cot_columns(df)
    df = filter_target_commodities(df, targets or DEFAULT_TARGETS)
    df = add_positioning_metrics(df)
    df = add_release_date(df)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect and normalize CFTC COT data for metals and crypto markets.")
    parser.add_argument(
        "--source-url",
        action="append",
        default=None,
        help="Remote CSV/ZIP/JSON URL for a CFTC COT report. Repeat to combine reports (e.g. the "
        "Disaggregated report for metals plus the Legacy report, which is where CME Bitcoin/Ether "
        "and Coinbase Nano Solana appear).",
    )
    parser.add_argument("--csv-path", default=None, help="Local CSV or ZIP file path to an existing CFTC COT export.")
    parser.add_argument(
        "--commodities",
        nargs="*",
        default=DEFAULT_TARGETS,
        help="Target names to keep (a leading '^' anchors to the start of the market name).",
    )
    args = parser.parse_args()

    sources = args.source_url or [None]
    frames = [
        load_cot_data(source=source, csv_path=args.csv_path, targets=args.commodities)
        for source in sources
    ]
    filtered = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    out_path = DATA_DIR / "cot_data.parquet"
    filtered.to_parquet(out_path, index=False)
    print(f"Saved {len(filtered)} COT rows to {out_path}")


if __name__ == "__main__":
    main()
