# Market Data Pipeline

This project collects market data from Alpaca, stores local parquet snapshots, and optionally uploads the result to Supabase. It is built for a small analysis workflow that tracks a basket of equities and crypto and looks for unusual options activity.

## Goals

- Collect daily price data for a configurable ticker list
- Collect filtered options activity snapshots near the money and near expiry
- Save local parquet files for a reproducible dataset
- Produce a combined Excel workbook for review
- Keep the logic structured so it is easier to maintain and test

## Project structure

```text
market-data-pipeline/
├── market_data_pipeline/
│   ├── __init__.py
│   ├── config.py
│   ├── tickers.py
│   ├── storage.py
│   ├── combine.py
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── price.py
│   │   ├── options.py
│   │   ├── cot.py
│   │   ├── fred.py
│   │   ├── sec.py
│   │   ├── congress.py
│   │   ├── insider.py
│   │   └── institutional.py
│   └── transforms/
│       ├── __init__.py
│       └── gex.py
├── collect_price_data.py
├── collect_options_data.py
├── collect_cot_data.py
├── combine.py
├── tickers.yaml
├── pyproject.toml
├── .env
├── README.md
└── data/
```

The package under `market_data_pipeline/` is the maintainable core. The root-level scripts remain as thin compatibility wrappers so direct calls still work.

## Prerequisites

- Python 3.11+
- `uv` installed and available on PATH
- Your existing `.env` file with Alpaca credentials
- Optional: Supabase credentials if you want DB writes enabled

## Setup

1. Open a terminal in the project folder.
2. Install dependencies:

```bash
uv sync
```

3. Make sure your `.env` file exists and includes the required values:

```env
ALPACA_API_KEY=your_alpaca_api_key
ALPACA_SECRET_KEY=your_alpaca_secret_key

# Required for the FRED collector — free key from
# https://fred.stlouisfed.org/docs/api/api_key.html
FRED_API_KEY=your_fred_api_key

# Required for the SEC collectors (filings, insider, institutional). SEC's
# fair-use policy requires a descriptive User-Agent with contact info.
SEC_USER_AGENT=YourApp your-email@example.com

# Optional
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_key
```

The project reads your existing `.env` automatically.

## Recommended execution pattern

For maintainability, prefer the package entrypoints instead of raw script files:

### 1) Collect price data

```bash
uv run python -m market_data_pipeline.collectors.price --days 90
```

Daily bars by default. For intraday bars, add `--timeframe`:

```bash
uv run python -m market_data_pipeline.collectors.price --timeframe 1Hour
uv run python -m market_data_pipeline.collectors.price --timeframe 4Hour
uv run python -m market_data_pipeline.collectors.price --timeframe 5Min
```

`--timeframe` choices are `1Day` (default, writes `price_data.parquet`),
`1Hour` (writes `price_data_1h.parquet`), `4Hour` (writes
`price_data_4h.parquet`), and `5Min` (writes `price_data_5m.parquet`) —
intraday runs never overwrite the daily dataset.

### 2) Detect swing-high/low structure from intraday bars

```bash
uv run python -m market_data_pipeline.transforms.swing_structure --timeframe 1h
uv run python -m market_data_pipeline.transforms.swing_structure --timeframe 4h
```

Reads the corresponding `price_data_{1h,4h}.parquet` (run the intraday price
collector first) and writes `data/swing_structure_{1h,4h}.parquet` with a
per-symbol `UPTREND`/`DOWNTREND`/`SIDEWAYS`/`UNKNOWN` classification plus the
swing-point values/timestamps it was derived from.

### 3) Flag unusual 5-minute volume for large-cap stocks

```bash
uv run python -m market_data_pipeline.transforms.volume_floor
```

Reads `data/price_data_5m.parquet` (run `collectors.price --timeframe 5Min`
first), defaults to the `large_cap` tickers (override with `--symbols`), and
flags each bar against **that symbol's own trailing baseline** — not a fixed
number like the futures volume-floor check. Default: a bar is flagged if its
volume is at least 2x (`--multiplier`) the trailing 20-bar (`--window`)
average for that symbol. Writes `data/volume_floor_5m.parquet`.

### 4) Collect options data

```bash
uv run python -m market_data_pipeline.collectors.options --moneyness 0.20 --max-expiry-days 60
```

### 5) Collect CFTC COT positioning data for metals

This is a complementary data source focused on large-speculator positioning in metals markets such as gold and silver. The current public source in this repo is the CFTC public reporting API, which returns weekly managed-money and positioning data in JSON form and is normalized before being filtered to gold/silver rows.

```bash
uv run python -m market_data_pipeline.collectors.cot --source-url "https://publicreporting.cftc.gov/api/v3/views/kh3c-gbw2/query.json"
```

You can also point to a local CSV, JSON, TXT, or ZIP file:

```bash
uv run python -m market_data_pipeline.collectors.cot --csv-path "./data/cot_raw.json"
```

Or set the environment variable once in `.env`:

```env
CFTC_COT_URL=https://publicreporting.cftc.gov/api/v3/views/kh3c-gbw2/query.json
```

This writes a parquet file to `data/cot_data.parquet`.

### 6) Calculate gamma exposure (GEX) from the options chain

```bash
uv run python -m market_data_pipeline.transforms.gex
```

Reads `data/options_data.parquet` (must have been collected with gamma
present) and writes `data/gex_data.parquet` with per-symbol total/call/put
gamma, gamma-wall strikes, and a `gamma_flip` estimate.

### 7) Collect FRED macroeconomic data

```bash
uv run python -m market_data_pipeline.collectors.fred
```

