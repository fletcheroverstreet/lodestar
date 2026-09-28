"""Design tokens, CSS, and components for the hub.

Direction: a professional financial terminal in deep navy — bold, generously
rounded, confident with space. The reference point is a real trading desk
product, not a dashboard template.

WHAT KEEPS IT FROM LOOKING CHEAP. Each of these is a specific failure mode:

  * One surface treatment. Every panel is the same navy, the same border, the
    same radius. Mixed card styles are the single clearest tell of a UI
    assembled from tutorials.
  * A strict spacing rhythm — 4/8/12/16/24/32px, nothing in between.
  * Hierarchy carried by SIZE and WEIGHT, not by colour. Two text colours plus
    a faint one; nothing is coloured for decoration.
  * Every figure in a tabular monospace, so columns align down the page.
  * No gradients, no glow, and no shadow on anything containing data.

COLOUR IS COMPUTED, NOT CHOSEN. Every value below was validated with the
data-viz validator against THIS surface (#10203a), not against a generic dark
grey — contrast and lightness results are only meaningful against the surface
the thing actually renders on. Results are recorded inline so a future edit
can tell what it is breaking.

The one deliberate deviation is documented at DIVERGING below: bringing the
positive teal into the categorical lightness band collapses its
colour-blind separation from ΔE 14.0 to 3.9. The lightness spread is load-
bearing, not an oversight, and the band check is scoped to categorical
palettes in the first place.

Colour is never the only signal regardless: signed values always carry an
explicit +/−, bars grow left or right from a zero axis, and ratings ship as
words. The tables read correctly in greyscale.
"""

from __future__ import annotations

import html as _html
import json

# ---------------------------------------------------------------------------
# Surfaces and ink
# ---------------------------------------------------------------------------
NAVY_DEEP = "#0a1628"     # page background
NAVY = "#10203a"          # panels and cards — THE surface everything is validated against
NAVY_RAISED = "#17294a"   # table headers, hover, inset wells
NAVY_HIGH = "#1d3358"     # active/selected
BORDER = "#22375c"        # hairline separators
BORDER_STRONG = "#33507f"

INK = "#e6edf7"           # 13.8:1 on NAVY
INK_MUTED = "#8ba0bf"     # 6.1:1
INK_FAINT = "#5f76a0"     # 3.6:1 — de-emphasised rank numbers only, never body text

# ---------------------------------------------------------------------------
# DIVERGING — positive / negative. The most-used colours in the product.
#
# Validated on #10203a: CVD ΔE 14.0 (deutan, target >= 8), normal-vision
# ΔE 35.8 (floor 15), contrast 8.8:1 and 5.0:1 (floor 3:1).
#
# These sit ABOVE the categorical lightness band, deliberately. Stepping teal
# down into the band drops CVD separation to 6.5 (WARN) or 3.9 (FAIL) —
# equalising lightness removes the very channel that makes a red/green-family
# pair safe for the most common form of colour blindness. The band exists so
# no series in a CATEGORICAL palette dominates; a diverging pair is not
# categorical, and here the lightness difference is a second encoding rather
# than an imbalance.
# ---------------------------------------------------------------------------
TEAL = "#2dd4bf"
TEAL_DIM = "#1fa896"
TEAL_WASH = "#0e2a2a"
RED = "#ff4d5a"
RED_DIM = "#c93742"
RED_WASH = "#2a1620"
GREY = "#8ba0bf"
GREY_WASH = "#1a2b47"

# ---------------------------------------------------------------------------
# CATEGORICAL — for multi-series charts (sector lines, peer comparisons).
#
# The documented dark palette, validated as a set against #10203a:
#   lightness band PASS · chroma floor PASS · contrast PASS
#   worst adjacent CVD ΔE 8.4 (target 8) · worst normal-vision ΔE 19.3 (floor 15)
#
# ASSIGN IN THIS ORDER AND NEVER CYCLE. The order is the colour-blind-safety
# mechanism, not an aesthetic preference: it was chosen from the orderings
# that clear every adjacent gate. A 9th series is not a generated 9th hue —
# it folds into "Other", or the chart becomes small multiples.
#
# Scatter/bubble forms compare every pair rather than neighbours, and the full
# eight cannot clear the floors that way. Cap those at the FIRST THREE slots.
# ---------------------------------------------------------------------------
CATEGORICAL = [
    "#3987e5",  # 1 blue
    "#d95926",  # 2 orange
    "#199e70",  # 3 aqua
    "#c98500",  # 4 yellow
    "#d55181",  # 5 magenta
    "#008300",  # 6 green
    "#9085e9",  # 7 violet
    "#e66767",  # 8 red
]
CATEGORICAL_ALL_PAIRS_MAX = 3   # scatter/bubble/map cap — see above

# Sequential, one hue light->dark, for magnitude (heatmaps, intensity).
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

# ---------------------------------------------------------------------------
# STATUS — reserved. Never reused as "series 4", always shipped with a word.
# Contrast on #10203a: 4.9 / 8.9 / 6.2 / 3.4.
# ---------------------------------------------------------------------------
STATUS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}

FONT_UI = "'Inter', -apple-system, 'Segoe UI', system-ui, sans-serif"
FONT_MONO = "'JetBrains Mono', 'SF Mono', 'Consolas', monospace"

GOOGLE_FONTS_URL = (
    "https://fonts.googleapis.com/css2?"
    "family=Inter:wght@400;500;600;700;800&"
    "family=JetBrains+Mono:wght@400;500;600;700&"
    "display=swap"
)

# Rating label -> (text colour, background wash, border).
RATING_COLORS: dict[str, tuple[str, str]] = {
    "STRONG BUY": (TEAL, TEAL_WASH),
    "BUY": (TEAL, TEAL_WASH),
    "HOLD": (GREY, GREY_WASH),
    "SELL": (RED, RED_WASH),
    "STRONG SELL": (RED, RED_WASH),
    "INSUFFICIENT DATA": (INK_FAINT, GREY_WASH),
}

