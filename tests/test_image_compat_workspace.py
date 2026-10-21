"""The compatibility tool respects edit guards and refreshes repaired assets."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Signal, Qt
from PySide6.QtWidgets import QDialog
from core import i18n
from core.image_compat import scan_skin_images, repair_skin_images
from ui.main_window import MainWindow
from tests.test_online_update_ui import APP, FakeUpdateService


class ImageRepairWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)/'skin'
        self.root.mkdir()
        (self.root/'skin.ini').write_text('[General]\nName: Repair fixture\n[Mania]\nKeys: 4\n')
        self.body = self.root/'body.png'
        Image.new('RGBA', (12, 64), (120, 130, 140, 180)).save(self.body, format='TIFF')
        QCoreApplication.setOrganizationName('OsuSkinEditorTests')
        QCoreApplication.setApplicationName('ImageRepairWorkspace')
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(Path(self.temp.name)/'settings'))
        QSettings().clear()
        QSettings().setValue('ui/language', 'zh-CN')
        with patch('ui.main_window.UpdateService', FakeUpdateService):
            self.window = MainWindow()
        self.window.setAttribute(Qt.WA_DontShowOnScreen)
        self.window.show()
        self.window._network_check_timer.stop()
        APP.processEvents()

    def tearDown(self):
        self.window._image_compat_dialog = None
        self.window._archive_controller._busy = False
        self.window.mania_ini_dock._dirty = False
        self.window.mania_design_dock._dirty = False
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def load(self):
        self.assertTrue(self.window.load_skin(str(self.root)))

    def test_menu_requires_skin_and_tracks_language(self):
        self.assertFalse(self.window.act_image_compat.isEnabled())
        self.load()
        self.assertTrue(self.window.act_image_compat.isEnabled())
        self.assertIn('图片兼容性', self.window.act_image_compat.text())
        self.window.on_change_language('en-US')
        self.assertIn('compatibility', self.window.act_image_compat.text().lower())

    def test_repair_action_reloads_same_skin_and_keeps_ini(self):
        self.load()
        before_ini = (self.root/'skin.ini').read_bytes()
        roots = []

        class RepairDialog(QDialog):
            assets_changed = Signal()
            def __init__(self, root, parent):
                super().__init__(parent)
                roots.append(root)
            def exec(dialog):
                scan = scan_skin_images(self.root)
                result = repair_skin_images(self.root, scan.issues)
                self.assertEqual(result.repaired, ('body.png',))
                dialog.assets_changed.emit()
                return QDialog.Accepted

        with patch('ui.main_window.ImageCompatibilityDialog', RepairDialog), \
                patch.object(self.window, 'load_skin', wraps=self.window.load_skin) as reload:
            self.window.act_image_compat.trigger()
        self.assertEqual(roots, [self.root])
        reload.assert_called_once_with(str(self.root), check_dirty=False)
        self.assertIsNone(self.window._image_compat_dialog)
        self.assertEqual((self.root/'skin.ini').read_bytes(), before_ini)
        with Image.open(self.body) as image:
            self.assertEqual(image.format, 'PNG')
        self.assertEqual(scan_skin_images(self.root).issues, ())

    def test_scan_without_repair_does_not_reload_or_write(self):
        self.load()
        original = self.body.read_bytes()
        with patch('ui.main_window.ImageCompatibilityDialog') as factory, \
                patch.object(self.window, 'load_skin') as reload:
            self.window._open_image_compatibility()
        reload.assert_not_called()
        factory.assert_called_once()
        self.assertEqual(self.body.read_bytes(), original)

    def test_dirty_guards_can_cancel_before_dialog_is_created(self):
        self.load()
        with patch.object(self.window, '_confirm_design_navigation', return_value=False), \
                patch('ui.main_window.ImageCompatibilityDialog') as factory:
            self.window._open_image_compatibility()
        factory.assert_not_called()
        with patch.object(self.window.mania_ini_dock, '_confirm_discard_if_dirty', return_value=False), \
                patch('ui.main_window.ImageCompatibilityDialog') as factory:
            self.window._open_image_compatibility()
        factory.assert_not_called()

    def test_archive_and_update_locks_block_programmatic_entry(self):
        self.load()
        with patch('ui.main_window.ImageCompatibilityDialog') as factory:
            self.window._archive_controller._busy = True
            self.window._open_image_compatibility()
            self.window._archive_controller._busy = False
            self.window._update_edit_locked = True
            self.window._open_image_compatibility()
            self.window._update_edit_locked = False
        factory.assert_not_called()

    def test_parent_close_waits_for_active_tool_to_cancel(self):
        self.load()
        with patch('ui.main_window.ImageCompatibilityDialog') as factory:
            dialog = factory.return_value
            self.window._image_compat_dialog = dialog
            self.assertFalse(self.window.close())
            dialog.reject.assert_called_once()
        self.assertTrue(self.window._close_after_image_compat)
        self.assertFalse(self.window._closing)
        self.window._close_after_image_compat = False


if __name__ == '__main__':
    unittest.main()