Collects a curated set of Federal Reserve, Treasury yield, inflation,
employment, growth, liquidity, dollar, and volatility series (see
`DEFAULT_SERIES` in `market_data_pipeline/collectors/fred.py`), overridable
with `--series`. Writes `data/fred_data.parquet`. Stores only the latest
value per series — see [DATA_DEFINITION.md](C:/Users/kimje/Documents/market-data-pipeline/DATA_DEFINITION.md)
for the revision/vintage-data caveat.

### 8) Collect SEC EDGAR filing metadata

```bash
uv run python -m market_data_pipeline.collectors.sec
```

Looks up each configured equity ticker's CIK and collects recent 10-K/10-Q/8-K
filing metadata (overridable with `--forms`). Writes `data/sec_filings.parquet`.
Most meaningful for the `large_cap` tickers — ETFs file different form types.

### 9) Collect Senate trading disclosures (historical)

```bash
uv run python -m market_data_pipeline.collectors.congress
```

Pulls from the community-maintained Senate Stock Watcher dataset, filtered to
the configured ticker universe. **This source only covers 2012-06-14 through
2020-12-02** — it is not a live feed (see DATA_DEFINITION.md). Writes
`data/congress_trades.parquet`.

### 10) Collect corporate insider transactions (Form 4)

```bash
uv run python -m market_data_pipeline.collectors.insider
```

For each `large_cap` ticker, fetches recent SEC Form 4 filings and parses
non-derivative (plain stock buy/sell) transactions. Writes
`data/insider_trades.parquet`. This makes many SEC requests (one per filing
found) — expect it to take longer than the other collectors.

### 11) Collect institutional 13F holdings

```bash
uv run python -m market_data_pipeline.collectors.institutional
```

Pulls the latest Form 13F holdings for a curated watchlist of well-known
institutional investors (Berkshire Hathaway, Bridgewater, Scion, Pershing
Square, Renaissance Technologies — see `DEFAULT_WATCHLIST` in
`market_data_pipeline/collectors/institutional.py`), overridable with
`--only`. Writes `data/institutional_holdings.parquet`.

### 12) Collect 5-minute futures volume (volume-floor check)

```bash
uv run python -m market_data_pipeline.collectors.futures_volume
```

No API key needed. **Read before relying on this:** this pulls from Yahoo
Finance's public chart endpoint, which is unofficial (no SLA, could change or
rate-limit without notice), delayed by roughly 10 minutes, and limited to
about 60 days of intraday history. Alpaca has no futures data at all, so this
was the only free option found. Default tickers come from the `futures` list
in `tickers.yaml` (`MNQ=F`, `MES=F`, `SIL=F`), overridable with `--tickers`.
Writes `data/futures_volume_5m.parquet` with a `volume_floor_met` flag
(`volume >= 20,000` contracts per 5-minute bar).

### 13) Combine the parquet files into Excel

```bash
uv run python -m market_data_pipeline.combine
```

The shortcut wrappers still work too:

```bash
uv run collect_cot_data.py 
uv run collect_price_data.py --days 90
uv run python -m market_data_pipeline.collectors.price --timeframe 1Hour
uv run python -m market_data_pipeline.collectors.price --timeframe 4Hour
uv run python -m market_data_pipeline.collectors.price --timeframe 5Min
uv run python -m market_data_pipeline.transforms.swing_structure --timeframe 1h
uv run python -m market_data_pipeline.transforms.swing_structure --timeframe 4h
uv run python -m market_data_pipeline.transforms.volume_floor


uv run python -m market_data_pipeline.collectors.options --moneyness 0.20 --max-expiry-days 60
uv run python -m market_data_pipeline.transforms.gex

uv run python -m market_data_pipeline.collectors.fred
uv run python -m market_data_pipeline.collectors.fomc


uv run python -m market_data_pipeline.collectors.futures_volume
uv run combine.py
```

## Testing and quality checks

This project is structured to support Python best practices and maintainability. Run the checks before relying on the pipeline:

```bash
uv run pytest
uv run ruff check .
```

## Ticker configuration

The ticker universe lives in `tickers.yaml` and is loaded at runtime:

```yaml
equities:
  broad_market:
    - SPY
    - QQQ
    - IWM
  large_cap:
    - AAPL
    - MSFT
    - NVDA

crypto:
  - BTC/USD
  - ETH/USD

futures:
  - MNQ=F
  - MES=F
  - SIL=F
```

The `large_cap` category holds individual stocks (as opposed to the ETF
categories above) — the SEC, insider, and 13F-adjacent collectors are far
more meaningful for operating companies than for ETF shares, and it's also
the default symbol set for the equity volume-floor check. `futures` uses
Yahoo Finance tickers (`SYMBOL=F`), since Alpaca has no futures data at all.

You can add or remove symbols without touching the code logic.

## Output files

When the collection scripts run, they generate files inside `data/`:

- `price_data.parquet`
- `price_data_1h.parquet`
- `price_data_4h.parquet`
- `price_data_5m.parquet`
- `swing_structure_1h.parquet`
- `swing_structure_4h.parquet`
- `volume_floor_5m.parquet`
- `futures_volume_5m.parquet`
- `options_data.parquet`
- `cot_data.parquet`
- `gex_data.parquet`
- `fred_data.parquet`
- `sec_filings.parquet`
- `congress_trades.parquet`
- `insider_trades.parquet`
- `institutional_holdings.parquet`
- `market_data.xlsx`

These are local runtime artifacts and should not be committed to source control.




For a deeper explanation of the collected data and the logic behind the unusual activity signal, see [DATA_DEFINITION.md](C:/Users/kimje/Documents/market-data-pipeline/DATA_DEFINITION.md).
