from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta, timezone
from urllib.request import Request, urlopen

import pandas as pd

from market_data_pipeline.raw import raw_run

BASE_URL = "https://www.deribit.com/api/v2/public"
SUPPORTED_CURRENCIES = ["BTC", "ETH", "SOL"]
DEFAULT_CURRENCIES = ["BTC", "ETH"]
EXPIRY_HOUR_UTC = 8
REQUEST_TIMEOUT_SECONDS = 30
# Deribit options: 1 contract = 1 unit of the underlying coin (equity options use 100).
DERIBIT_CONTRACT_MULTIPLIER = 1


def _get(path: str, **params):
    query = "&".join(f"{key}={value}" for key, value in params.items())
    request = Request(f"{BASE_URL}/{path}?{query}", headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        return json.load(response)["result"]


def parse_instrument(name: str) -> tuple[date, float, str] | None:
    """'BTC-27MAR26-80000-C' -> (2026-03-27, 80000.0, 'call'). None if it isn't a plain option name."""
    parts = name.split("-")
    if len(parts) != 4:
        return None
    _, expiry_text, strike_text, kind = parts
    try:
        expiry = datetime.strptime(expiry_text, "%d%b%y").date()
        strike = float(strike_text.replace("d", "."))
    except ValueError:
        return None
    option_type = {"C": "call", "P": "put"}.get(kind)
    if option_type is None:
        return None
    return expiry, strike, option_type


def bs_gamma(spot: float, strike: float, years: float, iv: float) -> float | None:
    """Black-Scholes gamma with zero rate. Deribit's book summary has no Greeks, so derive from mark IV."""
    if spot <= 0 or strike <= 0 or years <= 0 or iv <= 0:
        return None
    sigma_sqrt_t = iv * math.sqrt(years)
    d1 = (math.log(spot / strike) + 0.5 * sigma_sqrt_t**2) / sigma_sqrt_t
    pdf = math.exp(-0.5 * d1**2) / math.sqrt(2 * math.pi)
    return pdf / (spot * sigma_sqrt_t)


def build_chain(
    currency: str,
    summaries: list[dict],
    spot: float,
    now: datetime,
    moneyness: float,
    max_expiry_days: int,
) -> pd.DataFrame:
    strike_low, strike_high = spot * (1 - moneyness), spot * (1 + moneyness)
    expiry_cutoff = now.date() + timedelta(days=max_expiry_days)
    rows = []
    for item in summaries:
        parsed = parse_instrument(item["instrument_name"])
        if parsed is None:
            continue
        expiry, strike, option_type = parsed
        if not strike_low <= strike <= strike_high or expiry > expiry_cutoff:
            continue

        expires_at = datetime(expiry.year, expiry.month, expiry.day, EXPIRY_HOUR_UTC, tzinfo=timezone.utc)
        years = (expires_at - now).total_seconds() / (365 * 24 * 3600)
        iv = (item.get("mark_iv") or 0) / 100  # Deribit quotes IV in percent
        rows.append(
            {
                "contract_symbol": item["instrument_name"],
                "underlying_symbol": f"{currency}/USD",
                "strike_price": strike,
                "expiration_date": expiry,
                "type": option_type,
                "open_interest": float(item.get("open_interest") or 0),
                "volume": float(item.get("volume") or 0),
                "implied_volatility": iv or None,
                "gamma": bs_gamma(spot, strike, years, iv),
                "spot_price": spot,
                "contract_multiplier": DERIBIT_CONTRACT_MULTIPLIER,
                "snapshot_date": now.date().isoformat(),
            }
        )
    return pd.DataFrame(rows)


def fetch_chain(currency: str, moneyness: float, max_expiry_days: int) -> pd.DataFrame:
    now = datetime.now(timezone.utc)
    spot = _get("get_index_price", index_name=f"{currency.lower()}_usd")["index_price"]
    summaries = _get("get_book_summary_by_currency", currency=currency, kind="option")
    return build_chain(currency, summaries, spot, now, moneyness, max_expiry_days)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Deribit crypto option chains into the raw layer.")
    parser.add_argument("--currencies", nargs="*", choices=SUPPORTED_CURRENCIES, default=DEFAULT_CURRENCIES)
    parser.add_argument("--moneyness", type=float, default=0.20, help="Strike filter as +/- fraction of spot")
    parser.add_argument("--max-expiry-days", type=int, default=60)
    args = parser.parse_args()

    params = {"currencies": args.currencies, "moneyness": args.moneyness, "max_expiry_days": args.max_expiry_days}
    with raw_run("deribit", "options_chain", params, date_col="snapshot_date") as run:
        collect_chains(args, run)


def collect_chains(args, run) -> None:
    chains = []
    for currency in args.currencies:
        try:
            chain = fetch_chain(currency, args.moneyness, args.max_expiry_days)
        except Exception as exc:  # pragma: no cover - external API
            print(f"  [deribit] {currency} failed, skipping: {exc}")
            continue
        if chain.empty:
            print(f"  [deribit] {currency}: no contracts in range (Deribit may not list options for it)")
            continue
        print(f"  [deribit] {currency}: {len(chain)} contracts")
        chains.append(chain)

    if not chains:
        print("No Deribit options data collected.")
        return

    run.write(pd.concat(chains, ignore_index=True))

if __name__ == "__main__":
    main()
