"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Formatting tests

Offline. Column titles carry the right unit, and the table keeps every row and value.
"""

import pandas as pd

from app.analytics.analyzer import profile_result
from app.visualization.formatting import column_title, display_table


def frame():
    return pd.DataFrame({
        "state": ["SP", "RJ"], "total_revenue": [5769703.15, 2055401.57], "on_time_rate": [94.1, 85.96],
        "revenue_percentile": [99.0, 98.0], "freight_pct_of_revenue": [13.2, 15.1],
        "total_orders": [40501, 12350], "avg_order_value": [142.4, 166.4], "month_number": [1, 2],
    })


def test_titles_carry_units():
    df = frame()
    titles = [column_title(c, profile_result(df)) for c in df.columns]
    assert titles == ["State", "Total revenue (BRL)", "On time rate (%)", "Revenue percentile",
                      "Freight share of revenue (%)", "Total orders", "Avg order value (BRL)", "Month number"]


def test_table_formats_figures_and_keeps_rows():
    df = frame()
    html = display_table(df, profile_result(df)).to_html()
    assert "5,769,703.15" in html and "40,501" in html and ">1<" in html and "1,0" not in html.split("Month number")[1][:200]
    assert display_table(df, None).data.shape == df.shape


def test_format_value_by_unit():
    from app.visualization.formatting import format_value
    df = frame()
    p = profile_result(df)
    assert format_value("total_revenue", 985491.64, p) == "BRL 985,491.64"
    assert format_value("on_time_rate", 85.96, p) == "86.0%"
    assert format_value("total_orders", 26165, p) == "26,165"
    assert format_value("month_number", 11, p) == "11"
    assert format_value("total_orders", float("nan"), p) == "–"


def test_pretty_sql_puts_each_clause_on_its_own_line():
    from app.visualization.formatting import pretty_sql
    sql = ("SELECT state, total_revenue, total_orders FROM warehouse.vw_regional_analysis "
           "ORDER BY total_revenue DESC, state LIMIT 10")
    lines = pretty_sql(sql).splitlines()
    assert lines[0] == "SELECT state,"
    assert any(l.startswith("FROM warehouse.vw_regional_analysis") for l in lines)
    assert any(l.startswith("ORDER BY total_revenue DESC") for l in lines) and lines[-1] == "LIMIT 10"
    assert pretty_sql("") == ""


def test_pretty_sql_keeps_the_query_text():
    from app.visualization.formatting import pretty_sql
    sql = "SELECT a, b FROM warehouse.vw_rfm WHERE segment = 'Loyal' ORDER BY a LIMIT 5"
    assert " ".join(pretty_sql(sql).split()) == sql  # only whitespace changed


def test_readable_names_spell_out_category_codes_in_prose():
    from app.visualization.formatting import readable_names
    df = pd.DataFrame({"category": ["bed_bath_table", "auto"], "revenue": [1.0, 2.0]})
    text = "Peaked in November, led by bed_bath_table. Bed_bath_table beat auto; autonomy untouched."
    out = readable_names(text, df)
    assert "bed_bath_table" not in out.lower()
    assert out.count("Bed bath table") == 2
    assert "autonomy untouched" in out and "beat auto;" in out


def test_column_titles_drop_storage_suffixes_and_repeated_units():
    from app.visualization.formatting import column_title
    df = pd.DataFrame({"month_name": ["Jan"], "month_number": [1], "revenue_brl": [1.0],
                       "total_revenue": [2.0], "order_count": [3]})
    from app.analytics.analyzer import profile_result
    profile = profile_result(df)
    assert column_title("month_name", profile) == "Month"
    assert column_title("month_number", profile) == "Month number"
    assert column_title("revenue_brl", profile) == "Revenue (BRL)"
    assert column_title("total_revenue", profile) == "Total revenue (BRL)"
    assert column_title("order_count", profile) == "Order count"
    assert column_title("total_revenue_mom_growth_pct", profile).startswith("Total revenue month on month growth")


def test_table_shows_a_dash_for_growth_from_an_almost_empty_period():
    df = pd.DataFrame({
        "order_month": pd.to_datetime(["2017-01-01", "2017-02-01", "2017-03-01"]),
        "total_revenue": [127482.37, 271239.32, 414330.95],
        "total_revenue_mom_growth_pct": [649657.24, 112.77, 52.75],
    })
    html = display_table(df, profile_result(df)).to_html()
    assert "649,657" not in html and "112.77" in html and "–" in html
    assert df.at[0, "total_revenue_mom_growth_pct"] == 649657.24  # the stored rows are untouched
