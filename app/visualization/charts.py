"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Charts

Chooses a chart for a query result with fixed rules, using the analyzer's profile
(which column is the measure, which names the rows, which holds dates):

- one row per date and at least 3 rows        -> line, in date order
- a label column and 2 to 25 rows             -> horizontal bar, in the query's order
- anything else (one row, no measure, too many
  rows to read)                               -> no chart; the table is the answer

The chart always plots the same measure the explanation describes. The volume behind
each value (orders, reviews) is shown on hover. No styling is left to the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from app.analytics.analyzer import ResultProfile, short_label

MAX_BAR_ROWS = 25
MIN_LINE_POINTS = 3


@dataclass
class ChartSpec:
    kind: Literal["line", "bar"] | None
    x: str | None = None
    y: str | None = None
    reason: str = ""
    figure: go.Figure | None = None


def build_chart(df: pd.DataFrame | None, profile: ResultProfile | None, title: str = "") -> ChartSpec:
    if df is None or profile is None or df.empty:
        return ChartSpec(None, reason="No rows to chart.")
    measure = profile.primary_measure
    if not measure:
        return ChartSpec(None, reason="No numeric measure to chart.")
    if len(df) == 1:
        return ChartSpec(None, reason="A single row is clearer as a figure than as a chart.")
    if df[measure].isna().all():
        return ChartSpec(None, reason=f"{measure} has no values.")

    if _is_time_series(df, profile) and len(df) >= MIN_LINE_POINTS:
        return _line(df, profile, title)
    if profile.label_column and len(df) <= MAX_BAR_ROWS:
        return _bar(df, profile, title)
    if profile.label_column:
        return ChartSpec(None, reason=f"{len(df)} rows are too many for a readable bar chart; see the table.")
    return ChartSpec(None, reason="No column that names the rows or dates to chart against.")


def pretty(column: str) -> str:
    """'total_revenue' -> 'Total revenue'; 'churn_rate_pct' -> 'Churn rate'."""
    words = column.replace("_pct", "").replace("_", " ").strip()
    return words[:1].upper() + words[1:]


# --------------------------------------------------------------------------- chart types

def _bar(df: pd.DataFrame, profile: ResultProfile, title: str) -> ChartSpec:
    m, label = profile.primary_measure, profile.label_column
    data = df.copy()
    data[label] = _unique_labels(data[label])
    fig = px.bar(
        data, x=m, y=label, orientation="h", title=title or None,
        hover_data=[profile.volume_column] if profile.volume_column else None,
        labels={m: _axis_title(m, profile), label: pretty(label)},
    )
    # Keep the query's order: the first row at the top.
    fig.update_yaxes(categoryorder="array", categoryarray=list(reversed(list(data[label]))))
    fig.update_layout(height=max(260, 34 * len(df) + 120))
    return ChartSpec("bar", x=m, y=label, reason="A measure compared across rows.", figure=_style(fig))


def _line(df: pd.DataFrame, profile: ResultProfile, title: str) -> ChartSpec:
    x, m = profile.date_column, profile.primary_measure
    fig = px.line(
        df.sort_values(x), x=x, y=m, markers=True, title=title or None,
        hover_data=[profile.volume_column] if profile.volume_column else None,
        labels={m: _axis_title(m, profile), x: pretty(x)},
    )
    return ChartSpec("line", x=x, y=m, reason="A measure over time.", figure=_style(fig))


# --------------------------------------------------------------------------- helpers

def _is_time_series(df: pd.DataFrame, profile: ResultProfile) -> bool:
    """The rows are periods (one per date), not entities that happen to carry a date.
    'Revenue by month' is a series; 'top customers with their last order date' is not."""
    date = profile.date_column
    if not date or df[date].isna().any() or not df[date].is_unique:
        return False
    return profile.label_column is None or profile.order_by == date


def _axis_title(m: str, profile: ResultProfile) -> str:
    return pretty(m) + (" (%)" if profile.role(m) == "percent" else "")


def _style(fig: go.Figure) -> go.Figure:
    fig.update_layout(template="plotly_white", margin=dict(l=10, r=20, t=50, b=10))
    return fig


def _unique_labels(values: pd.Series) -> list[str]:
    """Short labels, kept unique so two rows never merge into one bar."""
    seen: dict[str, int] = {}
    out = []
    for value in values:
        text = short_label(value) or "(blank)"
        seen[text] = seen.get(text, 0) + 1
        out.append(text if seen[text] == 1 else f"{text} ({seen[text]})")
    return out
