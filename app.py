
# -*- coding: utf-8 -*-
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication
from ui.main_window import MainWindow
from core import i18n
import sys

def main():
    QCoreApplication.setOrganizationName("XiaoLan9999")
    QCoreApplication.setApplicationName("osu XiaoLan Skin Editor")
    app = QApplication(sys.argv)
    i18n.load_language()  # load last chosen language
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
