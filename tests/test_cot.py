import json
import zipfile

import pandas as pd

from market_data_pipeline.collectors.cot import (
    add_positioning_metrics,
    add_release_date,
    filter_target_commodities,
    load_cot_data,
    normalize_cot_columns,
)


def test_normalize_cot_columns_replaces_spaces_with_underscores():
    df = pd.DataFrame({"Commodity Name": ["Gold", "Silver"], "Open Interest": [12, 15]})

    normalized = normalize_cot_columns(df)

    assert list(normalized.columns) == ["commodity_name", "open_interest"]


def test_filter_target_commodities_keeps_gold_and_silver_rows():
    df = pd.DataFrame({
        "commodity_name": ["Gold", "Silver", "Crude Oil"],
        "long_positions": [10, 20, 30],
    })

    filtered = filter_target_commodities(df, ["gold", "silver"])

    assert filtered["commodity_name"].tolist() == ["Gold", "Silver"]


def test_load_cot_data_supports_json_api_payload(tmp_path):
    payload = [
        {
            "market_and_exchange_names": "GOLD - COMMODITY EXCHANGE INC.",
            "commodity_name": "Gold",
            "open_interest_all": 25,
        },
        {
            "market_and_exchange_names": "SILVER - COMMODITY EXCHANGE INC.",
            "commodity_name": "Silver",
            "open_interest_all": 15,
        },
        {
            "market_and_exchange_names": "CRUDE OIL - NEW YORK MERCANTILE EXCHANGE",
            "commodity_name": "Crude Oil",
            "open_interest_all": 40,
        },
    ]
    json_path = tmp_path / "cot.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")

    df = load_cot_data(csv_path=str(json_path))

    assert list(df.columns)[:2] == ["market_and_exchange_names", "commodity_name"]
    assert df["commodity_name"].tolist() == ["Gold", "Silver"]


def test_normalize_cot_columns_makes_duplicate_names_unique():
    df = pd.DataFrame([[1, 2]], columns=["id", "ID"])

    normalized = normalize_cot_columns(df)

    assert list(normalized.columns) == ["id", "id_2"]


def test_load_cot_data_supports_zip_with_txt_report(tmp_path):
    report_text = (
        '"Market and Exchange Names","Open Interest (All)"\n'
        '"GOLD - COMMODITY EXCHANGE INC.",25\n'
        '"SILVER - COMMODITY EXCHANGE INC.",15\n'
        '"CRUDE OIL - NEW YORK MERCANTILE EXCHANGE",40\n'
    )
    zip_path = tmp_path / "deacot2026.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("annual.txt", report_text)

    df = load_cot_data(csv_path=str(zip_path))

    assert list(df.columns)[:2] == ["market_and_exchange_names", "open_interest_all"]
    assert df["market_and_exchange_names"].str.contains("gold|silver", case=False, regex=True).any()
    assert not df["market_and_exchange_names"].str.contains("crude oil", case=False, regex=True).any()


def test_add_positioning_metrics_prefers_managed_money():
    df = pd.DataFrame({
        "commodity_name": ["Gold", "Gold"],
        "report_date_as_yyyy_mm_dd": ["2026-08-04", "2026-08-11"],
        "m_money_positions_long_all": [100, 130],
        "m_money_positions_short_all": [40, 30],
        "noncommercial_positions_long_all": [999, 999],
        "noncommercial_positions_short_all": [999, 999],
    })

    result = add_positioning_metrics(df)

    assert (result["spec_category"] == "managed_money").all()
    assert result["spec_net"].tolist() == [60, 100]
    change = result.sort_values("report_date_as_yyyy_mm_dd")["spec_net_change_1w"].tolist()
    assert change[0] != change[0]  # NaN for the first observation
    assert change[1] == 40


def test_add_positioning_metrics_falls_back_to_noncommercial():
    df = pd.DataFrame({
        "commodity_name": ["Silver"],
        "report_date_as_yyyy_mm_dd": ["2026-08-11"],
        "noncommercial_positions_long_all": [80],
        "noncommercial_positions_short_all": [20],
    })

    result = add_positioning_metrics(df)

    assert result["spec_category"].tolist() == ["noncommercial"]
    assert result["spec_net"].tolist() == [60]


def test_add_positioning_metrics_skips_when_no_matching_columns():
    df = pd.DataFrame({"commodity_name": ["Gold"], "open_interest_all": [25]})

    result = add_positioning_metrics(df)

    assert "spec_net" not in result.columns
    assert result["commodity_name"].tolist() == ["Gold"]


def test_add_release_date_is_three_days_after_observation():
    df = pd.DataFrame({"report_date_as_yyyy_mm_dd": ["2026-08-04"]})  # a Tuesday

    result = add_release_date(df)

    assert result["observation_date"].iloc[0] == pd.Timestamp("2026-08-04")
    assert result["release_date"].iloc[0] == pd.Timestamp("2026-08-07")  # the following Friday
