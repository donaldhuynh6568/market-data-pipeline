# Tech Stack

## Context

This project is a Python-based market-data pipeline that collects price and options data from Alpaca, stores local parquet snapshots, and combines the results into a single Excel workbook for review. It is intentionally lightweight and designed to be easy to understand, extend, and test.

## Current Phase

**Phase 1 (now):** package-based pipeline with compatibility wrappers.
- `market_data_pipeline/collectors/price.py` — collects daily OHLCV price history
- `market_data_pipeline/collectors/options.py` — collects filtered options snapshots and unusual activity flags
- `market_data_pipeline/combine.py` — reads the parquet files and writes `market_data.xlsx`
- root scripts like `collect_price_data.py` and `combine.py` remain as wrappers for compatibility

**Phase 2 (later):** add richer analytics, optional database structure, and a more formal reporting or scheduling layer.

## Core Infrastructure
- **Language**: Python 3.11+
- **Package Management**: `uv` with a project-level `pyproject.toml` and `uv.lock`
- **Secrets**: local `.env` file loaded via `python-dotenv`
- **Version Control**: Git + GitHub
- **Project Structure**: small package layout under `market_data_pipeline/` with tests in `tests/`

## Data Collection
- **Provider**: Alpaca via `alpaca-py`
  - Covers stock prices, crypto prices, and option data through one API key pair
  - The project uses the paper trading client for options work and standard historical clients for bars
- **Stock Price Data**: daily OHLCV bars from `StockHistoricalDataClient`
- **Crypto Price Data**: daily bars from `CryptoHistoricalDataClient`
- **Options Activity Data**: filtered option contract snapshots from the options chain and option contract API
  - Filters are based on moneyness and time to expiry
  - Dead contracts with zero volume and zero open interest are removed
  - The project computes a `vol_oi_ratio` to flag unusual activity

## Ticker Universe
The project uses a configurable ticker list in `tickers.yaml` rather than hardcoded symbol lists.

Current categories include:
- broad market: SPY, QQQ, IWM
- international: EFA, EEM
- defensive/rates: TLT, SHV
- sectors: XLE, XLU, XLP, XLC, XLV
- alternative: GLD
- crypto: BTC/USD, ETH/USD

This makes the universe easy to modify and extend without editing Python logic.

## Data Processing
- **DataFrames**: `pandas`
- **Parquet storage**: `pyarrow`
- **Excel export**: `openpyxl`
- **YAML ticker config**: `pyyaml`

## Database / Storage Layer
- **Local-first design**: parquet files in the `data/` directory are the main working cache
- **Optional external DB**: Supabase is integrated as an optional upsert target when credentials are set
- **Design principle**: local file output should not fail if Supabase is not configured

## Code Quality
- **Linting**: `ruff`
- **Testing**: `pytest`
- **Goal**: maintain small, readable, testable Python modules instead of a single large script

## Key Libraries
```text
# Package management
uv

# Secrets
python-dotenv

# Data collection
alpaca-py
pyyaml

# Data processing
pandas
pyarrow
openpyxl

# Optional DB
supabase

# Code quality
ruff
pytest
```

## Architecture (current phase)
```text
market_data_pipeline/
  config.py                 -> env vars + project paths
  tickers.py                -> ticker yaml loading logic
  storage.py                -> parquet writes + optional Supabase writes
  combine.py                -> workbook generation
  collectors/
    price.py                -> fetches OHLCV bars
    options.py              -> fetches filtered options + unusual activity

root compatibility scripts -> thin wrappers for the package modules
```

## Data Interpretation
- **Daily price data**: market bars for the configured lookback window, limited to trading days for equities
- **Options data**: filtered contracts near the money and close to expiry
- **Unusual activity**: contract rows where `volume / open_interest > 1.0`
- **Combined workbook**: a single Excel file containing raw price data, raw options data, and a filtered unusual-activity sheet for review

## Deferred / Not Needed Yet
This repo intentionally does not include infrastructure-heavy patterns like Kafka, Spark, Airflow, full container orchestration, or multi-service deployments. That is out of scope for the current personal pipeline stage.
