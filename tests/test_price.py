import pytest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from market_data_pipeline.collectors.price import OUTPUT_FILENAMES, TIMEFRAME_CHOICES


@pytest.mark.parametrize(
    ("choice", "expected_amount", "expected_unit"),
    [
        ("1Day", 1, TimeFrameUnit.Day),
        ("1Hour", 1, TimeFrameUnit.Hour),
        ("4Hour", 4, TimeFrameUnit.Hour),
        ("5Min", 5, TimeFrameUnit.Minute),
    ],
)
def test_timeframe_choices_map_to_correct_alpaca_timeframe(choice, expected_amount, expected_unit):
    timeframe = TIMEFRAME_CHOICES[choice]

    assert isinstance(timeframe, TimeFrame)
    assert timeframe.amount == expected_amount
    assert timeframe.unit == expected_unit


def test_default_daily_output_filename_is_unchanged_for_backward_compatibility():
    assert OUTPUT_FILENAMES["1Day"] == "price_data.parquet"


@pytest.mark.parametrize(
    ("choice", "expected_filename"),
    [
        ("1Hour", "price_data_1h.parquet"),
        ("4Hour", "price_data_4h.parquet"),
        ("5Min", "price_data_5m.parquet"),
    ],
)
def test_intraday_output_filenames_are_distinct_from_daily(choice, expected_filename):
    assert OUTPUT_FILENAMES[choice] == expected_filename
    assert OUTPUT_FILENAMES[choice] != OUTPUT_FILENAMES["1Day"]


def test_every_timeframe_choice_has_an_output_filename():
    assert set(TIMEFRAME_CHOICES) == set(OUTPUT_FILENAMES)
