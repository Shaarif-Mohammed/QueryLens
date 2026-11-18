"""
QueryLens — AI-Powered Marketplace Analytics Copilot
Charts

Chooses a chart for a query result with fixed rules, using the analyzer's profile
(which column is the measure, which names the rows, which are dimensions):

- two dimensions crossed with one measure      -> heatmap (or one line per group
  when the columns are periods and there are at most 6 groups)
- two named measures across 15+ named rows       -> scatter (point size = volume)
- one row per date, at least 3 rows              -> line (several lines when 2-4
  measures share a unit); not when the query picked its top N by something other
  than the date, which is a ranking of periods, not a series
- a label column and 2 to 25 rows                -> horizontal bar, in the query's order
  (the rows may also be named by a date or a group number such as a year or quintile)
- anything else (one row, no measure, too many
  rows to read)                                  -> no chart; the table is the answer

Every chart plots the same measure the explanation describes, prints its key values
on the chart in a compact form ("BRL 5.77M", "94.1%"), and has a tooltip with the
row's full name, the exact value and the volume behind it, flagged as a small sample
under 30. No styling is left to the model.
"""

from __future__ import annotations

import math
import numbers
from dataclasses import dataclass
from typing import Literal

import pandas as pd
import plotly.graph_objects as go

from app.analytics.analyzer import (
    AVERAGE_NAME, HASH_ID, SAMPLE_BASE_COLUMNS, SMALL_SAMPLE, ResultProfile, ordered_values,
)
from app.visualization.formatting import column_title, compact, display_value, format_value, pretty, unit

__all__ = ["ChartSpec", "build_chart", "pretty"]

MAX_BAR_ROWS = 25
MIN_LINE_POINTS = 3
MAX_LINE_GROUPS = 6
MAX_LINE_LABELS = 12  # label every point up to this many; beyond, only highest, lowest and latest
LOG_SPREAD = 50  # a scatter axis spanning more than this ratio uses a log scale

ACCENT, LIGHT, DEEP, TEXT, MUTED, PAGE = "#5B8CFF", "#A9C3FF", "#2F4F9E", "#E8ECF5", "#8A96B0", "#0A0E18"
SERIES = [(LIGHT, "solid"), (ACCENT, "dash"), (DEEP, "dot"), ("#7FA4FF", "dashdot"),
          (LIGHT, "longdash"), (ACCENT, "longdashdot")]
