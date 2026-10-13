"""Chart builders.

Every chart in the hub is built here so they read as one system rather than
as a collection of separate plots. The rules they all follow:

  * Thin marks, recessive grid and axes, no chart junk.
  * Colour by the job it does — diverging (teal/red) for polarity, the fixed
    categorical order for identity, one hue light-to-dark for magnitude.
  * Direction and explicit signs carry meaning alongside colour, so every
    chart survives greyscale and colour-blind readers.
  * Hover is on by default. An HTML chart is interactive; shipping one
    without a tooltip throws away the only advantage it has over a picture.
  * One y-axis, ever. Two measures of different scale become two charts.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from .theme import (
    BORDER_STRONG, INK_MUTED, RED, TEAL, diverging_colors, plot_layout,
    series_color,
)

MONO = dict(family="JetBrains Mono", color=INK_MUTED, size=11)


def bucket_bar_chart(bucket_df: pd.DataFrame) -> go.Figure:
    """Bucket contributions, growing left/right from a centre zero axis.

    Direction encodes sign as well as colour, so this survives greyscale.
    """
    df = bucket_df.sort_values("composite_contribution")
    fig = go.Figure(go.Bar(
        x=df["composite_contribution"], y=df["bucket"], orientation="h",
        marker_color=diverging_colors(df["composite_contribution"]),
        marker_line_width=0,
        text=[f"{v:+.3f}" for v in df["composite_contribution"]],
        textposition="outside", textfont=MONO,
        hovertemplate="%{y}: %{x:+.4f}<extra></extra>",
    ))
    fig.add_vline(x=0, line_color=BORDER_STRONG, line_width=1)
    fig.update_layout(**plot_layout(height=300))
    return fig


# Kept under the old private name: tests/test_ui.py imports it, and the chart
# it builds is unchanged.
_bucket_bar_chart = bucket_bar_chart


def translucent(hex_color: str, alpha: float) -> str:
    """`#rrggbb` + alpha -> an `rgba()` string.

    Plotly rejects 8-digit hex outright ("Invalid value of type
    'builtins.str' received for the 'fillcolor' property"), which CSS accepts
    happily — so a colour that works everywhere else in the theme takes the
    whole chart down. Converting here keeps one palette across CSS and charts
    instead of maintaining a second set of rgba literals that can drift.
    """
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def price_chart(px: pd.DataFrame, *, height: int = 280) -> go.Figure:
    """A price line, with its final point called out.

    The last close is labelled ON the chart deliberately. The header used to
    take its price from a different source — the close at the last fiscal
    quarter end — and printed a number the chart underneath plainly
    contradicted, by 27% in Microsoft's case. Both now read this same series,
    and marking the endpoint makes that agreement checkable at a glance
    instead of something the reader has to take on trust.
    """
    if px.empty:
        return go.Figure(layout=plot_layout(height=height))
    col = "adj_close" if "adj_close" in px.columns else "close"
    rising = px[col].iloc[-1] >= px[col].iloc[0]
    color = TEAL if rising else RED
    dates = pd.to_datetime(px["date"])
    fig = go.Figure(go.Scatter(
        x=dates, y=px[col], mode="lines",
        line=dict(color=color, width=2),
        fill="tozeroy", fillcolor=translucent(color, 0.08),
        hovertemplate="%{x|%b %d %Y}<br>%{y:$,.2f}<extra></extra>",
    ))

    last_x, last_y = dates.iloc[-1], float(px[col].iloc[-1])
    fig.add_trace(go.Scatter(
        x=[last_x], y=[last_y], mode="markers",
        marker=dict(size=7, color=color), showlegend=False,
        hovertemplate="last close %{x|%b %d %Y}<br>%{y:$,.2f}<extra></extra>",
    ))
    fig.add_annotation(
        x=last_x, y=last_y, text=f"${last_y:,.2f}", showarrow=False,
        xanchor="right", yanchor="bottom", yshift=8,
        font=dict(family="JetBrains Mono", size=11, color=color))

    fig.update_layout(**plot_layout(
        height=height, showlegend=False,
        yaxis=dict(gridcolor="#22375c", zerolinecolor="#33507f",
                   tickprefix="$", showgrid=True),
        xaxis=dict(gridcolor="rgba(0,0,0,0)", showgrid=False),
    ))
    return fig


def history_bars(series: pd.Series, *, height: int = 220,
                 percent: bool = False, diverging: bool = False) -> go.Figure:
    """A history of one figure, by period.

    `diverging=True` colours by sign — for quantities that are genuinely
    bidirectional (free cash flow, margins). For a quantity that is normally
    positive, a single colour is correct: colouring revenue teal every quarter
    says nothing and spends the reader's attention for free.
    """
    if series is None or series.empty:
        return go.Figure(layout=plot_layout(height=height))
    values = series.dropna()
    if values.empty:
        return go.Figure(layout=plot_layout(height=height))

    colors = (diverging_colors(values) if diverging
              else [series_color(0)] * len(values))
    fmt = ".1%" if percent else ",.0f"
    fig = go.Figure(go.Bar(
        x=[str(i) for i in values.index], y=values.values,
        marker_color=colors, marker_line_width=0,
        hovertemplate=f"%{{x}}<br>%{{y:{fmt}}}<extra></extra>",
    ))
    fig.update_layout(**plot_layout(
        height=height,
        yaxis=dict(gridcolor="#22375c", zerolinecolor="#33507f",
                   tickformat=".0%" if percent else "~s"),
        xaxis=dict(showgrid=False, tickangle=-45),
    ))
    return fig


def multi_line(frame: pd.DataFrame, *, height: int = 300,
               percent: bool = False) -> go.Figure:
    """Several series over time, coloured in the fixed categorical order.

    Never cycles: past the palette a series takes the muted ink, which reads
    as "and others" rather than as a colour that looks meaningful and isn't.
    """
    fig = go.Figure()
    for i, col in enumerate(frame.columns):
        s = frame[col].dropna()
        if s.empty:
            continue
        fig.add_trace(go.Scatter(
            x=[str(x) for x in s.index], y=s.values, mode="lines",
            name=str(col), line=dict(color=series_color(i), width=2),
            hovertemplate=f"<b>{col}</b><br>%{{x}}<br>"
                          f"%{{y:{'.1%' if percent else ',.2f'}}}<extra></extra>",
        ))
    fig.update_layout(**plot_layout(
        height=height,
        showlegend=len(frame.columns) > 1,
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    font=dict(color=INK_MUTED, size=11)),
        yaxis=dict(gridcolor="#22375c", zerolinecolor="#33507f",
                   tickformat=".0%" if percent else None),
        xaxis=dict(showgrid=False),
    ))
    return fig


def distribution_box(df: pd.DataFrame, *, group_col: str, value_col: str,
                     height: int = 520) -> go.Figure:
    """Score spread within each group. One colour — the groups are not
    separate identities to track, they are slices of one distribution."""
    fig = go.Figure()
    for name in sorted(df[group_col].dropna().unique()):
        values = df[df[group_col] == name][value_col].dropna()
        if len(values) == 0:
            continue
        fig.add_trace(go.Box(
            y=values, name=str(name), marker_color=series_color(0),
            line_color=INK_MUTED, boxpoints="all", jitter=0.3,
            pointpos=0, marker_size=5,
            hovertemplate="%{y:+.3f}<extra></extra>",
        ))
    fig.update_layout(**plot_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=140),
        yaxis=dict(title="composite score", gridcolor="#22375c",
                   zerolinecolor="#33507f"),
        xaxis=dict(tickangle=-40, gridcolor="rgba(0,0,0,0)"),
    ))
    return fig


def yield_curve(points: list[tuple[str, float]], *, height: int = 300) -> go.Figure:
    """The Treasury curve. Inversion is the thing a reader is looking for, so
    the shape is what the chart shows — no second axis, no clutter."""
    labels = [p[0] for p in points]
    values = [p[1] for p in points]
    fig = go.Figure(go.Scatter(
        x=labels, y=values, mode="lines+markers",
        line=dict(color=series_color(0), width=2),
        marker=dict(size=9, color=series_color(0)),
        hovertemplate="%{x}: %{y:.2f}%<extra></extra>",
    ))
    fig.update_layout(**plot_layout(
        height=height,
        yaxis=dict(ticksuffix="%", gridcolor="#22375c"),
        xaxis=dict(showgrid=False),
    ))
    return fig
