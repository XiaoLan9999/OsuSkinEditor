import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from core import i18n
from ui.main_window import MainWindow
from ui.theme import apply_theme


class LanguageCornerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.profile = tempfile.TemporaryDirectory()
        QCoreApplication.setOrganizationName("OsuSkinEditorTests")
        QCoreApplication.setApplicationName("LanguageCorner")
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, cls.profile.name)
        apply_theme(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.profile.cleanup()

    def setUp(self):
        QSettings().clear()
        i18n.load_language("zh-CN")
        self.window = MainWindow()
        self.window.setAttribute(Qt.WA_DontShowOnScreen)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.mania_ini_dock._dirty = False
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.window = None

    def test_language_is_available_at_top_right_without_loading_a_skin(self):
        button = self.window.language_button
        self.assertIs(self.window.menuBar().cornerWidget(Qt.TopRightCorner), button)
        self.assertEqual(button.text(), "语言 / Language")
        self.assertEqual(button.accessibleName(), "语言 / Language")
        self.assertTrue(button.isEnabled())
        self.assertTrue(button.isVisible())
        self.assertEqual(button.popupMode(), QToolButton.InstantPopup)
        self.assertIs(button.menu(), self.window.lang_menu)
        old_x = button.x()
        self.window.resize(self.window.width() + 200, self.window.height())
        self.app.processEvents()
        self.assertGreater(button.x(), old_x)

    def test_settings_retains_centering_without_duplicate_language_entry(self):
        menus = [action.menu() for action in self.window.settings_menu.actions()]
        self.assertIn(self.window.center_menu, menus)
        self.assertNotIn(self.window.lang_menu, menus)
        self.assertEqual([a.text() for a in self.window.lang_menu.actions()], ["中文", "English"])

    def test_language_action_switches_ui_and_keeps_bilingual_trigger_and_one_check(self):
        self.assertTrue(self.window.act_lang_zh.isChecked())
        self.assertFalse(self.window.act_lang_en.isChecked())
        self.window.act_lang_en.trigger()
        self.assertEqual(i18n.lang(), "en-US")
        self.assertEqual(QSettings().value("ui/language"), "en-US")
        self.assertEqual(self.window.btn_open.text(), "Open skin")
        self.assertEqual(self.window.language_button.text(), "语言 / Language")
        self.assertEqual(self.window.lang_menu.title(), "语言 / Language")
        self.assertTrue(self.window.act_lang_en.isChecked())
        self.assertFalse(self.window.act_lang_zh.isChecked())
        self.window.act_lang_zh.trigger()
        self.assertEqual(i18n.lang(), "zh-CN")
        self.assertTrue(self.window.act_lang_zh.isChecked())
        self.assertFalse(self.window.act_lang_en.isChecked())

    def test_language_popup_can_be_opened_with_keyboard(self):
        button = self.window.language_button
        self.assertEqual(button.focusPolicy(), Qt.StrongFocus)
        button.setFocus()
        observed = []

        def inspect_and_close():
            observed.append(self.window.lang_menu.isVisible())
            self.window.lang_menu.hide()

        QTimer.singleShot(0, inspect_and_close)
        QTest.keyClick(button, Qt.Key_Space)
        self.app.processEvents()
        self.assertEqual(observed, [True])


if __name__ == "__main__":
    unittest.main()
