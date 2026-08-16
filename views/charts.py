"""
views/charts.py
Shared Plotly chart builders for every analytics section (patient, doctor,
admin), styled to the SmartCare palette so charts feel native to the app:
ink #0E3B36 · sage #3E7C6E · clay #E1614A · Fraunces titles, Inter labels.

Each helper RENDERS the chart (st.plotly_chart) and degrades to a quiet
caption when there's no data, so callers never need their own guards.
"""
import streamlit as st

PALETTE = [
    "#3E7C6E",  # sage
    "#E1614A",  # clay
    "#0E3B36",  # ink
    "#4C9A72",  # success
    "#E8A87C",  # sand
    "#5B6864",  # slate-soft
    "#9AB8AF",  # sage-mist
    "#C0503D",  # alert
]

_FONT = dict(family="Inter, sans-serif", size=13, color="#23302D")
_TITLE_FONT = dict(family="Fraunces, serif", size=16, color="#0E3B36")
_GRID = "rgba(14,59,54,0.08)"
_CONFIG = {"displayModeBar": False}


def _layout(title: str, height: int = 300) -> dict:
    return dict(
        title=dict(text=title, font=_TITLE_FONT, x=0, xanchor="left"),
        font=_FONT,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=42, b=10),
        height=height,
    )


def bar_chart(labels, values, title: str, color: str = "#3E7C6E"):
    """Vertical bar chart with rounded ink-on-sage styling."""
    if not labels or not any(values):
        st.caption(f"{title}: no data yet.")
        return
    import plotly.graph_objects as go

    fig = go.Figure(go.Bar(
        x=list(labels), y=list(values),
        marker=dict(color=color, cornerradius=6),
        hovertemplate="%{x}: <b>%{y}</b><extra></extra>",
    ))
    fig.update_layout(**_layout(title))
    fig.update_xaxes(showgrid=False, tickfont=_FONT)
    fig.update_yaxes(gridcolor=_GRID, zeroline=False, tickfont=_FONT)
    st.plotly_chart(fig, use_container_width=True, config=_CONFIG)


def line_chart(x, y, title: str, series_name: str = "", color: str = "#E1614A"):
    """Smoothed line with soft area fill under it."""
    if x is None or len(x) == 0 or not any(v for v in y if v is not None):
        st.caption(f"{title}: no data yet.")
        return
    import plotly.graph_objects as go

    fig = go.Figure(go.Scatter(
        x=list(x), y=list(y),
        mode="lines+markers",
        name=series_name or title,
        line=dict(color=color, width=3, shape="spline", smoothing=0.6),
        marker=dict(size=6, color=color),
        fill="tozeroy",
        fillcolor="rgba(225,97,74,0.10)" if color == "#E1614A" else "rgba(62,124,110,0.10)",
        hovertemplate="%{x}: <b>%{y}</b><extra></extra>",
    ))
    fig.update_layout(**_layout(title))
    fig.update_xaxes(showgrid=False, tickfont=_FONT)
    fig.update_yaxes(gridcolor=_GRID, zeroline=False, tickfont=_FONT)
    st.plotly_chart(fig, use_container_width=True, config=_CONFIG)


def pie_chart(labels, values, title: str):
    """Full pie, palette-colored, labels + percents outside."""
    _round(labels, values, title, hole=0.0)


def doughnut_chart(labels, values, title: str, center_text: str = ""):
    """Doughnut (ring) chart with an optional bold number in the middle."""
    _round(labels, values, title, hole=0.55, center_text=center_text)


def _round(labels, values, title: str, hole: float, center_text: str = ""):
    pairs = [(l, v) for l, v in zip(labels, values) if v]
    if not pairs:
        st.caption(f"{title}: no data yet.")
        return
    import plotly.graph_objects as go

    labels, values = zip(*pairs)
    fig = go.Figure(go.Pie(
        labels=list(labels), values=list(values),
        hole=hole,
        marker=dict(colors=PALETTE[: len(labels)], line=dict(color="#FFFFFF", width=2)),
        textinfo="label+percent",
        textfont=dict(family="Inter, sans-serif", size=12),
        hovertemplate="%{label}: <b>%{value}</b> (%{percent})<extra></extra>",
        sort=False,
    ))
    annotations = []
    if hole and center_text:
        annotations.append(dict(
            text=f"<b>{center_text}</b>", showarrow=False,
            font=dict(family="Fraunces, serif", size=20, color="#0E3B36"),
        ))
    layout = _layout(title)
    layout["showlegend"] = False
    layout["annotations"] = annotations
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, config=_CONFIG)
