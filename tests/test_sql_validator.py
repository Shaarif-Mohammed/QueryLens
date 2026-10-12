"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Tests: SQL validator

Run from the project root:  python -m pytest tests/test_sql_validator.py -v

Two groups: real queries the SQL generator produced (must pass unchanged apart from
the row limit), and unsafe or off-limits queries (must be rejected).
"""

import sys
from pathlib import Path

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "metadata").is_dir())
sys.path.insert(0, str(ROOT))

from app.validation.sql_validator import MAX_ROWS, allowed_relations, validate_sql  # noqa: E402

# --------------------------------------------------------------------- must pass
GENERATED_QUERIES = {
    "top customers": "SELECT customer_unique_id, total_spend, order_count FROM warehouse.vw_rfm ORDER BY total_spend DESC, customer_unique_id ASC LIMIT 10",
    "churn rate": "SELECT COUNT(*) AS total_customers, COUNT(*) FILTER (WHERE customer_status = 'Lost') AS lost_customers, ROUND(100.0 * COUNT(*) FILTER (WHERE customer_status = 'Lost') / COUNT(*), 2) AS churn_rate_pct FROM warehouse.vw_customer_health",
    "best sellers": "SELECT seller_id, performance_score, performance_tier, total_orders, total_revenue FROM warehouse.vw_seller_performance WHERE performance_tier <> 'Insufficient Data' AND total_reviews > 0 AND delivered_orders > 0 ORDER BY performance_score DESC, seller_id ASC LIMIT 10",
    "last month revenue": "SELECT order_month, total_revenue FROM warehouse.vw_new_vs_returning_revenue WHERE order_month = '2018-08-01'",
    "rfm segments with share": "SELECT segment, COUNT(*) AS customer_count, ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) AS customer_share_pct FROM warehouse.vw_rfm GROUP BY segment ORDER BY customer_count DESC, segment",
    "late vs on-time": "SELECT dimension_value AS delivery_status, total_orders, avg_review_score FROM warehouse.vw_delivery_experience WHERE dimension = 'On-time vs Late' ORDER BY avg_review_score DESC",
    "high revenue poor delivery": "SELECT seller_id, total_revenue, revenue_percentile, on_time_rate, delivered_orders FROM warehouse.vw_seller_performance WHERE revenue_percentile >= 75 AND delivered_orders >= 5 ORDER BY on_time_rate ASC, seller_id ASC LIMIT 10",
    "states by revenue": "SELECT state, total_revenue, total_orders, unique_customers, revenue_share_pct FROM warehouse.vw_regional_analysis ORDER BY total_revenue DESC, state ASC LIMIT 10",
    "join of two views": "SELECT r.state, r.avg_delivery_days AS regional_days, d.avg_delivery_days AS delivery_view_days FROM warehouse.vw_regional_analysis r JOIN warehouse.vw_delivery_experience d ON d.dimension = 'By State' AND d.dimension_value = r.state::text ORDER BY r.total_revenue DESC, r.state LIMIT 10",
    "quoted aliases": 'SELECT seller_id AS "Seller ID", total_revenue AS "Total Revenue (BRL)" FROM warehouse.vw_seller_performance ORDER BY total_revenue DESC, seller_id LIMIT 10',
    "cte": "WITH monthly AS (SELECT order_month, total_revenue FROM warehouse.vw_new_vs_returning_revenue) SELECT order_month, total_revenue FROM monthly ORDER BY order_month",
    "raw tables with extract": "SELECT EXTRACT(YEAR FROM o.order_purchase_timestamp) AS order_year, COUNT(DISTINCT o.order_id) AS orders FROM warehouse.dim_order o JOIN warehouse.fact_order_items i ON i.order_key = o.order_key WHERE o.order_status = 'delivered' GROUP BY 1 ORDER BY 1",
    "subquery in from": "SELECT seg.segment, seg.n FROM (SELECT segment, COUNT(*) AS n FROM warehouse.vw_rfm GROUP BY segment) seg ORDER BY seg.n DESC, seg.segment",
    "ml table": "SELECT cluster_name, COUNT(*) AS sellers FROM warehouse.ml_seller_clusters GROUP BY cluster_name ORDER BY sellers DESC, cluster_name",
    "keyword inside a string": "SELECT category FROM warehouse.vw_product_customer_affinity WHERE category <> 'drop table; delete' ORDER BY category LIMIT 5",
    "trailing semicolon": "SELECT state FROM warehouse.vw_regional_analysis ORDER BY state LIMIT 5;",
    "is distinct from": "SELECT state FROM warehouse.vw_regional_analysis WHERE state IS DISTINCT FROM 'SP' ORDER BY state LIMIT 5",
    "generate_series": "SELECT d::date AS day FROM generate_series('2018-08-01'::date, '2018-08-31'::date, interval '1 day') AS g(d) ORDER BY 1",
}


@pytest.mark.parametrize("name", list(GENERATED_QUERIES))
def test_generated_queries_pass(name):
    result = validate_sql(GENERATED_QUERIES[name])
    assert result.ok, f"{name}: {result.errors}"
    assert result.sql


def test_relations_are_reported():
    result = validate_sql(GENERATED_QUERIES["join of two views"])
    assert result.relations == ["warehouse.vw_regional_analysis", "warehouse.vw_delivery_experience"]


def test_allow_list_comes_from_metadata():
    schema, names = allowed_relations()
    assert schema == "warehouse"
    assert len(names) == 26
    assert {"vw_rfm", "fact_order_items", "ml_seller_clusters"} <= names


# ----------------------------------------------------------------- must be rejected
REJECTED = {
    "empty": "",
    "only a comment": "-- nothing here",
    "delete": "DELETE FROM warehouse.dim_customer",
    "drop": "DROP TABLE warehouse.dim_customer",
    "update": "UPDATE warehouse.dim_order SET order_status = 'canceled'",
    "insert": "INSERT INTO warehouse.dim_seller (seller_id) VALUES ('x')",
    "truncate": "TRUNCATE warehouse.fact_payments",
    "second statement": "SELECT 1 FROM warehouse.vw_rfm; DROP TABLE warehouse.vw_rfm",
    "statement hidden after comment": "SELECT * FROM warehouse.vw_rfm -- harmless\n; DELETE FROM warehouse.dim_customer",
    "data-modifying cte": "WITH gone AS (DELETE FROM warehouse.dim_customer RETURNING *) SELECT * FROM gone",
    "select into": "SELECT * INTO new_table FROM warehouse.vw_rfm",
    "for update": "SELECT * FROM warehouse.dim_order FOR UPDATE",
    "for share": "SELECT * FROM warehouse.dim_order FOR SHARE",
    "staging schema": "SELECT * FROM staging.stg_orders",
    "public schema": "SELECT * FROM public.users",
    "system catalog": "SELECT usename, passwd FROM pg_catalog.pg_shadow",
    "unqualified catalog": "SELECT * FROM pg_user",
    "information_schema": "SELECT table_name FROM information_schema.tables",
    "unknown warehouse table": "SELECT * FROM warehouse.secret_salaries",
    "unqualified view": "SELECT * FROM vw_rfm",
    "three-part name": "SELECT * FROM otherdb.warehouse.vw_rfm",
    "sleep": "SELECT pg_sleep(60) FROM warehouse.vw_rfm",
    "read a file": "SELECT pg_read_file('/etc/passwd')",
    "dblink": "SELECT * FROM dblink('host=evil', 'select 1') AS t(x int)",
    "settings": "SELECT current_setting('data_directory') FROM warehouse.vw_rfm",
    "set_config": "SELECT set_config('statement_timeout', '0', false)",
    "copy": "COPY warehouse.dim_customer TO '/tmp/out.csv'",
    "not a select": "EXPLAIN ANALYZE SELECT * FROM warehouse.vw_rfm",
    "dollar quoting": "SELECT $x$ DROP $x$ FROM warehouse.vw_rfm",
    "unknown table after subquery": "SELECT * FROM (SELECT 1) a, warehouse.not_a_view",
    "unknown table after table function": "SELECT * FROM generate_series(1, 2) g, secret_table",
    "unterminated string": "SELECT * FROM warehouse.vw_rfm WHERE segment = 'Loyal",
    "set role": "SET ROLE postgres",
}


@pytest.mark.parametrize("name", list(REJECTED))
def test_unsafe_queries_rejected(name):
    result = validate_sql(REJECTED[name])
    assert not result.ok, f"{name} was allowed"
    assert result.errors
    assert result.sql == ""


# ----------------------------------------------------------------------- row limit
def test_small_limit_kept_unchanged():
    sql = GENERATED_QUERIES["top customers"]
    result = validate_sql(sql)
    assert result.sql == sql
    assert not result.warnings


def test_missing_limit_is_added():
    result = validate_sql(GENERATED_QUERIES["last month revenue"])
    assert result.sql.rstrip().endswith(f"LIMIT {MAX_ROWS}")


def test_large_limit_is_capped():
    result = validate_sql("SELECT customer_unique_id FROM warehouse.vw_rfm ORDER BY 1 LIMIT 100000")
    assert result.ok
    assert result.sql.startswith("SELECT * FROM (")
    assert result.sql.rstrip().endswith(f"LIMIT {MAX_ROWS}")
    assert any("capped" in w for w in result.warnings)


def test_limit_all_is_capped():
    result = validate_sql("SELECT customer_unique_id FROM warehouse.vw_rfm ORDER BY 1 LIMIT ALL")
    assert result.ok and result.sql.rstrip().endswith(f"LIMIT {MAX_ROWS}")


def test_limit_inside_subquery_does_not_count():
    result = validate_sql("SELECT * FROM (SELECT segment FROM warehouse.vw_rfm LIMIT 5) s")
    assert result.sql.rstrip().endswith(f"LIMIT {MAX_ROWS}")


# ------------------------------------------------------------------------ warnings
def test_current_date_is_a_warning_not_an_error():
    result = validate_sql(
        "SELECT order_month, total_revenue FROM warehouse.vw_new_vs_returning_revenue "
        "WHERE order_month >= CURRENT_DATE - INTERVAL '1 month'"
    )
    assert result.ok
    assert any("2018-09-01" in w for w in result.warnings)


def test_comments_are_removed_from_executed_sql():
    result = validate_sql("SELECT state FROM warehouse.vw_regional_analysis /* note */ ORDER BY state LIMIT 5")
    assert result.ok and "note" not in result.sql
