# Feature: FOMC Event Collector

## What

Add a collector that captures FOMC meeting dates and the actual rate decision made at each meeting (hike/cut/hold, and the size of the move). This is a companion to the existing `market_data_pipeline/collectors/fred.py`, not a replacement — FRED already covers the continuous macro series (fed funds rate, treasury yields, inflation). What's missing is the discrete event layer: which specific days were FOMC decision days, and what was decided.

## Why

The goal is to study how FOMC decisions impact markets — that requires being able to mark specific calendar dates as "this was a decision day" and know what was decided, so those dates can later be lined up against price/volume data. Continuous rate series alone (e.g. `DFF` ticking daily) don't make this easy: a rate change shows up in the series, but there's no direct signal for "this date was a scheduled FOMC meeting" vs. any other day.

## Domain context

- Existing FRED collector (`market_data_pipeline/collectors/fred.py`) already pulls `FEDFUNDS`, `DFF`, `SOFR`, treasury yields (`DGS2/5/10/30`), and inflation series (`CPIAUCSL`, `CPILFESL`, `PCEPI`, `PCEPILFE`) on a schedule, saving to parquet and upserting into Supabase via `save_parquet` / `upsert_to_supabase`.
- FRED does not have a clean "FOMC decision" series — meeting dates and decisions will likely need to come from a different source (e.g. the Federal Reserve's own published FOMC calendar/statements, or a maintained dataset). This needs research during the build — no source has been picked yet.
- Follow the same collector pattern as `fred.py` / `futures_volume.py`: fetch → parse into a DataFrame → save parquet → upsert to Supabase with an `on_conflict` key.

## Constraints / non-goals

- Standalone for now — this collector is not being wired into price data, strategy signals, or `orderflow_strategy_rules.yaml` yet. Just get the event data collected and stored first; correlating it with price/volume is a later, separate piece of work.
- Not building a rate-decision prediction model or any forward-looking analysis — historical record only.
- Should follow the existing collector conventions in this repo (argparse CLI entrypoint, parquet + Supabase output) rather than introducing a new pattern.
