"""Draws the app icon: the sidebar's gold pilcrow on an oxblood rounded square.

Writes packaging/icon.png (Linux menus, AppImage) and packaging/icon.ico (Windows app and installer). The files are
checked in, so run this only when the icon changes. Needs PyQt6, which the Linux build already installs:
    QT_QPA_PLATFORM=offscreen python packaging/make_icon.py
"""
import os
import struct

from PyQt6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter, QPainterPath

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = os.path.join(HERE, "..", "para", "static", "fonts", "space-grotesk-300-700.woff2")
OXBLOOD, GOLD = QColor("#6b2419"), QColor("#e9b872")
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw(size, family):
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    inset = size * 0.06  # room for the shadow some desktops draw, and the same visual size as other icons
    box = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    path = QPainterPath()
    path.addRoundedRect(box, box.width() * 0.22, box.width() * 0.22)
    p.fillPath(path, OXBLOOD)
    font = QFont(family)
    font.setVariableAxis(QFont.Tag("wght"), 600 if size < 48 else 500)  # heavier when small, so it stays legible
    font.setPixelSize(round(box.height() * 0.86))
    p.setFont(font)
    p.setPen(GOLD)
    p.drawText(box.translated(0, box.height() * 0.03), Qt.AlignmentFlag.AlignCenter, "¶")
    p.end()
    return img


def png_bytes(img):
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def write_ico(path, images):
    """An .ico holding a PNG per size (Windows Vista and later read these)."""
    data = [png_bytes(img) for img in images]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for img, blob in zip(images, data):
        w = img.width() if img.width() < 256 else 0  # 0 means 256
        out += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    with open(path, "wb") as f:
        f.write(out + b"".join(data))


if __name__ == "__main__":
    app = QGuiApplication([])
    family = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(FONT))[0]
    draw(512, family).save(os.path.join(HERE, "icon.png"))
    write_ico(os.path.join(HERE, "icon.ico"), [draw(s, family) for s in ICO_SIZES])
    print("Wrote packaging/icon.png and packaging/icon.ico")