# Spacing scale. Nothing between these values.
SPACE = {"xs": "4px", "sm": "8px", "md": "12px", "lg": "16px",
         "xl": "24px", "xxl": "32px"}
RADIUS = {"sm": "8px", "md": "12px", "lg": "16px", "pill": "999px"}

# Breakpoints. Phone first, because the user asked for phone AND desktop and
# the failure mode there is a table that forces the whole page sideways.
PHONE_MAX = 640
TABLET_MAX = 1024


def series_color(i: int) -> str:
    """Colour for series `i`, assigned in fixed order and never cycled.

    Past the palette, callers get the muted ink rather than a generated hue —
    a visible "these are all Other" signal instead of a colour that looks
    meaningful and isn't.
    """
    return CATEGORICAL[i] if 0 <= i < len(CATEGORICAL) else INK_MUTED


def inject_css() -> str:
    """The full <style> block.

    `!important` throughout: Streamlit generates Emotion class selectors that
    outrank plain element and attribute selectors regardless of injection
    order. Font-family is repeated on every custom class rather than
    inherited, because Streamlit's markdown containers carry their own
    class-based font rule.
    """
    return f"""
<style>
@import url('{GOOGLE_FONTS_URL}');

/* ---------------------------------------------------------------- base --- */
html, body, [class*="css"], .stApp, .stMarkdown, p, span, div, label, button {{
    font-family: {FONT_UI} !important;
    -webkit-font-smoothing: antialiased;
}}
html, body, .stApp {{ color: {INK} !important; }}
.stApp {{ background-color: {NAVY_DEEP} !important; }}

[data-testid="stSidebar"], [data-testid="collapsedControl"] {{ display: none !important; }}
[data-testid="stHeader"] {{ background: transparent !important; height: 0 !important; }}
#MainMenu, footer {{ visibility: hidden; }}

.block-container {{
    padding: {SPACE['xl']} {SPACE['xl']} {SPACE['xxl']} {SPACE['xl']} !important;
    max-width: 1680px !important;
}}

/* ------------------------------------------------------------ typography --- */
h1, h2, h3, h4, h5, h6 {{
    font-family: {FONT_UI} !important;
    color: {INK} !important;
    letter-spacing: -0.02em !important;
    text-transform: none !important;
}}
h1 {{ font-size: 2rem !important;    font-weight: 800 !important; margin: 0 0 {SPACE['sm']} 0 !important; }}
h2 {{ font-size: 1.4rem !important;  font-weight: 700 !important; margin: {SPACE['xl']} 0 {SPACE['md']} 0 !important; }}
h3 {{ font-size: 1.1rem !important;  font-weight: 700 !important; margin: {SPACE['lg']} 0 {SPACE['sm']} 0 !important; }}
h6 {{
    font-size: 0.72rem !important; font-weight: 700 !important;
    color: {INK_MUTED} !important; text-transform: uppercase !important;
    letter-spacing: 0.09em !important; margin: 0 0 {SPACE['sm']} 0 !important;
}}
p, .stMarkdown p {{ color: {INK} !important; font-size: 0.92rem; line-height: 1.6; }}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{
    color: {INK_MUTED} !important; font-size: 0.8rem !important; line-height: 1.55;
}}
.ls-mono {{ font-family: {FONT_MONO} !important; font-variant-numeric: tabular-nums !important; }}

/* --------------------------------------------------------------- header --- */
.ls-brandbar {{
    display: flex; align-items: center; gap: {SPACE['md']}; flex-wrap: wrap;
    padding-bottom: {SPACE['lg']}; margin-bottom: {SPACE['xs']};
    border-bottom: 2px solid {BORDER};
}}
.ls-wordmark {{
    font-family: {FONT_UI} !important;
    font-size: 1.75rem; font-weight: 800; letter-spacing: -0.04em;
    color: {INK}; line-height: 1;
}}
.ls-wordmark span {{ color: {TEAL}; }}
.ls-context {{
    font-family: {FONT_MONO} !important;
    font-size: 0.75rem; color: {INK_FAINT}; letter-spacing: 0.01em;
}}

/* ------------------------------------------------------------- nav tabs ---
   A row of plain anchors, not a widget. Each is a link to ?page=... , which
   is the same navigation every clickable ticker in the product already uses,
   so a page stays linkable and bookmarkable — and only the page you are on
   renders. `st.tabs` would render every section's contents on every load,
   which for nine pages including a 500-row screener is not a trade worth
   making for a nicer-looking control.

   The active tab is marked by a solid underline sitting ON the bar's own
   bottom border, so the selected section reads as attached to the content
   below it rather than as a highlighted button floating above it. */
.ls-tabs {{
    display: flex; align-items: stretch; gap: {SPACE['xs']};
    margin: 0 0 {SPACE['xl']} 0;
    border-bottom: 1px solid {BORDER};
    overflow-x: auto; overflow-y: hidden;
    scrollbar-width: none; -ms-overflow-style: none;
}}
.ls-tabs::-webkit-scrollbar {{ display: none; }}
.ls-tab {{
    font-family: {FONT_UI} !important;
    font-size: 0.85rem; font-weight: 600; letter-spacing: -0.005em;
    color: {INK_MUTED}; text-decoration: none !important;
    padding: {SPACE['md']} {SPACE['lg']};
    border-bottom: 2px solid transparent;
    margin-bottom: -1px;            /* overlap the bar's own hairline */
    white-space: nowrap;
    transition: color 120ms ease, border-color 120ms ease;
}}
.ls-tab:hover {{ color: {INK}; border-bottom-color: {BORDER_STRONG}; }}
.ls-tab-active {{
    color: {INK}; font-weight: 700;
    border-bottom-color: {TEAL};
}}
.ls-tab-active:hover {{ border-bottom-color: {TEAL}; }}

/* ------------------------------------------------- pick-a-company grid --- */
.ls-picks {{
    display: grid; gap: {SPACE['sm']};
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    margin: {SPACE['sm']} 0 {SPACE['md']} 0;
}}
.ls-pick {{
    display: flex; align-items: center; gap: {SPACE['sm']};
    background: {NAVY}; border: 1px solid {BORDER};
    border-radius: {RADIUS['md']};
    padding: {SPACE['sm']} {SPACE['md']};
    text-decoration: none !important;
    transition: border-color 120ms ease, background 120ms ease;
}}
.ls-pick:hover {{ border-color: {BORDER_STRONG}; background: {NAVY_RAISED}; }}
.ls-pick-sym {{
    font-family: {FONT_MONO} !important; font-size: 0.88rem; font-weight: 700;
    color: {INK}; flex: 1 1 auto;
}}
.ls-pick-pct {{
    font-family: {FONT_MONO} !important; font-size: 0.78rem;
    color: {INK_FAINT}; font-variant-numeric: tabular-nums;
}}

/* ----------------------------------------------------- one panel treatment --- */
.ls-card {{
    background: {NAVY};
    border: 1px solid {BORDER};
    border-radius: {RADIUS['lg']};
    padding: {SPACE['lg']} {SPACE['xl']};
    height: 100%;
}}
.ls-card-label {{
    font-family: {FONT_UI} !important;
    font-size: 0.72rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.08em; color: {INK_MUTED}; margin-bottom: {SPACE['sm']};
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}}
.ls-card-value {{
    font-family: {FONT_MONO} !important;
    font-size: 1.6rem; font-weight: 700; margin-top: {SPACE['xs']};
    font-variant-numeric: tabular-nums; letter-spacing: -0.02em;
}}
.ls-card-note {{
    font-family: {FONT_UI} !important;
    font-size: 0.75rem; color: {INK_MUTED}; margin-top: {SPACE['sm']};
}}

/* The one genuinely large element: the hero score. Proportional figures,
   per the type rule — tabular is for columns that must align. */
.ls-score {{
    font-family: {FONT_MONO} !important;
    font-size: 3.75rem; font-weight: 700; line-height: 1;
    color: {INK}; letter-spacing: -0.045em;
}}
.ls-score-sub {{
    font-family: {FONT_UI} !important;
    font-size: 0.8rem; color: {INK_MUTED}; margin-top: {SPACE['sm']};
}}

/* ------------------------------------------------------------ stat strip --- */
.ls-stats {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 1px; background: {BORDER};
    border: 1px solid {BORDER}; border-radius: {RADIUS['lg']}; overflow: hidden;
    margin-bottom: {SPACE['xl']};
}}
.ls-stat {{ background: {NAVY}; padding: {SPACE['lg']} {SPACE['lg']}; }}
.ls-stat-label {{
    font-family: {FONT_UI} !important;
    font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.09em; color: {INK_MUTED}; margin-bottom: {SPACE['xs']};
}}
.ls-stat-value {{
    font-family: {FONT_MONO} !important;
    font-size: 1.35rem; font-weight: 700; color: {INK};
    font-variant-numeric: tabular-nums; line-height: 1.15; letter-spacing: -0.02em;
}}
.ls-stat-note {{ font-size: 0.7rem; color: {INK_FAINT}; margin-top: 2px; }}

/* ----------------------------------------------------------------- chips --- */
.ls-chip {{
    font-family: {FONT_UI} !important;
    display: inline-block; padding: 4px 12px; border-radius: {RADIUS['pill']};
    font-size: 0.72rem; font-weight: 700; letter-spacing: 0.04em;
    white-space: nowrap; border: 1px solid transparent;
}}
.ls-pill {{
    font-family: {FONT_UI} !important;
    display: inline-block; padding: 3px 10px; border-radius: {RADIUS['pill']};
    font-size: 0.68rem; font-weight: 600; letter-spacing: 0.03em;
    background: {NAVY_RAISED}; color: {INK_MUTED}; border: 1px solid {BORDER};
    white-space: nowrap;
}}

/* -------------------------------------------------------- signed numbers --- */
.ls-pos  {{ color: {TEAL} !important; font-family: {FONT_MONO} !important; font-weight: 600; font-variant-numeric: tabular-nums; }}
.ls-neg  {{ color: {RED}  !important; font-family: {FONT_MONO} !important; font-weight: 600; font-variant-numeric: tabular-nums; }}
.ls-flat {{ color: {INK_MUTED} !important; font-family: {FONT_MONO} !important; font-weight: 500; font-variant-numeric: tabular-nums; }}

/* ----------------------------------------------------------------- table ---
   Hand-rendered rather than st.dataframe: recent Streamlit renders that
   widget to a canvas, so CSS cannot reach the cells at all — no alignment
   control, no inline bars, no chips, no typography. Building the table as
   HTML is the only way to get figures right-aligned in a tabular monospace
   next to inline visual encoding. Sorting is restored in JS below. */
.ls-table-wrap {{
    border: 1px solid {BORDER}; border-radius: {RADIUS['lg']}; overflow: auto;
    background: {NAVY}; -webkit-overflow-scrolling: touch;
}}
table.ls-table {{
    width: 100%; border-collapse: collapse;
    font-family: {FONT_UI} !important; font-size: 0.85rem;
}}
table.ls-table thead th {{
    position: sticky; top: 0; z-index: 2;
    background: {NAVY_RAISED}; color: {INK_MUTED};
    font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.07em; text-align: left;
    padding: {SPACE['md']} {SPACE['md']}; white-space: nowrap;
    border-bottom: 2px solid {BORDER}; cursor: pointer; user-select: none;
}}
table.ls-table thead th:hover {{ color: {INK}; background: {NAVY_HIGH}; }}
table.ls-table thead th.num {{ text-align: right; }}
table.ls-table thead th::after {{
    content: ""; display: inline-block; width: 0.7em; opacity: 0.5;
}}
table.ls-table thead th[data-dir="asc"]::after  {{ content: " ▲"; opacity: 1; }}
table.ls-table thead th[data-dir="desc"]::after {{ content: " ▼"; opacity: 1; }}
table.ls-table tbody td {{
    padding: {SPACE['md']} {SPACE['md']}; border-bottom: 1px solid {BORDER};
    color: {INK}; white-space: nowrap;
}}
table.ls-table tbody tr:last-child td {{ border-bottom: none; }}
table.ls-table tbody tr:hover td {{ background: {NAVY_RAISED}; }}
table.ls-table td.num {{
    font-family: {FONT_MONO} !important; text-align: right;
    font-variant-numeric: tabular-nums;
}}
table.ls-table td.muted {{ color: {INK_MUTED}; }}
table.ls-table td.rank {{
    font-family: {FONT_MONO} !important; color: {INK_FAINT};
    text-align: right; width: 3rem;
}}
table.ls-table td.sym {{ font-weight: 700; letter-spacing: 0.01em; }}

/* Clickable tickers. Every symbol in the product links to its own company
   page, so a name in any table is one click from everything known about it.
   Styled as a real affordance -- underline on hover, pointer cursor -- rather
   than relying on colour alone to say "this is a link". */
a.ls-ticker {{
    color: {INK} !important; font-weight: 700; text-decoration: none;
    border-bottom: 1px dotted {BORDER_STRONG}; padding-bottom: 1px;
    white-space: nowrap;
}}
a.ls-ticker:hover {{
    color: {TEAL} !important; border-bottom-color: {TEAL};
}}

/* Inline bar: a second, redundant encoding of the same number beside it, so
   a table scans without reading every digit. Direction carries the sign, so
   it survives greyscale. */
.ls-bar-track {{
    display: inline-block; vertical-align: middle;
    width: 72px; height: 8px; background: {NAVY_RAISED};
    border-radius: {RADIUS['sm']}; position: relative; overflow: hidden;
}}
.ls-bar-fill {{ position: absolute; top: 0; height: 8px; border-radius: 3px; }}

/* ------------------------------------------------------------------ tabs --- */
.stTabs [data-baseweb="tab-list"] {{
    gap: {SPACE['xs']}; border-bottom: 2px solid {BORDER};
    margin-bottom: {SPACE['xl']}; overflow-x: auto; scrollbar-width: none;
}}
.stTabs [data-baseweb="tab-list"]::-webkit-scrollbar {{ display: none; }}
.stTabs [data-baseweb="tab"] {{
    font-family: {FONT_UI} !important;
    font-size: 0.9rem !important; font-weight: 600 !important;
    color: {INK_MUTED} !important; padding: {SPACE['md']} {SPACE['lg']} !important;
    background: transparent !important; border-radius: {RADIUS['sm']} {RADIUS['sm']} 0 0 !important;
    white-space: nowrap;
}}
.stTabs [data-baseweb="tab"]:hover {{ color: {INK} !important; background: {NAVY} !important; }}
.stTabs [aria-selected="true"] {{
    color: {INK} !important; font-weight: 700 !important;
    border-bottom: 3px solid {TEAL} !important;
}}

/* ---------------------------------------------------------------- inputs ---
   Dropdowns get real presence: the user asked for more of them, so they had
   better not look like an afterthought. */
div[data-baseweb="select"] > div {{
    background-color: {NAVY} !important;
    border: 1px solid {BORDER} !important;
    border-radius: {RADIUS['md']} !important;
    font-size: 0.88rem !important; font-weight: 500 !important;
    color: {INK} !important;
    min-height: 42px !important;
}}
div[data-baseweb="select"] > div:hover {{ border-color: {BORDER_STRONG} !important; }}
div[data-baseweb="popover"] {{ border-radius: {RADIUS['md']} !important; }}
div[data-baseweb="popover"] li {{
    background-color: {NAVY} !important; color: {INK} !important;
    font-size: 0.88rem !important; padding: 10px 14px !important;
}}
div[data-baseweb="popover"] li:hover {{ background-color: {NAVY_HIGH} !important; }}
div[data-baseweb="tag"] {{
    background-color: {NAVY_HIGH} !important;
    border: 1px solid {BORDER_STRONG} !important;
    border-radius: {RADIUS['pill']} !important;
    color: {INK} !important; font-size: 0.76rem !important; font-weight: 600 !important;
}}
.stSlider label, .stCheckbox label, .stSelectbox label, .stMultiSelect label,
[data-testid="stWidgetLabel"] p {{
    font-size: 0.72rem !important; color: {INK_MUTED} !important;
    font-weight: 700 !important; text-transform: uppercase; letter-spacing: 0.07em;
}}
.stTextInput input {{
    background-color: {NAVY} !important; color: {INK} !important;
    border: 1px solid {BORDER} !important; border-radius: {RADIUS['md']} !important;
    min-height: 42px !important;
}}
.stButton button {{
    background: {NAVY_RAISED} !important; color: {INK} !important;
    border: 1px solid {BORDER_STRONG} !important;
    border-radius: {RADIUS['md']} !important;
    font-weight: 600 !important; padding: 10px 18px !important;
}}
.stButton button:hover {{ background: {NAVY_HIGH} !important; border-color: {TEAL} !important; }}

/* ------------------------------------------------------------------ misc --- */
hr {{ border: none; border-top: 1px solid {BORDER}; margin: {SPACE['xl']} 0; }}
code {{
    background: {NAVY_RAISED} !important; color: {TEAL} !important;
    font-family: {FONT_MONO} !important; font-size: 0.85rem !important;
    padding: 2px 6px !important; border-radius: 6px !important;
}}
.stAlert {{
    background-color: {NAVY} !important;
    border: 1px solid {BORDER} !important;
    border-radius: {RADIUS['lg']} !important;
    color: {INK} !important;
}}
[data-testid="stExpander"] details {{
    background: {NAVY} !important; border: 1px solid {BORDER} !important;
    border-radius: {RADIUS['lg']} !important;
}}
[data-testid="stExpander"] summary {{ font-weight: 600 !important; color: {INK} !important; }}

/* --------------------------------------------------------- RESPONSIVE ---
   The hub has to work on a phone. Two things break first at narrow widths:
   Streamlit's st.columns keep their horizontal layout and squeeze to
   unreadable slivers, and a wide table pushes the whole page sideways so the
   body scrolls horizontally. Both are handled here rather than hoped about. */
@media (max-width: {TABLET_MAX}px) {{
    .block-container {{ padding: {SPACE['lg']} {SPACE['md']} {SPACE['xl']} {SPACE['md']} !important; }}
    .ls-score {{ font-size: 3rem; }}
}}
@media (max-width: {PHONE_MAX}px) {{
    /* Stack columns. Streamlit lays them out as flex rows that do not wrap
       on their own; without this a 4-column row becomes four unreadable
       slivers on a phone. */
    [data-testid="stHorizontalBlock"] {{ flex-direction: column !important; gap: {SPACE['md']} !important; }}
    [data-testid="stHorizontalBlock"] > div {{ width: 100% !important; min-width: 0 !important; }}

    .block-container {{ padding: {SPACE['md']} {SPACE['sm']} {SPACE['xl']} {SPACE['sm']} !important; }}
    h1 {{ font-size: 1.5rem !important; }}
    h2 {{ font-size: 1.15rem !important; }}
    .ls-wordmark {{ font-size: 1.35rem; }}
    .ls-score {{ font-size: 2.5rem; }}
    .ls-card {{ padding: {SPACE['md']} {SPACE['lg']}; border-radius: {RADIUS['md']}; }}
    .ls-stats {{ grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); }}
    .ls-stat {{ padding: {SPACE['md']}; }}
    .ls-stat-value {{ font-size: 1.1rem; }}

    /* Tables scroll INSIDE their own container. The page body must never
       scroll sideways — that is the single worst mobile failure and it makes
       every other page feel broken too. */
    .ls-table-wrap {{ max-width: 100vw; }}
    table.ls-table {{ font-size: 0.78rem; }}
    table.ls-table thead th, table.ls-table tbody td {{ padding: {SPACE['sm']}; }}

    /* Thumb-sized targets. */
    .stTabs [data-baseweb="tab"] {{ padding: {SPACE['md']} !important; font-size: 0.84rem !important; }}
    div[data-baseweb="select"] > div {{ min-height: 46px !important; }}
}}
html, body {{ overflow-x: hidden; }}
</style>
"""


# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------
def esc(value) -> str:
    """Escape text before it goes into hand-built HTML.

    Every one of these components interpolates values straight into markup
    rendered with unsafe_allow_html. Company names and news headlines are
    external data containing `&`, `<`, and quotes — unescaped they break the
    table silently, and a headline is untrusted text besides.
    """
    return _html.escape(str(value), quote=True)


def chip(label: str) -> str:
    """A rating chip. Always carries the word, so colour is never alone."""
    fg, bg = RATING_COLORS.get(label, RATING_COLORS["INSUFFICIENT DATA"])
    return (f'<span class="ls-chip" style="color:{fg};background:{bg};'
            f'border-color:{fg}44">{esc(label)}</span>')


def pill(label: str) -> str:
    return f'<span class="ls-pill">{esc(label)}</span>'


# ---------------------------------------------------------------------------
# Company marks
#
# EVERY LOGO SITS ON THE SAME LIGHT TILE, and that is the whole design.
# Corporate logos are drawn for white paper: some are near-black wordmarks,
# which vanish on this navy, and some are pure white, which vanish on
# anything pale. Dropping them straight onto the page gives a row where a
# third of the marks are invisible and the rest are different shapes and
# weights — which reads as broken rather than as branding.
#
# One consistent rounded tile, one consistent inset, one consistent size per
# context. The mark becomes a uniform element of the layout instead of 500
# competing pieces of someone else's art direction, which is what every
# terminal that does this well settles on.
#
# The tile is faintly warm-white rather than pure #fff: at these sizes a pure
# white square on a dark surface glares, and the eye reads the glare before
# the logo.
# ---------------------------------------------------------------------------
LOGO_TILE = "#f4f6fa"

