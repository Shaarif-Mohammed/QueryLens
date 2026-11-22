"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Result Analyzer

Describes a query result before it is explained or charted:
- what each column is (date, identifier, label, key, measure, count, percent),
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

Role = Literal["date", "identifier", "label", "key", "measure", "count", "percent"]

# Rows whose volume is below this are small samples (matches the metadata conventions).
SMALL_SAMPLE = 30
# Volume columns that are the base of an average or rate.
SAMPLE_BASE_COLUMNS = {
    "delivered_orders", "total_reviews", "total_orders", "order_count", "orders",
    "review_count", "reviews",
    # A retention rate rests on the customers who came back: 13 of 3,037 is 0.43%,
    # and a few customers more or less move it visibly.
    "retained_customers",
}
# Shares of a total are only listed for short, complete results.
MAX_SHARE_ROWS = 30
# Rows sent to the explanation; larger results are summarised by the facts.
MAX_MODEL_ROWS = 50

PERCENT_NAME = re.compile(r"(_pct|_rate|_percent|percentage|_share|^share)$|^pct_", re.IGNORECASE)
COUNT_NAME = re.compile(
    r"(^|_)(orders|reviews|customers|sellers|items|products|count|n)$|^(count|num|number)_", re.IGNORECASE
)
# Whole numbers that position a row rather than measure it: month or period numbers,
# years, and the number of a group the query made (quintile 1-5, band, bucket).
KEY_NAME = re.compile(
    r"(_num|_number|_no|_idx|_index)$|^(month|year|week|quarter|period|day)(_|$)"
    r"|(^|_)(month|period|week|quarter|year|quintile|quartile|decile|bucket|band|group)$",
    re.IGNORECASE,
)
# A grid (heatmap) needs two dimensions of at most this many values each.
MAX_GRID_SIDE = 30
# A pair of measures per entity is drawn as a scatter only from this many rows.
MIN_PAIR_ROWS = 15
TIME_NAME = re.compile(r"date|month|year|week|quarter|period|day", re.IGNORECASE)
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
    # Shape of the result, for the chart and the facts:
    grid: tuple[str, str] | None = None  # (row dimension, column dimension) of a two-way table
    pair: tuple[str, str] | None = None  # (x, y): two measures compared across many rows
    sort_keys: dict[str, str] = field(default_factory=dict)  # dimension -> number column giving its order
    # growth column -> row labels whose growth compares with an almost empty period
    tiny_base: dict[str, list] = field(default_factory=dict)

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
    # A LIMIT a person would choose (top 10, top 50) makes the ORDER BY a ranking; the
    # validator's LIMIT 500 does not, and then ORDER BY may only be sorting groups.
    ranked = limit is not None and limit <= 100
    primary = _primary_measure(numeric, roles, measure_hint, order_by, ranked)

    volume = None
    if primary and (roles[primary] == "percent" or AVERAGE_NAME.search(primary)):
        volume = next((n for n in numeric if n.lower() in SAMPLE_BASE_COLUMNS and n != primary), None)

    profile = ResultProfile(
        row_count=len(df), complete=complete, columns=columns, primary_measure=primary,
        label_column=label_column, date_column=date_column, volume_column=volume, order_by=order_by,
    )
    profile.sort_keys = _sort_keys(df, roles)
    profile.grid = _grid(df, roles, primary, profile.sort_keys)
    if profile.grid is None:
        profile.pair = _pair(df, roles, numeric, measure_hint, label_column, date_column)
    profile.tiny_base = growth_from_tiny_base(df)
    profile.facts = _facts(df, profile, limit, truncated)
    return profile


GROWTH_NAME = re.compile(r"(^|_)(growth|change|mom|yoy)(_|$)", re.IGNORECASE)
TINY_BASE_SHARE = 0.01  # a period under 1% of the typical one is "almost empty"


