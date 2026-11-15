# -*- coding: utf-8 -*-
"""
Regenerate `app_icon.ico` — the Gigahelm wheel mark.

Run headless:  python tools/make_icon.py
The icon is a rounded square with a vertical dark gradient and a teal
wheel (outer ring, hub, three spokes) in the Gigahelm accent colour.
"""

import math
import os
import struct
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gui.qt import (  # noqa: E402  (import after sys.path setup)
    QApplication, QBuffer, QColor, QImage, QIODevice, QLinearGradient, QPainter,
    QPen, QPointF, QRectF, Qt,
)

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
BASE_SIZE = 256.0
CORNER_RADIUS = 58.0
BACKGROUND_TOP = "#1B2430"
BACKGROUND_BOTTOM = "#0A0E14"
MARK_COLOR = "#2DD4BF"
RING_RADIUS = 84.0
HUB_RADIUS = 26.0
STROKE_WIDTH = 22.0
SPOKE_ANGLES = (90.0, 210.0, 330.0)


def render_icon(size: int) -> QImage:
    """Render one square RGBA icon at `size` pixels."""
    scale = size / BASE_SIZE
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    gradient = QLinearGradient(0.0, 0.0, 0.0, float(size))
    gradient.setColorAt(0.0, QColor(BACKGROUND_TOP))
    gradient.setColorAt(1.0, QColor(BACKGROUND_BOTTOM))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    radius = CORNER_RADIUS * scale
    painter.drawRoundedRect(QRectF(0.0, 0.0, float(size), float(size)), radius, radius)

    pen = QPen(QColor(MARK_COLOR), max(1.0, STROKE_WIDTH * scale))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    center = QPointF(128.0 * scale, 128.0 * scale)
    painter.drawEllipse(center, RING_RADIUS * scale, RING_RADIUS * scale)
    painter.drawEllipse(center, HUB_RADIUS * scale, HUB_RADIUS * scale)

    for angle in SPOKE_ANGLES:
        radians = math.radians(angle)
        dx, dy = math.cos(radians), -math.sin(radians)
        painter.drawLine(
            QPointF(center.x() + dx * HUB_RADIUS * scale, center.y() + dy * HUB_RADIUS * scale),
            QPointF(center.x() + dx * RING_RADIUS * scale, center.y() + dy * RING_RADIUS * scale),
        )

    painter.end()
    return image


def encode_png(image: QImage) -> bytes:
    """Encode a QImage as PNG bytes."""
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("failed to encode the icon as PNG")
    data = bytes(buffer.data())
    buffer.close()
    return data


def build_ico(images) -> bytes:
    """Assemble a PNG-payload ICO file from (size, png bytes) pairs."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)

    directory = bytearray()
    payload = bytearray()
    for size, png in images:
        directory += struct.pack(
            "<BBBBHHII",
            size & 0xFF,      # 0 means 256
            size & 0xFF,
            0,                # colour count (true colour)
            0,                # reserved
            1,                # colour planes
            32,               # bits per pixel
            len(png),
            offset,
        )
        payload += png
        offset += len(png)

    return bytes(header + bytes(directory) + bytes(payload))


def main() -> int:
    app = QApplication([])  # noqa: F841  (needed for QImage painting)
    images = [(size, encode_png(render_icon(size))) for size in ICON_SIZES]
    icon_bytes = build_ico(images)

    target = os.path.join(REPO_ROOT, "app_icon.ico")
    with open(target, "wb") as handle:
        handle.write(icon_bytes)

    print(f"wrote {target} ({len(icon_bytes)} bytes, sizes: {', '.join(str(s) for s in ICON_SIZES)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