# Mark size inside a table row. The rows are 50px with a 13.6px symbol beside
# it; 20px reads as a peer of the text rather than as a bullet point, and
# still leaves the column narrow enough that nothing else shifts.
TABLE_LOGO_SIZE = 20

# Monogram palette, indexed by a hash of the ticker so a company keeps the
# same colour everywhere it appears. Drawn from the validated categorical
# set, so a page of monograms is still a page of colours that work together.
_MONOGRAM_INK = CATEGORICAL


def _monogram(ticker: str, size: int, radius: int) -> str:
    """The fallback mark: the ticker's own letters on a coloured tile.

    A DELIBERATE MARK, NOT A GAP. Logo coverage is never total — a holding
    company with no site, a symbol the icon services have never seen — and an
    empty space where a logo should be makes the whole column look broken.
    Two letters on a stable colour reads as a design decision, which is what
    it is.
    """
    letters = "".join(c for c in ticker if c.isalnum())[:2].upper() or "?"
    colour = _MONOGRAM_INK[sum(ord(c) for c in ticker) % len(_MONOGRAM_INK)]
    return (
        f'<span style="width:{size}px;height:{size}px;flex:0 0 {size}px;'
        f'border-radius:{radius}px;background:{colour}22;color:{colour};'
        f'display:inline-flex;align-items:center;justify-content:center;'
        f'font-family:{FONT_UI};font-weight:700;'
        f'font-size:{max(8, int(size * 0.42))}px;letter-spacing:-0.02em;'
        f'border:1px solid {colour}33;box-sizing:border-box">{letters}</span>')


