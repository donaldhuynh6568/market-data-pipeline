from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import TICKERS_PATH


def load_tickers_from_dict(raw: dict[str, Any] | None) -> dict[str, list[str]]:
    raw = raw or {}
    equities: list[str] = []
    for category_tickers in raw.get("equities", {}).values():
        if isinstance(category_tickers, list):
            equities.extend(category_tickers)

    return {
        "equities": equities,
        "crypto": list(raw.get("crypto", [])),
    }


def load_tickers(path: str | Path = TICKERS_PATH) -> dict[str, list[str]]:
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return load_tickers_from_dict(raw)
