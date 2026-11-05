"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Result analyzer tests

Built from result shapes the SQL step really produces (rankings, segment counts,
two-group comparisons, monthly series).
"""

import pandas as pd

from app.analytics.analyzer import format_number, grid_table, profile_result, rows_for_model, short_label

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


# ------------------------------------------------------------ dimensions, grids, pairs


def heatmap_frame():
    rows = []
    for k, month in enumerate(["January", "February", "March"], start=1):
        for j, cat in enumerate(["bed_bath_table", "health_beauty", "auto"]):
            rows.append({"order_month_num": k, "order_month": month, "category": cat,
                         "monthly_revenue": [4637.60, 13802.80, 5386.38, 18462.71, 25178.32, 9000.0,
                                             29856.01, 29463.99, 9500.0][(k - 1) * 3 + j]})
    return pd.DataFrame(rows)


HEAT_HINT = "monthly_revenue (BRL) grouped by purchase month and product category"


def test_month_number_is_a_dimension_not_a_measure():
    p = profile_result(heatmap_frame(), "SELECT ...\nLIMIT 500", HEAT_HINT)
    assert p.role("order_month_num") == "key" and p.primary_measure == "monthly_revenue"
    assert p.sort_keys == {"order_month": "order_month_num"}
    assert p.grid == ("category", "order_month")


def test_grid_facts_name_both_sides_of_each_cell():
    p = profile_result(heatmap_frame(), "SELECT ...\nLIMIT 500", HEAT_HINT)
    assert any("Highest single cell: 29,856.01 (bed_bath_table, March)" in f for f in p.facts)
    assert any("lowest single cell: 4,637.60 (bed_bath_table, January)" in f for f in p.facts)
    assert any("Total monthly_revenue by order_month: highest March" in f for f in p.facts)
    assert not any(f.startswith("Highest monthly_revenue") for f in p.facts)  # no single-name extremes


def test_grid_goes_to_the_model_whole_with_columns_in_order():
    df = heatmap_frame()
    p = profile_result(df, "SELECT ...\nLIMIT 500", HEAT_HINT)
    lines = rows_for_model(df, profile=p).splitlines()
    assert lines[0] == "category,January,February,March" and len(lines) == 4
    assert grid_table(df, p).index[0] == "health_beauty"  # largest total first


def test_ranking_with_a_date_is_not_a_grid():
    df = pd.DataFrame({"customer_unique_id": ["a" * 32, "b" * 32, "c" * 32, "d" * 32],
                       "total_spend": [4.0, 3.0, 2.0, 1.0],
                       "last_order_date": pd.to_datetime(["2017-09-29", "2018-07-15", "2018-03-01", "2018-01-01"])})
    assert profile_result(df, "SELECT ... ORDER BY total_spend DESC LIMIT 10", "total_spend").grid is None


def scatter_frame(n=40):
    return pd.DataFrame({"seller_id": [f"{i:032x}" for i in range(n)],
                         "total_revenue": [100000.0 - 2000 * i for i in range(n)],
                         "on_time_rate": [85.0 if i < 10 else 95.0 for i in range(n)],
                         "delivered_orders": [60] * n})


def test_pair_facts_cover_every_row():
    p = profile_result(scatter_frame(), "SELECT ... ORDER BY total_revenue DESC, seller_id LIMIT 500",
                       "total_revenue (BRL) and on_time_rate from vw_seller_performance")
    assert p.pair == ("total_revenue", "on_time_rate")
    assert any("Across all 40 rows: the quarter with the highest total_revenue (10 rows) has a median "
               "on_time_rate of 85; the quarter with the lowest total_revenue has a median on_time_rate of 95" in f
               for f in p.facts)


def test_pair_needs_exactly_two_named_measures_and_enough_rows():
    hint = "total_revenue (BRL) and on_time_rate from vw_seller_performance"
    assert profile_result(scatter_frame(10), "", hint).pair is None
    assert profile_result(scatter_frame(), "", "total_revenue only").pair is None


def test_sorting_groups_does_not_change_the_measure_without_a_top_n():
    df = pd.DataFrame({"delivery_status": ["On-Time", "Late"], "total_orders": [88644, 7826],
                       "avg_delivery_days": [10.4, 31.1], "avg_review_score": [4.29, 2.57]})
    sql = "SELECT ... ORDER BY avg_delivery_days ASC\nLIMIT 500"
    p = profile_result(df, sql, "avg_review_score and avg_delivery_days by delivery status")
    assert p.primary_measure == "avg_review_score"


def test_category_names_are_not_shortened_like_ids():
    assert short_label("bed_bath_table") == "bed_bath_table" and short_label("computers_accessories") == "computers_accessories"


def test_growth_from_an_almost_empty_period_is_left_out():
    from app.analytics.analyzer import profile_result, rows_for_model
    df = pd.DataFrame({
        "order_month": pd.to_datetime(["2017-01-01", "2017-02-01", "2017-03-01", "2017-04-01"]),
        "total_orders": [750, 1653, 2546, 2303],
        "total_revenue": [127482.37, 271239.32, 414330.95, 390812.40],
        "total_revenue_mom_growth_pct": [649657.24, 112.77, 52.75, -5.68],
    })
    p = profile_result(df, "SELECT * FROM warehouse.vw_x ORDER BY order_month",
                       "total_revenue (BRL) by order_month")
    assert p.tiny_base == {"total_revenue_mom_growth_pct": [0]}
    assert any("2017-01-01" in f and "not meaningful" in f for f in p.facts)
    sent = rows_for_model(df, profile=p)
    assert "649657" not in sent and "112.77" in sent


def test_ordinary_growth_and_falls_are_kept():
    from app.analytics.analyzer import growth_from_tiny_base
    df = pd.DataFrame({"revenue": [100.0, 50.0, 200.0], "revenue_growth_pct": [None, -50.0, 300.0],
                       "orders": [10, 5, 20]})
    assert growth_from_tiny_base(df) == {}

