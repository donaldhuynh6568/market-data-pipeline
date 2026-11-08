# Market Data Pipeline

Collectors write append-only raw parquet. dbt (on DuckDB) builds staging tables and analysis marts, and a Python step simulates the momentum rotation strategy (Track A) against three baselines, net of costs. A notebook and an Excel workbook let you review the result.

The design is set out in two specs:
- [market_analysis_restart_spec.md](reference/market_analysis_restart_spec.md): the layers, the raw contract and the phases.
- [analysis_strategy_spec.md](reference/analysis_strategy_spec.md): the marts, the evaluation rules and the baselines.

## Layers

```text
collectors ──► data/raw/<source>/<dataset>/ingest_date=YYYY-MM-DD/part-<run_id>.parquet   (append-only)
               data/raw/_manifest/...                                                   (one row per run)
dbt        ──► staging views   stg_price_daily, stg_fred, stg_trading_calendar          (data/warehouse.duckdb)
               marts           mart_price_daily, mart_targets, mart_features, mart_split,
                               mart_rebalance_dates, mart_signals, mart_quintiles       (data/marts/*.parquet)
sim        ──► mart_sim_trades, mart_sim_periods, mart_equity_curve, mart_rank_ic, mart_performance
review     ──► data/review/mart_review_<date>.xlsx, notebooks/mart_review.ipynb
```

| Layer | Logic allowed | Code |
|---|---|---|
| raw | None. Data is stored as received, plus `source`, `ingested_at`, `run_id` and `request_params` | `market_data_pipeline/raw.py`, `collectors/` |
| staging | Typing, renaming, dedup (latest `ingested_at` wins), point-in-time `available_at` | `dbt/models/staging/` |
| marts | Features, targets, splits, signals | `dbt/models/marts/` |
| simulation | Holding-period returns, costs, random baselines, metrics | `market_data_pipeline/sim/` |

## Setup

```bash
uv sync --all-groups        # dev + notebook groups
cp .env.example .env        # ALPACA_API_KEY, ALPACA_SECRET_KEY, FRED_API_KEY, SEC_USER_AGENT
```

## Run order

```bash
# 1. Collect (each run appends a new raw partition; nothing is overwritten)
uv run python -m market_data_pipeline.collectors.price                 # incremental daily bars
uv run python -m market_data_pipeline.collectors.fred                  # macro series incl. VIXCLS
uv run python -m market_data_pipeline.collectors.options               # snapshots: run daily, can't be re-fetched
uv run python -m market_data_pipeline.collectors.deribit_options

# 2. Build: universe seed -> dbt build (all tests + unit tests) -> feature registry check -> simulation
uv run python -m market_data_pipeline.build

# 3. Review
uv run python -m market_data_pipeline.review                           # Excel + validation verdicts
uv run jupyter lab notebooks/mart_review.ipynb
```

Other useful commands:
- `price --start 2016-01-01` backfills.
- `price --probe` reports how deep Alpaca's history goes.
- `build --include-test` reveals the held-out test split. Run it only once the strategy is frozen.
- `build --select staging` builds a subset with dbt.

The other collectors (`cot`, `sec`, `fundamentals`, `insider`, `institutional`, `congress`, `perps`, `news`, `futures_volume`, `fomc`) also write to raw. They get staging models only when a signal needs them.

## Configuration

| File | What it controls |
|---|---|
| `config/tickers.yaml` | The universe. `cash_like` names are kept but never ranked. |
| `config/strategy.yaml` | Rebalance timing, top-N grid, costs, target horizon, splits, trend filter, keep-or-cut thresholds and FRED release lags. These are provisional answers to the spec's OPEN items. |
| `config/feature_registry.yaml` | Every `mart_features` column and its role. The build fails if the mart and the registry disagree. |

## Tests

```bash
uv run pytest            # collectors, raw layer, simulation math, dbt project guarantees
uv run ruff check
```

dbt data tests and the forward-return unit test run inside `build`.

## Known limits
- **Survivorship and selection bias.** The universe was picked in 2026, so it contains the names that won. That inflates the absolute CAGR of every variant *and* every baseline. Read the results relative to equal-weight and random selection.
- **History depth.** Alpaca's daily history starts in 2016 for equities and 2021 for crypto.
- **FRED revisions.** FRED values are the latest vintage, not true point-in-time data. That is fine for VIXCLS, but not for revised series such as GDP.
