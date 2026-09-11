"""
views/charts.py
Shared Plotly chart builders for every analytics section (patient, doctor,
admin), styled to the SmartCare palette so charts feel native to the app:
ink #0E3B36 · sage #3E7C6E · clay #E1614A · Fraunces titles, Inter labels.

Each helper RENDERS the chart (st.plotly_chart) and degrades to a quiet
caption when there's no data, so callers never need their own guards.
"""
import streamlit as st

from views.components import get_theme, THEME_TOKENS

# Palette and font colors are now derived from the active theme
# (views.components.THEME_TOKENS) rather than hardcoded, so every chart
# repaints correctly when the user flips light/dark. PALETTE/_FONT/etc.
# below are kept as the light-theme defaults for any code that imported
# them directly before this change — new code should call get_palette()
# / _layout() (which already read the live theme) instead of the bare
# constants.
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

_CONFIG = {"displayModeBar": False}


def get_palette() -> list:
    """Theme-aware chart color sequence — sage, clay, ink, success, sand,
    slate, sage-mist, alert, in that order, pulled from whichever theme
    is currently active."""
    t = THEME_TOKENS[get_theme()]
    return [t["sage"], t["clay"], t["ink"], t["success"], t["sand"], t["slate"], t["sage-mist"], t["alert"]]


def _font():
    t = THEME_TOKENS[get_theme()]
    return dict(family="Inter, sans-serif", size=13, color=t["text"])


def _title_font():
    t = THEME_TOKENS[get_theme()]
    return dict(family="Fraunces, serif", size=16, color=t["ink"])


def _grid_color():
    return "rgba(237,234,224,0.12)" if get_theme() == "dark" else "rgba(14,59,54,0.08)"


def _layout(title: str, height: int = 300) -> dict:
    return dict(
        title=dict(text=title, font=_title_font(), x=0, xanchor="left"),
        font=_font(),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=10, r=10, t=42, b=10),
        height=height,
    )


# Existing call sites across views/*.py pass these two light-mode hex
# values by name (e.g. charts.bar_chart(..., color="#E1614A")). Rather
# than touch every call site, remap the known light-mode brand hex to
# its theme-token equivalent here — so a caller asking for "clay" or
# "sage" gets the correct-for-theme shade without needing to know which
# theme is active.
_LIGHT_HEX_TO_TOKEN = {"#3E7C6E": "sage", "#E1614A": "clay"}


def _resolve_color(color: str) -> str:
    token_name = _LIGHT_HEX_TO_TOKEN.get(color)
    if token_name:
        return THEME_TOKENS[get_theme()][token_name]
    return color


def bar_chart(labels, values, title: str, color: str = "#3E7C6E"):
    """Vertical bar chart with rounded ink-on-sage styling."""
    if not labels or not any(values):
        st.caption(f"{title}: no data yet.")
        return
    import plotly.graph_objects as go

    fig = go.Figure(go.Bar(
        x=list(labels), y=list(values),
        marker=dict(color=_resolve_color(color), cornerradius=6),
        hovertemplate="%{x}: <b>%{y}</b><extra></extra>",
    ))
    fig.update_layout(**_layout(title))
    fig.update_xaxes(showgrid=False, tickfont=_font())
    fig.update_yaxes(gridcolor=_grid_color(), zeroline=False, tickfont=_font())
    st.plotly_chart(fig, use_container_width=True, config=_CONFIG)


def line_chart(x, y, title: str, series_name: str = "", color: str = "#E1614A"):
    """Smoothed line with soft area fill under it."""
    if x is None or len(x) == 0 or not any(v for v in y if v is not None):
        st.caption(f"{title}: no data yet.")
        return
    import plotly.graph_objects as go

    is_clay = color == "#E1614A"  # checked against the ORIGINAL arg, before theme remapping
    resolved_color = _resolve_color(color)
    fig = go.Figure(go.Scatter(
        x=list(x), y=list(y),
        mode="lines+markers",
        name=series_name or title,
        line=dict(color=resolved_color, width=3, shape="spline", smoothing=0.6),
        marker=dict(size=6, color=resolved_color),
        fill="tozeroy",
        fillcolor="rgba(255,130,104,0.14)" if (is_clay and get_theme() == "dark")
        else "rgba(111,191,168,0.14)" if get_theme() == "dark"
        else ("rgba(225,97,74,0.10)" if is_clay else "rgba(62,124,110,0.10)"),
        hovertemplate="%{x}: <b>%{y}</b><extra></extra>",
    ))
    fig.update_layout(**_layout(title))
    fig.update_xaxes(showgrid=False, tickfont=_font())
    fig.update_yaxes(gridcolor=_grid_color(), zeroline=False, tickfont=_font())
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
        marker=dict(colors=get_palette()[: len(labels)], line=dict(color=THEME_TOKENS[get_theme()]["surface"], width=2)),
        textinfo="label+percent",
        textfont=dict(family="Inter, sans-serif", size=12),
        hovertemplate="%{label}: <b>%{value}</b> (%{percent})<extra></extra>",
        sort=False,
    ))
    annotations = []
    if hole and center_text:
        annotations.append(dict(
            text=f"<b>{center_text}</b>", showarrow=False,
            font=dict(family="Fraunces, serif", size=20, color=THEME_TOKENS[get_theme()]["ink"]),
        ))
    layout = _layout(title)
    layout["showlegend"] = False
    layout["annotations"] = annotations
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True, config=_CONFIG)