"""Small, bounded image preview with a transparency checkerboard."""
from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QColor, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import QWidget


class AssetInspector(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(124)
        self.setMaximumHeight(166)
        self.pixmap = QPixmap()
        self.image_size = QSize()
        self.placeholder = ""

    def set_asset(self, path=None):
        self.pixmap = QPixmap()
        self.image_size = QSize()
        if path:
            reader = QImageReader(str(path))
            self.image_size = reader.size()
            if self.image_size.isValid():
                reader.setScaledSize(self.image_size.scaled(1024, 1024, Qt.KeepAspectRatio))
            self.pixmap = QPixmap.fromImage(reader.read())
            self.pixmap.setDevicePixelRatio(1)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#0a1320"))
        p.drawRoundedRect(self.rect(), 8, 8)
        if self.pixmap.isNull():
            p.setPen(QColor("#73819b"))
            p.drawText(self.rect().adjusted(10, 10, -10, -10), Qt.AlignCenter | Qt.TextWordWrap, self.placeholder)
            return
        area = self.rect().adjusted(8, 8, -8, -8)
        for y in range(area.top(), area.bottom(), 12):
            for x in range(area.left(), area.right(), 12):
                p.fillRect(x, y, min(12, area.right() - x), min(12, area.bottom() - y),
                           QColor("#182c42" if ((x - area.left()) // 12 + (y - area.top()) // 12) % 2 else "#112033"))
        size = self.pixmap.size().scaled(area.size(), Qt.KeepAspectRatio)
        # Preserve small sprites' actual pixel size rather than inventing detail.
        if size.width() > self.pixmap.width():
            size = self.pixmap.size()
        target = QRectF(area.center().x() - size.width() / 2, area.center().y() - size.height() / 2,
                        size.width(), size.height())
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(target, self.pixmap, QRectF(self.pixmap.rect()))
