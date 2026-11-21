from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

TOOL = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint


def buddy_icon():
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    bubble = QPainterPath()
    bubble.moveTo(14, 36)
    bubble.cubicTo(-2, 29, 1, 4, 24, 4)
    bubble.cubicTo(50, 4, 52, 42, 21, 38)
    bubble.lineTo(9, 45)
    bubble.closeSubpath()
    painter.setPen(QPen(QColor("#1e7bea"), 2))
    painter.setBrush(QColor("#eaf3ff"))
    painter.drawPath(bubble)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#087a87"))
    painter.drawEllipse(15, 19, 6, 7)
    painter.drawEllipse(27, 19, 6, 7)
    painter.end()
    return QIcon(pixmap)
