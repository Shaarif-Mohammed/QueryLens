"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Query executor tests

Run without a database: a fake connection records what would be sent and returns
canned rows. The live checks (read-only session, server-side timeout) are in
test_live_database.py.
"""

import datetime as dt
from decimal import Decimal

import psycopg2
import psycopg2.errors
import pytest

from app.database.connection import DatabaseConfigError
from app.database.query_executor import execute_validated
from app.validation.sql_validator import ValidationResult, validate_sql

SQL = "SELECT customer_unique_id, total_spend FROM warehouse.vw_rfm ORDER BY total_spend DESC LIMIT 10"


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))
        if self.conn.raise_on_query and not sql.startswith("SET LOCAL"):
            raise self.conn.raise_on_query
        if not sql.startswith("SET LOCAL"):
            self.description = [(name,) for name in self.conn.columns]

    def fetchmany(self, size):
        self.conn.fetch_sizes.append(size)
        return self.conn.rows[:size]


class FakeConnection:
    def __init__(self, columns=(), rows=(), raise_on_query=None):
        self.columns, self.rows, self.raise_on_query = list(columns), list(rows), raise_on_query
        self.executed, self.fetch_sizes = [], []
        self.rolled_back = self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def run(conn, validation=None, **kwargs):
    return execute_validated(validation or validate_sql(SQL), connect=lambda: conn, **kwargs)


def test_runs_the_validated_sql_after_setting_a_timeout():
    conn = FakeConnection(["customer_unique_id", "total_spend"], [("c1", 10), ("c2", 5)])
    result = run(conn, timeout_ms=1234)
    assert result.ok and result.row_count == 2
    assert conn.executed[0] == ("SET LOCAL statement_timeout = %s", (1234,))
    assert conn.executed[1] == (validate_sql(SQL).sql, None)  # no parameters: '%' in LIKE stays literal
    assert list(result.dataframe.columns) == ["customer_unique_id", "total_spend"]
    assert conn.rolled_back and conn.closed


def test_refuses_a_failed_validation_without_connecting():
    def connect():
        raise AssertionError("must not connect")

    result = execute_validated(validate_sql("DELETE FROM warehouse.dim_order"), connect=connect)
    assert not result.ok and result.error_kind == "not_validated"


def test_refuses_raw_strings_and_empty_sql():
    assert execute_validated("SELECT 1", connect=lambda: None).error_kind == "not_validated"
    assert execute_validated(ValidationResult(True, "  "), connect=lambda: None).error_kind == "not_validated"


def test_revalidates_a_hand_built_result():
    forged = ValidationResult(True, "DELETE FROM warehouse.dim_order")
    result = execute_validated(forged, connect=lambda: FakeConnection())
    assert result.error_kind == "not_validated"
    assert "DELETE" in result.error


@pytest.mark.parametrize("sql", [
    SQL,
    "SELECT segment, COUNT(*) AS customers FROM warehouse.vw_rfm GROUP BY segment ORDER BY segment",
    "SELECT * FROM warehouse.vw_rfm LIMIT 5000",
])
def test_validator_output_passes_its_own_recheck(sql):
    validated = validate_sql(sql)
    assert validated.ok
    assert validate_sql(validated.sql).ok  # appended or wrapped LIMIT still validates


def test_fetches_at_most_max_rows_and_flags_truncation():
    conn = FakeConnection(["n"], [(i,) for i in range(600)])
    result = run(conn, max_rows=500)
    assert conn.fetch_sizes == [501]
    assert result.row_count == 500 and result.truncated


def test_not_truncated_at_exactly_max_rows():
    conn = FakeConnection(["n"], [(i,) for i in range(500)])
    result = run(conn, max_rows=500)
    assert result.row_count == 500 and not result.truncated


def test_converts_decimal_and_date_columns():
    conn = FakeConnection(
        ["label", "revenue", "order_month", "n"],
        [("a", Decimal("10.50"), dt.date(2018, 8, 1), 3), ("b", None, None, 4)],
    )
    df = run(conn).dataframe
    assert str(df["revenue"].dtype) == "float64" and df["revenue"].iloc[0] == 10.5
    assert str(df["order_month"].dtype).startswith("datetime64")
    assert list(df["label"]) == ["a", "b"] and str(df["n"].dtype) == "int64"  # text and ints untouched


def test_repeated_column_names_are_made_unique():
    conn = FakeConnection(["count", "count", "count"], [(1, 2, 3)])
    assert list(run(conn).dataframe.columns) == ["count", "count_2", "count_3"]


def test_empty_result_keeps_its_columns():
    result = run(FakeConnection(["a", "b"], []))
    assert result.ok and result.row_count == 0 and list(result.dataframe.columns) == ["a", "b"]


def test_timeout_is_reported_and_connection_closed():
    conn = FakeConnection(raise_on_query=psycopg2.errors.QueryCanceled("canceling statement due to statement timeout"))
    result = run(conn, timeout_ms=20000)
    assert result.error_kind == "timeout" and "20 seconds" in result.error
    assert result.dataframe is None and conn.closed


def test_database_error_is_reported():
    conn = FakeConnection(raise_on_query=psycopg2.errors.UndefinedColumn('column "x" does not exist\nLINE 1: ...'))
    result = run(conn)
    assert result.error_kind == "database"
    assert result.error.endswith('column "x" does not exist')  # first line only
    assert conn.closed


def test_connection_failure_is_reported():
    def connect():
        raise psycopg2.OperationalError("could not connect to server")

    result = execute_validated(validate_sql(SQL), connect=connect)
    assert result.error_kind == "connection" and not result.ok


def test_missing_settings_are_reported():
    def connect():
        raise DatabaseConfigError("Missing database setting(s): DB_HOST.")

    result = execute_validated(validate_sql(SQL), connect=connect)
    assert result.error_kind == "connection" and "DB_HOST" in result.error
