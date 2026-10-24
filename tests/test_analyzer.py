"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Result analyzer tests

Built from result shapes the SQL step really produces (rankings, segment counts,
two-group comparisons, monthly series).
"""

import pandas as pd

from app.analytics.analyzer import format_number, profile_result, rows_for_model, short_label

SELLER_ID = "b1b3948701c5c72445495bd161b83a4c"


def sellers(delivered=(14, 6, 5)):
    return pd.DataFrame({
        "seller_id": [SELLER_ID, "0873d9f8f36123f8d910f4760e788cfb", "397c4d0c005b6f41f90098ac724e28cb"],
        "total_revenue": [21924.25, 7426.65, 8622.37],
        "revenue_percentile": [96.0, 85.0, 88.0],
        "on_time_rate": [35.71, 50.0, 60.0],
        "delivered_orders": list(delivered),
    })


SELLER_SQL = ("SELECT seller_id, total_revenue, revenue_percentile, on_time_rate, delivered_orders "
              "FROM warehouse.vw_seller_performance WHERE revenue_percentile >= 75 "
              "ORDER BY on_time_rate ASC, seller_id ASC LIMIT 10")
SELLER_HINT = "total_revenue (BRL) and on_time_rate from vw_seller_performance"


def test_column_roles():
    p = profile_result(sellers(), SELLER_SQL, SELLER_HINT)
    roles = {c.name: c.role for c in p.columns}
    assert roles == {
        "seller_id": "identifier", "total_revenue": "measure", "revenue_percentile": "measure",
        "on_time_rate": "percent", "delivered_orders": "count",
    }
    assert p.label_column == "seller_id" and p.date_column is None


def test_ranking_column_named_by_the_sql_step_is_the_measure():
    p = profile_result(sellers(), SELLER_SQL, SELLER_HINT)
    assert p.primary_measure == "on_time_rate"  # ORDER BY column, also named in measure_used


def test_first_named_column_wins_when_ranking_column_is_not_named():
    df = pd.DataFrame({"state": ["SP", "RJ", "MG"], "total_revenue": [5.7e6, 2.0e6, 1.8e6],
                       "avg_delivery_days": [8.3, 14.8, 11.5], "on_time_rate": [94.1, 86.5, 94.4]})
    sql = "SELECT state, total_revenue, avg_delivery_days, on_time_rate FROM warehouse.vw_regional_analysis ORDER BY total_revenue DESC, state ASC LIMIT 10"
    p = profile_result(df, sql, "avg_delivery_days (average days) and on_time_rate from vw_regional_analysis")
    assert p.primary_measure == "avg_delivery_days"


def test_falls_back_to_ranking_column_then_first_non_count():
    df = pd.DataFrame({"segment": ["A", "B"], "customer_count": [10, 5], "avg_spend": [1.0, 2.0]})
    assert profile_result(df, "SELECT ... ORDER BY customer_count DESC").primary_measure == "customer_count"
    assert profile_result(df, "").primary_measure == "avg_spend"


def test_small_sample_fact_when_rows_are_under_30():
    p = profile_result(sellers(), SELLER_SQL, SELLER_HINT)
    assert p.volume_column == "delivered_orders" and p.small_sample_rows == 3
    assert any("3 of 3 rows have fewer than 30 delivered_orders" in f for f in p.facts)


def test_no_small_sample_fact_when_every_row_is_large():
    p = profile_result(sellers((140, 60, 50)), SELLER_SQL, SELLER_HINT)
    assert p.small_sample_rows == 0
    assert any("none is a small sample" in f for f in p.facts)


def test_no_volume_check_for_a_total():
    df = pd.DataFrame({"customer_unique_id": ["a" * 32, "b" * 32], "total_spend": [13664.08, 7571.63],
                       "order_count": [1, 2]})
    p = profile_result(df, "SELECT ... ORDER BY total_spend DESC LIMIT 10", "total_spend from vw_rfm")
    assert p.volume_column is None and not any("small sample" in f for f in p.facts)


def test_limited_result_is_a_subset_without_shares():
    df = pd.DataFrame({"state": ["SP", "RJ"], "total_orders": [40501, 12350]})
    p = profile_result(df, "SELECT state, total_orders FROM warehouse.vw_regional_analysis ORDER BY total_orders DESC LIMIT 2",
                       "total_orders")
    assert not p.complete
    assert any("top 2 rows only" in f for f in p.facts)
    assert not any("Share of that total" in f for f in p.facts)


def test_complete_count_result_lists_shares():
    df = pd.DataFrame({"segment": ["Loyal", "Champions", "Recent"], "customer_count": [26407, 3800, 3740]})
    p = profile_result(df, "SELECT segment, COUNT(*) AS customer_count FROM warehouse.vw_rfm GROUP BY segment\nLIMIT 500",
                       "customer_count by segment")
    assert p.complete
    share_fact = next(f for f in p.facts if "Share of that total" in f)
    assert "Total customer_count across all rows: 33,947" in share_fact
    assert "Loyal 77.8%" in share_fact


def test_truncated_result_is_not_complete():
    df = pd.DataFrame({"segment": ["A", "B"], "customer_count": [1, 2]})
    p = profile_result(df, "SELECT 1\nLIMIT 500", truncated=True)
    assert not p.complete and any("more rows existed" in f for f in p.facts)


def test_date_range_and_extremes():
    df = pd.DataFrame({"order_month": pd.to_datetime(["2018-06-01", "2018-07-01", "2018-08-01"]),
                       "total_revenue": [1000.0, 1200.5, 985.25]})
    p = profile_result(df, "SELECT ...\nLIMIT 500", "total_revenue")
    assert p.date_column == "order_month"
    assert "Rows run from 2018-06-01 to 2018-08-01 (order_month)." in p.facts
    assert "Highest total_revenue: 1,200.50 (2018-07-01); lowest: 985.25 (2018-08-01)." in p.facts


def test_empty_and_single_row_results():
    empty = profile_result(pd.DataFrame({"a": [], "n": []}), "")
    assert empty.row_count == 0 and "No rows matched the query's filters." in empty.facts
    one = profile_result(pd.DataFrame({"churn_rate_pct": [22.61]}), "", "churn_rate_pct")
    assert one.primary_measure == "churn_rate_pct" and not any("Highest" in f for f in one.facts)


def test_rows_for_model_shortens_ids_and_rounds():
    text = rows_for_model(pd.DataFrame({"seller_id": [SELLER_ID], "on_time_rate": [35.714285]}))
    assert text.splitlines() == ["seller_id,on_time_rate", "b1b39487…,35.71"]


def test_rows_for_model_caps_rows():
    text = rows_for_model(pd.DataFrame({"n": range(80)}), max_rows=50)
    assert len(text.splitlines()) == 51  # header + 50


def test_helpers():
    assert short_label(SELLER_ID) == "b1b39487…" and short_label("SP") == "SP" and short_label("At Risk") == "At Risk"
    assert format_number(26407) == "26,407" and format_number(985491.644) == "985,491.64"

