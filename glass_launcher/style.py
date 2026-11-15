"""Nothing-inspired look: near-black glass, white type, one red, dot-matrix numerals,
mono caps labels, and a pastel colour per widget card.

Fonts are bundled open fonts (SIL OFL, see fonts/): Doto (dot matrix), Space Grotesk
(body), Space Mono (labels). No proprietary Nothing fonts or assets are used.
"""
from pathlib import Path

ACCENT = "#D71921"            # signal red
RADIUS = 22
FILL = (12, 12, 13, 238)      # near-black glass (COSMIC does not blur X11 windows)
SCRIM = (0, 0, 0, 96)
BORDER = (255, 255, 255, 34)
TEXT = "#F2F2F2"
MUTED = "#9A9A9A"
INK = "#111111"               # text on pastel cards
WIDTH = 780
ROW = 60
VISIBLE_ROWS = 8

DOT = "Doto"
BODY = "Space Grotesk"
MONO = "Space Mono"
FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

# One pastel per built-in widget; plugins get one from the cycle by id.
PASTELS = {
    "focus": "#FFB3A7",     # coral
    "tasks": "#BDEFD1",     # mint
    "tips": "#D8CCFF",      # lavender
    "weather": "#BEE1FF",   # sky
    "calendar": "#FFD7AE",  # peach
    "music": "#FFC6E2",     # pink
    "note": "#FFF0A3",      # butter
    "insights": "#B3EEE7",  # aqua
    "todo": "#E4F5A1",      # lime
    "solat": "#C9E9D2",     # sage
    "timeline": "#F2E3D0",  # sand
    "heatmap": "#D3E4FF",   # periwinkle
    "toggles": "#F4D4D9",   # rose
}
CYCLE = ["#E3F1A6", "#C9D7FF", "#F7C9B5", "#CDEBC0", "#EBC8F2", "#F3E0B5"]
PAPER = "#EDEDED"           # answer card


def pastel_for(widget_id):
    if widget_id in PASTELS:
        return PASTELS[widget_id]
    return CYCLE[sum(map(ord, widget_id or "x")) % len(CYCLE)]


def load_fonts():
    from PySide6.QtGui import QFontDatabase
    for path in sorted(FONT_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(path))


def card_style(color):
    """Stylesheet for a pastel card and everything inside it (dark ink on colour)."""
    return f"""
QFrame#card {{ background: {color}; border: none; border-radius: 18px; }}
QFrame#card QWidget {{ color: {INK}; background: transparent; font-family: "{BODY}"; }}
QFrame#card QLabel#eyebrow {{ color: rgba(0,0,0,150); font-family: "{MONO}"; font-size: 8pt; letter-spacing: 1px; }}
QFrame#card QLabel#muted {{ color: rgba(0,0,0,150); font-size: 9pt; }}
QFrame#card QLabel#title {{ font-weight: 600; }}
QFrame#card QLabel#metric {{ font-family: "{DOT}"; font-size: 21pt; font-weight: 800; color: {INK}; }}
QFrame#card QPushButton {{
  background: rgba(0,0,0,16); border: 1px solid rgba(0,0,0,34); border-radius: 13px; padding: 5px 12px;
  font-family: "{MONO}"; font-size: 8.5pt; color: {INK};
}}
QFrame#card QPushButton:hover {{ background: rgba(0,0,0,34); }}
QFrame#card QPushButton:pressed {{ background: rgba(0,0,0,52); }}
QFrame#card QPushButton:disabled {{ color: rgba(0,0,0,80); border-color: rgba(0,0,0,18); background: transparent; }}
QFrame#card QPushButton#primary {{ background: {INK}; border-color: {INK}; color: #FFFFFF; }}
QFrame#card QPushButton#primary:hover {{ background: #2A2A2A; }}
QFrame#card QPushButton#primary:disabled {{ background: rgba(0,0,0,55); border-color: transparent; color: rgba(255,255,255,170); }}
QFrame#card QPushButton#ghost {{ background: transparent; border: none; color: rgba(0,0,0,140); padding: 0 4px; font-size: 11pt; }}
QFrame#card QPushButton#ghost:hover {{ color: {INK}; }}
QFrame#card QPushButton#icon {{ padding: 3px 8px; }}
QFrame#card QLineEdit {{
  background: rgba(255,255,255,150); border: 1px solid rgba(0,0,0,28); border-radius: 11px; padding: 6px 10px;
  color: {INK}; selection-background-color: {INK}; selection-color: #FFFFFF;
}}
QFrame#card QLineEdit:focus {{ border-color: rgba(0,0,0,120); }}
QFrame#card QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 7px; border: 1.5px solid rgba(0,0,0,150); background: transparent; }}
QFrame#card QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QFrame#card QCheckBox::indicator:checked {{ background: {INK}; border-color: {INK}; }}
QFrame#card QScrollBar:vertical {{ background: transparent; width: 6px; }}
QFrame#card QScrollBar::handle:vertical {{ background: rgba(0,0,0,60); border-radius: 3px; min-height: 24px; }}
"""


