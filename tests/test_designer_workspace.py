import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtWidgets import QApplication
from core import i18n
from core.mania_designer import ManiaDesign
from core.skin_ini import SkinIni
from ui.main_window import MainWindow

APP = QApplication.instance() or QApplication([])


class DesignerWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.source = self.directory / "skin"
        self.source.mkdir()
        (self.source / "skin.ini").write_text("[General]\nName: Designer fixture\n[Mania]\nKeys: 4\nHitPosition: 425\n[Mania]\nKeys: 7\nHitPosition: 410\n", encoding="utf-8")
        QCoreApplication.setOrganizationName("OsuSkinEditorTests")
        QCoreApplication.setApplicationName("DesignerWorkspace")
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(self.directory / "settings"))
        i18n.load_language("zh-CN")
        self.window = MainWindow()
        self.window.setAttribute(Qt.WA_DontShowOnScreen)
        self.window.show(); APP.processEvents()
        self.assertTrue(self.window.load_skin(str(self.source)))

    def tearDown(self):
        self.window.mania_design_dock._dirty = False
        self.window.mania_ini_dock._dirty = False
        self.window.close(); self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def test_playback_controls_change_preview_without_writing_skin(self):
        before = (self.source / "skin.ini").read_bytes()
        self.window.tabs.setCurrentIndex(1)
        controls = self.window.mania_playback
        controls.speed.setValue(35); controls.tempo.setValue(180); controls.guide.setChecked(True)
        self.assertEqual(self.window.mania_preview.scroll_speed, 35)
        self.assertEqual(self.window.mania_preview.demo_bpm, 180)
        self.assertTrue(self.window.mania_preview._show_hit_guide)
        self.assertEqual((self.source / "skin.ini").read_bytes(), before)
        self.window.tabs.setCurrentIndex(0)
        self.assertFalse(controls.isVisible())

    def test_cancelled_design_key_switch_restores_both_selectors_and_editor(self):
        dock = self.window.mania_design_dock
        dock.top_height.setValue(40)
        with patch.object(dock, "confirm_discard_if_dirty", return_value=False):
            self.window._request_mania_keys(7)
        self.assertEqual(self.window.mania_preview.keys, 4)
        self.assertEqual(self.window.mania_ini_dock.cmb_keys.currentData(), 4)
        self.assertEqual(self.window.mania_ini_dock._current_view_k, 4)
        self.assertEqual(self.window.mania_playback.keys.currentData(), 4)
        self.assertEqual(dock.options().top_mask_height, 40)

    def test_cancelled_design_close_keeps_window(self):
        self.window.mania_design_dock.top_height.setValue(20)
        with patch.object(self.window.mania_design_dock, "confirm_discard_if_dirty", return_value=False):
            self.assertFalse(self.window.close())
        self.assertTrue(self.window.isVisible())

    def test_export_loads_copy_and_does_not_apply_design_twice(self):
        original = (self.source / "skin.ini").read_bytes()
        dock = self.window.mania_design_dock
        self.window._request_mania_keys(7)
        dock.top_height.setValue(80)
        dock.top_fade.setValue(25)
        destination = self.directory / "exported"
        with patch.object(dock, "_choose_export_destination", return_value=destination):
            self.assertTrue(dock._export_copy())
        self.assertEqual(self.window.skin.root, destination)
        self.assertEqual(self.window.mania_preview.keys, 7)
        self.assertEqual(dock.options(), ManiaDesign())
        self.assertEqual(self.window.mania_preview.design_options, ManiaDesign())
        self.assertIsNotNone(self.window.mania_preview._design_overlay)
        self.assertEqual((self.source / "skin.ini").read_bytes(), original)
        self.assertIn("StageBottom", SkinIni.read(destination / "skin.ini").mania_get(7))
        self.assertNotIn("StageBottom", SkinIni.read(destination / "skin.ini").mania_get(4))

    def test_invalid_design_is_visible_and_blocks_export_until_fixed(self):
        dock = self.window.mania_design_dock
        self.window._request_mania_keys(7)  # HitPosition410 -180 is below240
        dock.receptor_raise.setValue(180)
        self.assertTrue(dock._preview_error)
        self.assertFalse(dock.export_button.isEnabled())
        dock.receptor_raise.setValue(0)
        self.assertEqual(dock._preview_error, "")
        self.assertTrue(dock.export_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
