# utf-8
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from PySide6.QtCore import QCoreApplication
from ui.main_window import MainWindow
from core import i18n
from ui.theme import apply_theme
from core.resources import brand_image_path
import sys, os

# 兼容源码运行 & PyInstaller(onefile) 的资源定位
def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(__file__))
    return os.path.join(base, rel)

def main():
    QCoreApplication.setOrganizationName("XiaoLan9999")
    QCoreApplication.setApplicationName("osu XiaoLan Skin Editor")
    app = QApplication(sys.argv)
    apply_theme(app)

    app.setWindowIcon(QIcon(brand_image_path()))

    i18n.load_language()  # load last chosen language
    win = MainWindow()

    try:
        win.setWindowIcon(QIcon(brand_image_path()))
    except Exception:
        pass

    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
