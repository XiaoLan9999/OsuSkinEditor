"""Keep wheel scrolling from editing fields until the user clicks them."""

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QAbstractScrollArea, QComboBox, QSpinBox


class _ClickWheelGuard:
    def _init_wheel_guard(self):
        self._wheel_edit_armed = False
        # Wheel focus must not turn an incidental hover into an edit. Keyboard
        # focus and editing still work, but only a mouse click arms wheel edits.
        self.setFocusPolicy(Qt.StrongFocus)

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            self._wheel_edit_armed = True

    def focusOutEvent(self, event):
        # A combo's own popup temporarily takes focus after an intentional click.
        if event.reason() != Qt.PopupFocusReason:
            self._wheel_edit_armed = False
        super().focusOutEvent(event)

    def hideEvent(self, event):
        self._wheel_edit_armed = False
        super().hideEvent(event)

    def wheelEvent(self, event):
        if self._wheel_edit_armed and self.hasFocus():
            super().wheelEvent(event)
        else:
            # Explicitly forward to the viewport: child editors and synthetic
            # wheel delivery do not consistently bubble ignored wheel events.
            parent = self.parentWidget()
            while parent is not None:
                if isinstance(parent, QAbstractScrollArea):
                    viewport = parent.viewport()
                    forwarded = QWheelEvent(
                        QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                        event.globalPosition(), event.pixelDelta(), event.angleDelta(),
                        event.buttons(), event.modifiers(), event.phase(),
                        event.inverted(), event.source(), event.pointingDevice(),
                    )
                    QApplication.sendEvent(viewport, forwarded)
                    event.setAccepted(forwarded.isAccepted())
                    return
                parent = parent.parentWidget()
            event.ignore()


class ClickWheelSpinBox(_ClickWheelGuard, QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_wheel_guard()
        # The editable text area is a child widget; its mouse events do not
        # normally reach the spinbox's mousePressEvent.
        self.lineEdit().installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.lineEdit():
            if event.type() == QEvent.MouseButtonPress:
                if event.button() == Qt.LeftButton:
                    self._wheel_edit_armed = True
            elif event.type() == QEvent.Wheel:
                self.wheelEvent(event)
                return True
        return super().eventFilter(watched, event)


class ClickWheelComboBox(_ClickWheelGuard, QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_wheel_guard()