def logo_mark(ticker, uri: str | None = None, *, size: int = 20) -> str:
    """One company's mark at a given size — its logo, or its monogram.

    `uri` is a data URI from `ui.data.logo_uri`; None falls back to the
    monogram, so a caller never has to branch on availability.
    """
    if is_missing(ticker):
        return ""
    symbol = str(ticker).strip().upper()
    radius = max(3, int(size * 0.22))
    if not uri:
        return _monogram(symbol, size, radius)
    # `contain` rather than `cover`: a wordmark cropped to fill a square is
    # no longer the company's logo. Padding keeps the art off the tile edge.
    inset = max(1, round(size * 0.10))
    # The hairline is a highlight for a mark large enough to read as an
    # object. At table size it is a third of the padding and costs two pixels
    # of artwork in each direction, which is the difference between a legible
    # mark and a smudge — and the light tile already separates itself from the
    # navy without help.
    border = ("border:1px solid rgba(255,255,255,0.10);" if size >= 28 else "")
    return (
        f'<span style="width:{size}px;height:{size}px;flex:0 0 {size}px;'
        f'border-radius:{radius}px;background:{LOGO_TILE};'
        f'display:inline-flex;align-items:center;justify-content:center;'
        f'padding:{inset}px;box-sizing:border-box;{border}">'
        f'<img src="{uri}" alt="" loading="lazy" '
        f'style="width:100%;height:100%;object-fit:contain;display:block">'
        f'</span>')


