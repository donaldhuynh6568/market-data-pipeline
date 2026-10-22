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
│   ├── picks.py
│   ├── collectors/
│   │   ├── __init__.py
│   │   ├── price.py
│   │   ├── options.py
│   │   ├── cot.py
│   │   ├── fred.py
│   │   ├── sec.py
│   │   ├── congress.py
│   │   ├── insider.py
│   │   ├── institutional.py
│   │   ├── perps.py
│   │   ├── deribit_options.py
│   │   └── news.py
│   ├── transforms/
│   │   ├── __init__.py
│   │   ├── gex.py
│   │   ├── momentum.py
│   │   └── location.py
│   └── reports/
│       ├── confluence_report.py
│       ├── rating_rules.py
│       ├── resistance_ladder.py
│       ├── note_templates.py
│       ├── insider_flags.py
│       ├── news_context.py
│       ├── position_levels.py
│       └── llm_notes.py
├── collect_price_data.py
├── collect_options_data.py
├── collect_cot_data.py
├── collect_news_data.py
├── combine.py
├── tickers.yaml
├── confluence_report_rules.yaml
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

# Optional: account sizing for the confluence report's Size / Position $ / Risk $ columns.
# These override the `sizing:` fallbacks in confluence_report_rules.yaml, so your real account
# number never has to live in a tracked file. The report still runs without them.
ACCOUNT_SIZE=100000
RISK_PER_TRADE_PCT=1.0
MAX_POSITION_PCT=25

