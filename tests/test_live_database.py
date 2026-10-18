"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Live database checks

These connect to the warehouse configured in .env, so they are skipped unless asked for:

    QUERYLENS_LIVE_DB_TESTS=1 python -m pytest tests/test_live_database.py -v

They prove the protections that do not depend on the SQL validator: the session
refuses writes on its own, and the server stops a long query.
"""

import os

import psycopg2
import pytest

from app.database.connection import get_connection
from app.database.query_executor import execute_validated
from app.validation.sql_validator import validate_sql

pytestmark = pytest.mark.skipif(
    os.getenv("QUERYLENS_LIVE_DB_TESTS") != "1",
    reason="live database checks; set QUERYLENS_LIVE_DB_TESTS=1 to run",
)


def test_a_simple_query_returns_a_dataframe():
    result = execute_validated(validate_sql("SELECT COUNT(*) AS customers FROM warehouse.vw_rfm"))
    assert result.ok, result.error
    assert result.row_count == 1 and result.dataframe["customers"].iloc[0] > 0


def test_session_refuses_writes_even_without_the_validator():
    conn = get_connection()
    try:
        with conn.cursor() as cur, pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
            cur.execute("CREATE TEMP TABLE querylens_write_probe (x int)")
    finally:
        conn.rollback()
        conn.close()


def test_server_stops_a_long_query():
    # generate_series is allowed by the validator, so only the timeout limits this query.
    validation = validate_sql("SELECT COUNT(*) AS n FROM generate_series(1, 2000000000) AS g")
    assert validation.ok
    result = execute_validated(validation, timeout_ms=500)
    assert result.error_kind == "timeout"
    assert result.elapsed_seconds < 10