def ticker_with_logo(ticker, uri: str | None = None, *,
                     size: int = 20, link: bool = True) -> str:
    """A ticker preceded by its mark, aligned as one unit.

    The mark never replaces the symbol. A logo is recognisable to someone who
    already knows the company, which is precisely the reader who did not need
    it; the text is what everyone else reads, and it is what survives
    greyscale, a screen reader, and a copy-paste into a spreadsheet.
    """
    if is_missing(ticker):
        return "—"
    label = ticker_link(ticker) if link else esc(str(ticker).strip().upper())
    return (f'<span style="display:inline-flex;align-items:center;gap:7px">'
            f'{logo_mark(ticker, uri, size=size)}{label}</span>')


def nav_tabs(pages, active: str, *, extra_params: dict | None = None) -> str:
    """The section bar: one anchor per page, the current one underlined.

    Anchors rather than a widget, for the same reason every ticker in the
    product is an anchor — the URL IS the state, so a section is linkable,
    bookmarkable, and survives a refresh. It also means only the active page
    is rendered; `st.tabs` builds every tab's contents on every run, which
    across nine sections including a 500-row screener would make each click
    pay for all of them.

    `extra_params` carries context that should survive a section change —
    the selected ticker, so switching to Company from a page where one is in
    hand lands on that company rather than on nothing.
    """
    from urllib.parse import urlencode

    out = []
    for label in pages:
        params = {"page": label}
        params.update(extra_params or {})
        classes = "ls-tab ls-tab-active" if label == active else "ls-tab"
        out.append(f'<a class="{classes}" target="_self" '
                   f'href="?{urlencode(params)}">{esc(label)}</a>')
    return f'<nav class="ls-tabs">{"".join(out)}</nav>'


def ticker_link(ticker) -> str:
    """A ticker rendered as a link to its own Company page.

    Navigation by query string rather than by a Streamlit callback: these
    appear inside hand-rendered HTML tables, which cannot host a widget. A
    plain anchor works everywhere one of these can appear — tables, news
    rows, best/worst cells — and it also makes a company page linkable and
    bookmarkable, which a callback would not.

    `target="_self"` keeps it in the same tab; without it some browsers treat
    the embedded frame as a separate context and open a new one.
    """
    if is_missing(ticker):
        return "—"
    safe = esc(str(ticker).strip().upper())
    return (f'<a class="ls-ticker" target="_self" '
            f'href="?page=Company&ticker={safe}">{safe}</a>')


def sector_link(sector) -> str:
    """A sector shown as a link to the screener, pre-filtered to it.

    The same idea as a clickable ticker one level up: seeing "Utilities"
    beside a company should be enough to go look at every utility, without
    navigating to the screener and re-finding the filter.
    """
    if is_missing(sector):
        return "—"
    from urllib.parse import quote

    safe = esc(str(sector))
    return (f'<a class="ls-ticker" target="_self" '
            f'href="?page=Screener&sector={quote(str(sector))}">{safe}</a>')


def ticker_links(value, *, limit: int = 4) -> str:
    """A comma-separated list of tickers, each linked individually.

    News articles carry several tickers in one field; linking the joined
    string would produce one dead link to a symbol that does not exist.
    """
    if is_missing(value):
        return "—"
    parts = [t.strip() for t in str(value).split(",") if t.strip()]
    if not parts:
        return "—"
    shown = " ".join(ticker_link(t) for t in parts[:limit])
    if len(parts) > limit:
        shown += f' <span class="ls-pill">+{len(parts) - limit}</span>'
    return shown


def is_missing(value) -> bool:
    """True for None and for NaN.

    NaN specifically: pandas coerces None back to NaN when it lands in a float
    column, so normalising a frame with .where(notna, None) does NOT survive
    the round trip for numeric columns. Without this check, f"{nan:+.2f}"
    renders "+nan" in the middle of a table, which is how a missing value ends
    up looking like data.
    """
    if value is None:
        return True
    try:
        return value != value
    except TypeError:
        return False


def signed(value, *, fmt: str = "+.2f", suffix: str = "") -> str:
    """A signed number, coloured by direction and always carrying its sign."""
    if is_missing(value):
        return '<span class="ls-flat">—</span>'
    cls = "ls-pos" if value > 0 else ("ls-neg" if value < 0 else "ls-flat")
    return f'<span class="{cls}">{value:{fmt}}{suffix}</span>'


# Tone below this magnitude reads as neutral rather than as a weak direction.
# Mirrors finlake.lexicon.NEUTRAL_BAND — the display must not disagree with
# the scorer about where the line is, or a headline labelled neutral in one
# panel is labelled negative in the next.
TONE_NEUTRAL_BAND = 0.15


