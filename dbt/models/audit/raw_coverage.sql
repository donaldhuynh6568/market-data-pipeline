-- Per-ticker history depth in staging, plus when its raw data was last ingested.
select
    ticker,
    asset_class,
    min(date) as first_date,
    max(date) as last_date,
    count(*) as n_bars,
    max(ingested_at) as last_ingested_at
from {{ ref('stg_price_daily') }}
group by ticker, asset_class
order by ticker
