# Market Analysis Restart: Spec (v2)

Status: v2. Phases 1-4 are built (hard reset on 2026-10-22, branch `restart`; the prior state is tagged `pre-reset`). Items still marked **OPEN** are decisions to make. The companion spec is `analysis_strategy_spec.md`.

## 1. Goal

Restart the analysis on top of the existing collectors using a raw / staging / mart structure. Keep only analysis that can show it improves returns net of costs. Momentum is the first and only committed signal.

## 2. Principles

- Raw is append-only and untouched. Collectors write it, nothing else does.
- Parquet is storage, DuckDB is the query engine, **dbt** owns the SQL transformations. No database server.
- Every table declares its grain and has a uniqueness test.
- Point-in-time correctness: slow data carries an `available_at` date so nothing leaks future information.
- Forward returns are targets only and are never used as features.
- A signal earns a place only if it beats baselines out of sample (section 8).

## 3. Layers and folder layout (as built)

```
data/
  raw/<source>/<dataset>/ingest_date=YYYY-MM-DD/part-<run_id>.parquet
  raw/_manifest/ingest_date=YYYY-MM-DD/part-<run_id>.parquet
  warehouse.duckdb            dbt target (staging views, audit tables)
  marts/<mart_name>.parquet   dbt external marts + simulation marts
  review/                     Excel review workbooks
config/   tickers.yaml, strategy.yaml, feature_registry.yaml, fomc_meeting_dates.yaml
dbt/      models/staging, models/marts, models/audit, macros, tests, seeds/universe.csv
market_data_pipeline/  collectors/, raw.py, settings.py, build.py, sim/, review/
notebooks/mart_review.ipynb
```

**Deviations from v1:**
- `dbt/` replaces the planned `sql/` folder.
- The simulation and performance marts are Python (`market_data_pipeline/sim/`) rather than SQL, because they need seeded random draws and vectorized portfolio math.

| Layer | Contents | Logic allowed |
|---|---|---|
| raw | Collector output as received, plus metadata columns | None |
| staging | One table per source: typed, renamed, deduped, keyed | Cleaning only |
| marts | Analysis tables built only from staging | Features, ranks, signals, backtests |

## 4. Collectors

Decision: **all collectors kept, all write to raw** through `raw.raw_run`.

| Collector | Raw dataset | Feeds staging? |
|---|---|---|
| price.py | `alpaca/bars_1d` (+ `bars_1h`, `bars_4h`, `bars_5m`) | Daily: yes. Intraday: parked |
| fred.py | `fred/series` | Yes (VIXCLS used by features) |
| options.py | `alpaca/options_chain` | Not yet |
| deribit_options.py | `deribit/options_chain` | Not yet |
| perps.py | `perps/funding_oi` | Not yet |
| news.py | `alpaca/news` | Not yet |
| fomc.py | `fred/fomc_decisions` | Not yet |
| sec.py, fundamentals.py, insider.py, institutional.py | `sec/filings`, `sec/companyfacts`, `sec/form4`, `sec/13f` | Not yet |
| cot.py | `cftc/cot` | Not yet |
| futures_volume.py | `yahoo/futures_5m` | Not yet |
| congress.py | `senate_watcher/trades` | **OPEN**: drop it? The data ends 2020-12-02 |

**Changes made at the reset:**
- Derived columns came out of the collectors, so raw stays as received:
  - options flow metrics
  - Deribit GEX and the `gex_crypto` file
  - the futures volume-floor flag
- The news `source` column was renamed `publisher`, because `source` is reserved for raw metadata.
- Supabase upload was removed.

## 5. Raw layer contract (built)

Every raw table keeps its original columns and values, plus `source`, `ingested_at` (UTC), `run_id` and `request_params` (JSON).
- Writing a column with one of those names is refused.
- The manifest logs one row per run: run_id, dataset, status (`success` / `empty` / `failed`), row and file count, min and max date, timing and error.
- Nothing is ever pruned or rewritten. Options snapshots now accumulate indefinitely.
- The roughly 90 days of option and Deribit snapshots from before the reset are in `data/_legacy/`, unread.

Exit check passed: two price runs produced two files; the incremental run resumed from `raw_max`; the manifest logged both.

## 6. Universe

