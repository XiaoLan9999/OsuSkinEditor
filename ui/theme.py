"""Midnight-blue desktop palette with restrained cyan instrument accents."""
from PySide6.QtGui import QColor, QFont, QPalette
from core.resources import resource_path


def apply_theme(app):
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    palette = QPalette()
    for role, color in {
        QPalette.Window: "#080f1b", QPalette.WindowText: "#e8f3ff",
        QPalette.Base: "#0a1320", QPalette.AlternateBase: "#101e30",
        QPalette.Text: "#e8f3ff", QPalette.Button: "#15263b",
        QPalette.ButtonText: "#e8f3ff", QPalette.Highlight: "#2167a0",
        QPalette.HighlightedText: "#ffffff", QPalette.ToolTipBase: "#14273b",
        QPalette.ToolTipText: "#e8f3ff", QPalette.Link: "#77def4",
    }.items():
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#5d7188"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#5d7188"))
    app.setPalette(palette)
    stylesheet = STYLESHEET
    for token, filename in (("@DOWN@", "chevron-down.svg"), ("@UP@", "chevron-up.svg"),
                            ("@DOWN_DISABLED@", "chevron-down-disabled.svg")):
        stylesheet = stylesheet.replace(token, resource_path("assets/ui/" + filename).replace("\\", "/"))
    app.setStyleSheet(stylesheet)


