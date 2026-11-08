{#
  The one definition of a forward return, used by every target. lead() looks FORWARD: the value at row t is the
  return from t's close to the close `horizon` rows later. Unit-tested on a hand-computed series (_marts.yml),
  because an off-by-one shift here silently turns a target into a leak or a lag.
#}
{% macro forward_return(price_col, horizon, partition_by, order_by) -%}
    lead({{ price_col }}, {{ horizon }}) over (partition by {{ partition_by }} order by {{ order_by }}) / {{ price_col }} - 1
{%- endmacro %}
