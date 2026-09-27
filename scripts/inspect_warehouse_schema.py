"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Warehouse Schema Inspection

Read-only inspection of the `warehouse` schema, run as the read-only role.
Writes to scripts/output/:

  warehouse_schema.txt          every table/view with columns, types, NOT NULL flags
  warehouse_columns.csv         the same column listing as CSV
  warehouse_keys_and_values.txt primary/foreign keys, row counts, and the distinct
                                values of categorical and discriminator columns

Usage (run from the project root so .env is found):
    python scripts/inspect_warehouse_schema.py
"""

import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2 import sql

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

SCHEMA = "warehouse"
OUTPUT_DIR = PROJECT_ROOT / "scripts" / "output"
MAX_VALUES = 40  # distinct values listed per column before truncating

# Categorical columns whose values are quoted in the metadata files: (object, column).
CATEGORICAL = [
    ("dim_order", "order_status"),
    ("fact_payments", "payment_type"),
    ("vw_rfm", "segment"),
    ("vw_clv", "clv_tier"),
    ("vw_clv", "rfm_segment"),
    ("vw_customer_health", "customer_status"),
    ("vw_seller_performance", "performance_tier"),
    ("vw_product_customer_affinity", "revenue_tier"),
    ("vw_pareto_analysis", "entity_type"),
    ("vw_seller_health", "metric_type"),
    ("vw_seller_health", "seller_type"),
    ("vw_seller_health", "seller_status"),
    ("ml_seller_clusters", "cluster_name"),
    ("ml_seller_clusters", "performance_tier"),
    ("ml_category_clusters", "cluster_name"),
    ("ml_review_score_feature_importance", "feature_group"),
]

# Views that return several result sets distinguished by `dimension`:
# (view, label columns to list per dimension).
DIMENSION_VIEWS = [
    ("vw_delivery_experience", ["dimension_value"]),
    ("vw_payment_behaviour", ["dimension_value", "sub_dimension"]),
    ("vw_review_response_intelligence", ["dimension_value", "sub_dimension"]),
]


def env(*names, default=None):
    """Return the first environment variable that is set."""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def connect():
    host = env("DB_HOST", "PGHOST", default="localhost")
    port = env("DB_PORT", "PGPORT", default="5432")
    dbname = env("DB_NAME", "DB_DATABASE", "PGDATABASE")
    user = env("DB_USER", "PGUSER")
    password = env("DB_PASSWORD", "DB_PASS", "PGPASSWORD")
    missing = [label for label, value in
               (("database name", dbname), ("user", user), ("password", password))
               if not value]
    if missing:
        sys.exit(f"Missing in .env: {', '.join(missing)}. "
                 "Check the DB_* variable names against your .env file.")
    conn = psycopg2.connect(host=host, port=port, dbname=dbname, user=user, password=password)
    conn.set_session(readonly=True, autocommit=True)
    return conn


# ---------------------------------------------------------------------------
# Part 1: columns
# ---------------------------------------------------------------------------

def fetch_columns(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT current_user")
        print(f"Connected as: {cur.fetchone()[0]}")
        cur.execute("SELECT table_name, table_type FROM information_schema.tables "
                    "WHERE table_schema = %s", (SCHEMA,))
        kinds = dict(cur.fetchall())
        cur.execute(
            """
            SELECT table_name, ordinal_position, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s
            ORDER BY table_name, ordinal_position
            """,
            (SCHEMA,),
        )
        rows = cur.fetchall()
    return kinds, rows


def write_columns(kinds, rows):
    with open(OUTPUT_DIR / "warehouse_columns.csv", "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["object_name", "object_type", "position", "column_name",
                         "data_type", "is_nullable"])
        for table, pos, col, dtype, nullable in rows:
            writer.writerow([table, kinds.get(table, "UNKNOWN"), pos, col, dtype, nullable])

    grouped = defaultdict(list)
    for table, pos, col, dtype, nullable in rows:
        grouped[table].append((pos, col, dtype, nullable))

    tables = sorted(t for t, k in kinds.items() if k == "BASE TABLE")
    views = sorted(t for t, k in kinds.items() if k == "VIEW")

    lines = [f"Schema: {SCHEMA}", f"{len(tables)} tables, {len(views)} views", ""]
    for heading, names in (("TABLES", tables), ("VIEWS", views)):
        lines.append(f"===== {heading} =====")
        for name in names:
            cols = grouped.get(name, [])
            lines += ["", f"{name}  ({len(cols)} columns)"]
            for pos, col, dtype, nullable in cols:
                null_flag = "" if nullable == "YES" else "  NOT NULL"
                lines.append(f"  {pos:>2}. {col:<36} {dtype}{null_flag}")
        lines.append("")
    (OUTPUT_DIR / "warehouse_schema.txt").write_text("\n".join(lines))
    return tables, views


# ---------------------------------------------------------------------------
# Part 2: keys, row counts, categorical values
# ---------------------------------------------------------------------------

def fetch_constraints(conn):
    """Primary/foreign/unique/check constraints from pg_catalog. (pg_catalog is
    readable by any role; information_schema.table_constraints would hide
    constraints from a SELECT-only role.)"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname, con.contype, con.conname, pg_get_constraintdef(con.oid)
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND con.contype IN ('p', 'f', 'u', 'c')
            ORDER BY c.relname,
                     array_position(ARRAY['p','f','u','c'], con.contype::text),
                     con.conname
            """,
            (SCHEMA,),
        )
        return cur.fetchall()


def count_rows(conn, name):
    try:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = '60s'")
            cur.execute(sql.SQL("SELECT count(*) FROM {}.{}").format(
                sql.Identifier(SCHEMA), sql.Identifier(name)))
            n = cur.fetchone()[0]
            cur.execute("SET statement_timeout = 0")
            return f"{n:,}"
    except psycopg2.Error as exc:
        return f"error: {str(exc).splitlines()[0]}"


def distinct_values(conn, obj, col):
    """[(value, count)] for one column, most frequent first."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT {c}, count(*) FROM {s}.{t} GROUP BY {c} "
                    "ORDER BY 2 DESC, 1 LIMIT %s").format(
                c=sql.Identifier(col), s=sql.Identifier(SCHEMA), t=sql.Identifier(obj)),
            (MAX_VALUES + 1,),
        )
        return cur.fetchall()