STYLE = f"""
QWidget {{ color: {TEXT}; font-family: "{BODY}", "Adwaita Sans", sans-serif; font-size: 10pt; background: transparent; }}
QLineEdit#search {{
  background: transparent; border: none; padding: 4px 2px; font-family: "{BODY}"; font-size: 19pt; font-weight: 300;
  selection-background-color: {ACCENT};
}}
QLabel#clock {{ font-family: "{DOT}"; font-size: 15pt; font-weight: 800; color: {TEXT}; }}
QLabel#date {{ font-family: "{MONO}"; font-size: 8.5pt; color: {MUTED}; }}
QFrame#rule {{ background: rgba(255,255,255,26); max-height: 1px; min-height: 1px; border: none; }}
QLabel#hint {{ color: rgba(255,255,255,190); font-family: "{MONO}"; font-size: 9pt; padding-left: 4px; }}
QLabel#hint[error="true"] {{ color: #FF6B6B; }}
QLabel#footer {{ color: rgba(255,255,255,105); font-family: "{MONO}"; font-size: 8pt; }}
QLabel#muted {{ color: {MUTED}; font-size: 9pt; }}
QLabel#eyebrow {{ color: {MUTED}; font-family: "{MONO}"; font-size: 8pt; }}
QPushButton {{
  background: rgba(255,255,255,18); border: 1px solid rgba(255,255,255,30); border-radius: 13px; padding: 5px 12px;
  font-family: "{MONO}"; font-size: 8.5pt;
}}
QPushButton:hover {{ background: rgba(255,255,255,36); }}
QPushButton#primary {{ background: {TEXT}; border-color: {TEXT}; color: {INK}; }}
QPushButton#add {{
  background: transparent; border: 1.5px dashed rgba(255,255,255,70); border-radius: 18px; padding: 16px;
  color: rgba(255,255,255,170); font-family: "{MONO}"; font-size: 9pt;
}}
QPushButton#add:hover {{ background: rgba(255,255,255,14); color: {TEXT}; border-color: rgba(255,255,255,140); }}
QLineEdit {{
  background: rgba(255,255,255,16); border: 1px solid rgba(255,255,255,26); border-radius: 11px; padding: 6px 10px;
  selection-background-color: {ACCENT};
}}
QListView, QScrollArea {{ background: transparent; border: none; outline: 0; }}
QScrollBar:vertical {{ background: transparent; width: 7px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,60); border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QMenu {{ background: #141414; border: 1px solid rgba(255,255,255,40); border-radius: 12px; padding: 6px; font-family: "{MONO}"; font-size: 9pt; }}
QMenu::item {{ padding: 7px 18px; border-radius: 7px; }}
QMenu::item:selected {{ background: {TEXT}; color: {INK}; }}
QMenu::item:disabled {{ color: rgba(255,255,255,90); }}
QMenu::separator {{ height: 1px; background: rgba(255,255,255,30); margin: 4px 8px; }}
QDialog {{ background: #141414; }}
QComboBox {{ background: rgba(255,255,255,16); border: 1px solid rgba(255,255,255,26); border-radius: 11px; padding: 5px 10px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 7px; border: 1.5px solid rgba(255,255,255,140); }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QToolTip {{ background: #141414; color: {TEXT}; border: 1px solid rgba(255,255,255,40); padding: 4px 6px; font-family: "{MONO}"; }}
"""