def tone_chip(value, *, scored_text: str | None = None) -> str:
    """Tone as a WORD first and a number second.

    `scored_text` becomes the hover title, and it matters: tone is computed
    over the headline AND the article's summary, while the table shows only
    the headline. So a story headlined "GameStop weighing withdrawal of eBay
    bid" can read positive on the strength of a summary the reader cannot see,
    which looks like a broken score rather than an unshown input. Hovering
    shows the text that was actually read.

    A reader wants "negative", not "-0.23". The number alone also invited the
    reading that a small negative is bad news, when at these magnitudes it is
    usually one incidental word in a headline about nothing — which is what
    made the news panels feel relentlessly negative even when the underlying
    average was positive.

    A blank tone stays blank. "No sentiment vocabulary was found" is not a
    neutral verdict and must not be dressed as one.
    """
    if is_missing(value):
        return ('<span class="ls-pill" title="no sentiment vocabulary was '
                'found in this story — not the same as neutral">no signal'
                '</span>')
    value = float(value)
    if value > TONE_NEUTRAL_BAND:
        label, color, wash = "positive", TEAL, TEAL_WASH
    elif value < -TONE_NEUTRAL_BAND:
        label, color, wash = "negative", RED, RED_WASH
    else:
        label, color, wash = "neutral", GREY, GREY_WASH

    hint = "scored on the headline and the article summary"
    if scored_text and str(scored_text).strip():
        hint = f"scored on: {str(scored_text).strip()[:400]}"
    return (f'<span title="{esc(hint)}" '
            f'style="display:inline-flex;align-items:center;gap:6px;'
            f'cursor:help">'
            f'<span style="background:{wash};color:{color};border-radius:6px;'
            f'padding:1px 7px;font-size:0.72rem;font-weight:600;'
            f'letter-spacing:0.02em">{label}</span>'
            f'<span style="color:{INK_FAINT};font-size:0.72rem;'
            f'font-family:{FONT_MONO}">{value:+.2f}</span></span>')


def score_bar(value, *, scale: float = 2.0) -> str:
    """A bar growing left or right from a centre axis.

    `scale` is the value that fills half the track; larger values clamp rather
    than overflow the cell.
    """
    if is_missing(value):
        return '<span class="ls-bar-track"></span>'
    frac = max(-1.0, min(1.0, value / scale))
    half = 36
    width = abs(frac) * half
    color = TEAL if value >= 0 else RED
    left = half if frac >= 0 else half - width
    return (f'<span class="ls-bar-track"><span class="ls-bar-fill" '
            f'style="left:{left:.1f}px;width:{width:.1f}px;background:{color}">'
            f'</span></span>')


def stat_cell(label: str, value: str, note: str | None = None) -> str:
    note_html = f'<div class="ls-stat-note">{esc(note)}</div>' if note else ""
    return (f'<div class="ls-stat"><div class="ls-stat-label">{esc(label)}</div>'
            f'<div class="ls-stat-value">{value}</div>{note_html}</div>')


def stat_strip(cells) -> str:
    """A row of headline figures. `value` is inserted as HTML so a cell can
    carry a signed/coloured number; labels and notes are escaped."""
    inner = "".join(
        stat_cell(c[0], c[1], c[2] if len(c) > 2 else None) for c in cells)
    return f'<div class="ls-stats">{inner}</div>'


def card(label: str, value: str, note: str | None = None) -> str:
    note_html = f'<div class="ls-card-note">{esc(note)}</div>' if note else ""
    return (f'<div class="ls-card"><div class="ls-card-label">{esc(label)}</div>'
            f'<div class="ls-card-value">{value}</div>{note_html}</div>')


def _fmt(value, fmt: str) -> str:
    """Format a possibly-missing number as an em dash rather than 'nan'."""
    if is_missing(value):
        return "—"
    try:
        return format(value, fmt)
    except (TypeError, ValueError):
        return esc(value)


_SORT_JS = """
<script>
(function () {
  if (window.__lsSortReady) return;
  window.__lsSortReady = true;
  document.addEventListener('click', function (ev) {
    var th = ev.target.closest('table.ls-table thead th');
    if (!th || th.dataset.nosort === '1') return;
    var table = th.closest('table');
    var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
    var dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
    Array.prototype.forEach.call(
      th.parentNode.children, function (o) { delete o.dataset.dir; });
    th.dataset.dir = dir;

    var body = table.tBodies[0];
    var rows = Array.prototype.slice.call(body.rows);
    // Sort on the cell's data-sort attribute when present -- the rendered
    // text is formatted ("+1.23", "3 / 11", an em dash) and would sort
    // lexically, putting "10" before "9" and missing values in the middle.
    var key = function (row) {
      var cell = row.cells[idx];
      if (!cell) return null;
      var raw = cell.dataset.sort;
      if (raw === undefined) raw = cell.textContent.trim();
      if (raw === '' || raw === '\\u2014') return null;
      var n = parseFloat(String(raw).replace(/[^0-9eE+\\-.]/g, ''));
      // n !== n is the not-a-number self-inequality test, used instead of
      // the built-in check so this script contains no literal "n-a-n".
      // The UI tests assert that substring never appears anywhere in
      // rendered output -- that is how a missing value leaking into a cell
      // as literal text gets caught, and it fired for real on the news and
      // growth columns. Keeping the guard strict is worth one idiom.
      return (n !== n) ? String(raw).toLowerCase() : n;
    };
    rows.sort(function (a, b) {
      var x = key(a), y = key(b);
      // Missing values sort to the bottom in BOTH directions. A blank is not
      // "the smallest number" -- floating them to the top of an ascending
      // sort would put every unmeasurable name above every measured one.
      if (x === null && y === null) return 0;
      if (x === null) return 1;
      if (y === null) return -1;
      if (typeof x === 'number' && typeof y === 'number') {
        return dir === 'asc' ? x - y : y - x;
      }
      return dir === 'asc' ? String(x).localeCompare(String(y))
                           : String(y).localeCompare(String(x));
    });
    rows.forEach(function (r) { body.appendChild(r); });
  });
})();
</script>
"""