- **Basket:** the whole of `config/tickers.yaml` (42 equities + 3 crypto pairs) minus `cash_like` (SHV). There is no separate 25-name list.
- **Context tickers:** SPY and QQQ for the trend filter; GLD as the defensive asset; VIX from FRED VIXCLS (point in time).
- **Crypto:** sampled on the equity (SPY) trading calendar, so all tickers share one date axis. A Monday return includes the weekend.
- **Minimum history:** a ticker becomes eligible once it has a full 3-month momentum score (64 trading days). SPCX, SNDK, GEV and IBIT therefore join late. IETH stopped trading on 2026-07-31 and drops out after its last bar plus the 5-day fill cap.

## 7. Staging (built, dbt)

- `stg_price_daily`: newest ingest wins. Equity dates are in New York time, crypto dates in UTC.
- `stg_fred`: `available_at` = observation date + a lag for the inferred frequency. The lags are daily 1, weekly 7, monthly 45 and quarterly 120 days (`config/strategy.yaml`).
  - Caveat: values are the latest vintage, not true revision history.
- `stg_trading_calendar`: SPY trading dates.
- Tests:
  - unique key and not-null on every grain
  - close > 0 and high ≥ low
  - a no-gap check against the SPY calendar (warn level)

## 8. Marts (built)

The marts follow the design in analysis spec section 9; the full list is in `DATA_DEFINITION.md`.

**Momentum v1 (as built):**
- 1, 3, 6 and 12-month returns, plus a 12-month return that skips the latest month.
- 1 and 3-month realized volatility, computed on returns.
- Score = 3-month return ÷ 3-month volatility, with a cross-sectional rank.
- Distance from the 200-day MA, plus the SPY and QQQ MA flags.

**Evaluation settings:**
- Baselines: equal-weight, SPY buy-and-hold, and 200 random draws per top-N.
- Costs: 0.10% per side for equities, 0.25% per side for crypto.
- Rebalance monthly: the signal at the month-end close, filled at the next day's close.
- Top-N grid: {1, 2, 3, 5}.
- Split: 70/15/15 with 21-day gaps.

All of these are in `config/strategy.yaml` and remain provisional.

## 9. Phases

1. ✅ **Raw refactor.** Partitioned appends, metadata, manifest, pruning stopped.
2. ✅ **Backfill.** Alpaca daily history starts on **2016-01-04 for equities** and **2021-01-01 for crypto** (found with `price --probe`). The full backfill landed 105k bars.
3. ✅ **Staging** in dbt.
4. ✅ **Momentum mart and baseline backtest.** The simulation and review (notebook + Excel) are built too.
5. **Candidate signals**, one at a time, each judged against baselines. Not started.

**First results (2026-10-22, test split unseen):**
- **Train** (2016-10 to 2023-09, 83 rebalances): top-3 and top-5 net CAGR of 40% and 37%, against 23% for equal-weight and 13% for SPY. Both beat 99% of same-size random draws.
- **Validation** (2023-10 to 2025-03, 17 rebalances):
  - Only top-1 beats all baselines (random percentile 0.98), and it rests on just 17 rebalances.
  - Top-2, top-3 and top-5 lose to same-size random selection.
  - Mean rank IC is −0.05.
- **Verdict:** nothing is a KEEP candidate yet. The robustness grid fails: top-1 is a lone good cell.
- **Caveat:** survivorship bias in the hand-picked universe inflates every absolute number.

## 10. Open questions

1. ~~Overwrite or append?~~ Append-only raw is built.
2. ~~Which collectors stay?~~ All of them; staging is added per need.
3. ~~Context tickers and crypto handling?~~ SPY, QQQ, GLD and VIXCLS; crypto on the equity calendar.
4. ~~Alpaca history depth?~~ 2016 for equities, 2021 for crypto. **OPEN:** source a longer history (and a survivorship-free universe)?
5. ~~Costs, rebalance, top N, splits?~~ Provisional values in `config/strategy.yaml`. **OPEN:** confirm them.
6. ~~Excel workbook?~~ It is now a review output built from the marts (`python -m market_data_pipeline.review`).
7. ~~25-name basket?~~ The whole universe is used. **OPEN:** remove near-duplicate exposures (IBIT/IETH vs BTC/ETH)?
8. ~~Supabase?~~ Dropped.
9. Drop the congress collector (data ends 2020)? **OPEN.**
