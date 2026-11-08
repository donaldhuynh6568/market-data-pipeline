"""Money and signal-quality metrics per (variant, split), per analysis spec section 6.

Money first: net CAGR, max drawdown, Sharpe, turnover. Signal quality second: hit rate against the base rate
(not against 50%), rank IC, and the number of rebalances behind each number.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

RANDOM_PERCENTILES = (5, 50, 95)


def _max_drawdown(net: pd.Series) -> float:
    equity = (1 + net).cumprod()
    return float((equity / equity.cummax().clip(lower=1.0) - 1).min())


def summarize(periods: pd.DataFrame) -> dict[str, float]:
    """Metrics for one variant's sequence of holding periods (already restricted to one split)."""
    periods = periods.sort_values("rebalance_date")
    net = periods["net_ret"]
    years = (periods["exit_date"].max() - periods["entry_date"].min()).days / 365.25
    n = len(periods)
    per_year = n / years if years > 0 else np.nan
    equity = float((1 + net).prod())
    gross_equity = float((1 + periods["gross_ret"]).prod())
    std = net.std(ddof=1)
    return {
        "n_rebalances": n,
        "start_date": periods["entry_date"].min(),
        "end_date": periods["exit_date"].max(),
        "years": years,
        "net_cagr": equity ** (1 / years) - 1 if years > 0 else np.nan,
        "gross_cagr": gross_equity ** (1 / years) - 1 if years > 0 else np.nan,
        "total_net_return": equity - 1,
        "max_drawdown": _max_drawdown(net),
        "ann_vol": std * np.sqrt(per_year) if n > 1 else np.nan,
        "sharpe": net.mean() / std * np.sqrt(per_year) if n > 1 and std > 0 else np.nan,
        "period_hit_rate": float((net > 0).mean()),
        "avg_turnover": periods["turnover"].mean(),
        "avg_trades": periods["n_trades"].mean(),
        "avg_positions": periods["n_positions"].mean(),
        "total_cost": periods["cost"].sum(),
    }


def position_hit_rates(trades: pd.DataFrame, eligible_returns: pd.DataFrame) -> pd.DataFrame:
    """Share of held positions with a positive holding return, beside the base rate for the same periods.

    Base rate = share of ALL eligible (name, period) returns that were positive: what picking at random hits.
    """
    held = trades[trades["weight"] > 0]
    hits = held.groupby(["variant", "split"])["gross_ret"].apply(lambda r: float((r > 0).mean())).rename("hit_rate")
    base = (
        eligible_returns.groupby("split")["gross_ret"].apply(lambda r: float((r > 0).mean())).rename("base_rate")
    )
    return hits.reset_index().merge(base.reset_index(), on="split", how="left")


def rank_ic(signals: pd.DataFrame, eligible_returns: pd.DataFrame) -> pd.DataFrame:
    """Spearman correlation between momentum score and the next holding-period return, per rebalance."""
    scored = signals.loc[signals["variant"] == "equal_weight", ["rebalance_date", "ticker", "score"]]
    joined = scored.merge(eligible_returns, on=["rebalance_date", "ticker"]).dropna(subset=["score", "gross_ret"])
    rows = []
    for (date, split), g in joined.groupby(["rebalance_date", "split"]):
        if len(g) >= 5:
            ic = g["score"].rank().corr(g["gross_ret"].rank())
            rows.append({"rebalance_date": date, "split": split, "rank_ic": ic, "n_names": len(g)})
    return pd.DataFrame(rows, columns=["rebalance_date", "split", "rank_ic", "n_names"])


