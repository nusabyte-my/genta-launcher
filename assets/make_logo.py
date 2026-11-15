"""Generate the Genta launcher logo (SVG + PNG) from the same dot bell the agent sprite uses.

Run: python assets/make_logo.py   (writes assets/logo.svg, assets/logo-mark.svg, assets/logo.png)
"""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from glass_launcher.sprite import BELL, CLAPPER  # noqa: E402

# 5x7 dot glyphs for the wordmark (original, same style as Genta's dot text).
GLYPHS = {
    "G": ["01111", "10000", "10000", "10111", "10001", "10001", "01111"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"],
    "N": ["10001", "11001", "11001", "10101", "10011", "10011", "10001"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"],
}
RED, WHITE, INK = "#D71921", "#F2F2F2", "#0C0C0D"


def bell_dots(step, ox, oy, colour, clapper_colour):
    dots = []
    for y, row in enumerate(BELL):
        for x, c in enumerate(row):
            if c == "#":
                dots.append((ox + x * step, oy + y * step, colour))
    for x, y in CLAPPER:
        dots.append((ox + x * step, oy + y * step, clapper_colour))
    return dots


def word_dots(text, step, ox, oy, colour):
    dots = []
    for index, char in enumerate(text):
        for y, row in enumerate(GLYPHS[char]):
            for x, bit in enumerate(row):
                if bit == "1":
                    dots.append((ox + (index * 6 + x) * step, oy + y * step, colour))
    return dots


def svg(width, height, dots, radius, background=True):
    body = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius}" fill="{c}"/>' for x, y, c in dots)
    back = f'<rect width="{width}" height="{height}" rx="{min(width, height) * 0.22:.0f}" fill="{INK}"/>' if background else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<title>Genta</title>{back}{body}</svg>\n')


def main():
    # Square app mark: white bell, red clapper on near-black.
    step = 14
    origin = (256 - 12 * step) / 2          # bell is 13 dots wide and 13 dots tall
    mark = bell_dots(step, origin, origin, WHITE, RED)
    (HERE / "logo-mark.svg").write_text(svg(256, 256, mark, 4.9))
    # Wide logo: bell + GENTA wordmark.
    step = 9
    dots = bell_dots(step, 52, 46, WHITE, RED) + word_dots("GENTA", step, 200, 46 + 6 * step - 3 * step, WHITE)
    (HERE / "logo.svg").write_text(svg(640, 200, dots, 3.2))
    try:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QGuiApplication, QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer
        app = QGuiApplication.instance() or QGuiApplication([])
        for name, size in (("logo", (1280, 400)), ("logo-mark", (512, 512))):
            image = QImage(*size, QImage.Format.Format_ARGB32)
            image.fill(0)
            painter = QPainter(image)
            QSvgRenderer(str(HERE / f"{name}.svg")).render(painter)
            painter.end()
            image.save(str(HERE / f"{name}.png"))
    except ImportError:
        pass


if __name__ == "__main__":
    main()
