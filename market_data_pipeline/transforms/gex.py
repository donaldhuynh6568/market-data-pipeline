from __future__ import annotations

import pandas as pd

from market_data_pipeline.config import DATA_DIR

CONTRACT_MULTIPLIER = 100
NEAR_FLIP_THRESHOLD = 0.01


def compute_contract_gex(df: pd.DataFrame) -> pd.DataFrame:
    """Estimate per-contract dealer gamma exposure from the option chain's Greeks.

    Assumes dealers are long call gamma / short put gamma (the standard retail
    approximation) — this is an estimate of market structure, not an observation
    of actual dealer books.
    """
    result = df.copy()
    sign = result["type"].map({"call": 1, "put": -1})
    gamma = pd.to_numeric(result["gamma"], errors="coerce").fillna(0)
    open_interest = pd.to_numeric(result["open_interest"], errors="coerce").fillna(0)
    spot = pd.to_numeric(result["spot_price"], errors="coerce")
    if "contract_multiplier" in result.columns:
        multiplier = pd.to_numeric(result["contract_multiplier"], errors="coerce").fillna(CONTRACT_MULTIPLIER)
    else:
        multiplier = CONTRACT_MULTIPLIER
    result["gamma_exposure"] = sign * gamma * open_interest * multiplier * spot**2 * 0.01
    return result


def summarize_gex_by_symbol(df: pd.DataFrame) -> pd.DataFrame:
    if "gamma_exposure" not in df.columns:
        df = compute_contract_gex(df)

    group_keys = ["underlying_symbol", "snapshot_date"]
    total_gamma = df.groupby(group_keys)["gamma_exposure"].sum()
    call_gamma = df[df["type"] == "call"].groupby(group_keys)["gamma_exposure"].sum()
    put_gamma = df[df["type"] == "put"].groupby(group_keys)["gamma_exposure"].sum()

    summary = pd.DataFrame(
        {"total_gamma": total_gamma, "call_gamma": call_gamma, "put_gamma": put_gamma}
    ).fillna(0)
    return summary.reset_index()


def find_gamma_strikes(df: pd.DataFrame) -> pd.DataFrame:
    if "gamma_exposure" not in df.columns:
        df = compute_contract_gex(df)

    rows = []
    for (symbol, snapshot_date), group in df.groupby(["underlying_symbol", "snapshot_date"]):
        by_strike = group.groupby("strike_price")["gamma_exposure"].sum().sort_index()
        if by_strike.empty:
            continue

        max_positive_gamma_strike = by_strike.idxmax() if by_strike.max() > 0 else None
        max_negative_gamma_strike = by_strike.idxmin() if by_strike.min() < 0 else None

        cumulative = by_strike.cumsum()
        items = list(cumulative.items())
        gamma_flip = None
        for (strike_a, cum_a), (strike_b, cum_b) in zip(items, items[1:]):
            if cum_a == 0:
                gamma_flip = strike_a
                break
            if (cum_a < 0) != (cum_b < 0):
                weight = -cum_a / (cum_b - cum_a)
                gamma_flip = strike_a + weight * (strike_b - strike_a)
                break

        rows.append(
            {
                "underlying_symbol": symbol,
                "snapshot_date": snapshot_date,
                "max_positive_gamma_strike": max_positive_gamma_strike,
                "max_negative_gamma_strike": max_negative_gamma_strike,
                "gamma_flip": gamma_flip,
            }
        )
    return pd.DataFrame(rows)


def classify_gamma_regime(total_gamma: float, spot: float, gamma_flip: float | None) -> str:
    if gamma_flip is not None and spot and abs(spot - gamma_flip) / spot < NEAR_FLIP_THRESHOLD:
        return "NEAR_FLIP"
    return "POSITIVE_GAMMA" if total_gamma > 0 else "NEGATIVE_GAMMA"


def compute_gex(df: pd.DataFrame) -> pd.DataFrame:
    contract_gex = compute_contract_gex(df)
    summary = summarize_gex_by_symbol(contract_gex)
    strikes = find_gamma_strikes(contract_gex)
    result = summary.merge(strikes, on=["underlying_symbol", "snapshot_date"], how="left")

    spot_by_group = contract_gex.groupby(["underlying_symbol", "snapshot_date"])["spot_price"].first()
    result = result.set_index(["underlying_symbol", "snapshot_date"])
    result["spot_price"] = spot_by_group
    result["gamma_regime"] = result.apply(
        lambda row: classify_gamma_regime(row["total_gamma"], row["spot_price"], row["gamma_flip"]),
        axis=1,
    )
    return result.reset_index().rename(columns={"underlying_symbol": "symbol", "snapshot_date": "date"})


def main() -> None:
    options_path = DATA_DIR / "options_data.parquet"
    if not options_path.exists():
        raise SystemExit(
            f"Missing {options_path}. Run the options collector first: "
            "uv run python -m market_data_pipeline.collectors.options"
        )

    df = pd.read_parquet(options_path)
    if "gamma" not in df.columns or df["gamma"].isna().all():
        raise SystemExit(
            "options_data.parquet has no gamma values. Re-run the options collector "
            "to pick up gamma from the chain snapshot."
        )

    result = compute_gex(df)
    out_path = DATA_DIR / "gex_data.parquet"
    result.to_parquet(out_path, index=False)
    print(f"Saved {len(result)} GEX rows to {out_path}")
    for _, row in result.iterrows():
        print(f"  [{row['symbol']}] total_gamma={row['total_gamma']:.2e} regime={row['gamma_regime']}")


if __name__ == "__main__":
    main()
