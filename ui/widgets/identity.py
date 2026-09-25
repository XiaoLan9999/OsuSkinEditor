"""Static brand and panel decoration; no background animation or extra timers."""
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QWidget
from core.resources import brand_image_path


class AvatarBadge(QWidget):
    def __init__(self, size=72, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._image = QPixmap(brand_image_path())
        self.setToolTip("小蓝 · XiaoLan")
        self.setAccessibleName("小蓝头像 / XiaoLan avatar")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        frame = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        cut = max(7, self.width()*0.09)
        path = QPainterPath()
        path.moveTo(frame.left(), frame.top()+cut)
        path.lineTo(frame.left()+cut, frame.top())
        path.lineTo(frame.right(), frame.top())
        path.lineTo(frame.right(), frame.bottom()-cut)
        path.lineTo(frame.right()-cut, frame.bottom())
        path.lineTo(frame.left(), frame.bottom())
        path.closeSubpath()
        painter.fillPath(path, QColor("#101c2d"))
        if not self._image.isNull():
            painter.save()
            painter.setClipPath(path)
            side = min(self._image.width(), self._image.height())
            source = QRectF((self._image.width()-side)/2, (self._image.height()-side)/2, side, side)
            painter.drawPixmap(frame, self._image, source)
            painter.restore()
        border = QLinearGradient(frame.topLeft(), frame.bottomRight())
        border.setColorAt(0, QColor("#8de9fa"))
        border.setColorAt(0.5, QColor("#274f72"))
        border.setColorAt(1, QColor("#2989e8"))
        painter.setPen(QPen(border, 1.5))
        painter.drawPath(path)
        painter.setPen(QPen(QColor("#c2f3ff"), 2))
        painter.drawLine(QPointF(frame.left()+cut, frame.top()), QPointF(frame.left()+cut+12, frame.top()))
        painter.drawLine(QPointF(frame.right()-cut-12, frame.bottom()), QPointF(frame.right()-cut, frame.bottom()))


class TechPanel(QFrame):
    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.setObjectName(name)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#4cb6d4"), 1.2))
        painter.drawLine(18, 1, 58, 1)
        painter.setPen(QPen(QColor("#315774"), 1))
        painter.drawLine(self.width()-24, self.height()-2, self.width()-12, self.height()-2)
        painter.drawLine(self.width()-2, self.height()-24, self.width()-2, self.height()-12)
        if self.objectName() == "Header":
            painter.setPen(QPen(QColor(60, 127, 165, 30), 1))
            for offset in range(0, 160, 16):
                x = self.width()-offset-18
                painter.drawLine(x, 1, x-22, 23)


class WelcomeCanvas(QWidget):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # Faint plotting marks keep the background quiet behind the artwork.
        painter.setPen(QPen(QColor(112, 181, 218, 26), 1))
        for x in range(24, self.width(), 32):
            for y in range(24, self.height(), 32):
                painter.drawPoint(x, y)
        painter.setPen(QPen(QColor(69, 160, 208, 45), 1))
        for x, direction in ((16, 1), (self.width()-16, -1)):
            painter.drawLine(x, 20, x+direction*30, 20)
            painter.drawLine(x, 20, x, 50)
            painter.drawLine(x, self.height()-20, x+direction*30, self.height()-20)
            painter.drawLine(x, self.height()-20, x, self.height()-50)