# Optional
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_key
```

The project reads your existing `.env` automatically.

## Quick start: what to run

Copy a block, paste it into the terminal, and run it from the project root. Single quotes around URLs work in both Git Bash and PowerShell.

Two kinds of script, and it matters when something looks stale:

- **Collectors** (`collectors.*`, and the `collect_*.py` wrappers) call an outside API and are the only things that bring in new data. They need network access and, for most of them, API keys in `.env`.
- **Transforms and reports** (`transforms.*`, `combine`, `reports.*`) never fetch anything. They only read the parquet files already in `data/`, so re-running one after a code change is instant and safe — but it cannot make the underlying data any fresher than the last collector run.

So a number that looks out of date is almost always a collector that has not been run, not a transform that needs re-running.

### Daily refresh (prices, momentum, crypto derivatives, workbook)

```bash
uv run collect_price_data.py
uv run python -m market_data_pipeline.transforms.momentum
uv run python -m market_data_pipeline.transforms.location
uv run python -m market_data_pipeline.transforms.ict_levels      # opens, session highs/lows, equal highs/lows
uv run python -m market_data_pipeline.transforms.flow_indicators # CMF/MFI/OBV, relative strength, anchored VWAP, volume profile
uv run python -m market_data_pipeline.transforms.flow_backtest   # scores the daily flow votes (adds a Flow Backtest tab)
uv run python -m market_data_pipeline.transforms.backtest
uv run python -m market_data_pipeline.collectors.perps --venues hyperliquid
uv run python -m market_data_pipeline.collectors.deribit_options
uv run python -m market_data_pipeline.collectors.news
uv run python -m market_data_pipeline.combine   # --days 90 by default: trailing window on the big sheets so Excel can open it
uv run python -m market_data_pipeline.reports.confluence_report
```

The location line derives entry/stop levels from the daily bars, the news line fetches headlines for your picks, and the last line builds the confluence report; it only reads what the earlier lines produced.

The price collector now defaults to two years, which is what makes the 200-day MA flag work and gives the backtest
a usable sample. Intraday timeframes are capped near 60 days by the free tier and simply return what exists. Drop `--venues hyperliquid` to also try Binance (geo-blocked in the US, so it just prints an error and is skipped).

### Confluence report (analysis only)

Reads the parquet files already in `data/` and writes `data/Confluence_Report_<date>.xlsx`. It does **not** run any collectors, so refresh your data first (the "Run everything" block below). If a required file is missing it tells you which one and the command that creates it. Thresholds and the focus list are in `confluence_report_rules.yaml`. If `data/news_data.parquet` exists the report adds a News column (see below).

```bash
uv run python -m market_data_pipeline.reports.confluence_report
```

Optional: have Claude rewrite the notes (needs `ANTHROPIC_API_KEY` or `ant auth login`; ratings and targets are unchanged):

```bash
uv run --with anthropic python -m market_data_pipeline.reports.confluence_report --llm-notes
```

### Weekly (CFTC publishes Fridays): COT positioning for gold, silver, BTC, ETH, SOL

```bash
uv run python -m market_data_pipeline.collectors.cot --source-url 'https://publicreporting.cftc.gov/api/v3/views/kh3c-gbw2/query.json' --source-url 'https://publicreporting.cftc.gov/resource/6dca-aqww.json?$limit=50000&$where=market_and_exchange_names%20like%20%27%25BITCOIN%25%27%20OR%20market_and_exchange_names%20like%20%27%25ETHER%25%27%20OR%20market_and_exchange_names%20like%20%27%25SOLANA%25%27'
uv run python -m market_data_pipeline.combine
```

If CFTC returns an HTTP 503, their API is down; try again later.

### Options and GEX (equities)

```bash
uv run python -m market_data_pipeline.collectors.options --moneyness 0.20 --max-expiry-days 60
uv run python -m market_data_pipeline.transforms.gex
uv run python -m market_data_pipeline.combine
```

### Intraday structure and volume

```bash
uv run python -m market_data_pipeline.collectors.price --timeframe 1Hour
uv run python -m market_data_pipeline.collectors.price --timeframe 4Hour
uv run python -m market_data_pipeline.collectors.price --timeframe 5Min
uv run python -m market_data_pipeline.transforms.swing_structure --timeframe 1h
uv run python -m market_data_pipeline.transforms.swing_structure --timeframe 4h
uv run python -m market_data_pipeline.transforms.volume_floor
uv run python -m market_data_pipeline.collectors.futures_volume
```

### Macro, fundamentals, and filings (occasional)

```bash
uv run python -m market_data_pipeline.collectors.fred
uv run python -m market_data_pipeline.collectors.fomc
uv run python -m market_data_pipeline.collectors.fundamentals
uv run python -m market_data_pipeline.transforms.fundamentals_ratios
uv run python -m market_data_pipeline.collectors.sec
uv run python -m market_data_pipeline.collectors.insider
uv run python -m market_data_pipeline.collectors.institutional
uv run python -m market_data_pipeline.combine
```

Details for each step follow below.

## Recommended execution pattern

For maintainability, prefer the package entrypoints instead of raw script files:

### 1) Collect price data

```bash
uv run python -m market_data_pipeline.collectors.price --days 730
```

Daily bars by default. Refreshes are incremental: if the parquet already exists, only bars newer than each symbol's last stored bar (minus a short overlap that replaces the still-forming bar) are fetched and appended, so re-running is cheap and never shrinks history. `--days` only sizes a first run or a newly added ticker. Pass `--full` to re-download the whole window and overwrite the file (it warns if that would drop stored bars). The same applies to every `--timeframe`.

The perps, options and Deribit collectors also append: each run adds a snapshot (same-day reruns replace that day), so funding/OI and options history build up. Options and Deribit keep the last 90 days (`--keep-days`); the confluence report and the combined workbook read only the latest snapshot. For intraday bars, add `--timeframe`:

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
first), covers every symbol in the file by default including ETFs and crypto
(restrict with `--symbols`), and
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

CME Bitcoin, CME Ether and Coinbase Nano Solana futures are also kept, but they only appear in the CFTC **Legacy** report, not the Disaggregated one above. To include them, pass the Legacy dataset as a second source (repeat `--source-url`):

```bash
uv run python -m market_data_pipeline.collectors.cot --source-url 'https://publicreporting.cftc.gov/api/v3/views/kh3c-gbw2/query.json' --source-url 'https://publicreporting.cftc.gov/resource/6dca-aqww.json?$limit=50000&$where=market_and_exchange_names%20like%20%27%25BITCOIN%25%27%20OR%20market_and_exchange_names%20like%20%27%25ETHER%25%27%20OR%20market_and_exchange_names%20like%20%27%25SOLANA%25%27'
```

### Momentum, crypto perps, and Deribit options

```bash
uv run python -m market_data_pipeline.transforms.momentum          # score + 5/10/20d deltas; needs price data, use --days 90+; optional --as-of YYYY-MM-DD
uv run python -m market_data_pipeline.collectors.perps             # funding rate + open interest (BTC/ETH/SOL)
uv run python -m market_data_pipeline.collectors.deribit_options   # BTC/ETH options + crypto GEX
```

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

### 13) Collect fundamentals / cash-flow data (SEC XBRL)

```bash
uv run python -m market_data_pipeline.collectors.fundamentals
```

For each `large_cap` ticker, fetches SEC's XBRL companyfacts API and extracts
raw cash-flow, balance-sheet, and income-statement facts (revenue, net
income, operating income, interest expense, D&A, operating cash flow, CapEx,
cash, total debt, shares outstanding) — one row per fiscal period (10-K/10-Q).
No new API key needed; reuses `SEC_USER_AGENT`. Writes
`data/fundamentals_data.parquet`. See DATA_DEFINITION.md for the XBRL
tag-fallback caveats.

### 14) Compute derived fundamentals ratios

```bash
uv run python -m market_data_pipeline.transforms.fundamentals_ratios
```

Reads `data/fundamentals_data.parquet` and derives free cash flow (FCF), FCF
margin, cash conversion ratio, CapEx as % of revenue (+ trailing trend), net
debt, net debt/EBITDA, and interest coverage. Writes
`data/fundamentals_ratios.parquet`.

### 15) Combine the parquet files into Excel

```bash
uv run python -m market_data_pipeline.combine
```

### 16) Collect news for your picks

```bash
uv run python -m market_data_pipeline.collectors.news
```

Pulls Alpaca's news feed (Benzinga; uses your existing Alpaca keys) and writes `data/news_data.parquet`. With no flags it fetches your picks: the focus list in `confluence_report_rules.yaml` plus every ACCELERATING name in `momentum.parquet` (so run the momentum step first). Override with tickers, crypto as `BTC/USD`:

```bash
uv run python -m market_data_pipeline.collectors.news --symbols AVGO AMD BTC/USD --days 14
```

Options: `--days` (how far back each run looks, default 7), `--limit` (max articles per ticker, default 50), `--keep-days` (drop stored articles older than this, default 90). Each run appends to the file and dedupes by article id, so history builds up. Every article is rated HIGH (headline names the asset), MEDIUM (tagged to it and only a few others) or LOW (multi-ticker roundup). The feed has no company names, so add a name for any pick you want matched in headlines under `news.aliases` in `confluence_report_rules.yaml`. See DATA_DEFINITION.md section 22.

### 17) Derive entry, stop and target levels

```bash
uv run python -m market_data_pipeline.transforms.location
```

Reads `price_data.parquet` (daily bars) and writes `data/location_levels.parquet` (one row per symbol) and
`data/location_zones.parquet` (every detected zone). Implements the price-action layer in
`reference/Location Confluence Playbook.md`: the dealing range and its CE/OTE quadrants, three-candle fair value
gaps with a displacement score, the entry at a zone's near edge, and the stop at the pre-imbalance candle.
Thresholds live under `location:` in `confluence_report_rules.yaml`. See DATA_DEFINITION.md section 23.

### 18) Build the confluence report

```bash
uv run python -m market_data_pipeline.reports.confluence_report
```

Analysis only: reads the existing parquets (momentum, GEX, options, 1H/4H swing structure, plus optional Deribit, perps, insider, COT, news and location data) and writes `data/Confluence_Report_<date>.xlsx` with ratings, targets and a resistance ladder. It never runs collectors, so refresh the data first. Thresholds and the focus list live in `confluence_report_rules.yaml`. Add `--llm-notes` (with `uv run --with anthropic ...`) to have Claude rewrite the notes. With location data present it also fills the Setup/Entry/Stop/Risk %/Target/R:R/Size columns and a Key Levels tab.

**The Summary tab is the daily read.** Fifteen columns in plain words, grouped under banners: the `Read` (one
sentence on the technical picture), then **MOMENTUM** (Trend, Score), **LEVELS (ICT)** (Level, Buy at, Stop,
Target, Stop room), **RISK** (Shares, Cost $, Risk $, Reward:Risk) and **EVIDENCE** (`Past edge`).

Rows are ranked by `Past edge` - what setups sharing that profile have actually been worth on the bars collected
so far, taken from the Backtest tab's **ICT target-exit** scorecard (momentum, zone quality and whether the entry
zone already failed the body-vs-wick respect test). Stop width plays no part in ranking - it is a risk-sizing
input, not a reason one setup should out-rank another. The sheet describes and ranks; it does not tell you to buy,
because as of today nothing in it has shown an edge worth instructing on.

Supporting tabs: **Detail** (the raw technicals behind each read), **Key Levels** (the zone ladder per name),
**Resistance Ladder Detail**, **Momentum & Delta** (the full universe) and **Backtest** (below).

### Has any of this worked?

```bash
uv run python -m market_data_pipeline.transforms.backtest
```

Scores every historical zone entry on the rule the report uses - buy the near edge, stop at candle 1 - two ways.
First against a swept set of fixed R-multiples (0.5R-3R), reporting win rate and expectancy by zone quality, stop
width, momentum at entry and reward multiple next to a random-entry control; writes `data/backtest_summary.parquet`.
Second, resolved against the **real ICT target** a live plan would actually hold for (CE, OTE, the dealing-range
high or the nearest smooth swing high) instead of a fixed multiple - this is what the Summary tab's `Rank` is
actually built on; writes `data/backtest_target_summary.parquet`. Both land on the Backtest tab.

**Read the output before trusting the report.** On two years of daily bars (3,988 zone trades, fixed-R scorecard)
momentum at entry is the one feature that orders size outcomes consistently: score above 0.2 returns +0.210R,
negative returns -0.194R - that is why momentum drives position size. Zone quality does not order size the same
way (crossed against momentum it inverts), so it does not change size, but it is re-included in **ranking** via the
target-exit scorecard, where the question is asked against a real exit rather than a synthetic one. The
random-entry control still beats the average zone entry on both scorecards, so treat ranking as relative order
only - the report says so on both tabs. See DATA_DEFINITION.md sections 21 to 23.

### Run everything (copy the whole block)

This runs every collector and transform in dependency order, then builds the workbook. Paste the whole block into the terminal from the project root. It can take several minutes; if one step fails, the next ones still run.

```bash
uv run python -m market_data_pipeline.collectors.insider
uv run python -m market_data_pipeline.collectors.cot --source-url 'https://publicreporting.cftc.gov/api/v3/views/kh3c-gbw2/query.json' --source-url 'https://publicreporting.cftc.gov/resource/6dca-aqww.json?$limit=50000&$where=market_and_exchange_names%20like%20%27%25BITCOIN%25%27%20OR%20market_and_exchange_names%20like%20%27%25ETHER%25%27%20OR%20market_and_exchange_names%20like%20%27%25SOLANA%25%27'

