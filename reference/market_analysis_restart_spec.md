# Market Analysis Restart: Spec (v1, draft)

Status: v1 draft. The factual OPEN items are resolved from a code audit (2026-10-22). Items still marked **OPEN** are decisions to make.

## 1. Goal

Restart the analysis on top of the existing collectors using a raw / staging / mart structure. Keep only analysis that can show it improves returns net of costs. Momentum is the first and only committed signal.

## 2. Principles

- Raw is append-only and untouched. Collectors write it, nothing else does.
- Parquet is storage, DuckDB is the query engine. No database server.
- Every table declares its grain and has a uniqueness test.
- Point-in-time correctness: slow data (FRED, COT, fundamentals) carries an `available_at` date so nothing leaks future information.
- Forward returns are targets only and are never used as features.
- A signal earns a place only if it beats baselines out of sample (see section 8).

## 3. Layers and folder layout

```
data/
  raw/<source>/<dataset>/ingest_date=YYYY-MM-DD/part-*.parquet
  staging/<dataset>.parquet
  marts/<mart_name>.parquet
config/tickers.yaml
sql/staging/*.sql
sql/marts/*.sql
```

| Layer | Contents | Logic allowed |
|---|---|---|
| raw | Collector output as received, plus metadata columns | None |
| staging | One table per source: typed, renamed, deduped, keyed | Cleaning only |
| marts | Analysis tables built only from staging | Features, ranks, backtests |

## 4. Collectors in scope

Decision: **every collector moves to the raw layer.** Whether a source feeds staging or marts is decided separately, source by source.

How collectors write today:
- **merge**: load the flat file, concat, dedupe on key (newest wins), rewrite the file.
- **overwrite**: full re-fetch through `save_parquet`.
- **plain overwrite**: `df.to_parquet` directly, bypassing `storage.py`.

| Collector | Dataset | Grain | Write today | Raw layer? |
|---|---|---|---|---|
| price.py | Alpaca bars, stocks and crypto (1Day, 1Hour, 4Hour, 5Min) | symbol, timestamp (one file per timeframe) | merge; `--full` overwrites | Yes |
| options.py | Alpaca option chain plus flow metrics | contract_symbol, snapshot_date | merge, **prunes snapshots older than 90 days** | Yes |
| deribit_options.py | Deribit BTC/ETH (SOL optional) chains; also writes `gex_crypto.parquet` | contract_symbol, snapshot_date | merge, **prunes snapshots older than 90 days** | Yes |
| perps.py | Binance, Hyperliquid (Bybit optional) perpetuals | symbol, venue, timestamp | merge, then pruned | Yes |
| news.py | Headlines per ticker | article | merge, keeps 90 days | Yes |
| fred.py | About 27 macro series | series_id, date | overwrite | Yes |
| fomc.py | FOMC decisions | meeting date | overwrite | Yes |
| fundamentals.py | SEC XBRL companyfacts | ticker, metric, period | overwrite | Yes |
| sec.py | EDGAR filing list | accession | overwrite | Yes |
| cot.py | CFTC Commitments of Traders | market, report_date (weekly) | plain overwrite | Yes |
| insider.py | Form 4 filings, last 10 per ticker | transaction | plain overwrite | Yes |
| institutional.py | 13F holdings, fund watchlist | fund, filing, holding | plain overwrite | Yes |
| futures_volume.py | Yahoo 5-minute bars, MNQ/MES/SIL | symbol, timestamp | plain overwrite | Yes |
| congress.py | Senate Stock Watcher | transaction | plain overwrite | Yes. **OPEN**: drop it? The data ends 2020-12-02 (`congress.py:18-19`). |

Intraday bars (5-minute, 1H, 4H) land in raw but stay **parked** for analysis.

## 5. Raw layer contract

Every raw table keeps original columns and values, plus:

- `source`
- `ingested_at` (UTC)
- `run_id`
- `request_params`

Also: a manifest table logging run_id, dataset, row count, min/max date, status.

**Current state (resolved):** Nothing appends to partitions today, and there are no metadata columns and no manifest.
- **Data is being lost now.** Options snapshots can't be re-fetched, yet `options.py:223-226` and `deribit_options.py:134-137` delete them after 90 days (via `prune_old_snapshots`, `--keep-days` default 90).
- **The write path to refactor is `storage.py`:** `save_parquet`, `merge_parquet`, `prune_old_snapshots`.
- **Collectors that bypass it** must be routed through it: `cot.py:281`, `congress.py:58`, `insider.py:158`, `institutional.py:142`, `futures_volume.py:99`, and `deribit_options.py:142-143` (the gex_crypto file).
- **OPEN:** keep or drop the best-effort Supabase upsert (`storage.py:91-101`).

## 6. Universe

Current state (resolved):
- `tickers.yaml` has **no 25-asset list**. "25 assets" appears only in prose in `reference/rotationstrategy.md`.
- It holds 42 unique equities across groups:
  - `broad_market`: SPY, QQQ, IWM, XLU, SMH
  - `large_cap`: 27 names
  - `defensive`: TLT, SHV
  - `sectors`: XLE, XLU, XLP, XLC, XLV
  - `alternative`: GLD, SLV, IBIT, IETH
