"""Regenerate lcr_studio/assets/icon.png and packaging/icon.ico (run on Windows for the Bahnschrift face)."""
import math
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parent.parent
app = QGuiApplication(sys.argv)
S = 512
img = QImage(S, S, QImage.Format_ARGB32)
img.fill(Qt.transparent)
p = QPainter(img)
p.setRenderHint(QPainter.Antialiasing)
g = QLinearGradient(0, 0, S, S)
g.setColorAt(0, QColor("#4c8dff"))
g.setColorAt(1, QColor("#1d4ed8"))
p.setBrush(g)
p.setPen(Qt.NoPen)
p.drawRoundedRect(QRectF(16, 16, S - 32, S - 32), 112, 112)
wave = QPainterPath()
for i in range(0, 361, 4):
    x = 96 + (S - 192) * i / 360
    y = 372 + 34 * math.sin(math.radians(i * 2))
    wave.moveTo(x, y) if i == 0 else wave.lineTo(x, y)
p.setPen(QPen(QColor(255, 255, 255, 150), 18, Qt.SolidLine, Qt.RoundCap))
p.setBrush(Qt.NoBrush)
p.drawPath(wave)
f = QFont("Bahnschrift", 150)
f.setWeight(QFont.DemiBold)
p.setFont(f)
p.setPen(QColor("#ffffff"))
p.drawText(QRectF(0, 90, S, 200), Qt.AlignCenter, "LCR")
p.end()
png = ROOT / "lcr_studio" / "assets" / "icon.png"
img.save(str(png))
Image.open(png).save(ROOT / "packaging" / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", png)
