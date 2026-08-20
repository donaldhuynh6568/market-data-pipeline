from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

import pandas as pd
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.requests import (
    OptionBarsRequest,
    OptionChainRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetStatus
from alpaca.trading.requests import GetOptionContractsRequest

from market_data_pipeline.config import require_alpaca_credentials
from market_data_pipeline.storage import save_parquet, upsert_to_supabase
from market_data_pipeline.tickers import load_tickers


def get_spot_price(stock_client: StockHistoricalDataClient, symbol: str) -> float | None:
    try:
        trade = stock_client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbol)
        )
        return trade[symbol].price
    except Exception as exc:  # pragma: no cover - external API
        print(f"  [{symbol}] couldn't get spot price, skipping: {exc}")
        return None


def get_filtered_contracts(
    trading_client: TradingClient,
    symbol: str,
    spot: float,
    moneyness: float,
    max_expiry_days: int,
) -> pd.DataFrame:
    strike_low = spot * (1 - moneyness)
    strike_high = spot * (1 + moneyness)
    expiry_cutoff = date.today() + timedelta(days=max_expiry_days)

    request = GetOptionContractsRequest(
        underlying_symbols=[symbol],
        status=AssetStatus.ACTIVE,
        strike_price_gte=str(round(strike_low, 2)),
        strike_price_lte=str(round(strike_high, 2)),
        expiration_date_lte=expiry_cutoff,
        limit=1000,
    )
    contracts = trading_client.get_option_contracts(request).option_contracts

    rows = [
        {
            "contract_symbol": c.symbol,
            "underlying_symbol": symbol,
            "strike_price": float(c.strike_price),
            "expiration_date": c.expiration_date,
            "type": c.type.value,
            "open_interest": int(c.open_interest) if c.open_interest else 0,
        }
        for c in contracts
    ]
    return pd.DataFrame(rows)


def add_daily_volume(
    option_client: OptionHistoricalDataClient, contracts_df: pd.DataFrame
) -> pd.DataFrame:
    if contracts_df.empty:
        return contracts_df

    symbols = contracts_df["contract_symbol"].tolist()
    request = OptionBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=datetime.now() - timedelta(days=1),
    )
    try:
        bars = option_client.get_option_bars(request).df.reset_index()
    except Exception as exc:  # pragma: no cover - external API
        print(f"  couldn't fetch option bars: {exc}")
        contracts_df["volume"] = 0
        return contracts_df

    if bars.empty:
        contracts_df["volume"] = 0
        return contracts_df

    latest = bars.sort_values("timestamp").groupby("symbol").last()["volume"]
    contracts_df["volume"] = (
        contracts_df["contract_symbol"].map(latest).fillna(0).astype(int)
    )
    return contracts_df


def add_chain_snapshot(
    option_client: OptionHistoricalDataClient,
    symbol: str,
    contracts_df: pd.DataFrame,
) -> pd.DataFrame:
    if contracts_df.empty:
        return contracts_df

    request = OptionChainRequest(underlying_symbol=symbol, feed="indicative")
    try:
        chain = option_client.get_option_chain(request)
    except Exception as exc:  # pragma: no cover - external API
        print(f"  [{symbol}] couldn't fetch chain snapshot: {exc}")
        return contracts_df

    iv_map: dict[str, float | None] = {}
    delta_map: dict[str, float | None] = {}
    for contract_symbol, snap in chain.items():
        iv_map[contract_symbol] = snap.implied_volatility
        delta_map[contract_symbol] = snap.greeks.delta if snap.greeks else None

    contracts_df["implied_volatility"] = contracts_df["contract_symbol"].map(iv_map)
    contracts_df["delta"] = contracts_df["contract_symbol"].map(delta_map)
    return contracts_df


def compute_flow_metrics(df: pd.DataFrame) -> pd.DataFrame:
    df["vol_oi_ratio"] = df.apply(
        lambda row: row["volume"] / row["open_interest"] if row["open_interest"] > 0 else None,
        axis=1,
    )
    df["unusual_activity"] = df["vol_oi_ratio"].apply(
        lambda value: value is not None and value > 1.0
    )

    put_call = (
        df.groupby(["underlying_symbol", "type"])["volume"].sum().unstack(fill_value=0)
    )
    if "put" in put_call.columns and "call" in put_call.columns:
        put_call_ratio = (
            put_call["put"] / put_call["call"].replace(0, pd.NA)
        ).to_dict()
    else:
        put_call_ratio = {}
    df["put_call_vol_ratio"] = df["underlying_symbol"].map(put_call_ratio)
    df["volume_zscore"] = None
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect filtered option activity snapshots.")
    parser.add_argument(
        "--moneyness",
        type=float,
        default=0.20,
        help="Strike filter as +/- fraction of spot price",
    )
    parser.add_argument(
        "--max-expiry-days",
        type=int,
        default=60,
        help="Only include contracts expiring within this many days",
    )
    args = parser.parse_args()

    api_key, secret_key = require_alpaca_credentials()
    tickers = load_tickers()
    trading_client = TradingClient(api_key, secret_key, paper=True)
    stock_client = StockHistoricalDataClient(api_key, secret_key)
    option_client = OptionHistoricalDataClient(api_key, secret_key)

    all_contracts = []
    for symbol in tickers["equities"]:
        print(f"Processing {symbol}...")
        spot = get_spot_price(stock_client, symbol)
        if spot is None:
            continue

        contracts_df = get_filtered_contracts(
            trading_client, symbol, spot, args.moneyness, args.max_expiry_days
        )
        if contracts_df.empty:
            print(f"  [{symbol}] no contracts in range, skipping")
            continue

        contracts_df = add_daily_volume(option_client, contracts_df)
        contracts_df = add_chain_snapshot(option_client, symbol, contracts_df)

        before = len(contracts_df)
        contracts_df = contracts_df[
            (contracts_df["volume"] > 0) | (contracts_df["open_interest"] > 0)
        ]
        print(
            f"  [{symbol}] {before} contracts in range, {len(contracts_df)} with real activity"
        )

        contracts_df["spot_price"] = spot
        all_contracts.append(contracts_df)

    if not all_contracts:
        print("No options data collected.")
        return

    combined = pd.concat(all_contracts, ignore_index=True)
    combined = compute_flow_metrics(combined)
    combined["snapshot_date"] = date.today().isoformat()

    out_path = save_parquet(combined, "options_data.parquet")
    print(f"\nSaved {len(combined)} contract rows to {out_path}")

    flagged = combined[combined["unusual_activity"]]
    print(f"{len(flagged)} contracts flagged for unusual activity (vol/OI > 1.0)")

    records = (
        combined.astype(object).where(pd.notnull(combined), None).to_dict(orient="records")
    )
    upsert_to_supabase("options_data", records, on_conflict="contract_symbol,snapshot_date")


if __name__ == "__main__":
    main()
