"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Result Analyzer

Describes a query result before it is explained or charted:
- what each column is (date, identifier, label, measure, count, percent),
- which measure the answer is about,
- plain-English facts computed from the rows by code: row count, highest and lowest
  values, shares of a total, and how many rows rest on small samples.

The facts are computed here, not by the language model, so the explanation can rely
on them. In particular, the SQL step writes its caveats before any data exists; the
facts let the explanation keep a caveat the rows support and drop one they contradict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

Role = Literal["date", "identifier", "label", "measure", "count", "percent"]

# Rows whose volume is below this are small samples (matches the metadata conventions).
SMALL_SAMPLE = 30
# Volume columns that are the base of an average or rate.
SAMPLE_BASE_COLUMNS = {
    "delivered_orders", "total_reviews", "total_orders", "order_count", "orders",
    "review_count", "reviews",
}
# Shares of a total are only listed for short, complete results.
MAX_SHARE_ROWS = 30
# Rows sent to the explanation; larger results are summarised by the facts.
MAX_MODEL_ROWS = 50

PERCENT_NAME = re.compile(r"(_pct|_rate|_percent|percentage|_share|^share)$|^pct_", re.IGNORECASE)
COUNT_NAME = re.compile(
    r"(^|_)(orders|reviews|customers|sellers|items|products|count|n)$|^(count|num|number)_", re.IGNORECASE
)
AVERAGE_NAME = re.compile(r"(^|_)(avg|average|mean|median|score|rate|pct|percentile)(_|$)", re.IGNORECASE)


@dataclass
class ColumnInfo:
    name: str
    role: Role


@dataclass
class ResultProfile:
    row_count: int
    complete: bool  # False when a LIMIT or the row cap may have cut the result
    columns: list[ColumnInfo]
    primary_measure: str | None  # the numeric column the answer is about
    label_column: str | None  # the column that names each row
    date_column: str | None
    volume_column: str | None  # base of the primary measure, if it is an average or rate
    order_by: str | None = None  # first ORDER BY column of the executed query
    small_sample_rows: int = 0
    facts: list[str] = field(default_factory=list)

    def role(self, name: str) -> Role | None:
        return next((c.role for c in self.columns if c.name == name), None)


def profile_result(
    df: pd.DataFrame, sql: str = "", measure_hint: str = "", truncated: bool = False
) -> ResultProfile:
    """Profile a query result. `sql` is the executed query (used to find its LIMIT and
    ORDER BY); `measure_hint` is the SQL step's measure_used sentence."""
    columns = [ColumnInfo(name, _role(name, df[name])) for name in df.columns]
    roles = {c.name: c.role for c in columns}
    numeric = [c.name for c in columns if c.role in {"measure", "count", "percent"}]
    date_column = next((c.name for c in columns if c.role == "date"), None)
    label_column = next((c.name for c in columns if c.role in {"label", "identifier"}), None)

    limit = _top_level_limit(sql)
    complete = not truncated and (limit is None or len(df) < limit)
    order_by = _order_by_column(sql)
    primary = _primary_measure(numeric, roles, measure_hint, order_by)

    volume = None
    if primary and (roles[primary] == "percent" or AVERAGE_NAME.search(primary)):
        volume = next((n for n in numeric if n.lower() in SAMPLE_BASE_COLUMNS and n != primary), None)

    profile = ResultProfile(
        row_count=len(df), complete=complete, columns=columns, primary_measure=primary,
        label_column=label_column, date_column=date_column, volume_column=volume, order_by=order_by,
    )
    profile.facts = _facts(df, profile, limit, truncated)
    return profile


def rows_for_model(df: pd.DataFrame, max_rows: int = MAX_MODEL_ROWS) -> str:
    """The rows as compact CSV for the explanation: identifiers shortened, numbers
    rounded to 2 decimals, at most `max_rows` rows."""
    shown = df.head(max_rows).copy()
    for name in shown.columns:
        if name.lower().endswith("_id"):
            shown[name] = shown[name].map(short_label)
        elif pd.api.types.is_datetime64_any_dtype(shown[name]):
            shown[name] = shown[name].dt.strftime("%Y-%m-%d")
    return shown.to_csv(index=False, float_format="%.2f").strip()


def short_label(value) -> str:
    """Long identifiers (32-character hashes) shortened for display: 'b1b39487…'."""
    text = "" if pd.isna(value) else str(value)
    return text[:8] + "…" if len(text) > 12 and " " not in text else text


