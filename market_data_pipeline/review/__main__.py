"""python -m market_data_pipeline.review: write the Excel summary of the mart layer and print the verdicts."""

from __future__ import annotations

import pandas as pd

from market_data_pipeline.review import queries
from market_data_pipeline.review.excel import write_workbook


def main() -> None:
    path = write_workbook()
    print(f"Wrote {path}")
    verdicts = queries.keep_or_cut()
    verdicts = verdicts[verdicts["split"] == "validation"]
    cols = ["variant", "n_rebalances", "net_cagr", "sharpe", "random_pctile", "equal_weight_cagr", "spy_bh_cagr", "verdict"]
    with pd.option_context("display.width", 200, "display.float_format", "{:.3f}".format):
        print(verdicts[cols].to_string(index=False))


if __name__ == "__main__":
    main()