def performance_table(
    periods: pd.DataFrame, trades: pd.DataFrame, eligible_returns: pd.DataFrame, ic: pd.DataFrame,
    splits: list[str],
) -> pd.DataFrame:
    """One row per (variant, split). Random draws collapse into p5 / p50 / p95 rows per top-N."""
    periods = periods[periods["split"].isin(splits)]
    rows = [
        {"variant": variant, "split": split, **summarize(g)}
        for (variant, split), g in periods.groupby(["variant", "split"])
    ]
    table = pd.DataFrame(rows)
    table = table.merge(position_hit_rates(trades[trades["split"].isin(splits)], eligible_returns),
                        on=["variant", "split"], how="left")

    mean_ic = ic[ic["split"].isin(splits)].groupby("split")["rank_ic"].agg(["mean", "count"])
    is_momentum = table["variant"].str.startswith("momentum_")
    table["mean_rank_ic"] = np.where(is_momentum, table["split"].map(mean_ic["mean"]), np.nan)
    table["ic_t_stat"] = np.where(
        is_momentum,
        table["split"].map(
            ic[ic["split"].isin(splits)].groupby("split")["rank_ic"].apply(
                lambda s: s.mean() / s.std(ddof=1) * np.sqrt(len(s)) if len(s) > 1 and s.std(ddof=1) > 0 else np.nan
            )
        ),
        np.nan,
    )

    is_random = table["variant"].str.startswith("random_")
    deterministic = table[~is_random].copy()
    randoms = table[is_random].assign(family=lambda d: d["variant"].str.replace(r"_d\d+$", "", regex=True))

    # Where a momentum variant's net CAGR falls among the random draws holding the same number of names:
    # 0.95 means it beat 95% of them. This is the "beats random selection" test, as a number.
    top_n = deterministic["variant"].str.extract(r"^momentum_top(\d+)", expand=False)
    deterministic["random_family"] = np.where(top_n.notna(), "random_top" + top_n.fillna(""), None)
    draws = randoms.groupby(["family", "split"])["net_cagr"].apply(np.asarray).to_dict()
    deterministic["random_pctile"] = [
        float((draws[(fam, split)] < cagr).mean()) if (fam, split) in draws else np.nan
        for fam, split, cagr in zip(deterministic["random_family"], deterministic["split"], deterministic["net_cagr"])
    ]
    deterministic = deterministic.drop(columns="random_family")
    metric_cols = [c for c in table.columns if c not in ("variant", "split", "start_date", "end_date")]
    summaries = []
    for (family, split), g in randoms.groupby(["family", "split"]):
        for q in RANDOM_PERCENTILES:
            row = {"variant": f"{family}_p{q:02d}", "split": split,
                   "start_date": g["start_date"].min(), "end_date": g["end_date"].max()}
            row.update({c: g[c].quantile(q / 100) for c in metric_cols})
            row["n_draws"] = len(g)
            summaries.append(row)
    out = pd.concat([deterministic, pd.DataFrame(summaries)], ignore_index=True)
    return out.sort_values(["split", "variant"]).reset_index(drop=True)


def equity_curves(periods: pd.DataFrame, splits: list[str]) -> pd.DataFrame:
    """Cumulative net equity per variant through the reported splits (gap periods included for continuity).

    Random draws collapse into a p5 / p50 / p95 band per top-N, taken across draws at each date.
    """
    shown = periods[periods["split"].isin([*splits, "gap"])].sort_values(["variant", "rebalance_date"]).copy()
    shown["equity"] = shown.groupby("variant")["net_ret"].transform(lambda r: (1 + r).cumprod())
    shown["drawdown"] = shown["equity"] / shown.groupby("variant")["equity"].cummax().clip(lower=1.0) - 1
    cols = ["variant", "rebalance_date", "exit_date", "split", "net_ret", "equity", "drawdown"]

    is_random = shown["variant"].str.startswith("random_")
    curves = shown.loc[~is_random, cols]
    randoms = shown[is_random].assign(family=lambda d: d["variant"].str.replace(r"_d\d+$", "", regex=True))
    bands = []
    for q in RANDOM_PERCENTILES:
        band = (
            randoms.groupby(["family", "rebalance_date", "exit_date", "split"], as_index=False)
            .agg(net_ret=("net_ret", lambda s, q=q: s.quantile(q / 100)),
                 equity=("equity", lambda s, q=q: s.quantile(q / 100)),
                 drawdown=("drawdown", lambda s, q=q: s.quantile(q / 100)))
        )
        band["variant"] = band["family"] + f"_p{q:02d}"
        bands.append(band[cols])
    return pd.concat([curves, *bands], ignore_index=True).sort_values(["variant", "rebalance_date"]).reset_index(drop=True)
