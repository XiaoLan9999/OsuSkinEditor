"""A second window hosting the existing preview, never a second simulation."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow
from core import i18n


class DetachedPreviewWindow(QMainWindow):
    restore_requested = Signal()
    fullscreen_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setMinimumSize(720, 480)
        self.resize(1280, 860)
        self.fullscreen_action = QAction(self)
        self.fullscreen_action.setShortcut("F11")
        self.fullscreen_action.setShortcutContext(Qt.WindowShortcut)
        self.fullscreen_action.setAutoRepeat(False)
        self.fullscreen_action.triggered.connect(self.toggle_fullscreen)
        self.addAction(self.fullscreen_action)
        self.retranslate()

    def retranslate(self):
        self.setWindowTitle(i18n.t("preview_window.title", "皮肤游玩预览"))
        self.fullscreen_action.setText(i18n.t("preview_window.fullscreen", "全屏 / F11"))

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()
        self.fullscreen_changed.emit(self.isFullScreen())

    def keyPressEvent(self, event):
        # Mania accepts the first Esc to pause. Only an unhandled Esc reaches
        # this host, so leaving full screen never prevents game input cleanup.
        if event.key() == Qt.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self.restore_requested.emit()
        event.accept()
