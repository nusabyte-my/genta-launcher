"""Original five-by-seven dot typography. No proprietary Nothing font/assets."""
from PySide6.QtCore import QSize, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QWidget

ACCENT = '#e84a3c'
GLYPHS = {
 'A':['01110','10001','10001','11111','10001','10001','10001'],
 'B':['11110','10001','10001','11110','10001','10001','11110'],
 'C':['01111','10000','10000','10000','10000','10000','01111'],
 'D':['11110','10001','10001','10001','10001','10001','11110'],
 'E':['11111','10000','10000','11110','10000','10000','11111'],
 'F':['11111','10000','10000','11110','10000','10000','10000'],
 'G':['01111','10000','10000','10111','10001','10001','01111'],
 'H':['10001','10001','10001','11111','10001','10001','10001'],
 'I':['11111','00100','00100','00100','00100','00100','11111'],
 'J':['00111','00010','00010','00010','10010','10010','01100'],
 'K':['10001','10010','10100','11000','10100','10010','10001'],
 'L':['10000','10000','10000','10000','10000','10000','11111'],
 'M':['10001','11011','10101','10101','10001','10001','10001'],
 'N':['10001','11001','11001','10101','10011','10011','10001'],
 'O':['01110','10001','10001','10001','10001','10001','01110'],
 'P':['11110','10001','10001','11110','10000','10000','10000'],
 'Q':['01110','10001','10001','10001','10101','10010','01101'],
 'R':['11110','10001','10001','11110','10100','10010','10001'],
 'S':['01111','10000','10000','01110','00001','00001','11110'],
 'T':['11111','00100','00100','00100','00100','00100','00100'],
 'U':['10001','10001','10001','10001','10001','10001','01110'],
 'V':['10001','10001','10001','10001','10001','01010','00100'],
 'W':['10001','10001','10001','10101','10101','10101','01010'],
 'X':['10001','10001','01010','00100','01010','10001','10001'],
 'Y':['10001','10001','01010','00100','00100','00100','00100'],
 'Z':['11111','00001','00010','00100','01000','10000','11111'],
 '0':['01110','10001','10011','10101','11001','10001','01110'],
 '1':['00100','01100','00100','00100','00100','00100','01110'],
 '2':['01110','10001','00001','00010','00100','01000','11111'],
 '3':['11110','00001','00001','01110','00001','00001','11110'],
 '4':['00010','00110','01010','10010','11111','00010','00010'],
 '5':['11111','10000','10000','11110','00001','00001','11110'],
 '6':['01110','10000','10000','11110','10001','10001','01110'],
 '7':['11111','00001','00010','00100','01000','01000','01000'],
 '8':['01110','10001','10001','01110','10001','10001','01110'],
 '9':['01110','10001','10001','01111','00001','00001','01110'],
 ':':['00000','00100','00100','00000','00100','00100','00000'],
 '.':['00000','00000','00000','00000','00000','00100','00100'],
 '-':['00000','00000','00000','11111','00000','00000','00000'],
 '/':['00001','00010','00010','00100','01000','01000','10000'],
 ' ':['00000']*7,
}


def draw_dot_text(painter, text, x, y, scale=2, color='#f0efeb'):
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    for index, char in enumerate(text.upper()):
        glyph = GLYPHS.get(char)
        if glyph is None:
            painter.setFont(QFont('sans-serif', max(8, int(scale * 6))))
            painter.setPen(QColor(color))
            painter.drawText(int(x + index * 6 * scale), int(y + 7 * scale), char)
            painter.setPen(Qt.PenStyle.NoPen)
            continue
        for row, bits in enumerate(glyph):
            for column, bit in enumerate(bits):
                if bit == '1':
                    radius = max(0.65, scale * .34)
                    cx, cy = x + (index * 6 + column) * scale, y + row * scale
                    painter.drawEllipse(QRectF(cx, cy, radius * 2, radius * 2))


class DotText(QWidget):
    def __init__(self, text, size=2, parent=None):
        super().__init__(parent)
        self.text, self.scale = text, size
        self.setAccessibleName(text)
        self.setMinimumSize(self.sizeHint())

    def setText(self, text):
        if text != self.text:
            self.text = text
            self.setAccessibleName(text)
            self.setMinimumSize(self.sizeHint())
            self.updateGeometry()
            self.update()

    def sizeHint(self):
        return QSize(int(max(1, len(self.text)) * 6 * self.scale + 2), int(8 * self.scale + 2))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        draw_dot_text(painter, self.text, 1, max(1, (self.height() - 7 * self.scale) / 2), self.scale)


STYLE = '''
QWidget { color:#eeece7; font-family:'Adwaita Sans','Segoe UI',sans-serif; font-size:13px; }
QFrame#shell { background:#101010; border:1px solid #414141; border-radius:24px; }
QFrame#card { background:#1a1a1a; border:1px solid #353535; border-radius:14px; }
QLabel { background:transparent; border:none; }
QLabel#brand { font-size:20px; font-weight:600; }
QLabel#eyebrow { color:#aaaaa7; font-size:10px; letter-spacing:2px; }
QLabel#muted { color:#a4a4a0; }
QLabel#heading { font-size:24px; font-weight:500; }
QLabel#metric { font-size:36px; font-family:monospace; color:#eeece7; }
QPushButton { background:#242424; border:1px solid #454545; border-radius:15px; padding:7px 12px; }
QPushButton:hover { background:#353535; border-color:#9b9b98; }
QPushButton:pressed { background:#464646; }
QPushButton:disabled { color:#73736f; background:#191919; border-color:#303030; }
QPushButton#primary { background:#e84a3c; color:#ffffff; border:none; font-weight:600; }
QPushButton#primary:hover { background:#fb5a4a; }
QPushButton#nav { color:#9d9d99; background:transparent; border:none; padding:8px 12px; }
QPushButton#nav:checked { color:#111; background:#e5e3dd; }
QPushButton#icon { color:#b1b1ad; background:transparent; border:none; padding:5px 8px; }
QPushButton#icon:checked { color:#fff; background:#a42d25; }
QLineEdit,QPlainTextEdit,QComboBox,QSpinBox,QDateEdit,QTimeEdit,QDateTimeEdit { background:#1d1d1d; border:1px solid #4a4a4a; border-radius:10px; padding:8px; selection-background-color:#a6372e; }
QLineEdit:focus,QPlainTextEdit:focus { border-color:#d8d8d2; }
QComboBox QAbstractItemView { background:#222; selection-background-color:#60302a; }
QScrollArea { background:transparent; border:none; }
QScrollArea>QWidget>QWidget { background:transparent; }
QScrollBar:vertical { background:transparent; width:5px; }
QScrollBar::handle:vertical { background:#555; border-radius:2px; min-height:24px; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
QCheckBox { spacing:9px; }
QCheckBox::indicator { width:17px; height:17px; border:1px solid #777; border-radius:8px; background:transparent; }
QCheckBox::indicator:checked { background:#e84a3c; border:4px solid #5a2924; }
QMenu { background:#1c1c1c; border:1px solid #555; padding:5px; }
QMenu::item { padding:8px 18px; }
QMenu::item:selected { background:#4a2b27; }
QToolTip { background:#252525; color:#eee; border:1px solid #777; }
QDialog { background:#101010; }
QSlider::groove:horizontal { background:#444; height:3px; }
QSlider::handle:horizontal { background:#e84a3c; width:12px; margin:-5px 0; border-radius:6px; }
'''
