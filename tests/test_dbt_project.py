"""Static guarantees about the dbt project and its config, checked without building anything."""

import re

import pandas as pd
import pytest
import yaml

from market_data_pipeline.build import DBT_DIR, check_feature_registry, load_feature_registry
from market_data_pipeline.settings import DBT_VAR_KEYS, dbt_vars, load_strategy

MODELS = DBT_DIR / "models"


def _refs(model: str) -> set[str]:
    sql = next(MODELS.rglob(f"{model}.sql")).read_text(encoding="utf-8")
    return set(re.findall(r"ref\(\s*'([^']+)'\s*\)", sql))


@pytest.mark.parametrize("model", ["mart_features", "mart_signals", "mart_split", "mart_rebalance_dates"])
def test_targets_never_feed_features_or_signals(model):
    assert "mart_targets" not in _refs(model)


def test_feature_registry_matches_the_features_select_list():
    sql = (MODELS / "marts" / "mart_features.sql").read_text(encoding="utf-8")
    final_select = sql[sql.rindex("\nselect"):]
    selected = re.findall(r"(?:j\.(\w+)|as (\w+))\s*,?\s*$", final_select, flags=re.M)
    columns = [a or b for a, b in selected]

    assert columns == list(load_feature_registry())


def test_registry_refuses_targets_and_unknown_roles(tmp_path):
    path = tmp_path / "registry.yaml"
    path.write_text(yaml.safe_dump({"columns": {"ticker": "id", "fwd_ret": "feature"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="targets"):
        load_feature_registry(path)

    path.write_text(yaml.safe_dump({"columns": {"ticker": "label"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="roles"):
        load_feature_registry(path)


def test_registry_check_fails_on_drift_either_way(tmp_path):
    mart = tmp_path / "mart_features.parquet"
    pd.DataFrame({"ticker": ["A"], "ret_21": [0.1], "surprise": [1]}).to_parquet(mart)

    with pytest.raises(SystemExit, match=r"unregistered columns \['surprise'\].*absent \['vol_21'\]"):
        check_feature_registry(mart, {"ticker": "id", "ret_21": "feature", "vol_21": "feature"})
    check_feature_registry(mart, {"ticker": "id", "ret_21": "feature", "surprise": "drop"})


def test_strategy_config_supplies_every_dbt_var_and_dbt_defaults_match():
    settings = load_strategy()
    project_vars = yaml.safe_load((DBT_DIR / "dbt_project.yml").read_text(encoding="utf-8"))["vars"]

    assert set(DBT_VAR_KEYS) <= set(settings)
    assert dbt_vars(settings) == {k: project_vars[k] for k in DBT_VAR_KEYS}   # the fallbacks never drift


def test_include_test_adds_the_test_split_only_when_asked():
    assert "test" not in dbt_vars()["report_splits"]
    assert dbt_vars(include_test=True)["report_splits"][-1] == "test"


def test_split_fractions_must_sum_to_one(tmp_path):
    path = tmp_path / "strategy.yaml"
    path.write_text(yaml.safe_dump({"split": {"train": 0.7, "validation": 0.2, "test": 0.2}}), encoding="utf-8")
    with pytest.raises(ValueError, match="sum to 1"):
        load_strategy(path)
