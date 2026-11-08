"""python -m market_data_pipeline.build [--include-test] [--skip-sim] [--select ...]

The whole post-collection pipeline in order, with strategy parameters taken from config/strategy.yaml:
  1. regenerate the dbt `universe` seed from config/tickers.yaml
  2. dbt build (seeds, staging, marts, every data test and unit test)
  3. check mart_features against config/feature_registry.yaml
  4. the simulation (market_data_pipeline.sim), which writes the performance marts
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pyarrow.parquet as pq
import yaml

from market_data_pipeline.config import CONFIG_DIR, DATA_DIR, ROOT_DIR
from market_data_pipeline.settings import dbt_vars_arg
from market_data_pipeline.tickers import export_universe_seed

DBT_DIR = ROOT_DIR / "dbt"
FEATURE_REGISTRY_PATH = CONFIG_DIR / "feature_registry.yaml"
ROLES = {"id", "feature", "drop"}


def load_feature_registry(path: Path = FEATURE_REGISTRY_PATH) -> dict[str, str]:
    with open(path, encoding="utf-8") as handle:
        registry = (yaml.safe_load(handle) or {}).get("columns", {})
    bad_roles = {c: r for c, r in registry.items() if r not in ROLES}
    if bad_roles:
        raise ValueError(f"feature registry roles must be one of {sorted(ROLES)}: {bad_roles}")
    targets = [c for c in registry if c.startswith("fwd_")]
    if targets:
        raise ValueError(f"forward returns are targets and cannot be registered as features: {targets}")
    return registry


def check_feature_registry(mart_path: Path, registry: dict[str, str]) -> None:
    """Fail when mart_features and the registry disagree in either direction."""
    columns = pq.read_schema(mart_path).names
    unregistered = [c for c in columns if c not in registry]
    missing = [c for c in registry if c not in columns]
    if unregistered or missing:
        raise SystemExit(
            f"mart_features does not match {FEATURE_REGISTRY_PATH.name}: "
            f"unregistered columns {unregistered}, registered but absent {missing}"
        )
    print(f"  [build] feature registry OK ({sum(r == 'feature' for r in registry.values())} features)")


def run_dbt(include_test: bool, select: list[str] | None = None) -> None:
    from dbt.cli.main import dbtRunner

    args = ["build", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR),
            "--vars", dbt_vars_arg(include_test=include_test)]
    if select:
        args += ["--select", *select]
    result = dbtRunner().invoke(args)
    if not result.success:
        raise SystemExit("dbt build failed; see the output above.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build staging and marts with dbt, then run the simulation.")
    parser.add_argument("--include-test", action="store_true", help="Also report the held-out test split.")
    parser.add_argument("--skip-sim", action="store_true", help="Stop after dbt and the registry check.")
    parser.add_argument("--select", nargs="*", help="dbt node selection (skips the simulation).")
    args = parser.parse_args()

    # dbt paths (raw sources, the warehouse file, data/marts) are relative to the repo root.
    os.chdir(ROOT_DIR)
    (DATA_DIR / "marts").mkdir(parents=True, exist_ok=True)
    print(f"  [build] universe seed: {export_universe_seed(DBT_DIR / 'seeds' / 'universe.csv')}")
    run_dbt(args.include_test, args.select)
    if args.select:
        return

    check_feature_registry(DATA_DIR / "marts" / "mart_features.parquet", load_feature_registry())
    if args.skip_sim:
        return

    from market_data_pipeline.sim.__main__ import run

    run(include_test=args.include_test)


if __name__ == "__main__":
    main()