def format_number(value) -> str:
    if pd.isna(value):
        return "no value"
    value = float(value)
    return f"{value:,.0f}" if value.is_integer() else f"{value:,.2f}"


# --------------------------------------------------------------------------- helpers

def _role(name: str, series: pd.Series) -> Role:
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    if pd.api.types.is_bool_dtype(series) or not pd.api.types.is_numeric_dtype(series):
        return "identifier" if name.lower().endswith("_id") else "label"
    if "percentile" in name.lower():
        return "measure"
    if PERCENT_NAME.search(name):
        return "percent"
    if COUNT_NAME.search(name):
        return "count"
    return "measure"


def _primary_measure(numeric, roles, hint, order_by) -> str | None:
    """The column the answer is about: the ranking column when the SQL step also named
    it as a measure; otherwise the first column it named; otherwise the ranking column;
    otherwise the first non-count number."""
    if not numeric:
        return None
    hint = hint or ""
    named = {}
    for n in numeric:
        if m := re.search(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])", hint):
            named[n] = m.start()
    named_cols = sorted(named, key=named.get)
    if order_by in named_cols:
        return order_by
    if named_cols:
        return named_cols[0]
    if order_by in numeric:
        return order_by
    return next((n for n in numeric if roles[n] != "count"), numeric[0])


def _top_level_limit(sql: str) -> int | None:
    match = re.search(r"\bLIMIT\s+(\d+)\s*$", sql or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


def _order_by_column(sql: str) -> str | None:
    """First column of the last ORDER BY, without table alias or direction."""
    matches = re.findall(r"\bORDER\s+BY\s+([A-Za-z_][\w.]*)", sql or "", re.IGNORECASE)
    return matches[-1].split(".")[-1].lower() if matches else None


def _facts(df: pd.DataFrame, p: ResultProfile, limit: int | None, truncated: bool) -> list[str]:
    n = p.row_count
    facts = [f"The result has {n} row{'s' if n != 1 else ''}."]
    if n == 0:
        facts.append("No rows matched the query's filters.")
        return facts
    if truncated:
        facts.append(f"Only the first {n} rows were fetched; more rows existed.")
    elif not p.complete and limit is not None:
        facts.append(
            f"The query returned the top {limit} rows only, so these rows are a subset; "
            "do not describe totals or shares across them as marketplace-wide."
        )

    if p.date_column:
        dates = df[p.date_column].dropna()
        if not dates.empty:
            facts.append(
                f"Rows run from {dates.min():%Y-%m-%d} to {dates.max():%Y-%m-%d} ({p.date_column})."
            )

    m = p.primary_measure
    if m:
        values = df[m]
        name_col = p.label_column or p.date_column
        if n >= 2 and values.notna().any():
            hi, lo = values.idxmax(), values.idxmin()
            facts.append(
                f"Highest {m}: {format_number(values[hi])}{_row_name(df, name_col, hi)}; "
                f"lowest: {format_number(values[lo])}{_row_name(df, name_col, lo)}."
            )
        if (
            p.complete and p.role(m) == "count" and p.label_column
            and 2 <= n <= MAX_SHARE_ROWS and values.notna().all() and values.sum() > 0
        ):
            total = values.sum()
            shares = ", ".join(
                f"{short_label(df.at[i, p.label_column])} {values[i] / total * 100:.1f}%" for i in df.index
            )
            facts.append(f"Total {m} across all rows: {format_number(total)}. Share of that total: {shares}.")

    if p.volume_column:
        volumes = df[p.volume_column]
        small = int((volumes < SMALL_SAMPLE).sum())
        p.small_sample_rows = small
        if small:
            facts.append(
                f"{small} of {n} rows have fewer than {SMALL_SAMPLE} {p.volume_column} "
                f"(smallest: {format_number(volumes.min())}); their {m} values can swing on a few orders."
            )
        else:
            facts.append(
                f"Every row has at least {SMALL_SAMPLE} {p.volume_column} "
                f"(smallest: {format_number(volumes.min())}); none is a small sample."
            )
    return facts


def _row_name(df: pd.DataFrame, column: str | None, index) -> str:
    if not column:
        return ""
    value = df.at[index, column]
    if isinstance(value, pd.Timestamp):
        return f" ({value:%Y-%m-%d})"
    return f" ({short_label(value)})"
