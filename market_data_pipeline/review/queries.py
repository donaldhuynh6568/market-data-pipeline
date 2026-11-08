"""The review queries, shared by the Excel workbook and the notebook so both always show the same numbers."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from market_data_pipeline.config import DATA_DIR
from market_data_pipeline.settings import load_strategy

MARTS_DIR = DATA_DIR / "marts"
WAREHOUSE_PATH = DATA_DIR / "warehouse.duckdb"


def _con(marts_dir: Path | None = None) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    for path in sorted((marts_dir or MARTS_DIR).glob("*.parquet")):
        con.execute(f"CREATE VIEW {path.stem} AS SELECT * FROM read_parquet('{path.as_posix()}')")
    return con


def sql(query: str, marts_dir: Path | None = None) -> pd.DataFrame:
    """Run any query against the marts, each available as a view named after its file (mart_features, ...)."""
    return _con(marts_dir).sql(query).df()


def performance(marts_dir: Path | None = None) -> pd.DataFrame:
    return sql("SELECT * FROM mart_performance ORDER BY split, variant", marts_dir)


def keep_or_cut(marts_dir: Path | None = None, settings: dict | None = None) -> pd.DataFrame:
    """Each momentum variant beside the three required baselines, per split, with a verdict on validation."""
    settings = settings or load_strategy()
    perf = performance(marts_dir).set_index(["variant", "split"])
    rows = []
    for (variant, split), r in perf.iterrows():
        if not variant.startswith("momentum_"):
            continue
        n = variant.removeprefix("momentum_top").split("_")[0]
        ew, spy = perf.loc[("equal_weight", split)], perf.loc[("spy_bh", split)]
        rand = perf.loc[(f"random_top{n}_p50", split)]
        beats_ew = r["net_cagr"] > ew["net_cagr"]
        beats_spy = r["net_cagr"] > spy["net_cagr"]
        beats_random = r["random_pctile"] >= settings["random_pctile_to_beat"]
        enough = r["n_rebalances"] >= settings["min_rebalances_to_trust"]
        if split != "validation":
            verdict = "in-sample (not a verdict)"
        elif not (beats_ew and beats_spy and beats_random):
            verdict = "CUT: loses to a baseline"
        elif not enough:
            verdict = f"PROMISING: only {int(r['n_rebalances'])} rebalances (< {settings['min_rebalances_to_trust']})"
        else:
            verdict = "KEEP candidate"
        rows.append({
            "split": split, "variant": variant, "n_rebalances": int(r["n_rebalances"]),
            "net_cagr": r["net_cagr"], "sharpe": r["sharpe"], "max_drawdown": r["max_drawdown"],
            "avg_turnover": r["avg_turnover"], "random_pctile": r["random_pctile"],
            "equal_weight_cagr": ew["net_cagr"], "spy_bh_cagr": spy["net_cagr"], "random_p50_cagr": rand["net_cagr"],
            "beats_equal_weight": beats_ew, "beats_spy": beats_spy, "beats_random": beats_random,
            "verdict": verdict,
        })
    order = {"train": 0, "validation": 1, "test": 2}
    out = pd.DataFrame(rows)
    return out.sort_values(["split", "variant"], key=lambda s: s.map(order) if s.name == "split" else s).reset_index(drop=True)


def latest_holdings(marts_dir: Path | None = None) -> pd.DataFrame:
    """What each deterministic variant holds after the most recent rebalance (the live portfolio)."""
    return sql("""
        SELECT s.variant, s.ticker, s.weight, s.score AS mom_score, s.score_rank, r.rebalance_date, r.entry_date
        FROM mart_signals s
        JOIN mart_rebalance_dates r USING (rebalance_date)
        WHERE s.hold AND s.rebalance_date = (SELECT max(rebalance_date) FROM mart_rebalance_dates)
        ORDER BY s.variant, s.score_rank NULLS LAST, s.ticker
    """, marts_dir)


def latest_ranks(marts_dir: Path | None = None) -> pd.DataFrame:
    """Today's momentum ranking across the universe (daily features, so this moves between rebalances)."""
    return sql("""
        SELECT date, ticker, asset_class, mom_rank, mom_score, ret_21, ret_63, ret_252, vol_63, dist_ma200,
               above_ma200, trend_risk_on, vix
        FROM mart_features
        WHERE date = (SELECT max(date) FROM mart_features) AND is_rankable
        ORDER BY mom_rank NULLS LAST, ticker
    """, marts_dir)


def quintiles(marts_dir: Path | None = None) -> pd.DataFrame:
    return sql("SELECT * FROM mart_quintiles ORDER BY split, quintile", marts_dir)


def rank_ic(marts_dir: Path | None = None) -> pd.DataFrame:
    return sql("SELECT * FROM mart_rank_ic ORDER BY rebalance_date", marts_dir)


def equity_curve(marts_dir: Path | None = None) -> pd.DataFrame:
    return sql("SELECT * FROM mart_equity_curve ORDER BY variant, rebalance_date", marts_dir)


def split_summary(marts_dir: Path | None = None) -> pd.DataFrame:
    return sql("""
        SELECT split, min(date) AS first_date, max(date) AS last_date, count(*) AS trading_days
        FROM mart_split GROUP BY split ORDER BY first_date
    """, marts_dir)


def coverage(warehouse_path: Path | None = None) -> pd.DataFrame:
    """Per-ticker history depth (the raw_coverage audit model, which lives in the dbt warehouse)."""
    path = warehouse_path or WAREHOUSE_PATH
    with duckdb.connect(str(path), read_only=True) as con:
        return con.sql("SELECT * FROM raw_coverage ORDER BY first_date DESC, ticker").df()


def collector_runs(warehouse_path: Path | None = None) -> pd.DataFrame:
    path = warehouse_path or WAREHOUSE_PATH
    with duckdb.connect(str(path), read_only=True) as con:
        return con.sql("SELECT * FROM raw_runs").df()
