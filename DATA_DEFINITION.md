# Data definition and collection logic

This document explains what the pipeline collects, how often it collects it, how the exported sheets are combined, and what "unusual activity" means in this project.

## 1) Price data

The price collector pulls daily OHLCV bars for the ticker universe in `tickers.yaml`.

### Frequency
- Default collection frequency: daily bars
- Lookback range: configurable with `--days` in the price collector
- Example: `uv run python -m market_data_pipeline.collectors.price --days 30`

### What each row contains
Each row is a bar for a specific symbol and timestamp. Typical fields include:
- `symbol`
- `timestamp`
- `open`
- `high`
- `low`
- `close`
- `volume`
- `asset_class` (`equity` or `crypto`)

### Why it matters
These rows are the baseline market data used to understand broad price movement and compare later option activity against the underlying asset.

### Future modifications
If you want more detail later, you could add:
- intraday intervals (e.g. 1-minute, 5-minute)
- custom date windows
- asset-specific metadata
- alternate providers or granular bar intervals

## 2) Options data

The options collector filters the option chain around each equity and pulls data for active contracts with the highest signal value.

### Frequency
- Snapshot-based collection, not a full historical options time series
- The project pulls a daily snapshot of option contracts near the money and near expiry
- The script filters by:
  - moneyness (default ±20% from spot)
  - maximum expiry window (default 60 days)
- Configurable with:
  - `--moneyness`
  - `--max-expiry-days`

### Contract filtering logic
The project intentionally does not collect every option contract. It focuses on the area most likely to show meaningful flow:
- strikes close to the current stock price
- expirations near-term
- active contracts only
- contracts with zero volume and zero open interest are removed as dead strikes

### Key option fields
Each row includes:
- `contract_symbol`
- `underlying_symbol`
- `strike_price`
- `expiration_date`
- `type` (`call` or `put`)
- `open_interest`
- `volume`
- `implied_volatility`
- `delta`
- `spot_price`
- `snapshot_date`

## 3) What “unusual activity” means

This project defines unusual activity as a contract or set of contracts showing unusually high volume relative to open interest.

### Calculation
The script computes:
- `vol_oi_ratio = volume / open_interest`

If `open_interest > 0` and `vol_oi_ratio > 1.0`, the script flags the contract as unusual.

### Interpretation
This means today's trading volume exceeds the current open interest. In simple terms, the market is seeing more activity than the existing position base suggests. That often points to:
- new positioning
- fresh interest in a contract
- a possible directional or sentiment signal

This is not a guaranteed directional prediction, but it is a practical signal for further review.

### Why this heuristic is useful
It is a lightweight, explainable signal that does not require a large data pipeline or complicated backtesting setup. It is meant to give an analyst a quick shortlist of contracts to review.

### Future tuning
If you want to evolve the definition later, you can adjust:
- threshold for unusual activity (`> 1.0` right now)
- which metrics are included in the final output
- additional logic such as comparing to a rolling average or prior days

## 4) What the combined workbook does

The combine step reads the local parquet files and writes a single Excel workbook named `market_data.xlsx`.

### Workbook structure
The workbook includes three sheets:
- `Price Data` — all price rows
- `Options Data` — all filtered option rows
- `Unusual Activity` — only contracts flagged by the unusual activity logic

### Why combine them
This gives a single review file that can be opened in Excel without manual joins or additional work. It is designed for quick analysis and manual review.

### Why the unusual activity sheet is important
The full options dataset can be large. The filtered unusual-activity sheet narrows the data to the rows most relevant for signs of fresh market positioning.

## 5) Data frequency summary

| Dataset | Frequency | Notes |
|---|---|---|
| Price data | Daily bars | Configurable with `--days` |
| Options snapshot | Daily collection pass | Uses moneyness and expiry filters |
| Combined workbook | On-demand after collection | Produces a review-friendly Excel file |

## 6) How this should evolve

This project is intentionally designed so these definitions can be changed without rewriting the whole pipeline.

If the logic changes later, the places most likely to be updated are:
- `tickers.yaml` for the symbol universe
- `market_data_pipeline/collectors/price.py` for price data frequency and fields
- `market_data_pipeline/collectors/options.py` for contract filtering and unusual activity logic
- `market_data_pipeline/combine.py` for workbook structure and sheet output

The goal is to keep the business definitions clear and explicit, so future data collection changes are easy to reason about.