STYLESHEET = """
QWidget { color: #e8f3ff; }
QWidget:disabled { color: #5d7188; }
QMainWindow, QDialog { background: #080f1b; }
QMenuBar { background: #080f1b; color: #9ab0c8; padding: 5px 14px; border-bottom: 1px solid #20334a; }
QMenuBar::item { padding: 5px 10px; background: transparent; }
QMenuBar::item:selected, QMenu::item:selected { color: #a3edff; background: #17354e; border-radius: 3px; }
QMenu { background: #101e30; border: 1px solid #355775; padding: 6px; }
QMenu::item { padding: 8px 24px; }
QMenu::item:disabled { color: #5d7188; }
QMenu::separator { height: 1px; background: #294058; margin: 5px; }
QWidget#Header, QWidget#Sidebar, QWidget#PreviewPanel { background: #101c2d; border: 1px solid #294058; border-radius: 6px; }
QLabel#Brand { background: #173a59; color: #9beaff; border: 1px solid #4381a4; border-radius: 6px; font-size: 18px; font-weight: 800; }
QLabel#Eyebrow { color: #73cbe8; font-family: Consolas; font-size: 10px; font-weight: 700; }
QLabel#SectionCode { color: #5b829f; font-family: Consolas; font-size: 10px; }
QLabel#Title { font-size: 21px; font-weight: 700; color: #f2f8ff; }
QLabel#Muted, QLabel#SkinPath { color: #8da5bf; font-size: 11px; }
QLabel#SectionTitle { font-size: 13px; font-weight: 700; color: #e8f3ff; }
QLabel#Counter, QLabel#Badge { color: #8be6fa; background: #102f43; border: 1px solid #285268; padding: 3px 8px; border-radius: 3px; font-size: 10px; }
QLabel#WelcomeMark { background: #102e46; color: #91e8fc; border: 1px solid #3f839f; border-radius: 10px; font-size: 37px; font-weight: 700; }
QLabel#WelcomeTitle { font-size: 27px; font-weight: 700; color: #f2f8ff; }
QLabel#WelcomeDescription { color: #a0b6ce; font-size: 13px; }
QLabel#WelcomeHint { color: #7893ae; font-size: 11px; }
QLabel#DesignError { color: #ffc085; padding: 6px; border: 1px solid #815331; border-radius: 4px; }
QPushButton, QToolButton { background: #15263b; border: 1px solid #35516e; border-radius: 4px; padding: 8px 14px; font-weight: 600; }
QPushButton:hover, QToolButton:hover { background: #1c3550; border-color: #578aac; }
QPushButton:pressed, QToolButton:pressed { background: #0d2136; border-color: #77def4; }
QPushButton:focus, QToolButton:focus { border-color: #77def4; }
QPushButton:checked, QToolButton:checked { color: #a6efff; background: #164064; border-color: #438bb6; }
QPushButton:disabled, QToolButton:disabled { background: #101c2c; color: #5d7188; border-color: #22374d; }
QToolButton#LanguageButton { background: transparent; color: #9ab0c8; border: 1px solid transparent; border-radius: 3px; padding: 5px 23px 5px 10px; font-weight: 400; }
QToolButton#LanguageButton:hover, QToolButton#LanguageButton:pressed { color: #a3edff; background: #17354e; border-color: #355775; }
QToolButton#LanguageButton:focus { color: #a3edff; border-color: #77def4; }
QToolButton#LanguageButton::menu-indicator { image: url("@DOWN@"); width: 10px; height: 10px; subcontrol-origin: padding; subcontrol-position: right center; right: 7px; }
QPushButton#PrimaryButton { background: #2386ed; color: #ffffff; border-color: #53a5f3; }
QPushButton#PrimaryButton:hover { background: #379afa; border-color: #8cc9ff; }
QPushButton#PrimaryButton:pressed { background: #1469c1; border-color: #77def4; }
QPushButton#PrimaryButton:focus { border-color: #b7f0ff; }
QPushButton#PrimaryButton:disabled { background: #183b60; color: #6489aa; border-color: #284c6e; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit { background: #0a1320; border: 1px solid #294058; border-radius: 4px; padding: 7px 9px; selection-background-color: #2167a0; selection-color: #ffffff; }
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover { border-color: #41647f; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus { border-color: #66c9e8; }
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled { background: #0d1826; color: #5d7188; border-color: #213347; }
QComboBox::drop-down { border: 0; width: 23px; }
QComboBox::down-arrow { image: url("@DOWN@"); width: 12px; height: 12px; }
QComboBox::down-arrow:disabled { image: url("@DOWN_DISABLED@"); }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("@UP@"); width: 9px; height: 9px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("@DOWN@"); width: 9px; height: 9px; }
QComboBox QAbstractItemView { background: #101e30; border: 1px solid #355775; selection-background-color: #19476a; selection-color: #b8f1ff; outline: none; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 7px 6px; margin: 2px 0; border: 1px solid transparent; border-radius: 3px; }
QListWidget::item:hover { background: #152b41; border-color: #29465f; }
QListWidget::item:selected { color: #b5efff; background: #153c5c; border-color: #4387ac; }
QListWidget::item:disabled { color: #5d7188; }
QTabWidget::pane { border: 1px solid #294058; border-radius: 4px; top: -1px; background: #0a1320; }
QTabBar::tab { background: transparent; color: #8da5bf; padding: 11px 22px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #98e9ff; border-bottom-color: #60d2f0; background: #142b43; }
QTabBar::tab:hover { color: #d6f5ff; background: #12253b; }
QTabBar::tab:disabled { color: #526981; }
QStatusBar { background: #080f1b; color: #7893ae; border-top: 1px solid #1c3045; padding: 3px 12px; font-size: 11px; }
QStatusBar::item { border: none; }
QSplitter::handle { background: transparent; width: 10px; }
QSplitter::handle:hover { background: #19354c; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #0b1624; width: 8px; margin: 0; }
QScrollBar:horizontal { background: #0b1624; height: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #34516b; border-radius: 3px; min-height: 26px; }
QScrollBar::handle:horizontal { background: #34516b; border-radius: 3px; min-width: 26px; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: #53829f; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical, QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }
QDockWidget { color: #e8f3ff; }
QDockWidget::title { background: #15263b; padding: 9px; border-bottom: 1px solid #294058; }
QGroupBox { border: 1px solid #294058; border-radius: 4px; margin-top: 16px; padding-top: 14px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #a3c8e4; }
QHeaderView::section { background: #15263b; color: #a3bfd8; border: none; border-bottom: 1px solid #355775; padding: 9px; }
QTableWidget { background: #0a1320; alternate-background-color: #101e30; border: 1px solid #294058; gridline-color: #21374d; selection-background-color: #19476a; selection-color: #b8f1ff; }
QTableCornerButton::section { background: #15263b; border: none; }
QCheckBox, QRadioButton { spacing: 7px; }
QCheckBox:focus, QRadioButton:focus { color: #98e9ff; }
QToolTip { background: #14273b; color: #e8f3ff; border: 1px solid #4381a4; padding: 6px; }
QSlider::groove:horizontal { height: 4px; background: #243d55; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #449cce; border-radius: 2px; }
QSlider::handle:horizontal { background: #95e8fb; border: 1px solid #b7f0ff; width: 12px; margin: -5px 0; border-radius: 3px; }
QSlider::handle:horizontal:hover { background: #c7f5ff; }
QSlider::handle:horizontal:disabled { background: #4b687e; border-color: #5c788e; }
QProgressBar { background: #0a1320; border: 1px solid #294058; border-radius: 3px; text-align: center; }
QProgressBar::chunk { background: #2386ed; }
"""
