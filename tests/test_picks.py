import pandas as pd
import pytest
import yaml

from market_data_pipeline.picks import load_picks, select_names


def _momentum() -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": ["UP", "FOC", "SHV", "OTHER"],
        "delta_signal": ["ACCELERATING", "MIXED", "ACCELERATING", "MIXED"],
        "cash_like": [False, False, True, False],
    })


def test_focus_then_accelerating_skipping_cash_like_and_unknown_symbols():
    names = select_names(_momentum(), {"focus": ["FOC", "MISSING"], "include_accelerating": True})

    assert names == ["FOC", "UP"]


def test_accelerating_can_be_turned_off():
    assert select_names(_momentum(), {"focus": ["FOC"], "include_accelerating": False}) == ["FOC"]


def test_load_picks_reads_momentum_and_rules(tmp_path):
    _momentum().to_parquet(tmp_path / "momentum.parquet", index=False)
    rules = tmp_path / "rules.yaml"
    rules.write_text(yaml.safe_dump({"focus": ["FOC"]}), encoding="utf-8")

    assert load_picks(tmp_path, rules) == ["FOC", "UP"]


def test_load_picks_without_momentum_explains_what_to_run(tmp_path):
    rules = tmp_path / "rules.yaml"
    rules.write_text(yaml.safe_dump({"focus": ["FOC"]}), encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        load_picks(tmp_path, rules)

    assert "transforms.momentum" in str(error.value) and "--symbols" in str(error.value)
