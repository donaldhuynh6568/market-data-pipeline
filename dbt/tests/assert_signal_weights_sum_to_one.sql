-- Every variant is fully invested at every rebalance (weights sum to 1). A trend variant whose defensive asset
-- has no momentum score that month would be in cash; that is surfaced here rather than silently ignored.
select variant, rebalance_date, sum(weight) as total_weight
from {{ ref('mart_signals') }}
group by variant, rebalance_date
having abs(sum(weight) - 1.0) > 1e-9