uv run collect_price_data.py --days 90
uv run python -m market_data_pipeline.collectors.price --timeframe 1Hour
uv run python -m market_data_pipeline.collectors.price --timeframe 4Hour
uv run python -m market_data_pipeline.collectors.price --timeframe 5Min
uv run python -m market_data_pipeline.transforms.swing_structure --timeframe 1h
uv run python -m market_data_pipeline.transforms.swing_structure --timeframe 4h
uv run python -m market_data_pipeline.transforms.volume_floor
uv run python -m market_data_pipeline.transforms.momentum
uv run python -m market_data_pipeline.transforms.location
uv run python -m market_data_pipeline.transforms.backtest
uv run python -m market_data_pipeline.collectors.options --moneyness 0.20 --max-expiry-days 60
uv run python -m market_data_pipeline.transforms.gex

uv run python -m market_data_pipeline.collectors.perps --venues hyperliquid
uv run python -m market_data_pipeline.collectors.deribit_options
uv run python -m market_data_pipeline.collectors.news
uv run python -m market_data_pipeline.collectors.fred
uv run python -m market_data_pipeline.collectors.fomc
uv run python -m market_data_pipeline.collectors.futures_volume
uv run python -m market_data_pipeline.collectors.fundamentals
uv run python -m market_data_pipeline.transforms.fundamentals_ratios
uv run python -m market_data_pipeline.combine
uv run python -m market_data_pipeline.reports.confluence_report
```

`collect_cot_data.py`, `collect_price_data.py` and `combine.py` in the project root are shortcut wrappers for the COT, price and combine entrypoints. The COT wrapper needs `CFTC_COT_URL` set in `.env`, which is why the block above calls the module with explicit URLs instead. SEC filings, insider trades and 13F holdings (`collectors.sec`, `collectors.insider`, `collectors.institutional`) are left out of the block because they are slow and rarely change; run them separately when you want them.

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
- `momentum.parquet`
- `perps_data.parquet`
- `deribit_options.parquet`
- `gex_crypto.parquet`
- `news_data.parquet`
- `location_levels.parquet`
- `location_zones.parquet`
- `fred_data.parquet`
- `sec_filings.parquet`
- `congress_trades.parquet`
- `insider_trades.parquet`
- `institutional_holdings.parquet`
- `market_data.xlsx`
- `Confluence_Report_<date>.xlsx`

These are local runtime artifacts and should not be committed to source control.




For a deeper explanation of the collected data and the logic behind the unusual activity signal, see [DATA_DEFINITION.md](C:/Users/kimje/Documents/market-data-pipeline/DATA_DEFINITION.md).
