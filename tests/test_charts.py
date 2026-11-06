"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Chart selection tests
"""

import pandas as pd

from app.analytics.analyzer import profile_result
from app.visualization.charts import MONTHS, build_chart, pretty  # noqa: F401


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


def test_volume_is_in_the_tooltip_with_a_small_sample_note():
    df = pd.DataFrame({"seller_id": ["x" * 32, "y" * 32], "on_time_rate": [35.7, 50.0], "delivered_orders": [14, 60]})
    chart = chart_for(df, "SELECT ... ORDER BY on_time_rate ASC LIMIT 10", "on_time_rate")
    tips = list(chart.figure.data[0].customdata)
    assert "Delivered orders: 14 · small sample" in tips[0] and "Delivered orders: 60" in tips[1]
    assert "small sample" not in tips[1] and "delivered_orders" not in tips[0]  # no raw column names


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


# ------------------------------------------------------------ labels, tooltips, heatmaps, scatters and multi-line charts
SELLER = "4869f7a5dfa277a7dca6462dcf3b52b2"


def test_bar_prints_compact_values_and_full_names_in_the_tooltip():
    df = pd.DataFrame({"category": ["health_beauty", "watches_gifts"], "total_revenue": [1412089.53, 1264333.12],
                       "total_orders": [8647, 5495]})
    chart = chart_for(df, "SELECT ... ORDER BY total_revenue DESC LIMIT 10", "total_revenue")
    bar = chart.figure.data[0]
    assert list(bar.text) == ["BRL 1.41M", "BRL 1.26M"] and list(bar.y) == ["Health beauty", "Watches gifts"]
    assert "Total revenue (BRL): BRL 1,412,089.53" in bar.customdata[0] and "Total orders: 8,647" in bar.customdata[0]
    assert chart.figure.layout.xaxis.title.text == "Total revenue (BRL)"


def test_seller_tooltip_shows_the_full_id():
    df = pd.DataFrame({"seller_id": [SELLER, "7c67e1448b00f6e969d365cea6b010ab"], "total_revenue": [247007.06, 237806.69]})
    chart = chart_for(df, "SELECT ... ORDER BY total_revenue DESC LIMIT 10", "total_revenue")
    assert chart.figure.data[0].y[0] == "4869f7a5…" and SELLER in chart.figure.data[0].customdata[0]


MONTHS_3 = ("January", "February", "March")


def grid_frame(categories=("bed_bath_table", "health_beauty", "watches_gifts", "auto"), months=MONTHS_3):
    rows = []
    for k, m in enumerate(months, start=1):
        for j, c in enumerate(categories):
            rows.append({"order_month_num": k, "order_month": m, "category": c, "monthly_revenue": 1000.0 * (k + j)})
    return pd.DataFrame(rows)


GRID_HINT = "monthly_revenue (price + freight, delivered orders, BRL) grouped by purchase month and product category"


def test_category_by_month_becomes_a_heatmap_with_values_in_cells():
    cats = [f"cat_{i}" for i in range(8)]
    chart = chart_for(grid_frame(cats), hint=GRID_HINT)
    assert chart.kind == "heatmap" and chart.x == "order_month" and chart.y == "category"
    heat = chart.figure.data[0]
    assert list(heat.x) == ["Jan", "Feb", "Mar"]           # months in calendar order, not alphabetical
    assert heat.y[-1] == "Cat 7"                            # largest total on top
    assert len(chart.figure.layout.annotations) == 24       # every cell labelled
    assert "Monthly revenue (BRL): BRL" in heat.customdata[0][0]


def test_few_groups_over_periods_become_one_line_each():
    chart = chart_for(grid_frame(("health_beauty", "auto")), hint=GRID_HINT)
    assert chart.kind == "multiline" and len(chart.figure.data) == 2
    assert [t.name for t in chart.figure.data] == ["Auto", "Health beauty"]  # largest total first
    assert {t.line.dash for t in chart.figure.data} == {"solid", "dash"}     # told apart without colour


def sellers_frame(n=40):
    return pd.DataFrame({
        "seller_id": [f"{i:032x}" for i in range(n)],
        "total_revenue": [100000.0 - 2000 * i for i in range(n)],
        "on_time_rate": [80.0 + (i % 20) for i in range(n)],
        "delivered_orders": [50 + 10 * i for i in range(n)],
    })


PAIR_HINT = "total_revenue (BRL) and on_time_rate from vw_seller_performance"
PAIR_SQL = "SELECT ... ORDER BY total_revenue DESC, seller_id LIMIT 500"


def test_two_named_measures_across_many_rows_become_a_scatter():
    chart = chart_for(sellers_frame(), PAIR_SQL, PAIR_HINT)
    assert chart.kind == "scatter" and (chart.x, chart.y) == ("total_revenue", "on_time_rate")
    points = chart.figure.data[0]
    assert len(points.x) == 40 and min(points.marker.size) < max(points.marker.size)  # sized by volume
    assert "On time rate (%): 80.0%" in points.customdata[0] and "Delivered orders: 50" in points.customdata[0]
    assert points.text is None  # too many points to label


def test_few_rows_with_two_measures_stay_a_bar():
    df = pd.DataFrame({"instalment_band": ["1 (Single)", "2-3", "4-6", "7-12", "12+"],
                       "avg_payment_value": [120.24, 135.42, 181.98, 332.71, 414.31],
                       "avg_order_item_total": [120.24, 135.42, 181.98, 332.71, 414.31],
                       "order_count": [48292, 22788, 16199, 11976, 185]})
    chart = chart_for(df, hint="avg_payment_value (BRL) and avg_order_item_total (BRL) from vw_payment_behaviour")
    assert chart.kind == "bar"


def test_monthly_line_labels_highest_lowest_and_latest_when_long():
    months = pd.date_range("2017-01-01", periods=20, freq="MS")
    df = pd.DataFrame({"order_month": months, "total_revenue": [100000.0 + 1000 * i for i in range(20)]})
    df.loc[10, "total_revenue"] = 500000.0
    line = chart_for(df, hint="total_revenue").figure.data[0]
    shown = [t for t in line.text if t]
    assert len(shown) == 3 and "BRL 500k" in shown and line.text[-1]

