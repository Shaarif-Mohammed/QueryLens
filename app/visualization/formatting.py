"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Number and column formatting

How figures and column names are written on screen, in one place so the table and
the charts agree: readable column titles with units ("Total revenue (BRL)",
"On-time rate (%)"), thousands separators, and dates without a time of day.
"""

from __future__ import annotations

import re

import pandas as pd

from app.analytics.analyzer import HASH_ID, ResultProfile, short_label, without_tiny_base

# Money columns by name. Percent columns are recognised first (by the analyzer's role),
# so "freight_pct_of_revenue" is a percentage, not money.
MONEY_NAME = re.compile(r"revenue|spend|clv|_value$|price|freight|order_item_total|payment_value", re.IGNORECASE)
NOT_MONEY_NAME = re.compile(r"percentile|rank|_count$|orders$|customers$|sellers$", re.IGNORECASE)
# Percentages the analyzer's name rule misses because "pct" sits mid-name.
PERCENT_ANYWHERE = re.compile(r"(^|_)pct(_|$)|_rate$|_percent$|percentage|_share$", re.IGNORECASE)
# Whole numbers that are labels or positions, written without thousands separators.
PLAIN_INTEGER_NAME = re.compile(r"year|month|period|_number$|^number_", re.IGNORECASE)


DAYS_NAME = re.compile(r"(^|_)days(_|$)", re.IGNORECASE)


def pretty(column: str) -> str:
    """'total_revenue' -> 'Total revenue'; 'churn_rate_pct' -> 'Churn rate'."""
    words = column.replace("_pct", "").replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def display_value(value) -> str:
    """A row name as people read it: hash IDs shortened ('b1b39487…'), snake_case
    category names spelled out ('health_beauty' -> 'Health beauty'), the rest as is."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return "–"
    if isinstance(value, pd.Timestamp):
        return f"{value:%b %Y}" if value.day == 1 else f"{value:%Y-%m-%d}"
    text = str(value)
    if HASH_ID.fullmatch(text):
        return short_label(text)
    if " " not in text and ("_" in text or text.islower()):  # Olist category names: health_beauty, auto
        words = text.replace("_", " ")
        return words[:1].upper() + words[1:]
    return text


def compact(column: str, value, profile: ResultProfile | None, with_unit: bool = True) -> str:
    """A figure short enough to print on a chart: 'BRL 5.77M', 'BRL 861k', '94.1%',
    '8.3 days', '26,165', '4.29'."""
    if value is None or pd.isna(value):
        return ""
    v = float(value)
    u = unit(column, profile)
    if u == "%":
        return f"{v:.1f}%"
    if DAYS_NAME.search(column):
        return f"{v:.1f} days"
    is_count = (profile is not None and profile.role(column) == "count") or (v.is_integer() and u != "BRL")
    if abs(v) >= 1e6:
        text = f"{v / 1e6:.2f}M"
    elif is_count:
        text = f"{v:,.0f}"
    elif u == "BRL" and abs(v) >= 1e4:
        text = f"{v / 1e3:.0f}k"
    elif u == "BRL" and abs(v) >= 1e3:
        text = f"{v / 1e3:.1f}k"
    elif u == "BRL" or abs(v) >= 100:
        text = f"{v:,.0f}"
    else:
        text = f"{v:.2f}"
    return f"BRL {text}" if u == "BRL" and with_unit else text


def unit(column: str, profile: ResultProfile | None) -> str | None:
    """'%' for percent columns, 'BRL' for money columns, otherwise None."""
    role = profile.role(column) if profile else None
    if role == "percent" or (role in {"measure", None} and PERCENT_ANYWHERE.search(column)):
        return "%"
    if role in {"measure", "count", None} and MONEY_NAME.search(column) and not NOT_MONEY_NAME.search(column):
        return "BRL"
    return None


def column_title(column: str, profile: ResultProfile | None) -> str:
    """'total_revenue' -> 'Total revenue (BRL)'; 'freight_pct_of_revenue' ->
    'Freight share of revenue (%)'."""
    u = unit(column, profile)
    name = re.sub(r"_pct_of_", "_share_of_", column)
    name = re.sub(r"(^|_)mom(_|$)", r"\1month_on_month\2", name)
    name = re.sub(r"(^|_)yoy(_|$)", r"\1year_on_year\2", name)
    # A unit already in the name moves to the brackets: revenue_brl -> Revenue (BRL).
    name = re.sub(r"_(brl|pct)$", "", name, flags=re.IGNORECASE)
    # month_name -> Month (month_number stays, so the two never share a title).
    name = re.sub(r"^((?:order_)?(?:month|day|week|quarter))_name$", r"\1", name)
    return pretty(name) + (f" ({u})" if u else "")


def display_table(df: pd.DataFrame, profile: ResultProfile | None):
    """The rows with readable titles and formatted figures, as a pandas Styler.
    Identifiers are shown in full so they can be copied."""
    df = without_tiny_base(df, profile)
    titles = {c: column_title(c, profile) for c in df.columns}
    shown = df.rename(columns=titles)
    formats = {}
    for column in df.columns:
        series, title = df[column], titles[column]
        if pd.api.types.is_datetime64_any_dtype(series):
            formats[title] = lambda v: "" if pd.isna(v) else f"{v:%Y-%m-%d}"
        elif pd.api.types.is_bool_dtype(series):
            continue
        elif pd.api.types.is_integer_dtype(series):
            formats[title] = "{}" if PLAIN_INTEGER_NAME.search(column) else "{:,}"
        elif pd.api.types.is_float_dtype(series):
            formats[title] = "{:,.2f}"
    return shown.style.format(formats, na_rep="–")


def format_value(column: str, value, profile: ResultProfile | None) -> str:
    """One figure as it reads on screen: 'BRL 985,491.64', '22.6%', '26,165'."""
    if value is None or pd.isna(value):
        return "–"
    u = unit(column, profile)
    if isinstance(value, pd.Timestamp):
        return f"{value:%Y-%m-%d}"
    if u == "%":
        return f"{float(value):.1f}%"
    if u == "BRL":
        return f"BRL {float(value):,.2f}"
    if float(value).is_integer():
        return f"{int(value):,}" if not PLAIN_INTEGER_NAME.search(column) else str(int(value))
    return f"{float(value):,.2f}"


def pretty_sql(sql: str) -> str:
    """SQL laid out for reading: one clause per line, one column per line. Only line
    breaks and indentation change (letter case is kept: a keyword-case option would
    also capitalise column names such as `segment`). Display only; the query that ran
    is unchanged."""
    if not sql:
        return ""
    try:
        import sqlparse

        return sqlparse.format(sql, reindent=True, indent_width=4).strip()
    except Exception:  # noqa: BLE001 — showing the original is always acceptable
        return sql


def readable_names(text: str, df: pd.DataFrame | None) -> str:
    """Spell out snake_case names from the result inside the model's prose, so the
    summary reads 'Bed bath table' rather than 'bed_bath_table'."""
    if not text or df is None or df.empty:
        return text
    names = set()
    for column in df.columns:
        if not pd.api.types.is_numeric_dtype(df[column]):
            names.update(v for v in df[column].dropna().unique()
                         if isinstance(v, str) and "_" in v and " " not in v)
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(rf"(?<![\w]){re.escape(name)}(?![\w])", display_value(name), text, flags=re.IGNORECASE)
    return text
