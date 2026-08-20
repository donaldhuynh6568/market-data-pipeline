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
│   └── collectors/
│       ├── __init__.py
│       ├── price.py
│       └── options.py
├── common.py
├── collect_price_data.py
├── collect_options_data.py
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

### 2) Collect options data

```bash
uv run python -m market_data_pipeline.collectors.options --moneyness 0.20 --max-expiry-days 60
```

### 3) Combine the parquet files into Excel

```bash
uv run python -m market_data_pipeline.combine
```

The shortcut wrappers still work too:

```bash
uv run collect_price_data.py --days 90
uv run collect_options_data.py --moneyness 0.20 --max-expiry-days 60
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

crypto:
  - BTC/USD
  - ETH/USD
```

You can add or remove symbols without touching the code logic.

## Output files

When the collection scripts run, they generate files inside `data/`:

- `price_data.parquet`
- `options_data.parquet`
- `market_data.xlsx`

These are local runtime artifacts and should not be committed to source control.

## Known issue fixed

The parquet import error was caused by missing `pyarrow` support. The dependency is now included in `pyproject.toml`, and the project is configured so parquet writes work correctly.

## Notes on maintainability

This repo now follows a more professional Python layout:

- separate config and data-loading modules
- reusable business logic in `market_data_pipeline/`
- small, testable units for ticker handling and data transformation
- compatibility wrappers so historic script calls still work
- a clearer, more structured workflow for future development

For a deeper explanation of the collected data and the logic behind the unusual activity signal, see [DATA_DEFINITION.md](C:/Users/kimje/Documents/market-data-pipeline/DATA_DEFINITION.md).