def growth_from_tiny_base(df: pd.DataFrame) -> dict[str, list]:
    """Growth figures that compare with an almost empty period.

    A growth column such as total_revenue_mom_growth_pct is paired with the column it
    is the growth of (total_revenue). The earlier period's value is implied by the two:
    value / (1 + growth / 100). Where that earlier value is under 1% of the column's
    typical (median) value, the growth is arithmetically true but says nothing (Jan 2017
    revenue grew 649,657% because Dec 2016 had about BRL 20). Returns
    {growth column: [row index, ...]}."""
    found: dict[str, list] = {}
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]
    for growth in numeric:
        if not GROWTH_NAME.search(growth):
            continue
        bases = [c for c in numeric if c != growth and growth.startswith(c + "_") and not GROWTH_NAME.search(c)]
        if not bases:
            continue
        base = max(bases, key=len)
        typical = df[base].abs().median()
        if not typical or pd.isna(typical):
            continue
        rate = 1 + df[growth] / 100
        earlier = (df[base] / rate.where(rate > 0)).abs()
        rows = list(df.index[(earlier < typical * TINY_BASE_SHARE).fillna(False)])
        if rows:
            found[growth] = rows
    return found


def without_tiny_base(df: pd.DataFrame, profile: ResultProfile | None) -> pd.DataFrame:
    """A copy with growth figures from an almost empty period blanked out."""
    if profile is None or not profile.tiny_base:
        return df
    out = df.copy()
    for column, rows in profile.tiny_base.items():
        if column in out.columns:
            out[column] = out[column].astype(float)
            out.loc[[r for r in rows if r in out.index], column] = float("nan")
    return out


def rows_for_model(df: pd.DataFrame, max_rows: int = MAX_MODEL_ROWS, profile: ResultProfile | None = None) -> str:
    """The rows as compact CSV for the explanation: identifiers shortened, numbers
    rounded to 2 decimals, at most `max_rows` rows. A two-way table is sent whole as a
    grid (one row per row dimension, one column per column dimension), so the model
    sees every cell rather than the first rows of a long list."""
    if profile is not None and profile.grid and profile.primary_measure:
        return grid_table(df, profile).to_csv(float_format="%.2f").strip()
    shown = without_tiny_base(df, profile).head(max_rows).copy()
    for name in shown.columns:
        if name.lower().endswith("_id"):
            shown[name] = shown[name].map(short_label)
        elif pd.api.types.is_datetime64_any_dtype(shown[name]):
            shown[name] = shown[name].dt.strftime("%Y-%m-%d")
    return shown.to_csv(index=False, float_format="%.2f").strip()


HASH_ID = re.compile(r"[0-9a-fA-F]{16,}")


def short_label(value) -> str:
    """Long identifiers (32-character hashes) shortened for display: 'b1b39487…'.
    Readable names such as 'bed_bath_table' are left whole."""
    text = "" if pd.isna(value) else str(value)
    return text[:8] + "…" if HASH_ID.fullmatch(text) else text


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
    if pd.api.types.is_integer_dtype(series) and KEY_NAME.search(name):
        return "key"
    if "percentile" in name.lower():
        return "measure"
    if PERCENT_NAME.search(name):
        return "percent"
    if COUNT_NAME.search(name):
        return "count"
    return "measure"


def _primary_measure(numeric, roles, hint, order_by, ranked: bool = True) -> str | None:
    """The column the answer is about. In a top-N result the ranking column wins when the
    SQL step also named it as a measure; otherwise the first column it named; otherwise
    the ranking column; otherwise the first non-count number. Without a top-N LIMIT,
    ORDER BY may only sort groups (bands, statuses), so the first named column wins.
    A ranking column named only as the basis of the selection ("review scores for the
    top 10 categories by total_revenue") is not the measure: the question was about the
    review scores."""
    if not numeric:
        return None
    hint = hint or ""
    named_cols = _named_columns(numeric, hint, all_columns=roles)
    ranking_basis_only = order_by in named_cols and named_cols[0] != order_by and bool(
        re.search(rf"\bby\s+(?:the\s+)?(?:highest\s+|lowest\s+)?{re.escape(order_by)}(?![A-Za-z0-9_])", hint, re.IGNORECASE)
    )
    if order_by in named_cols and ranked and not ranking_basis_only:
        return order_by
    if named_cols:
        return named_cols[0]
    if order_by in numeric:
        return order_by
    return next((n for n in numeric if roles[n] != "count"), numeric[0])


def _sort_keys(df: pd.DataFrame, roles: dict) -> dict[str, str]:
    """Dimensions that come with a number giving their order, one to one
    (order_month 'March' with order_month_num 3)."""
    keys = {}
    for key in (c for c, r in roles.items() if r == "key"):
        for label in (c for c, r in roles.items() if r in {"label", "identifier"}):
            pairs = df[[label, key]].drop_duplicates()
            if len(pairs) == df[label].nunique() == df[key].nunique():
                keys[label] = key
    return keys


