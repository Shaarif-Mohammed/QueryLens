"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Chart selection tests
"""

import pandas as pd

from app.analytics.analyzer import profile_result
from app.visualization.charts import build_chart, pretty


def chart_for(df, sql="SELECT ...\nLIMIT 500", hint=""):
    return build_chart(df, profile_result(df, sql, hint))


def test_ranking_becomes_a_bar_chart_in_query_order():
    df = pd.DataFrame({"state": ["SP", "RJ", "MG"], "total_revenue": [5.7e6, 2.0e6, 1.8e6]})
    chart = chart_for(df, "SELECT ... ORDER BY total_revenue DESC LIMIT 10", "total_revenue")
    assert chart.kind == "bar" and chart.x == "total_revenue" and chart.y == "state"
    # Plotly draws the last category at the top, so the array is reversed: SP on top.
    assert list(chart.figure.layout.yaxis.categoryarray) == ["MG", "RJ", "SP"]


def test_bar_labels_shorten_long_ids_and_stay_unique():
    df = pd.DataFrame({"seller_id": ["a" * 32, "a" * 8 + "b" * 24], "performance_score": [97.2, 96.4]})
    chart = chart_for(df, "SELECT ... ORDER BY performance_score DESC LIMIT 10", "performance_score")
    assert list(chart.figure.data[0].y) == ["aaaaaaaa…", "aaaaaaaa… (2)"]


def test_percent_measure_axis_is_labelled():
    df = pd.DataFrame({"delivery_status": ["On-Time", "Late"], "on_time_rate": [90.0, 10.0]})
    chart = chart_for(df, hint="on_time_rate")
    assert chart.figure.layout.xaxis.title.text == "On time rate (%)"


def test_volume_column_is_in_the_hover():
    df = pd.DataFrame({"seller_id": ["x" * 32, "y" * 32], "on_time_rate": [35.7, 50.0], "delivered_orders": [14, 60]})
    chart = chart_for(df, "SELECT ... ORDER BY on_time_rate ASC LIMIT 10", "on_time_rate")
    assert "delivered_orders" in chart.figure.data[0].hovertemplate


def test_monthly_series_becomes_a_sorted_line():
    df = pd.DataFrame({"order_month": pd.to_datetime(["2018-08-01", "2018-06-01", "2018-07-01"]),
                       "total_revenue": [985.0, 1000.0, 1200.0]})
    chart = chart_for(df, hint="total_revenue")
    assert chart.kind == "line" and chart.x == "order_month"
    assert [str(x)[:10] for x in chart.figure.data[0].x] == ["2018-06-01", "2018-07-01", "2018-08-01"]


def test_no_chart_for_single_row_empty_or_too_many_rows():
    assert chart_for(pd.DataFrame({"churn_rate_pct": [22.61]})).kind is None
    assert chart_for(pd.DataFrame({"a": [], "n": []})).kind is None
    many = pd.DataFrame({"state": [f"S{i}" for i in range(27)], "total_orders": range(27)})
    chart = chart_for(many)
    assert chart.kind is None and "too many" in chart.reason


def test_no_chart_without_a_measure_or_a_label():
    assert chart_for(pd.DataFrame({"segment": ["A", "B"]})).kind is None
    assert chart_for(pd.DataFrame({"x": [1.0, 2.0], "y": [3.0, 4.0]})).kind is None
    assert build_chart(None, None).kind is None


def test_pretty_names():
    assert pretty("total_revenue") == "Total revenue" and pretty("churn_rate_pct") == "Churn rate"


def test_ranking_with_a_date_attribute_is_a_bar_not_a_line():
    df = pd.DataFrame({"customer_unique_id": ["a" * 32, "b" * 32, "c" * 32],
                       "total_spend": [13664.08, 7571.63, 7274.88],
                       "last_order_date": pd.to_datetime(["2017-09-29", "2018-07-15", "2018-03-01"])})
    chart = chart_for(df, "SELECT ... ORDER BY total_spend DESC, customer_unique_id LIMIT 10", "total_spend")
    assert chart.kind == "bar" and chart.x == "total_spend"


def test_repeated_dates_are_not_a_single_line():
    df = pd.DataFrame({"order_month": pd.to_datetime(["2018-06-01", "2018-06-01", "2018-07-01"]),
                       "total_revenue": [1.0, 2.0, 3.0]})
    assert chart_for(df, hint="total_revenue").kind is None

