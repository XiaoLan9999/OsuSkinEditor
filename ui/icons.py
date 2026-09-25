"""Small line icons drawn at 2x for the workspace toolbar."""
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def workspace_icon(name: str) -> QIcon:
    pixmap = QPixmap(40, 40)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor("#c1e8ff"), 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if name in ("folder", "open"):
        path = QPainterPath(QPointF(2.5, 6.5))
        path.lineTo(2.5, 4); path.lineTo(7.5, 4); path.lineTo(9.5, 6)
        path.lineTo(17.5, 6); path.lineTo(17.5, 16); path.lineTo(2.5, 16); path.closeSubpath()
        painter.drawPath(path)
        if name == "open":
            painter.drawLine(QPointF(3, 9), QPointF(17, 9))
    elif name == "reload":
        painter.drawArc(QRectF(3, 3, 14, 14), 40*16, 285*16)
        painter.drawLine(QPointF(16.5, 3), QPointF(16.5, 7))
        painter.drawLine(QPointF(12.5, 7), QPointF(16.5, 7))
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)