def _dimensions(roles: dict, sort_keys: dict) -> list[str]:
    used_keys = set(sort_keys.values())
    return [c for c, r in roles.items()
            if r in {"label", "identifier", "date"} or (r == "key" and c not in used_keys)]


def _grid(df: pd.DataFrame, roles: dict, primary: str | None, sort_keys: dict) -> tuple[str, str] | None:
    """Two dimensions crossed with one measure, each pair once (category by month)."""
    dims = _dimensions(roles, sort_keys)
    if primary is None or len(dims) != 2 or len(df) < 4:
        return None
    a, b = dims
    sizes = {d: df[d].nunique() for d in dims}
    if min(sizes.values()) < 2 or max(sizes.values()) > MAX_GRID_SIDE:
        return None
    if df.duplicated(subset=dims).any() or len(df) <= max(sizes.values()):
        return None  # not a cross table (e.g. a ranking that also carries a date)

    def time_like(d):
        return roles[d] in {"date", "key"} or d in sort_keys or bool(TIME_NAME.search(d))

    if time_like(a) != time_like(b):
        column = a if time_like(a) else b
    else:
        column = a if sizes[a] >= sizes[b] else b
    row = b if column == a else a
    return row, column


def _pair(df, roles, numeric, hint, label, date) -> tuple[str, str] | None:
    """Exactly two measures named by the SQL step, compared across many named rows."""
    if label is None or date is not None or not (MIN_PAIR_ROWS <= len(df) <= 500):
        return None
    candidates = [n for n in numeric if roles[n] != "count" and n.lower() not in SAMPLE_BASE_COLUMNS]
    named = _named_columns(candidates, hint or "", all_columns=roles)
    if len(named) != 2:
        return None
    return named[0], named[1]


# Words that say how a figure is totalled or in which currency, not what it measures:
# total_revenue and revenue_brl are the same measure.
GENERIC_NAME_WORDS = {"total", "sum", "avg", "average", "mean", "brl", "value", "amount", "overall"}
# Relation names in the measure text are not columns.
RELATION_PREFIX = re.compile(r"^(vw|ml|fact|dim)_", re.IGNORECASE)
SNAKE_NAME = re.compile(r"(?<![A-Za-z0-9_.])([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?![A-Za-z0-9_])")


def _core_words(name: str) -> frozenset[str]:
    """The words that say what a column measures: total_revenue and revenue_brl -> {revenue}."""
    words = (w[:-1] if len(w) > 3 and w.endswith("s") else w for w in name.lower().split("_"))
    return frozenset(w for w in words if w and w not in GENERIC_NAME_WORDS)


def _named_columns(candidates: list[str], hint: str, all_columns=()) -> list[str]:
    """Candidate columns the SQL step's measure text names, in the order it names them.

    A column named exactly counts first. The SQL step sometimes names the measure as the
    view calls it (total_revenue) while the query returns it under its own alias
    (revenue_brl); a snake_case name in the text that is not a result column then stands
    for the one candidate column with the same core words, if there is exactly one."""
    found: dict[str, int] = {}
    for n in candidates:
        if m := re.search(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])", hint):
            found[n] = m.start()
    known = {c.lower() for c in all_columns} | {c.lower() for c in candidates}
    for m in SNAKE_NAME.finditer(hint):
        word = m.group(1)
        if word.lower() in known or RELATION_PREFIX.match(word):
            continue
        core = _core_words(word)
        if not core:
            continue
        matches = [n for n in candidates if n not in found and _core_words(n) == core]
        if len(matches) == 1:
            found[matches[0]] = m.start()
    return [n for n, _ in sorted(found.items(), key=lambda item: item[1])]


def ordered_values(df: pd.DataFrame, column: str, profile: ResultProfile) -> list:
    """A dimension's values in reading order: by its number column, by date, or as they
    first appear."""
    key = profile.sort_keys.get(column)
    if key:
        return list(df.drop_duplicates(column).sort_values(key)[column])
    if profile.role(column) in {"date", "key"}:
        return sorted(df[column].dropna().unique())
    return list(dict.fromkeys(df[column].dropna()))


