# utf-8
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from PySide6.QtCore import QCoreApplication, QTimer
from ui.main_window import MainWindow
from core import i18n
from ui.theme import apply_theme
from core.resources import brand_image_path
from core.update_announcements import CURRENT_BUILD_ID
from core.update_launch import acknowledge_update_startup
import argparse
import sys, os

# 兼容源码运行 & PyInstaller(onefile) 的资源定位
def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(__file__))
    return os.path.join(base, rel)

def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--update-ready-file")
    parser.add_argument("--update-nonce")
    update_args, qt_args = parser.parse_known_args(sys.argv[1:])
    QCoreApplication.setOrganizationName("XiaoLan9999")
    QCoreApplication.setApplicationName("osu XiaoLan Skin Editor")
    QCoreApplication.setApplicationVersion(CURRENT_BUILD_ID)
    app = QApplication([sys.argv[0], *qt_args])
    apply_theme(app)

    app.setWindowIcon(QIcon(brand_image_path()))

    i18n.load_language()  # load last chosen language
    win = MainWindow()

    try:
        win.setWindowIcon(QIcon(brand_image_path()))
    except Exception:
        pass

    win.show()
    QTimer.singleShot(0, lambda: acknowledge_update_startup(update_args.update_ready_file, update_args.update_nonce))
    win.schedule_update_announcement()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