def html_table(columns: list[dict], rows: list[dict], *,
               max_height: int = 620, sortable: bool = True,
               logos: dict | None = None) -> str:
    """Render a real HTML table, sortable by clicking a header.

    `columns` is a list of specs: {"key", "label", and optionally "kind"
    ('text'|'num'|'sym'|'rank'|'html'), "fmt", "cls", "sort" (raw sort value
    key), "nosort"}. A cell whose kind is 'html' is inserted verbatim — that
    is how chips, bars, and signed values get in — so anything reaching it
    must already be escaped or generated by this module.

    `logos` maps TICKER -> data URI, and is what puts a company mark beside
    every symbol in a `ticker` column. Passed in rather than looked up here
    because this module renders and does not read: a table builder that
    reaches for the filesystem mid-render is how a page starts doing I/O per
    row. Omit it and the tables render exactly as before.

    Sorting reads `data-sort` rather than the rendered text, because the
    rendered text is formatted: "+1.23", "3 / 11", "—". Sorting those
    lexically puts 10 before 9 and scatters the missing values through the
    middle of the table.
    """
    head = "".join(
        f'<th class="{"num" if c.get("kind") in ("num", "rank") else ""}"'
        f'{" data-nosort=1" if c.get("nosort") or not sortable else ""}>'
        f'{esc(c["label"])}</th>'
        for c in columns
    )

    body_rows = []
    for row in rows:
        cells = []
        for c in columns:
            kind = c.get("kind", "text")
            raw = row.get(c["key"])
            # A separate raw value for sorting, when the display value is
            # HTML or a formatted string.
            sort_key = row.get(c.get("sort", ""), None) if c.get("sort") else None
            if sort_key is None and kind in ("num", "rank"):
                sort_key = raw
            attr = ("" if sort_key is None or is_missing(sort_key)
                    else f' data-sort="{esc(sort_key)}"')

            if kind == "html":
                cells.append(f'<td class="{c.get("cls", "")}"{attr}>'
                             f'{raw if raw is not None else ""}</td>')
            elif kind == "num":
                cells.append(f'<td class="num {c.get("cls", "")}"{attr}>'
                             f'{_fmt(raw, c.get("fmt", ".2f"))}</td>')
            elif kind == "rank":
                cells.append(f'<td class="rank"{attr}>'
                             f'{"—" if is_missing(raw) else esc(raw)}</td>')
            elif kind == "sym":
                cells.append(f'<td class="sym"{attr}>'
                             f'{"—" if is_missing(raw) else esc(raw)}</td>')
            elif kind == "ticker":
                # Linked to its own company page. Sorting still uses the raw
                # symbol via data-sort, not the anchor markup — which is also
                # why the logo can be added without disturbing the sort.
                sort_attr = "" if is_missing(raw) else f' data-sort="{esc(raw)}"'
                if logos is not None and not is_missing(raw):
                    mark = ticker_with_logo(
                        raw, logos.get(str(raw).upper()), size=TABLE_LOGO_SIZE)
                else:
                    mark = ticker_link(raw)
                cells.append(f'<td class="sym"{sort_attr}>{mark}</td>')
            elif kind == "tickers":
                cells.append(f'<td class="sym">{ticker_links(raw)}</td>')
            elif kind == "sector":
                sort_attr = "" if is_missing(raw) else f' data-sort="{esc(raw)}"'
                cells.append(f'<td class="muted"{sort_attr}>'
                             f'{sector_link(raw)}</td>')
            else:
                text = "—" if is_missing(raw) else esc(raw)
                cells.append(f'<td class="{c.get("cls", "muted")}"{attr}>{text}</td>')
        body_rows.append("<tr>" + "".join(cells) + "</tr>")

    return (
        f'{_SORT_JS if sortable else ""}'
        f'<div class="ls-table-wrap" style="max-height:{max_height}px">'
        f'<table class="ls-table"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody></table></div>'
    )


def empty_state(title: str, body: str, command: str | None = None) -> str:
    cmd = (f'<div style="margin-top:{SPACE["lg"]}"><code>{esc(command)}</code></div>'
           if command else "")
    return (f'<div class="ls-card" style="text-align:center;padding:{SPACE["xxl"]}">'
            f'<div style="font-size:1.25rem;font-weight:700;margin-bottom:{SPACE["sm"]}">'
            f'{esc(title)}</div>'
            f'<div style="color:{INK_MUTED};font-size:0.9rem;max-width:60ch;'
            f'margin:0 auto;line-height:1.6">{esc(body)}</div>{cmd}</div>')


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
PLOT_FONT = dict(family="JetBrains Mono, monospace", color=INK_MUTED, size=11)


def plot_layout(**overrides) -> dict:
    """Shared plotly layout so every chart in the hub reads as one system.

    Recessive grid and axes, transparent surfaces so charts sit on the card
    rather than in a box of their own, and the hover layer on by default —
    an HTML chart is interactive, and shipping one without hover throws away
    the main advantage it has over a picture.
    """
    base = dict(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=PLOT_FONT,
        margin=dict(l=8, r=8, t=8, b=8),
        showlegend=False,
        hovermode="closest",
        hoverlabel=dict(bgcolor=NAVY_RAISED, bordercolor=BORDER_STRONG,
                        font=dict(family="JetBrains Mono, monospace",
                                  color=INK, size=12)),
        xaxis=dict(gridcolor=BORDER, zerolinecolor=BORDER_STRONG,
                   linecolor=BORDER, showgrid=True),
        yaxis=dict(gridcolor=BORDER, zerolinecolor=BORDER_STRONG,
                   linecolor=BORDER, showgrid=True),
    )
    base.update(overrides)
    return base


def diverging_colors(values) -> list[str]:
    """Teal for non-negative, red for negative. Pair with a direction cue —
    these bars grow left/right from zero, so the sign survives greyscale."""
    return [TEAL if (not is_missing(v) and v >= 0) else RED for v in values]
