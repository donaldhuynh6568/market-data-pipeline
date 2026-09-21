# Feature: Fundamentals / Cash-Flow Collector (Phase 1: SEC XBRL)

## What

Add a collector that pulls raw cash-flow, balance-sheet, and income-statement facts for the `large_cap` ticker universe from SEC's XBRL companyfacts API, plus a transform that derives the actual ratios: free cash flow (FCF), FCF margin, cash conversion ratio, CapEx as % of revenue (+ trend), net debt, net debt/EBITDA, and interest coverage.

This is a companion to the existing `market_data_pipeline/collectors/sec.py` — not a replacement. `sec.py` already pulls SEC filing *metadata* (form type, filing date, accession number) for the full equities universe. What's missing is the actual financial-statement *data* inside those filings, scoped to just the individual operating companies where fundamentals ratios are meaningful.

## Why

The pipeline currently tracks positioning and price (COT, options/GEX, insider/13F, futures volume) but has no view into whether the underlying businesses are getting healthier or weaker. Several of the largest recent single-day moves in the tracked universe were fundamentals-driven — a company can show rising revenue while FCF collapses if CapEx is eating everything, or carry rising leverage that only becomes visible after a credit-spread headline. Right now that shows up as news to react to, not a number the pipeline already has. This closes that blind spot using data already sitting inside filings the pipeline is already indexing.

## Domain context

- Existing `market_data_pipeline/collectors/sec.py` has the reusable pieces: `TICKER_MAP_URL`, `build_ticker_cik_map`, `fetch_json`, `fetch_text`, `select_document_by_root_tag`, `REQUEST_DELAY_SECONDS`, and `require_sec_user_agent()` (in `config.py`). `insider.py` and `institutional.py` already cross-import these same helpers — this collector follows that precedent rather than duplicating CIK-lookup logic.
- Ticker universe: `load_equity_category("large_cap")` (from `market_data_pipeline/tickers.py`), **not** `load_tickers()["equities"]` like `sec.py` uses. Fundamentals ratios don't apply to the ETF categories (`broad_market`, `sectors`, `defensive`, `alternative`) — only individual operating companies. This matches `insider.py`'s existing choice for the same reason.
- New endpoint: SEC's XBRL companyfacts API (`https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json`), not previously used anywhere in this repo. It returns every XBRL-tagged fact a company has ever reported, keyed by taxonomy (`us-gaap`, `dei`) and tag name, each with a list of period/value entries across fiscal periods and filing forms.
- **Known XBRL pain point**: different filers use different tags for economically identical concepts (e.g. some report `Revenues`, others `RevenueFromContractWithCustomerExcludingAssessedTax`), and companyfacts includes duplicate/overlapping periods (a Q3 10-Q reports both the quarter and the year-to-date cumulative for cash-flow items) plus occasional restatements. The collector uses a per-metric tag-fallback list and dedupes by preferring the most-recently-filed entry for a given period — this is a permanent characteristic of XBRL data, not a bug to eliminate.
- EBIT/EBITDA are never standard XBRL tags anywhere — always derived from `OperatingIncomeLoss` (+ D&A for EBITDA), never looked up directly.
- Data shape: long/tall, one row per ticker per fiscal period — matching the repo's existing convention (`fred_data.parquet` is one row per series per date, `sec_filings.parquet` is one row per filing), not a wide one-row-per-ticker table.
- Collector (raw pull) and transform (derived ratios) are kept separate, following the existing split between `collectors/*.py` (raw I/O) and `transforms/gex.py` (derived metrics computed from an already-collected parquet file).

## Constraints / non-goals

- **Forward guidance and analyst consensus estimates are explicitly out of scope for this phase.** These are not present in XBRL (companies don't tag forward-looking guidance as a standardized fact) and require a separate third-party provider — recommended candidate is Financial Modeling Prep (FMP), to be finalized only when that phase is actually built, including a new API key added to `config.py`/`.env` at that time. Not started now.
- Valuation ratios that need market cap (P/E, EV/EBITDA, EV/FCF, FCF yield) are a natural immediate follow-up once this phase lands — they only need shares outstanding (collected here via `dei:EntityCommonStockSharesOutstanding`) times price (already collected in `price_data.parquet`) — but are not part of this build.
- No new scheduler/orchestration — run manually via `uv run python -m market_data_pipeline.collectors.fundamentals`, same as every other collector in this repo.
- Not attempting to "fix" tag inconsistency across filers beyond a documented fallback list; gaps are surfaced (via a `*_tag_used` provenance column), not silently guessed at.
