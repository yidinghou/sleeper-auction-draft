"""Shared dark-theme look for the HTML surfaces.

Two pages render draft data: `report.py` (the post-mortem board for a finished
simulation) and `live.py` (the live board polling a real Sleeper draft). They
should read as the same product, so the palette, the position badge and the
page chrome live here rather than being copied.

Colors mirror the Next.js app (`lib/positions.ts` / `app/globals.css`).
Page-specific rules — roster cards, the points matrix, the pick timeline, the
live table — stay in their own module; only what both pages use belongs here.

There are two palettes. The report is dark; the live board is **light**, so that
a tool sitting open beside Sleeper's dark app for three hours can never be
mistaken for it mid-auction. `BASE_CSS` / `POS_COLOR` are the dark set,
`BASE_CSS_LIGHT` / `POS_COLOR_LIGHT` the light one — each surface picks once.
"""

from __future__ import annotations

import html

# Position badge colors, mirrored from the app's Sleeper theme.
POS_COLOR = {
    "QB": "#ff6482",
    "RB": "#28e757",
    "WR": "#00d7ff",
    "TE": "#ffab0e",
}
POS_FALLBACK = "#98b3d6"  # K, DEF, anything else

# Compact labels for the long flex slot names (matches `slotShortLabel()`).
SLOT_LABEL = {"SUPER_FLEX": "SFLX", "REC_FLEX": "W/T", "WRRB_FLEX": "W/R"}

# Palette, named so page CSS below and in callers reads as intent, not hex.
BG = "#05091d"
BG_SUNK = "#080d24"
PANEL = "#131b38"
BORDER = "#414566"
RULE = "#252942"
TEXT = "#fafafa"
DIM = "#98b3d6"
FAINT = "#4a5170"
STRUCK = "#6b7395"
ACCENT = "#00d7ff"
MONEY = "#ffab0e"
BAD = "#ff6482"
GOOD = "#28e757"

# Chrome shared by both pages: page frame, headings, badges, table scroller.
# Braces are doubled because callers embed this inside an f-string.
BASE_CSS = f"""
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px;
    background: {BG}; color: {TEXT};
    font: 14px/1.4 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  }}
  h1 {{ font-size: 18px; margin: 0 0 4px; }}
  h2 {{ font-size: 15px; margin: 28px 0 10px; padding-top: 6px; }}
  .sub {{ color: {DIM}; margin: 0 0 12px; font-size: 13px; }}
  .nav {{ margin: 0 0 20px; font-size: 13px; }}
  .nav a {{ color: {ACCENT}; text-decoration: none; margin-right: 14px; }}
  .nav a:hover {{ text-decoration: underline; }}
  .legend {{ color: {DIM}; font-size: 12px; margin: 8px 0 0; }}
  .muted {{ color: {FAINT}; }}
  .warn {{ color: {BAD}; font-weight: 700; }}
  .flag {{ color: {BAD}; }}
  .badge {{ font-size: 10px; font-weight: 700; text-align: center;
    padding: 2px 0; border-radius: 5px; min-width: 30px; }}
  .tablewrap {{ overflow-x: auto; }}
"""


# -- the light palette, for the live board ------------------------------------
#
# Two palettes, on purpose. The post-mortem report is a dark page and stays one;
# the *live* board is light, because it sits open beside Sleeper's own dark app
# for three hours and must never be mistaken for it mid-auction. Nothing here is
# a "theme switch" -- each surface picks its palette once and keeps it.

L_PAGE = "#ffffff"
L_PANEL = "#ffffff"
L_SUNK = "#f4f4f4"  # a hole: absence, drawn as flat grey rather than tinted
L_RULE = "#eeeeee"
L_BORDER = "#dddddd"
L_TEXT = "#1a1a1a"
L_DIM = "#666666"
L_FAINT = "#aaaaaa"
L_LABEL = "#555555"  # slot labels: legible, but never louder than a name
L_MONEY = "#1a7f37"
L_BAD = "#c0392b"

# Position colors for the light board. Deliberately *not* `POS_COLOR`, which
# mirrors the app and was picked to glow on near-black -- mixed down to a 26%
# tint on white those go acidic. These are chosen against white, so the pale
# row tint they produce is the thing you actually read a name off.
POS_COLOR_LIGHT = {
    "QB": "#ff2a6d",
    "RB": "#00ceb8",
    "WR": "#58a7ff",
    "TE": "#ffae58",
}
POS_FALLBACK_LIGHT = "#7c90a0"  # K, DEF, anything else

# The light twin of BASE_CSS -- same rules, same doubled braces (callers embed
# it in an f-string), different palette.
BASE_CSS_LIGHT = f"""
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px;
    background: {L_PAGE}; color: {L_TEXT};
    font: 14px/1.4 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  }}
  h1 {{ font-size: 18px; margin: 0 0 4px; }}
  h2 {{ font-size: 15px; margin: 28px 0 10px; padding-top: 6px; }}
  .sub {{ color: {L_DIM}; margin: 0 0 12px; font-size: 13px; }}
  .nav {{ margin: 0 0 20px; font-size: 13px; }}
  .nav a {{ color: {L_MONEY}; text-decoration: none; margin-right: 14px; }}
  .nav a:hover {{ text-decoration: underline; }}
  .legend {{ color: {L_DIM}; font-size: 12px; margin: 8px 0 0; }}
  .muted {{ color: {L_FAINT}; }}
  .warn {{ color: {L_BAD}; font-weight: 700; }}
  .flag {{ color: {L_BAD}; }}
  .badge {{ font-size: 10px; font-weight: 700; text-align: center;
    padding: 2px 0; border-radius: 5px; min-width: 30px; }}
  .tablewrap {{ overflow-x: auto; }}
"""


def badge(pos: str, light: bool = False) -> str:
    """A colored position pill, e.g. RB in green.

    On white the dark board's `color + "33"` wash all but disappears, so the
    light variant fills the pill with the position colour and writes on it --
    the same badge, legible against the page it is actually on.
    """
    if light:
        color = POS_COLOR_LIGHT.get(pos, POS_FALLBACK_LIGHT)
        return (
            f'<span class="badge" style="color:#fff;background:{color}">'
            f"{html.escape(pos)}</span>"
        )
    color = POS_COLOR.get(pos, POS_FALLBACK)
    return (
        f'<span class="badge" style="color:{color};background:{color}33">'
        f"{html.escape(pos)}</span>"
    )