def grid_table(df: pd.DataFrame, profile: ResultProfile) -> pd.DataFrame:
    """A two-way result as a table: rows by total (largest first), columns in order."""
    row, column = profile.grid
    m = profile.primary_measure
    table = df.pivot_table(index=row, columns=column, values=m, aggfunc="sum")
    table = table.reindex(columns=ordered_values(df, column, profile))
    table = table.loc[table.sum(axis=1).sort_values(ascending=False).index]
    if profile.role(column) == "date":
        table.columns = [f"{c:%Y-%m-%d}" for c in table.columns]
    table.index = [short_label(i) for i in table.index]
    table.index.name = row
    return table


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
    if m and p.grid:
        facts += _grid_facts(df, p)
    elif m:
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

    if p.pair:
        facts += _pair_facts(df, p)

    for column, rows in p.tiny_base.items():
        name_col = p.date_column or p.label_column
        where = ", ".join(_plain(df.at[i, name_col]) if name_col else f"row {i + 1}" for i in rows)
        facts.append(
            f"{column} is left blank for {where}: the period before it had almost no activity, "
            "so that growth figure is not meaningful. Do not mention or estimate it."
        )

    if m and not p.volume_column and not p.grid and p.role(m) == "count" and n >= 2:
        # The count is itself the measure (customers retained, sellers per state): rows
        # resting on a handful of people are compared on very little.
        counts = df[m].dropna()
        small = int((counts < SMALL_SAMPLE).sum())
        p.small_sample_rows = small
        if small:
            facts.append(
                f"{small} of {n} rows have fewer than {SMALL_SAMPLE} {m} (smallest: {format_number(counts.min())}); "
                f"differences between those rows rest on a handful of {m} and can change with a few."
            )

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


def _grid_facts(df: pd.DataFrame, p: ResultProfile) -> list[str]:
    row, column, m = p.grid[0], p.grid[1], p.primary_measure
    values = df[m]
    if not values.notna().any():
        return []
    hi, lo = values.idxmax(), values.idxmin()

    def cell(i):
        return f"{short_label(df.at[i, row])}, {_plain(df.at[i, column])}"

    by_column = df.groupby(column)[m].sum()
    by_row = df.groupby(row)[m].sum()
    return [
        f"The result is a two-way table: {df[row].nunique()} {row} values by {df[column].nunique()} "
        f"{column} values ({len(df)} cells). It is shown to you whole, as a grid.",
        f"Highest single cell: {format_number(values[hi])} ({cell(hi)}); "
        f"lowest single cell: {format_number(values[lo])} ({cell(lo)}).",
        f"Total {m} by {column}: highest {_plain(by_column.idxmax())} ({format_number(by_column.max())}), "
        f"lowest {_plain(by_column.idxmin())} ({format_number(by_column.min())}).",
        f"Total {m} by {row}: highest {short_label(by_row.idxmax())} ({format_number(by_row.max())}), "
        f"lowest {short_label(by_row.idxmin())} ({format_number(by_row.min())}).",
        f"These totals add up only the {df[row].nunique()} {row} values in the result; "
        "do not describe them as marketplace-wide.",
    ]


def _pair_facts(df: pd.DataFrame, p: ResultProfile) -> list[str]:
    """How the second measure moves with the first, over every row (not just the rows
    the model is shown): medians in the top and bottom quarter by the first measure."""
    x, y = p.pair
    data = df[[x, y] + ([p.label_column] if p.label_column else [])].dropna(subset=[x, y])
    n = len(data)
    if n < 8:
        return []
    q = n // 4
    ordered = data.sort_values(x)
    top, bottom = ordered.tail(q)[y].median(), ordered.head(q)[y].median()
    ymin, ymax = data[y].idxmin(), data[y].idxmax()
    name = p.label_column
    return [
        f"Across all {n} rows: the quarter with the highest {x} ({q} rows) has a median {y} of "
        f"{format_number(top)}; the quarter with the lowest {x} has a median {y} of {format_number(bottom)}.",
        f"{y} ranges from {format_number(data.at[ymin, y])}{_row_name(df, name, ymin)} "
        f"to {format_number(data.at[ymax, y])}{_row_name(df, name, ymax)}; "
        f"its median over all rows is {format_number(data[y].median())}.",
    ]


def _plain(value) -> str:
    return f"{value:%Y-%m-%d}" if isinstance(value, pd.Timestamp) else str(value)


def _row_name(df: pd.DataFrame, column: str | None, index) -> str:
    if not column:
        return ""
    value = df.at[index, column]
    if isinstance(value, pd.Timestamp):
        return f" ({value:%Y-%m-%d})"
    return f" ({short_label(value)})"
