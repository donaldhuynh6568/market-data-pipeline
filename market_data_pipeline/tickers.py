from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import TICKERS_PATH


def _unique(symbols: list[str]) -> list[str]:
    """First-seen order, no duplicates. A symbol can sit in two categories (XLU is broad_market and a sector)
    and must still be collected, stored and reported once."""
    return list(dict.fromkeys(symbols))


def load_tickers_from_dict(raw: dict[str, Any] | None) -> dict[str, list[str]]:
    raw = raw or {}
    equities: list[str] = []
    for category_tickers in raw.get("equities", {}).values():
        if isinstance(category_tickers, list):
            equities.extend(category_tickers)

    return {
        "equities": _unique(equities),
        "crypto": _unique(list(raw.get("crypto", []))),
    }


def load_tickers(path: str | Path = TICKERS_PATH) -> dict[str, list[str]]:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return load_tickers_from_dict(raw)


def load_equity_category(category: str, path: str | Path = TICKERS_PATH) -> list[str]:
    """Return just one equities category (e.g. "large_cap") without flattening the rest."""
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return list(raw.get("equities", {}).get(category, []))


def load_futures_tickers(path: str | Path = TICKERS_PATH) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return list(raw.get("futures", []))


def load_cash_like_tickers(path: str | Path = TICKERS_PATH) -> list[str]:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return list(raw.get("cash_like", []))


def universe_frame(path: str | Path = TICKERS_PATH):
    """One row per collected ticker: the dbt `universe` seed. Cash-like names are kept but not rankable."""
    import pandas as pd

    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    cash_like = set(raw.get("cash_like", []))
    rows: dict[str, dict[str, Any]] = {}
    for category, symbols in raw.get("equities", {}).items():
        for symbol in symbols or []:
            rows.setdefault(symbol, {"ticker": symbol, "asset_class": "equity", "category": category})
    for symbol in raw.get("crypto", []):
        rows.setdefault(symbol, {"ticker": symbol, "asset_class": "crypto", "category": "crypto"})
    frame = pd.DataFrame(list(rows.values()), columns=["ticker", "asset_class", "category"])
    frame["is_cash_like"] = frame["ticker"].isin(cash_like)
    frame["is_rankable"] = ~frame["is_cash_like"]
    return frame


def export_universe_seed(out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    universe_frame().to_csv(out_path, index=False, lineterminator="\n")
    return out_path


if __name__ == "__main__":
    import argparse

    from .config import ROOT_DIR

    parser = argparse.ArgumentParser(description="Ticker universe utilities.")
    parser.add_argument("--export-seed", action="store_true", help="Write dbt/seeds/universe.csv from tickers.yaml.")
    if parser.parse_args().export_seed:
        print(f"Wrote {export_universe_seed(ROOT_DIR / 'dbt' / 'seeds' / 'universe.csv')}")
