"""Preview-only controls kept separate from exported skin settings."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QCheckBox, QPushButton, QToolButton, QMenu
from core import i18n
from ui.widgets.wheel_guard import ClickWheelSpinBox, ClickWheelComboBox


class ManiaPreviewControls(QWidget):
    speed_changed = Signal(int)
    tempo_changed = Signal(int)
    keys_requested = Signal(int)
    guide_changed = Signal(bool)
    restart_requested = Signal()
    test_mode_changed = Signal(str)
    test_pattern_changed = Signal(str)
    judgement_requested = Signal(str)
    combo_requested = Signal()
    assets_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ManiaPlayback")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        row = QHBoxLayout()
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
        layout.addLayout(row)

        play_row = QHBoxLayout()
        play_row.setSpacing(8)
        self.test_mode = ClickWheelComboBox()
        for mode in ("auto", "play", "demo"):
            self.test_mode.addItem(mode, mode)
        self.test_mode.currentIndexChanged.connect(lambda: self.test_mode_changed.emit(self.test_mode.currentData()))
        self.test_pattern = ClickWheelComboBox()
        for pattern in ("mixed", "taps", "holds", "chords"):
            self.test_pattern.addItem(pattern, pattern)
        self.test_pattern.currentIndexChanged.connect(lambda: self.test_pattern_changed.emit(self.test_pattern.currentData()))
        self.judgement_button = QToolButton()
        self.judgement_button.setPopupMode(QToolButton.InstantPopup)
        self.judgement_menu = QMenu(self)
        self.judgement_actions = {}
        for kind in ("300g", "300", "200", "100", "50", "0"):
            action = self.judgement_menu.addAction(kind)
            action.triggered.connect(lambda checked=False, grade=kind: self.judgement_requested.emit(grade))
            self.judgement_actions[kind] = action
        self.judgement_button.setMenu(self.judgement_menu)
        self.judgement_menu.addSeparator()
        self.assets_action = self.judgement_menu.addAction("")
        self.assets_action.triggered.connect(self.assets_requested)
        self.combo_button = QPushButton()
        self.combo_button.clicked.connect(self.combo_requested)
        play_row.addWidget(self.test_mode)
        play_row.addWidget(self.test_pattern)
        play_row.addStretch(1)
        play_row.addWidget(self.judgement_button)
        play_row.addWidget(self.combo_button)
        layout.addLayout(play_row)
        self.input_hint = QLabel()
        self.input_hint.setObjectName("Muted")
        self.input_hint.setWordWrap(True)
        layout.addWidget(self.input_hint)
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
        for index, key in enumerate(("auto", "play", "demo")):
            self.test_mode.setItemText(index, i18n.t("playtest.mode_" + key))
        for index, key in enumerate(("mixed", "taps", "holds", "chords")):
            self.test_pattern.setItemText(index, i18n.t("playtest.pattern_" + key))
        self.test_mode.setAccessibleName(i18n.t("playtest.mode", "测试模式"))
        self.test_pattern.setAccessibleName(i18n.t("playtest.pattern", "测试排列"))
        self.judgement_button.setText(i18n.t("playtest.judgements", "判定图"))
        self.combo_button.setText(i18n.t("playtest.combo_art", "连击图"))
        self.assets_action.setText(i18n.t("playtest.asset_map", "查看素材映射…"))
        for kind, action in self.judgement_actions.items():
            action.setText(i18n.t("playtest.grade_" + kind))
        for control in (self.judgement_button, self.combo_button):
            control.setToolTip(i18n.t("playtest.inspect_help", "暂停并查看指定皮肤素材，不改变试玩成绩"))

    def update_input_hint(self, key_labels=()):
        mode = self.test_mode.currentData()
        self.test_pattern.setEnabled(mode != "demo")
        if mode == "play":
            self.input_hint.setText(i18n.t("playtest.keyboard_help").format(keys=" · ".join(key_labels)))
        elif mode == "auto":
            self.input_hint.setText(i18n.t("playtest.auto_help"))
        else:
            self.input_hint.setText(i18n.t("playtest.demo_help"))
