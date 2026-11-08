-- Grain: date. Temporal train / validation / test assignment over the usable range (from the first date SPY has
-- a full 200-day MA, since nothing can be signalled before that). The target_horizon trading days before each
-- boundary are 'gap', so a forward return measured in one split never ends inside the next.
{% set h = var('target_horizon') %}
{% set split = var('split') %}

with usable as (
    select f.date, row_number() over (order by f.date) - 1 as idx, count(*) over () as n
    from {{ ref('mart_features') }} f
    where f.ticker = 'SPY' and f.dist_ma200 is not null
),

bounds as (
    select
        *,
        cast(floor(n * {{ split['train'] }}) as bigint) as b_validation,
        cast(floor(n * ({{ split['train'] }} + {{ split['validation'] }})) as bigint) as b_test
    from usable
)

select
    date,
    case
        when idx >= b_validation - {{ h }} and idx < b_validation then 'gap'
        when idx >= b_test - {{ h }} and idx < b_test then 'gap'
        when idx < b_validation then 'train'
        when idx < b_test then 'validation'
        else 'test'
    end as split
from bounds