def fmt_values(rows):
    out = []
    for value, n in rows[:MAX_VALUES]:
        out.append(f"    {value!r:<42} {n:>9,} rows")
    if len(rows) > MAX_VALUES:
        out.append(f"    ... more than {MAX_VALUES} distinct values (truncated)")
    return out


def write_keys_and_values(conn, kinds):
    lines = [f"Schema: {SCHEMA}", "", "===== PRIMARY / FOREIGN / UNIQUE / CHECK CONSTRAINTS ====="]
    constraints = fetch_constraints(conn)
    by_table = defaultdict(list)
    for table, ctype, cname, cdef in constraints:
        by_table[table].append((ctype, cname, cdef))
    for table in sorted(t for t, k in kinds.items() if k == "BASE TABLE"):
        lines += ["", table]
        entries = by_table.get(table, [])
        if not entries:
            lines.append("  (no constraints found)")
        for ctype, cname, cdef in entries:
            lines.append(f"  {cdef}")
    lines.append("")

    lines += ["===== ROW COUNTS ====="]
    for name in sorted(kinds):
        lines.append(f"  {name:<40} {kinds[name]:<11} {count_rows(conn, name):>12}")
    lines.append("")

    lines += ["===== CATEGORICAL VALUES ====="]
    for obj, col in CATEGORICAL:
        lines += ["", f"{obj}.{col}"]
        if obj not in kinds:
            lines.append("    (object not found)")
            continue
        try:
            lines += fmt_values(distinct_values(conn, obj, col))
        except psycopg2.Error as exc:
            lines.append(f"    error: {str(exc).splitlines()[0]}")
    lines.append("")

    lines += ["===== DIMENSION VIEWS (result sets distinguished by `dimension`) ====="]
    for view, label_cols in DIMENSION_VIEWS:
        lines += ["", view]
        if view not in kinds:
            lines.append("    (object not found)")
            continue
        try:
            for dim, n in distinct_values(conn, view, "dimension"):
                lines.append(f"  dimension = {dim!r}  ({n:,} rows)")
                for label_col in label_cols:
                    with conn.cursor() as cur:
                        cur.execute(
                            sql.SQL("SELECT DISTINCT {l} FROM {s}.{t} WHERE dimension IS NOT DISTINCT "
                                    "FROM %s ORDER BY 1 LIMIT %s").format(
                                l=sql.Identifier(label_col), s=sql.Identifier(SCHEMA),
                                t=sql.Identifier(view)),
                            (dim, MAX_VALUES + 1),
                        )
                        vals = [r[0] for r in cur.fetchall()]
                    shown = ", ".join(repr(v) for v in vals[:MAX_VALUES])
                    more = f"  ... (more than {MAX_VALUES})" if len(vals) > MAX_VALUES else ""
                    lines.append(f"      {label_col}: {shown}{more}")
        except psycopg2.Error as exc:
            lines.append(f"    error: {str(exc).splitlines()[0]}")
    lines.append("")

    (OUTPUT_DIR / "warehouse_keys_and_values.txt").write_text("\n".join(lines))


def main():
    conn = connect()
    try:
        kinds, rows = fetch_columns(conn)
        if not rows:
            sys.exit(f"No columns found in schema '{SCHEMA}'. Check the role's grants.")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        tables, views = write_columns(kinds, rows)
        write_keys_and_values(conn, kinds)
    finally:
        conn.close()

    print(f"Found {len(tables)} tables and {len(views)} views in '{SCHEMA}'.")
    for name in ("warehouse_schema.txt", "warehouse_columns.csv", "warehouse_keys_and_values.txt"):
        print(f"Wrote: {(OUTPUT_DIR / name).relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
