"""Controls for the synthetic Standard skin scene and single-object inspector."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QToolButton, QMenu
from ui.widgets.wheel_guard import ClickWheelComboBox, ClickWheelSpinBox
from core import i18n


class StdPreviewControls(QWidget):
    mode_changed = Signal(str)
    pattern_changed = Signal(str)
    circle_size_changed = Signal(int)
    approach_rate_changed = Signal(int)
    restart_requested = Signal()
    judgement_requested = Signal(str)
    combo_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.mode = ClickWheelComboBox()
        for mode in ("auto", "inspect"):
            self.mode.addItem(mode, mode)
        self.mode.currentIndexChanged.connect(lambda: self.mode_changed.emit(self.mode.currentData()))
        self.pattern = ClickWheelComboBox()
        for pattern in ("mixed", "circles", "sliders", "spinner"):
            self.pattern.addItem(pattern, pattern)
        self.pattern.currentIndexChanged.connect(lambda: self.pattern_changed.emit(self.pattern.currentData()))
        self.circle_size = ClickWheelSpinBox()
        self.circle_size.setRange(0, 10)
        self.circle_size.setValue(4)
        self.circle_size.setPrefix("CS ")
        self.circle_size.setFixedWidth(90)
        self.circle_size.valueChanged.connect(self.circle_size_changed)
        self.approach_rate = ClickWheelSpinBox()
        self.approach_rate.setRange(0, 10)
        self.approach_rate.setValue(5)
        self.approach_rate.setPrefix("AR ")
        self.approach_rate.setFixedWidth(90)
        self.approach_rate.valueChanged.connect(self.approach_rate_changed)
        self.restart = QPushButton()
        self.restart.clicked.connect(self.restart_requested)
        self.judgement_button = QToolButton()
        self.judgement_button.setPopupMode(QToolButton.InstantPopup)
        self.judgement_menu = QMenu(self)
        self.judgement_actions = {}
        for kind in ("300", "100", "50", "0"):
            action = self.judgement_menu.addAction(kind)
            action.triggered.connect(lambda checked=False, grade=kind: self.judgement_requested.emit(grade))
            self.judgement_actions[kind] = action
        self.judgement_button.setMenu(self.judgement_menu)
        self.combo_button = QPushButton()
        self.combo_button.clicked.connect(self.combo_requested)
        for control in (self.mode, self.pattern, self.circle_size, self.approach_rate):
            row.addWidget(control)
        row.addStretch(1)
        for control in (self.judgement_button, self.combo_button, self.restart):
            row.addWidget(control)
        layout.addLayout(row)
        self.hint = QLabel()
        self.hint.setObjectName("Muted")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.retranslate()

    def retranslate(self):
        for index, mode in enumerate(("auto", "inspect")):
            self.mode.setItemText(index, i18n.t("std_scene.mode_"+mode))
        for index, pattern in enumerate(("mixed", "circles", "sliders", "spinner")):
            self.pattern.setItemText(index, i18n.t("std_scene.pattern_"+pattern))
        self.mode.setAccessibleName(i18n.t("playtest.mode"))
        self.pattern.setAccessibleName(i18n.t("std_scene.pattern"))
        self.circle_size.setAccessibleName(i18n.t("std_scene.cs"))
        self.approach_rate.setAccessibleName(i18n.t("std_scene.ar"))
        self.circle_size.setToolTip(i18n.t("std_scene.cs"))
        self.approach_rate.setToolTip(i18n.t("std_scene.ar"))
        self.restart.setText(i18n.t("workspace.restart"))
        self.judgement_button.setText(i18n.t("playtest.judgements"))
        self.combo_button.setText(i18n.t("playtest.combo_art"))
        for kind, action in self.judgement_actions.items():
            action.setText("MISS" if kind == "0" else kind)
        for control in (self.judgement_button, self.combo_button):
            control.setToolTip(i18n.t("playtest.inspect_help"))
        self.update_hint()

    def update_hint(self):
        auto = self.mode.currentData() == "auto"
        for control in (self.pattern, self.circle_size, self.approach_rate,
                        self.judgement_button, self.combo_button):
            control.setEnabled(auto)
        self.hint.setText(i18n.t("std_scene.help_auto" if auto else "std_scene.help_inspect"))
