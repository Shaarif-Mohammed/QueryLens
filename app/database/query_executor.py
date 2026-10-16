"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Query Executor

Runs a validated query against the warehouse and returns the result as a pandas
DataFrame. It is the only code in QueryLens that sends generated SQL to the database.

Safeguards, each independent of the others:
- Only a ValidationResult that passed the SQL validator is accepted, and its SQL is
  validated again here, so a hand-built or altered result cannot slip through.
- The session is read-only (see connection.py).
- A statement timeout stops long-running queries on the server.
- At most MAX_ROWS rows are fetched, whatever the query's own LIMIT.
- The transaction is always rolled back and the connection closed.

Errors never raise: they come back in ExecutionResult with a kind the interface can
turn into a plain-English message.
"""

from __future__ import annotations

import datetime as dt
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Literal

import pandas as pd
import psycopg2
import psycopg2.errors

from app.database.connection import DatabaseConfigError, get_connection
from app.validation.sql_validator import MAX_ROWS, ValidationResult, validate_sql

# Server-side limit for one query; DB_STATEMENT_TIMEOUT_MS overrides the default.
STATEMENT_TIMEOUT_MS = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "20000"))

ErrorKind = Literal["not_validated", "connection", "timeout", "database"]


@dataclass
class ExecutionResult:
    ok: bool
    dataframe: pd.DataFrame | None
    sql: str = ""  # the SQL actually sent to the database
    row_count: int = 0
    truncated: bool = False  # more rows existed than were fetched
    elapsed_seconds: float = 0.0
    error: str | None = None
    error_kind: ErrorKind | None = None


def execute_validated(
    validation: ValidationResult,
    *,
    timeout_ms: int = STATEMENT_TIMEOUT_MS,
    max_rows: int = MAX_ROWS,
    connect: Callable[[], object] = get_connection,
) -> ExecutionResult:
    """Execute the SQL of a passed validation. `connect` is replaceable for tests."""
    started = time.monotonic()

    def fail(kind: ErrorKind, message: str, sql: str = "") -> ExecutionResult:
        return ExecutionResult(
            False, None, sql=sql, elapsed_seconds=time.monotonic() - started,
            error=message, error_kind=kind,
        )

    if not isinstance(validation, ValidationResult) or not validation.ok or not validation.sql.strip():
        return fail("not_validated", "Only a query that passed the SQL validator can be executed.")
    recheck = validate_sql(validation.sql)
    if not recheck.ok:
        return fail("not_validated", "The query failed validation at execution time: " + " ".join(recheck.errors))
    sql = recheck.sql

    try:
        conn = connect()
    except DatabaseConfigError as exc:
        return fail("connection", str(exc), sql)
    except psycopg2.Error as exc:
        return fail("connection", f"Could not connect to the warehouse: {_first_line(exc)}", sql)

    try:
        with conn.cursor() as cur:
            # SET LOCAL lasts only for this transaction, so it works through
            # connection poolers that reject session-level startup options.
            cur.execute("SET LOCAL statement_timeout = %s", (int(timeout_ms),))
            cur.execute(sql)  # no parameters: psycopg2 leaves '%' in LIKE patterns untouched
            columns = [col[0] for col in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows + 1)
    except psycopg2.errors.QueryCanceled:
        return fail("timeout", f"The query was stopped after {timeout_ms / 1000:g} seconds.", sql)
    except psycopg2.OperationalError as exc:
        return fail("connection", f"The connection to the warehouse failed: {_first_line(exc)}", sql)
    except psycopg2.Error as exc:
        return fail("database", f"The warehouse rejected the query: {_first_line(exc)}", sql)
    finally:
        _close_quietly(conn)

    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    df = _to_dataframe(columns, rows)
    return ExecutionResult(
        True, df, sql=sql, row_count=len(df), truncated=truncated,
        elapsed_seconds=time.monotonic() - started,
    )


# --------------------------------------------------------------------------- helpers

def _to_dataframe(columns: list[str], rows: list[tuple]) -> pd.DataFrame:
    """Rows to a DataFrame with types pandas and Plotly handle well:
    NUMERIC (Decimal) becomes float, DATE becomes datetime64."""
    df = pd.DataFrame.from_records(rows, columns=_unique_names(columns))
    for name in df.columns:
        values = df[name].dropna()
        if values.empty or df[name].dtype != object:
            continue
        if values.map(lambda v: isinstance(v, Decimal)).all():
            df[name] = df[name].astype(float)
        elif values.map(lambda v: isinstance(v, dt.date)).all():
            df[name] = pd.to_datetime(df[name])
    return df


def _unique_names(columns: list[str]) -> list[str]:
    """Make repeated column names unique (count, count_2, ...) so every column is addressable."""
    seen: dict[str, int] = {}
    out = []
    for name in columns:
        seen[name] = seen.get(name, 0) + 1
        out.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return out


def _first_line(exc: Exception) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def _close_quietly(conn) -> None:
    for action in (conn.rollback, conn.close):
        try:
            action()
        except Exception:  # noqa: BLE001 — the connection may already be broken
            pass
