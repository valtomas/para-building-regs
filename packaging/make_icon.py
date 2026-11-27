"""Draws the app icon: the sidebar's gold pilcrow on an oxblood rounded square.

Writes packaging/icon.png (Linux menus, AppImage), packaging/icon.ico (Windows app and installer), packaging/icon.icns
(the Mac app; without it the Dock shows Python's icon), and the Windows
installer's pictures (packaging/windows/wizard-*.bmp: the tall one on its first and last pages, the small one in the
corner of the others). The files are checked in, so run this only when the icon changes. Needs PyQt6, which the Linux build already installs:
    QT_QPA_PLATFORM=offscreen python packaging/make_icon.py
"""
import os
import struct

from PyQt6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter, QPainterPath

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = os.path.join(HERE, "..", "para", "static", "fonts", "space-grotesk-300-700.woff2")
MONO = os.path.join(HERE, "..", "para", "static", "fonts", "ibm-plex-mono-400.woff2")
OXBLOOD, GOLD, CREAM, MUTED = QColor("#6b2419"), QColor("#e9b872"), QColor("#f4ead9"), QColor("#d7a98f")
# Inno Setup's modern wizard: the sizes it picks from for each screen scaling (100, 150 and 200 %)
BANNERS = ((164, 314), (246, 459), (328, 604))
SMALL = ((55, 55), (83, 80), (110, 106))
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def draw(size, family, inset=0.06):
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    inset = size * inset  # room for the shadow some desktops draw, and the same visual size as other icons
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


def banner(w, h, family, mono):
    """The installer's tall picture, like the app's sidebar: oxblood, the gold pilcrow, the name in cream."""
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(OXBLOOD)
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    font = QFont(family)
    font.setVariableAxis(QFont.Tag("wght"), 500)
    font.setPixelSize(round(w * 0.62))
    p.setFont(font)
    p.setPen(GOLD)
    p.drawText(QRectF(0, h * 0.16, w, w * 0.8), Qt.AlignmentFlag.AlignCenter, "¶")
    font.setVariableAxis(QFont.Tag("wght"), 600)
    font.setPixelSize(round(w * 0.24))
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 97)
    p.setFont(font)
    p.setPen(CREAM)
    p.drawText(QRectF(0, h * 0.16 + w * 0.82, w, w * 0.3), Qt.AlignmentFlag.AlignCenter, "Para")
    small = QFont(mono)
    small.setPixelSize(max(9, round(w * 0.055)))
    small.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, w * 0.007)
    p.setFont(small)
    p.setPen(MUTED)
    p.drawText(QRectF(0, h * 0.16 + w * 1.12, w, w * 0.3), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
               "BUILDING REGULATIONS\nWITH THEIR SOURCES")
    p.end()
    return img


def corner(w, h, family):
    """The small picture at the top right of the installer's other pages: the icon on the page's white."""
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(Qt.GlobalColor.white)
    side = min(w, h)
    p = QPainter(img)
    p.drawImage((w - side) // 2, (h - side) // 2, draw(side, family))
    p.end()
    return img


def save_bmp(img, path):
    """24-bit BMP, which every Inno Setup version reads."""
    img.convertToFormat(QImage.Format.Format_RGB888).save(path, "BMP")


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


# The Mac's icon grid puts the rounded square at 824 of 1024 px, so the icon sits the same size as others in the Dock
MAC_INSET = 100 / 1024
# (type, pixels): the PNG entries macOS reads, from 16 px to 512 px at twice the density
ICNS_TYPES = ((b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128), (b"ic08", 256), (b"ic09", 512),
              (b"ic10", 1024), (b"ic11", 32), (b"ic12", 64), (b"ic13", 256), (b"ic14", 512))


def write_icns(path, family):
    """An .icns holding a PNG per size, so the Mac app can be built on any computer (no iconutil needed)."""
    cache = {}
    body = b""
    for kind, px in ICNS_TYPES:
        if px not in cache:
            cache[px] = png_bytes(draw(px, family, MAC_INSET))
        body += kind + struct.pack(">I", 8 + len(cache[px])) + cache[px]
    with open(path, "wb") as f:
        f.write(b"icns" + struct.pack(">I", 8 + len(body)) + body)


if __name__ == "__main__":
    app = QGuiApplication([])
    family = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(FONT))[0]
    draw(512, family).save(os.path.join(HERE, "icon.png"))
    write_ico(os.path.join(HERE, "icon.ico"), [draw(s, family) for s in ICO_SIZES])
    write_icns(os.path.join(HERE, "icon.icns"), family)
    mono = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(MONO))[0]
    for w, h in BANNERS:
        save_bmp(banner(w, h, family, mono), os.path.join(HERE, "windows", f"wizard-{w}.bmp"))
    for w, h in SMALL:
        save_bmp(corner(w, h, family), os.path.join(HERE, "windows", f"wizard-small-{w}.bmp"))
    print("Wrote packaging/icon.png, icon.ico, icon.icns and packaging/windows/wizard-*.bmp")
