"""Shared dark-theme look for the HTML surfaces.

Two pages render draft data: `report.py` (the post-mortem board for a finished
simulation) and `live.py` (the live board polling a real Sleeper draft). They
should read as the same product, so the palette, the position badge and the
page chrome live here rather than being copied.

Colors mirror the Next.js app (`lib/positions.ts` / `app/globals.css`).
Page-specific rules — roster cards, the points matrix, the pick timeline, the
live table — stay in their own module; only what both pages use belongs here.
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
SLOT_LABEL = {"SUPER_FLEX": "SFLX", "REC_FLEX": "RFLX", "WRRB_FLEX": "W/R"}

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


def badge(pos: str) -> str:
    """A colored position pill, e.g. RB in green."""
    color = POS_COLOR.get(pos, POS_FALLBACK)
    return (
        f'<span class="badge" style="color:{color};background:{color}33">'
        f"{html.escape(pos)}</span>"
    )
