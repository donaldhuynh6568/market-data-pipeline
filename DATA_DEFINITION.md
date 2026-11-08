# Data definition

Each table below lists its grain, the code that builds it, and what it is for. Column-level documentation for dbt models lives in `dbt/models/**/_*.yml`. Every grain has a uniqueness test.

## Raw (`data/raw/`, append-only)

Every raw file carries the collector's columns as received, plus `source`, `ingested_at` (UTC), `run_id` and `request_params` (JSON). Partitions are `ingest_date=YYYY-MM-DD`.

| Dataset | Collector | Natural key |
|---|---|---|
| `alpaca/bars_1d` (also `bars_1h`, `bars_4h`, `bars_5m`) | `price.py` (split/dividend adjusted) | symbol, timestamp |
| `alpaca/options_chain` | `options.py` | contract_symbol, snapshot_date |
| `alpaca/news` | `news.py` (`publisher` = news source) | id, ticker |
| `deribit/options_chain` | `deribit_options.py` | contract_symbol, snapshot_date |
| `perps/funding_oi` | `perps.py` | symbol, venue, timestamp |
| `fred/series` | `fred.py` | series_id, date |
| `fred/fomc_decisions` | `fomc.py` | date |
| `sec/filings` | `sec.py` | accessionNumber |
| `sec/companyfacts` | `fundamentals.py` | ticker, fiscal_period_end, form |
| `sec/form4` | `insider.py` | transaction |
| `sec/13f` | `institutional.py` | fund, filing, holding |
| `cftc/cot` | `cot.py` | market, report date |
| `yahoo/futures_5m` | `futures_volume.py` | symbol, timestamp |
| `senate_watcher/trades` | `congress.py` (data ends 2020-12-02) | transaction |
| `_manifest` | `raw.raw_run` | run_id |

`data/_legacy/` holds the option and Deribit snapshot files from before the reset (2026-10-22). Nothing reads them.

## Staging (dbt views in `data/warehouse.duckdb`)

| Model | Grain | Notes |
|---|---|---|
| `stg_price_daily` | ticker, date | Newest ingest wins. Equity dates are New York dates; crypto dates are UTC. |
| `stg_fred` | series_id, observation_date | `available_at` = observation date + a release lag for the series' inferred frequency |
| `stg_trading_calendar` | date | SPY trading dates: the one date axis every mart uses |
| `raw_coverage`, `raw_runs` (audit) | ticker / run_id | History depth per ticker; the collector run log |

## Marts (`data/marts/*.parquet`)

| Mart | Grain | Built by | Contents |
|---|---|---|---|
| `mart_price_daily` | ticker, date | dbt | Spine (calendar × universe), capped forward fill, `ret_1d` |
| `mart_targets` | ticker, date | dbt | `fwd_ret` over `target_horizon`, its rank, and whether it is above the median. **Targets only.** |
| `mart_features` | ticker, date | dbt | Returns, volatility, `mom_score`/`mom_rank`, MA200 trend flags, point-in-time VIX, calendar flags |
| `mart_split` | date | dbt | train / validation / test / gap |
| `mart_rebalance_dates` | rebalance_date | dbt | Signal date (month end), entry date, exit date, split |
| `mart_signals` | variant, ticker, rebalance_date | dbt | Target weights: `momentum_topN`, `momentum_topN_trend`, `equal_weight`, `spy_bh` |
| `mart_quintiles` | split, quintile | dbt | Forward return by momentum quintile (gradient test) |
| `mart_sim_trades` | variant, ticker, rebalance_date | sim | Weight, turnover, gross return, cost, net contribution |
| `mart_sim_periods` | variant, rebalance_date | sim | Portfolio gross/net return per holding period |
| `mart_equity_curve` | variant, rebalance_date | sim | Cumulative net equity and drawdown; random draws as a p5/p50/p95 band |
| `mart_rank_ic` | rebalance_date | sim | Spearman correlation of `mom_score` with the next holding-period return |
| `mart_performance` | variant, split | sim | Net/gross CAGR, max drawdown, Sharpe, turnover, hit rate vs base rate, rank IC, `random_pctile` |

The test split appears in the sim marts and in `mart_quintiles` only when the build runs with `--include-test`.