- It also holds crypto (BTC/USD, ETH/USD, SOL/USD) and futures (MNQ=F, MES=F, SIL=F).
- PLTR, CAVA and HOOD are **not** in `tickers.yaml`.
- SHV is `cash_like` and is excluded from the momentum rank.
- There is no VIX ticker. FRED `VIXCLS` is the only VIX proxy.
- Crypto is counted per bar (calendar days) and annualized with 365 (`momentum.py:18`). Nothing aligns crypto to trading days.

Decisions:
- Momentum basket: the 25-asset rotation list. **OPEN**: define it, likely as a new `rotation` group in `tickers.yaml`.
- Context tickers: **OPEN**. SPY, QQQ, GLD and TLT are already collected. For VIX, use FRED VIXCLS or add a ticker?
- Crypto handling (BTC and ETH trade on weekends): **OPEN**. Align to the equity calendar, or keep crypto on its own calendar?
- Short-history names: **OPEN**. Decide a minimum-history rule, and whether PLTR, CAVA and HOOD join at all.

## 7. Staging contract

- Types fixed, columns renamed to one consistent style.
- Duplicates removed by keeping the latest `ingested_at`.
- Tests: unique key, no null keys, price > 0, no unexplained date gaps.
- `available_at` added for slow data. Lag rules per source: **OPEN**.

Current state: DuckDB is not yet a dependency (`pyproject.toml`), and there is no `sql/` folder or staging code. Adding DuckDB is part of Phase 3.

## 8. Marts

| Mart | Grain | Purpose | Status |
|---|---|---|---|
| mart_price_daily | ticker, date | Clean daily spine, returns | Defined |
| mart_momentum | ticker, rebalance_date | Momentum features and rank | Defined, details **OPEN** |
| mart_regime | date | Trend filter, VIX, later GEX | **OPEN** |
| mart_backtest | rebalance_date | Holdings and net returns | **OPEN** |

Momentum v1 starting point (to be confirmed):
- Returns at 1, 3, 6, 12 months, plus skip-recent-month variant.
- Volatility-adjusted score.
- Cross-sectional rank per rebalance date.
- 200-day MA flag.

Current code, for reference. These marts are **new builds, not ports**.
- **`transforms/momentum.py`**:
  - Its main score is the 10-bar return ÷ 10-bar annualized vol (vol floored at 5%).
  - It also computes 5/20/60-bar returns, a 60-bar vol-adjusted score and acceleration labels.
  - It writes **one latest snapshot per symbol**, with no history by rebalance date.
  - `above_200d_ma` is computed (`momentum.py:106-109`) but not used as a filter.
  - None of the 1/3/6/12-month lookbacks exist.
- **`transforms/backtest.py`** is an ICT-zone R-multiple test with a random-entry control and score-tercile forward returns. It is not a top-N rotation backtest, and it assumes no transaction costs.

Keep-or-cut baselines: equal-weight basket, SPY buy and hold, random selection. Still **OPEN**:
- Cost assumption.
- Rebalance frequency (monthly vs weekly check).
- Position count (top N).
- Train / validation / test date split.

## 9. Phases

1. **Raw refactor.** Exit: two runs produce two partitions, no data loss.
   1. Stop the 90-day pruning in options and deribit_options. Do this first, because that data is unrecoverable.
   2. Make `storage.py` write partitioned appends under `data/raw/<source>/<dataset>/ingest_date=.../`.
   3. Add the metadata columns (`source`, `ingested_at`, `run_id`, `request_params`).
   4. Add the manifest table.
   5. Route the collectors that bypass `storage.py` through it (section 5).
2. **Backfill daily bars** for the full universe. Exit: per-ticker date coverage logged in the manifest.
   - The default first fetch is 730 days (`price.py:32`).
   - **OPEN**: how far back Alpaca actually goes for stocks and crypto. Find out with a test fetch.
3. **Staging** for price, then the other sources. Add DuckDB here.
4. **Momentum mart and baseline backtest.**
5. **Candidate signals**, one at a time, each judged against baselines.

**Legacy outputs:** from Phase 1 on, existing outputs may break. That covers `combine.py` (`market_data.xlsx`), `reports/confluence_report.py`, and the current transforms and reports that read flat files in `data/`. Each will either be rebuilt from marts or retired.

## 10. Open questions summary

1. ~~Overwrite or append today?~~ **Answered:** merge or overwrite of flat files, nothing appended. Options history is pruned at 90 days (section 5).
2. ~~Which non-price collectors stay?~~ **Answered:** all of them move to raw. Staging and mart use is decided per source.
3. Context tickers and crypto handling? (section 6)
4. Alpaca history depth for stocks and crypto? **Partly answered:** 730 days is the current default; the true maximum is untested.
5. Costs, rebalance frequency, top N, and date splits?
6. Does the Excel workbook come back as a review output built from marts? (Breaking it during the restart is accepted.)
7. Which 25 names make up the rotation basket? Add them to `tickers.yaml` as a group.
8. Keep or drop the Supabase upsert?
9. Drop the congress collector (data ends 2020)?
