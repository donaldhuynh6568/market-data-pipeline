"""Summary workbook over the mart layer: data/review/mart_review_<date>.xlsx.

Small, decision-level tables only (no full daily marts, which belong in DuckDB or the notebook). Every sheet comes
from review/queries.py, the same functions the notebook uses.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from market_data_pipeline.config import DATA_DIR
from market_data_pipeline.review import queries

REVIEW_DIR = DATA_DIR / "review"

PERCENT_COLUMNS = {
    "net_cagr", "gross_cagr", "total_net_return", "max_drawdown", "ann_vol", "period_hit_rate", "hit_rate",
    "base_rate", "random_pctile", "equal_weight_cagr", "spy_bh_cagr", "random_p50_cagr", "mean_fwd_ret",
    "median_fwd_ret", "share_positive", "ret_21", "ret_63", "ret_252", "vol_63", "dist_ma200", "weight",
    "total_cost",
}
DECIMAL_COLUMNS = {"sharpe", "avg_turnover", "avg_trades", "avg_positions", "mean_rank_ic", "ic_t_stat", "mom_score",
                   "rank_ic", "years", "vix"}
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
GOOD_FILL = PatternFill("solid", fgColor="E2EFDA")
BAD_FILL = PatternFill("solid", fgColor="FCE4D6")


def _naive(df: pd.DataFrame) -> pd.DataFrame:
    """Excel cannot store tz-aware datetimes."""
    out = df.copy()
    for col in out.columns:
        if isinstance(out[col].dtype, pd.DatetimeTZDtype):
            out[col] = out[col].dt.tz_localize(None)
    return out


def _format_sheet(ws, df: pd.DataFrame) -> None:
    for idx, col in enumerate(df.columns, start=1):
        letter = get_column_letter(idx)
        header = ws.cell(row=1, column=idx)
        header.font = Font(bold=True, color="FFFFFF")
        header.fill = HEADER_FILL
        header.alignment = Alignment(wrap_text=True, vertical="center")
        fmt = "0.0%" if col in PERCENT_COLUMNS else "0.00" if col in DECIMAL_COLUMNS else None
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            fmt = "yyyy-mm-dd"
        if fmt:
            for row in range(2, len(df) + 2):
                ws.cell(row=row, column=idx).number_format = fmt
        values = [str(col), *(str(v) for v in df[col].head(200))]
        ws.column_dimensions[letter].width = min(max(len(v) for v in values) + 2, 48)
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions


def _shade_verdicts(ws, df: pd.DataFrame) -> None:
    if "verdict" not in df.columns:
        return
    col = list(df.columns).index("verdict") + 1
    for row, verdict in enumerate(df["verdict"], start=2):
        fill = GOOD_FILL if verdict.startswith(("KEEP", "PROMISING")) else BAD_FILL if verdict.startswith("CUT") else None
        if fill:
            ws.cell(row=row, column=col).fill = fill


def build_sheets(marts_dir: Path | None = None, warehouse_path: Path | None = None) -> dict[str, pd.DataFrame]:
    perf = queries.performance(marts_dir)
    return {
        "Keep or cut": queries.keep_or_cut(marts_dir),
        "Performance": perf,
        "Holdings (latest)": queries.latest_holdings(marts_dir),
        "Ranks (today)": queries.latest_ranks(marts_dir),
        "Quintiles": queries.quintiles(marts_dir),
        "Rank IC": queries.rank_ic(marts_dir),
        "Split": queries.split_summary(marts_dir),
        "Coverage": queries.coverage(warehouse_path),
    }


def write_workbook(out_path: Path | None = None, marts_dir: Path | None = None, warehouse_path: Path | None = None) -> Path:
    out_path = out_path or REVIEW_DIR / f"mart_review_{date.today().isoformat()}.xlsx"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheets = build_sheets(marts_dir, warehouse_path)
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df = _naive(df)
            df.to_excel(writer, sheet_name=name, index=False)
            ws = writer.sheets[name]
            _format_sheet(ws, df)
            _shade_verdicts(ws, df)
    return out_path
