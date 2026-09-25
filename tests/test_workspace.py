import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings, QCoreApplication, Qt, QMimeData, QUrl, QEvent
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication, QMessageBox
from core import i18n
from ui.main_window import MainWindow
from ui.theme import apply_theme


class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.profile = tempfile.TemporaryDirectory()
        QCoreApplication.setOrganizationName("OsuSkinEditorTests")
        QCoreApplication.setApplicationName("Workspace")
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, cls.profile.name)
        apply_theme(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.profile.cleanup()

    def setUp(self):
        QSettings().clear()
        i18n.load_language("zh-CN")
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "skin.ini").write_text("[General]\nName: Test skin\n[Mania]\nKeys: 4\n", encoding="utf-8")
        for name in ("hitcircle", "mania-note1", "menu-background"):
            img = QImage(64, 64, QImage.Format_ARGB32)
            img.fill(QColor("white")); img.save(str(self.root / (name + ".png")))
        self.window = MainWindow()
        self.window.setAttribute(Qt.WA_DontShowOnScreen)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.mania_ini_dock._dirty = False
        self.window.close()
        self.window.deleteLater()
        # Complete native QObject deletion before the next theme/window setup.
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.window = None
        self.temp.cleanup()

    def test_load_search_category_and_image_inspection(self):
        self.assertTrue(self.window.load_skin(str(self.root)))
        self.assertEqual(self.window.skin_title.text(), "Test skin")
        self.assertEqual(self.window.preview_stack.currentIndex(), 1)
        self.assertEqual(self.window.asset_list.count(), 3)
        self.window.asset_list.setCurrentRow(0)
        self.assertFalse(self.window.asset_inspector.pixmap.isNull())
        self.window.asset_search.setText("MANIA")
        self.assertEqual(self.window.asset_count.text(), "1 / 3")
        self.window.asset_filter.setCurrentIndex(1)
        self.assertEqual(self.window.asset_count.text(), "0 / 3")
        self.assertTrue(self.window.no_results.isVisible())

    def test_failed_load_keeps_existing_skin_and_preview(self):
        self.window.load_skin(str(self.root))
        old = self.window.skin
        with patch.object(QMessageBox, "critical"):
            self.assertFalse(self.window.load_skin(str(self.root / "missing")))
        self.assertIs(self.window.skin, old)
        self.assertIs(self.window.std_preview.skin, old)
        self.assertEqual(self.window.asset_list.count(), 3)

    def test_cancelled_dirty_guard_keeps_workspace_and_window(self):
        self.window.load_skin(str(self.root))
        old = self.window.skin
        with patch.object(self.window.mania_ini_dock, "_confirm_discard_if_dirty", return_value=False):
            self.assertFalse(self.window.load_skin(str(self.root)))
            self.assertFalse(self.window.close())
        self.assertIs(self.window.skin, old)
        self.assertTrue(self.window.isVisible())

    def test_pause_survives_mode_switch(self):
        self.window.load_skin(str(self.root))
        self.app.processEvents()
        self.window.btn_pause.setChecked(True)
        self.window.tabs.setCurrentIndex(1)
        self.app.processEvents()
        self.assertFalse(self.window.std_preview.timer.isActive())
        self.assertFalse(self.window.mania_preview.timer.isActive())
        self.window.btn_pause.setChecked(False)
        self.app.processEvents()
        self.assertTrue(self.window.mania_preview.timer.isActive())
        self.assertFalse(self.window.std_preview.timer.isActive())

    def test_language_switch_translates_new_and_addon_controls(self):
        self.window.on_change_language("en-US")
        self.assertEqual(self.window.btn_open.text(), "Open skin")
        self.assertEqual(self.window.act_center_image.text(), "Approach center: Image")
        self.assertEqual(self.window.asset_filter.itemText(0), "All images")

    def test_drop_accepts_skin_ini_and_rejects_non_skin_directory(self):
        mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(self.root / "skin.ini"))])
        self.assertEqual(self.window._dropped_skin(mime), self.root)
        mime.setUrls([QUrl("https://example.com/skin.ini")])
        self.assertIsNone(self.window._dropped_skin(mime))

    def test_uppercase_asset_names_use_the_correct_category(self):
        (self.root / "mania-note1.png").rename(self.root / "Mania-Note2.PNG")
        self.window.load_skin(str(self.root))
        self.window.asset_filter.setCurrentIndex(2)
        self.assertEqual(self.window.asset_count.text(), "1 / 3")

    def test_discard_before_asset_manager_restores_saved_form(self):
        self.window.load_skin(str(self.root))
        dock = self.window.mania_ini_dock
        original = dock.spn_hit_pos.value()
        dock.spn_hit_pos.setValue(original + 20)
        self.assertTrue(dock._dirty)
        with patch.object(dock, "_confirm_discard_if_dirty", return_value=True), \
                patch("ui.main_window.AssetsManagerDialog"):
            self.window._open_assets_manager()
        self.assertEqual(dock.spn_hit_pos.value(), original)
        self.assertFalse(dock._dirty)


if __name__ == "__main__":
    unittest.main()
