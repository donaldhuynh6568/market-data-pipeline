import pytest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from market_data_pipeline.collectors.price import RAW_DATASETS, TIMEFRAME_CHOICES


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


def test_every_timeframe_has_its_own_raw_dataset():
    assert set(TIMEFRAME_CHOICES) == set(RAW_DATASETS)
    assert RAW_DATASETS["1Day"] == "bars_1d"
    assert len(set(RAW_DATASETS.values())) == len(RAW_DATASETS)


# --- incremental refresh -----------------------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

import pandas as pd  # noqa: E402

from market_data_pipeline.collectors import price  # noqa: E402

END = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def _stored(last_bars: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"symbol": s, "timestamp": pd.Timestamp(t, tz="UTC"), "close": 1.0} for s, t in last_bars.items()]
    )


def test_plan_fetch_first_run_backfills_everything():
    assert price.plan_fetch(None, ["A", "B"], 30, END, timedelta(days=1)) == [(["A", "B"], END - timedelta(days=30))]
    assert price.plan_fetch(None, [], 30, END, timedelta(days=1)) == []


def test_plan_fetch_known_symbols_resume_from_stalest_bar_minus_overlap_and_new_ticker_backfills():
    stored = _stored({"A": "2026-10-05", "B": "2026-10-01"})

    groups = price.plan_fetch(stored, ["A", "B", "NEW"], 730, END, timedelta(days=2))

    assert groups[0] == (["A", "B"], datetime(2026, 9, 29, tzinfo=timezone.utc))    # B is stalest: Oct 1 - 2d
    assert groups[1] == (["NEW"], END - timedelta(days=730))


def test_plan_fetch_never_reaches_before_the_requested_window():
    stored = _stored({"A": "2020-01-01"})
    [(symbols, start)] = price.plan_fetch(stored, ["A"], 30, END, timedelta(days=1))
    assert start == END - timedelta(days=30)


def test_plan_fetch_ignores_stored_symbols_no_longer_requested():
    stored = _stored({"A": "2026-10-05", "OLD": "2020-01-01"})
    [(symbols, start)] = price.plan_fetch(stored, ["A"], 730, END, timedelta(days=1))
    assert symbols == ["A"] and start == datetime(2026, 10, 4, tzinfo=timezone.utc)


def test_collect_bars_fetches_only_the_tail(monkeypatch):
    calls = []

    def fake(kind):
        def fetch(symbols, start, end, timeframe):
            calls.append((kind, symbols, start))
            ts = pd.Timestamp("2026-10-05", tz="UTC")
            return pd.DataFrame({"symbol": symbols, "timestamp": ts, "close": 2.0})
        return fetch

    monkeypatch.setattr(price, "fetch_equity_bars", fake("equity"))
    monkeypatch.setattr(price, "fetch_crypto_bars", fake("crypto"))
    stored = _stored({"A": "2026-10-04", "BTC/USD": "2026-10-04"})
    tickers = {"equities": ["A", "ZZZ"], "crypto": ["BTC/USD"]}

    fetched = price.collect_bars(tickers, stored, 730, END, price.TIMEFRAME_CHOICES["1Day"], timedelta(days=1))

    assert [(k, s) for k, s, _ in calls] == [("equity", ["A"]), ("equity", ["ZZZ"]), ("crypto", ["BTC/USD"])]
    assert calls[0][2] == datetime(2026, 10, 3, tzinfo=timezone.utc)      # tail only, not 730 days
    assert calls[1][2] == END - timedelta(days=730)                       # the new ticker backfills in full

    assert set(fetched["symbol"]) == {"A", "BTC/USD", "ZZZ"}


def test_plan_fetch_gives_a_long_stale_symbol_its_own_request():
    stored = _stored({"A": "2026-10-05", "B": "2026-10-04", "HALTED": "2026-07-31"})

    groups = price.plan_fetch(stored, ["A", "B", "HALTED"], 730, END, timedelta(days=1))

    assert groups == [
        (["A", "B"], datetime(2026, 10, 3, tzinfo=timezone.utc)),
        (["HALTED"], datetime(2026, 7, 30, tzinfo=timezone.utc)),
    ]


def test_main_appends_to_raw_and_resumes_from_raw(monkeypatch, tmp_path):
    from market_data_pipeline import raw

    monkeypatch.setattr(raw, "RAW_DIR", tmp_path)
    monkeypatch.setattr(price, "load_tickers", lambda: {"equities": ["A"], "crypto": []})
    starts = []

    def fetch(symbols, start, end, timeframe):
        starts.append(start)
        return pd.DataFrame({"symbol": symbols, "timestamp": pd.Timestamp("2026-10-05", tz="UTC"), "close": 2.0})

    monkeypatch.setattr(price, "fetch_equity_bars", fetch)
    monkeypatch.setattr("sys.argv", ["price"])
    price.main()
    price.main()

    stored = raw.read_raw("alpaca", "bars_1d")
    assert len(stored) == 2 and stored["run_id"].nunique() == 2          # appended, nothing overwritten
    assert starts[1] > starts[0]                                         # second run resumed from raw
