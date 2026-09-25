"""Preview-only controls kept separate from exported skin settings."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QCheckBox, QPushButton
from core import i18n
from ui.widgets.wheel_guard import ClickWheelSpinBox, ClickWheelComboBox


class ManiaPreviewControls(QWidget):
    speed_changed = Signal(int)
    tempo_changed = Signal(int)
    keys_requested = Signal(int)
    guide_changed = Signal(bool)
    restart_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ManiaPlayback")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.keys = ClickWheelComboBox()
        self.keys.setMinimumWidth(66)
        self.keys.currentIndexChanged.connect(self._request_keys)
        self.speed_label = QLabel()
        self.speed_label.setObjectName("Muted")
        self.speed = ClickWheelSpinBox()
        self.speed.setRange(1, 40); self.speed.setValue(20)
        self.speed.setFixedWidth(76)
        self.speed.valueChanged.connect(self.speed_changed)
        self.tempo_label = QLabel()
        self.tempo_label.setObjectName("Muted")
        self.tempo = ClickWheelSpinBox()
        self.tempo.setRange(40, 300); self.tempo.setValue(120)
        self.tempo.setSuffix(" BPM"); self.tempo.setFixedWidth(112)
        self.tempo.valueChanged.connect(self.tempo_changed)
        self.guide = QCheckBox()
        self.guide.toggled.connect(self.guide_changed)
        self.restart = QPushButton()
        self.restart.clicked.connect(self.restart_requested)
        for widget in (self.keys, self.speed_label, self.speed, self.tempo_label, self.tempo):
            row.addWidget(widget)
        row.addStretch(1)
        row.addWidget(self.guide)
        row.addWidget(self.restart)
        self.retranslate()

    def _request_keys(self, index):
        if index >= 0:
            self.keys_requested.emit(int(self.keys.itemData(index)))

    def set_keys(self, available, selected):
        self.keys.blockSignals(True)
        try:
            self.keys.clear()
            for count in sorted(set(available) | {int(selected)}):
                self.keys.addItem(f"{count}K", count)
            self.keys.setCurrentIndex(self.keys.findData(int(selected)))
        finally:
            self.keys.blockSignals(False)

    def retranslate(self):
        self.speed_label.setText(i18n.t("workspace.flow_speed", "流速"))
        self.tempo_label.setText(i18n.t("workspace.demo_tempo", "演示节奏"))
        self.guide.setText(i18n.t("workspace.hit_guide", "判定参考线"))
        self.restart.setText(i18n.t("workspace.restart", "重置演示"))
        self.speed.setToolTip(i18n.t("workspace.flow_help", "仅影响演示预览，采用固定流速，不写入 skin.ini"))
        self.speed.setAccessibleName(self.speed_label.text())
        self.tempo.setAccessibleName(self.tempo_label.text())
        self.keys.setAccessibleName(i18n.t("workspace.keys", "预览键数"))
        self.guide.setToolTip(i18n.t("workspace.guide_help", "显示真实判定位置的辅助线，仅用于预览"))