SEQUENTIAL = ["#121A2B", "#16213A", "#1B2A4D", "#223666", "#2F4F9E",
              "#3F66C4", "#5B8CFF", "#7FA4FF", "#A9C3FF", "#D4E1FF"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
HOVER = "%{customdata}<extra></extra>"


@dataclass
class ChartSpec:
    kind: Literal["line", "multiline", "bar", "scatter", "heatmap"] | None
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

    if profile.grid:
        row, column = profile.grid
        if _time_like(column, profile) and df[row].nunique() <= MAX_LINE_GROUPS:
            return _grouped_lines(df, profile, title)
        return _heatmap(df, profile, title)
    if profile.pair:
        return _scatter(df, profile, title)
    if _is_time_series(df, profile) and len(df) >= MIN_LINE_POINTS:
        return _line(df, profile, title)

    label = profile.label_column or _fallback_label(df, profile)
    if label and profile.role(label) == "key" and len(_same_unit_measures(df, profile)) > 1 \
            and MIN_LINE_POINTS <= len(df) <= MAX_BAR_ROWS:
        # Month 1-8 with revenue_2017 and revenue_2018: one line per measure, so the
        # comparison the query was built for is what the chart shows.
        return _lines_over_key(df, profile, title, label)
    if label and len(df) <= MAX_BAR_ROWS:
        return _bar(df, profile, title, label)
    if label:
        return ChartSpec(None, reason=f"{len(df)} rows are too many for a readable bar chart; see the table.")
    return ChartSpec(None, reason="No column that names the rows or dates to chart against.")


# --------------------------------------------------------------------------- chart types

def _bar(df: pd.DataFrame, profile: ResultProfile, title: str, label: str | None = None) -> ChartSpec:
    m, label = profile.primary_measure, label or profile.label_column
    named_by_label = label == profile.label_column
    names = _unique_labels(df[label]) if named_by_label else _unique_labels(df[label].map(_period_label))
    values = df[m]
    volume = _volume(df, profile, exclude={m})
    fig = go.Figure(go.Bar(
        x=values, y=names, orientation="h", marker=dict(color=ACCENT, line=dict(width=0)),
        text=[compact(m, v, profile) for v in values], textposition="outside", cliponaxis=False,
        textfont=dict(color=TEXT, size=12),
        customdata=[_tooltip(df, i, profile, m, volume, name=None if named_by_label else names[k])
                    for k, i in enumerate(df.index)],
        hovertemplate=HOVER,
    ))
    low, high = min(0.0, float(values.min())), max(0.0, float(values.max()))
    pad = (high - low) * 0.22 or 1
    fig.update_xaxes(title_text=column_title(m, profile), range=[low - (pad if low < 0 else 0), high + pad])
    # Keep the query's order: the first row at the top.
    fig.update_yaxes(title_text=column_title(label, profile), categoryorder="array", categoryarray=list(reversed(names)))
    fig.update_layout(height=max(260, 36 * len(df) + 110), bargap=0.32, title_text=title or None)
    return ChartSpec("bar", x=m, y=label, reason="A measure compared across rows.", figure=fig)


def _line(df: pd.DataFrame, profile: ResultProfile, title: str) -> ChartSpec:
    x, m = profile.date_column, profile.primary_measure
    data = df.sort_values(x)
    measures = _same_unit_measures(data, profile)
    fig = go.Figure()
    for k, column in enumerate(measures):
        colour, dash = SERIES[k % len(SERIES)] if len(measures) > 1 else (ACCENT, "solid")
        values = data[column]
        fig.add_trace(go.Scatter(
            x=list(data[x]), y=values, name=pretty(column), mode="lines+markers+text",
            line=dict(color=colour, width=2.5, dash=dash), marker=dict(size=6, color=colour),
            text=_point_labels(values, column, profile, latest_only=len(measures) > 1),
            textposition=["top center"] * (len(values) - 1) + ["top left"], textfont=dict(color=TEXT, size=11),
            customdata=[_tooltip(data, i, profile, column, _volume(data, profile, exclude={column}),
                                 name=display_value(data.at[i, x])) for i in data.index],
            hovertemplate=HOVER,
        ))
    fig.update_xaxes(title_text=column_title(x, profile))
    fig.update_yaxes(title_text=column_title(m, profile) if len(measures) == 1 else _unit_title(m, profile))
    fig.update_layout(height=380, showlegend=len(measures) > 1, title_text=title or None, legend=_legend())
    kind = "multiline" if len(measures) > 1 else "line"
    return ChartSpec(kind, x=x, y=m, reason="A measure over time.", figure=fig)


def _lines_over_key(df: pd.DataFrame, profile: ResultProfile, title: str, key: str) -> ChartSpec:
    """One line per measure across a group number (month 1-12, period 0-23)."""
    data = df.sort_values(key)
    measures = _same_unit_measures(data, profile)
    xs = [_period_label(v) for v in data[key]]
    fig = go.Figure()
    for k, column in enumerate(measures):
        colour, dash = SERIES[k % len(SERIES)]
        fig.add_trace(go.Scatter(
            x=xs, y=data[column], name=pretty(column), mode="lines+markers+text",
            line=dict(color=colour, width=2.5, dash=dash), marker=dict(size=6, color=colour),
            text=_point_labels(data[column], column, profile, latest_only=True), textposition="top left",
            textfont=dict(color=TEXT, size=11),
            customdata=[_tooltip(data, i, profile, column, None, name=xs[j]) for j, i in enumerate(data.index)],
            hovertemplate=HOVER,
        ))
    fig.update_xaxes(title_text=column_title(key, profile), type="category")
    fig.update_yaxes(title_text=_unit_title(profile.primary_measure, profile))
    fig.update_layout(height=380, title_text=title or None, legend=_legend())
    return ChartSpec("multiline", x=key, y=profile.primary_measure, reason="Measures compared across periods.", figure=fig)


def _grouped_lines(df: pd.DataFrame, profile: ResultProfile, title: str) -> ChartSpec:
    """One line per group (at most 6) across periods."""
    row, column = profile.grid
    m = profile.primary_measure
    periods = ordered_values(df, column, profile)
    groups = list(df.groupby(row)[m].sum().sort_values(ascending=False).index)
    fig = go.Figure()
    for k, group in enumerate(groups):
        part = df[df[row] == group].set_index(column).reindex(periods)
        colour, dash = SERIES[k % len(SERIES)]
        xs = [_period_label(p) for p in periods]
        fig.add_trace(go.Scatter(
            x=xs, y=part[m], name=display_value(group), mode="lines+markers+text",
            line=dict(color=colour, width=2.5, dash=dash), marker=dict(size=6, color=colour),
            text=_point_labels(part[m], m, profile, latest_only=True), textposition="top left",
            textfont=dict(color=TEXT, size=11),
            customdata=[f"<b>{display_value(group)} · {_period_label(p)}</b><br>"
                        f"{column_title(m, profile)}: {format_value(m, v, profile)}" for p, v in zip(periods, part[m])],
            hovertemplate=HOVER,
        ))
    fig.update_xaxes(title_text=column_title(column, profile))
    fig.update_yaxes(title_text=column_title(m, profile))
    fig.update_layout(height=400, title_text=title or None, legend=_legend())
    return ChartSpec("multiline", x=column, y=m, reason="A measure over time, one line per group.", figure=fig)


def _scatter(df: pd.DataFrame, profile: ResultProfile, title: str) -> ChartSpec:
    x, y = profile.pair
    label = profile.label_column
    data = df.dropna(subset=[x, y])
    volume = _volume(data, profile, exclude={x, y}, any_count=True)
    sizes = 9
    if volume:
        root = data[volume].clip(lower=0).map(math.sqrt)
        span = float(root.max() - root.min()) or 1.0
        sizes = list(7 + (root - root.min()) / span * 17)
    show_names = len(data) <= 15
    fig = go.Figure(go.Scatter(
        x=data[x], y=data[y], mode="markers+text" if show_names else "markers",
        marker=dict(size=sizes, color=ACCENT, opacity=0.62, line=dict(color=LIGHT, width=0.6)),
        text=[display_value(v) for v in data[label]] if show_names else None,
        textposition="top center", textfont=dict(color=TEXT, size=11),
        customdata=[_pair_tooltip(data, i, profile, x, y, volume) for i in data.index], hovertemplate=HOVER,
    ))
    xs = data[x]
    spread = xs.min() > 0 and xs.max() / xs.min() > LOG_SPREAD
    if spread:  # a few large values would squeeze the rest against the axis
        fig.update_xaxes(type="log", title_text=column_title(x, profile)[:-1] + ", log scale)"
                         if column_title(x, profile).endswith(")") else column_title(x, profile) + " (log scale)")
    else:
        fig.update_xaxes(title_text=column_title(x, profile))
    fig.update_yaxes(title_text=column_title(y, profile))
    note = f"Point size: {pretty(volume).lower()}" if volume else ""
    fig.update_layout(height=420, title_text=title or None,
                      annotations=[dict(text=note, x=1, y=1.04, xref="paper", yref="paper", showarrow=False,
                                        xanchor="right", font=dict(color=MUTED, size=11))] if note else [])
    return ChartSpec("scatter", x=x, y=y, reason="Two measures compared across many rows.", figure=fig)


def _heatmap(df: pd.DataFrame, profile: ResultProfile, title: str) -> ChartSpec:
    row, column = profile.grid
    m = profile.primary_measure
    columns = ordered_values(df, column, profile)
    rows = list(df.groupby(row)[m].sum().sort_values(ascending=False).index)
    table = df.pivot_table(index=row, columns=column, values=m, aggfunc="sum").reindex(index=rows, columns=columns)
    # Plotly draws the first row at the bottom; reverse so the largest total is on top.
    table = table.iloc[::-1]
    z = table.to_numpy()
    xs = [_period_label(c) for c in columns]
    ys = [display_value(r) for r in table.index]
    finite = pd.Series(z.ravel()).dropna()
    low, high = float(finite.min()), float(finite.max())
    hover = [[f"<b>{display_value(r)} · {_period_label(c)}</b><br>{column_title(m, profile)}: "
              f"{format_value(m, table.at[r, c], profile)}" for c in columns] for r in table.index]
    fig = go.Figure(go.Heatmap(
        z=z, x=xs, y=ys, colorscale=[[i / (len(SEQUENTIAL) - 1), c] for i, c in enumerate(SEQUENTIAL)],
        xgap=2, ygap=2, customdata=hover, hovertemplate=HOVER,
        colorbar=dict(title=dict(text=column_title(m, profile), side="right"), thickness=10, outlinewidth=0,
                      tickfont=dict(color=MUTED, size=10)),
    ))
    # Value in every cell, dark on light cells and light on dark ones.
    for yi, r in enumerate(table.index):
        for xi, c in enumerate(columns):
            v = table.at[r, c]
            if pd.isna(v):
                continue
            share = (float(v) - low) / ((high - low) or 1)
            fig.add_annotation(x=xs[xi], y=ys[yi], text=compact(m, v, profile, with_unit=False), showarrow=False,
                               font=dict(size=10, color=PAGE if share > 0.62 else TEXT))
    fig.update_xaxes(title_text=column_title(column, profile), side="bottom", showgrid=False)
    fig.update_yaxes(title_text=column_title(row, profile), showgrid=False)
    fig.update_layout(height=max(320, 34 * len(rows) + 140), title_text=title or None)
    return ChartSpec("heatmap", x=column, y=row, reason="A measure across two dimensions.", figure=fig)


# --------------------------------------------------------------------------- helpers

def _is_time_series(df: pd.DataFrame, profile: ResultProfile) -> bool:
    """The rows are periods (one per date), not entities that happen to carry a date.
    'Revenue by month' is a series; 'top customers with their last order date' is not;
    neither is 'the 10 cohorts that kept the most customers', a top N picked by a measure:
    a line through it would join months that are not next to each other."""
    date = profile.date_column
    if not date or df[date].isna().any() or not df[date].is_unique:
        return False
    if not profile.complete and profile.order_by not in (None, date):
        return False
    return profile.label_column is None or profile.order_by == date


def _fallback_label(df: pd.DataFrame, profile: ResultProfile) -> str | None:
    """Something to name the bars by when no text column does: a date column (a ranking
    of periods) or a group number the query made (a year, a quintile)."""
    date = profile.date_column
    if date and df[date].notna().all() and df[date].is_unique:
        return date
    keys = [c.name for c in profile.columns if c.role == "key" and c.name not in profile.sort_keys.values()]
    if len(keys) == 1 and df[keys[0]].notna().all() and df[keys[0]].is_unique:
        return keys[0]
    return None


def _time_like(column: str, profile: ResultProfile) -> bool:
    return profile.role(column) in {"date", "key"} or column in profile.sort_keys


def _same_unit_measures(df: pd.DataFrame, profile: ResultProfile) -> list[str]:
    """The main measure plus up to three more of the same kind (all money, all %), for
    several lines on one axis. Counts and volumes stay in the tooltip. Totals and
    averages are not mixed: monthly revenue (about 1 million) and average order value
    (about 150) share a unit but not a scale."""
    m = profile.primary_measure
    kind = (unit(m, profile), profile.role(m), bool(AVERAGE_NAME.search(m)))
    others = [c.name for c in profile.columns
              if c.name != m and c.role in {"measure", "percent"} and c.name.lower() not in SAMPLE_BASE_COLUMNS
              and (unit(c.name, profile), c.role, bool(AVERAGE_NAME.search(c.name))) == kind
              and "growth" not in c.name.lower()]
    return [m] + others[:3]


def _volume(df: pd.DataFrame, profile: ResultProfile, exclude: set, any_count: bool = False) -> str | None:
    """The volume behind a value (orders, reviews), for the tooltip or point size."""
    if profile.volume_column and profile.volume_column in df.columns and profile.volume_column not in exclude:
        return profile.volume_column
    for c in profile.columns:
        if c.name in exclude or c.name not in df.columns:
            continue
        if c.name.lower() in SAMPLE_BASE_COLUMNS or (any_count and c.role == "count"):
            return c.name
    return None


def _volume_line(df: pd.DataFrame, i, volume: str | None, profile: ResultProfile) -> str:
    if not volume or pd.isna(df.at[i, volume]):
        return ""
    n = df.at[i, volume]
    note = " · small sample" if n < SMALL_SAMPLE else ""
    return f"<br>{pretty(volume)}: {format_value(volume, n, profile)}{note}"


def _tooltip(df, i, profile, measure, volume, name: str | None = None) -> str:
    label = profile.label_column
    if name is None:
        raw = df.at[i, label] if label else ""
        # Seller and customer IDs in full so they can be copied; other names readable.
        name = str(raw) if HASH_ID.fullmatch(str(raw)) else display_value(raw)
    return (f"<b>{name}</b><br>{column_title(measure, profile)}: {format_value(measure, df.at[i, measure], profile)}"
            + _volume_line(df, i, volume, profile))


def _pair_tooltip(df, i, profile, x, y, volume) -> str:
    raw = df.at[i, profile.label_column]
    name = str(raw) if HASH_ID.fullmatch(str(raw)) else display_value(raw)
    return (f"<b>{name}</b><br>{column_title(x, profile)}: {format_value(x, df.at[i, x], profile)}"
            f"<br>{column_title(y, profile)}: {format_value(y, df.at[i, y], profile)}"
            + _volume_line(df, i, volume, profile))


def _point_labels(values: pd.Series, column: str, profile: ResultProfile, latest_only: bool = False) -> list[str]:
    """Every point up to 12; beyond that only the highest, lowest and latest."""
    values = list(values)
    labels = [""] * len(values)
    present = [k for k, v in enumerate(values) if not pd.isna(v)]
    if not present:
        return labels
    if latest_only:
        keep = {present[-1]}
    elif len(values) <= MAX_LINE_LABELS:
        keep = set(present)
    else:
        keep = {max(present, key=lambda k: values[k]), min(present, key=lambda k: values[k]), present[-1]}
    for k in keep:
        labels[k] = compact(column, values[k], profile)
    return labels


def _period_label(value) -> str:
    if isinstance(value, pd.Timestamp):
        return f"{value:%b %Y}" if value.day == 1 else f"{value:%Y-%m-%d}"
    if isinstance(value, str) and value in MONTHS:
        return value[:3]
    if isinstance(value, numbers.Real) and not isinstance(value, bool) and float(value).is_integer():
        return str(int(value))  # a year or group number: 2018, not 2,018
    return display_value(value)


def _unit_title(m: str, profile: ResultProfile) -> str:
    u = unit(m, profile)
    return {"BRL": "BRL", "%": "%"}.get(u, "")


def _legend() -> dict:
    return dict(orientation="h", y=1.08, x=0, font=dict(color=MUTED, size=12), bgcolor="rgba(0,0,0,0)")


def _unique_labels(values: pd.Series) -> list[str]:
    """Readable labels, kept unique so two rows never merge into one bar."""
    seen: dict[str, int] = {}
    out = []
    for value in values:
        text = display_value(value) or "(blank)"
        seen[text] = seen.get(text, 0) + 1
        out.append(text if seen[text] == 1 else f"{text} ({seen[text]})")
    return out
