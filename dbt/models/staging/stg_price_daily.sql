-- One row per ticker and trading date: the newest ingested copy of each daily bar.
-- Equity bars are stamped at 00:00 New York time, so their trading date is the New York date. Crypto bars are
-- stamped at 00:00 UTC and are dated in UTC.
with bars as (
    select
        symbol as ticker,
        asset_class,
        case
            when asset_class = 'equity' then cast(timezone('America/New_York', "timestamp") as date)
            else cast(timezone('UTC', "timestamp") as date)
        end as date,
        cast(open as double) as open,
        cast(high as double) as high,
        cast(low as double) as low,
        cast(close as double) as close,
        cast(volume as double) as volume,
        cast(vwap as double) as vwap,
        cast(trade_count as double) as trade_count,
        ingested_at,
        run_id
    from {{ source('raw', 'alpaca_bars_1d') }}
)

select *
from bars
qualify row_number() over (partition by ticker, date order by ingested_at desc, run_id desc) = 1
