"""Lifecycle and input checks for moving the existing preview into a window"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import warnings

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from core import i18n
from ui.main_window import MainWindow


APP = QApplication.instance() or QApplication([])


class DetachedPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.source = self.directory / "skin"
        self.source.mkdir()
        (self.source / "skin.ini").write_text(
            "[General]\nName: Detached fixture\nVersion: 2.7\n"
            "[Mania]\nKeys: 4\nHitPosition: 402\n[Mania]\nKeys: 7\nHitPosition: 410\n",
            encoding="utf-8")
        for name in ("mania-note1", "mania-note2", "hitcircle"):
            Image.new("RGBA", (48, 48), (80, 190, 220, 255)).save(self.source / f"{name}.png")
        QCoreApplication.setOrganizationName("OsuSkinEditorTests")
        QCoreApplication.setApplicationName("DetachedPreview")
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope,
                          str(self.directory / "settings"))
        QSettings().clear()
        i18n.load_language("zh-CN")
        self.window = MainWindow()
        self.window.setAttribute(Qt.WA_DontShowOnScreen)
        self.window.show()
        self.activate(self.window)
        APP.processEvents()
        self.assertTrue(self.window.load_skin(str(self.source)))
        self.window.tabs.setCurrentIndex(1)
        APP.processEvents()
        self.preview = self.window.mania_preview
        self.controls = self.window.mania_playback
        self.freeze_timers()
        self.original_files = self.file_hashes()

    def tearDown(self):
        if isValid(self.window):
            self.window.mania_design_dock._dirty = False
            self.window.mania_ini_dock._dirty = False
            self.window.close()
            self.window.deleteLater()
        self.flush_deletes()
        self.temp.cleanup()

    @staticmethod
    def activate(window):
        window.activateWindow()
        # Offscreen windows have no native window manager to activate them
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            APP.setActiveWindow(window)

    @staticmethod
    def flush_deletes():
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def freeze_timers(self):
        self.window.std_preview.timer.stop()
        self.preview.timer.stop()
        self.preview._clock.invalidate()

    def detach(self):
        self.window._detach_preview()
        host = self.window._preview_window
        self.assertIsNotNone(host)
        host.setAttribute(Qt.WA_DontShowOnScreen)
        self.activate(host)
        APP.processEvents()
        self.freeze_timers()
        return host

    def file_hashes(self):
        return {p.relative_to(self.source).as_posix(): sha256(p.read_bytes()).hexdigest()
                for p in self.source.rglob("*") if p.is_file()}

    def score_snapshot(self):
        game = self.preview.game
        return (game.score, game.combo, game.max_combo, dict(game.counts), game.now_ms,
                tuple((note.id, note.status) for note in game.notes))

    def manual_mode(self):
        self.controls.test_mode.setCurrentIndex(self.controls.test_mode.findData("play"))
        self.freeze_timers()

    def start_manual(self):
        QTest.mouseClick(self.preview, Qt.LeftButton)
        self.freeze_timers()
        self.assertTrue(self.preview.hasFocus())
        self.assertTrue(self.preview._playing)
        self.assertTrue(self.preview._input_armed)

    def test_detach_moves_same_panel_controls_widgets_game_and_cache(self):
        panel = self.window.preview_panel
        widgets = (self.window.tabs, self.preview, self.window.std_preview, self.controls)
        game, assets = self.preview.game, self.preview._assets
        host = self.detach()
        self.assertIs(host.centralWidget(), panel)
        self.assertIs(panel.window(), host)
        for widget in widgets:
            self.assertTrue(panel.isAncestorOf(widget))
        self.assertIs(self.preview.game, game)
        self.assertIs(self.preview._assets, assets)
        self.assertEqual(self.window.splitter.count(), 2)
        self.assertIs(self.window.splitter.widget(1), self.window._preview_placeholder)
        self.assertTrue(self.window._preview_placeholder.isVisible())
        self.assertTrue(panel.isVisible())
        self.assertFalse(host.isModal())
        self.assertFalse(host.isFullScreen())
        self.assertEqual(self.file_hashes(), self.original_files)

    def test_detach_twice_reuses_host_instead_of_creating_another_renderer(self):
        host = self.detach()
        self.window._detach_preview()
        self.assertIs(self.window._preview_window, host)
        self.assertIs(host.centralWidget(), self.window.preview_panel)
        self.assertEqual(self.window.splitter.count(), 2)

    def test_restore_returns_same_panel_at_original_splitter_position_and_remains_renderable(self):
        panel, game = self.window.preview_panel, self.preview.game
        self.window.btn_pause.setChecked(True)
        host = self.detach()
        self.window._restore_preview()
        self.flush_deletes()
        self.assertIsNone(self.window._preview_window)
        self.assertIs(self.window.splitter.widget(1), panel)
        self.assertIs(panel.window(), self.window)
        self.assertIs(self.preview.game, game)
        self.assertTrue(isValid(panel))
        self.assertTrue(panel.isVisible())
        self.assertFalse(self.preview.grab().isNull())
        self.assertTrue(not isValid(host) or not host.isVisible())
        self.assertTrue(self.window.isVisible())

    def test_host_close_restores_panel_without_closing_main_window(self):
        host = self.detach()
        panel = self.window.preview_panel
        self.assertTrue(host.close())
        self.flush_deletes()
        self.assertIsNone(self.window._preview_window)
        self.assertIs(self.window.splitter.widget(1), panel)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(isValid(self.preview))
        self.assertFalse(self.preview.grab().isNull())

    def test_repeated_detach_restore_preserves_controls_and_single_session(self):
        panel, game, controls = self.window.preview_panel, self.preview.game, self.controls
        self.window.btn_pause.setChecked(True)
        for _ in range(3):
            self.detach()
            self.window._restore_preview()
            self.flush_deletes()
            self.assertIs(self.window.preview_panel, panel)
            self.assertIs(self.preview.game, game)
            self.assertIs(self.window.mania_playback, controls)
            self.assertEqual(self.window.splitter.count(), 2)
            self.assertEqual(self.window.splitter.indexOf(panel), 1)
        self.controls.speed.setValue(33)
        self.assertEqual(self.preview.scroll_speed, 33)
        self.assertEqual(self.file_hashes(), self.original_files)

    def test_manual_reparent_pauses_input_without_resetting_score_or_notes(self):
        self.manual_mode()
        self.start_manual()
        self.preview.t = 2000
        self.preview._advance_game()
        QTest.keyPress(self.preview, Qt.Key_D)
        self.freeze_timers()
        self.assertGreater(self.preview.game.score, 0)
        before = self.score_snapshot()
        self.detach()
        self.assertEqual(self.score_snapshot(), before)
        self.assertFalse(self.preview._playing)
        self.assertFalse(self.preview._input_armed)
        self.assertFalse(self.preview.game.pressed_lanes)
        self.assertTrue(self.window.btn_pause.isChecked())
        self.window._restore_preview()
        self.assertEqual(self.score_snapshot(), before)
        self.assertFalse(self.preview._playing)
        self.assertFalse(self.preview._input_armed)

    def test_detached_manual_toolbar_resume_and_pause_keep_focus_contract(self):
        self.manual_mode()
        self.detach()
        QTest.mouseClick(self.window.btn_pause, Qt.LeftButton)
        self.freeze_timers()
        self.assertTrue(self.preview.hasFocus())
        self.assertTrue(self.preview._playing)
        self.assertTrue(self.preview._input_armed)
        QTest.mouseClick(self.window.btn_pause, Qt.LeftButton)
        self.freeze_timers()
        self.assertFalse(self.preview._playing)
        self.assertTrue(self.window.btn_pause.isChecked())

    def test_escape_pauses_mania_before_exiting_fullscreen_and_never_closes_host(self):
        self.manual_mode()
        host = self.detach()
        host.toggle_fullscreen()
        APP.processEvents()
        self.activate(host)
        self.assertTrue(host.isFullScreen())
        self.start_manual()
        QTest.keyClick(self.preview, Qt.Key_Escape)
        self.freeze_timers()
        self.assertFalse(self.preview._playing)
        self.assertTrue(host.isFullScreen())
        self.assertIs(self.window._preview_window, host)
        QTest.keyClick(self.preview, Qt.Key_Escape)
        APP.processEvents()
        self.freeze_timers()
        self.assertFalse(host.isFullScreen())
        self.assertIs(self.window._preview_window, host)
        self.assertTrue(host.isVisible())

    def test_f11_works_from_focused_child_and_is_scoped_to_detached_window(self):
        host = self.detach()
        self.controls.speed.setFocus()
        QTest.keyClick(self.controls.speed, Qt.Key_F11)
        APP.processEvents()
        self.assertTrue(host.isFullScreen())
        QTest.keyClick(APP.focusWidget() or host, Qt.Key_F11)
        APP.processEvents()
        self.assertFalse(host.isFullScreen())
        self.activate(self.window)
        self.window.asset_search.setFocus()
        QTest.keyClick(self.window.asset_search, Qt.Key_F11)
        APP.processEvents()
        self.assertFalse(host.isFullScreen())

    def test_language_changes_and_skin_reload_still_reach_the_moved_widgets(self):
        self.detach()
        self.window.btn_pause.setChecked(True)
        panel = self.window.preview_panel
        self.window.act_lang_en.trigger()
        self.freeze_timers()
        self.assertEqual(self.window.btn_pause.text(), i18n.t("workspace.resume"))
        self.assertEqual(self.controls.test_mode.currentText(), i18n.t("playtest.mode_auto"))
        replacement = self.directory / "replacement"
        replacement.mkdir()
        (replacement / "skin.ini").write_text(
            "[General]\nName: Reload while detached\n[Mania]\nKeys: 7\n", encoding="utf-8")
        self.assertTrue(self.window.load_skin(str(replacement)))
        self.freeze_timers()
        self.assertIs(self.window._preview_window.centralWidget(), panel)
        self.assertEqual(self.preview.keys, 7)
        self.assertEqual(self.controls.keys.currentData(), 7)
        self.assertEqual(self.preview.skin.root, replacement)
        self.assertEqual(self.window.std_preview.skin.root, replacement)
        self.assertEqual(self.file_hashes(), self.original_files)

    def test_open_and_reload_shortcuts_work_in_detached_window_without_duplicate_dispatch(self):
        self.detach()
        self.preview.setFocus()
        with patch.object(self.window, "load_skin", wraps=self.window.load_skin) as load:
            QTest.keyClick(self.preview, Qt.Key_F5)
            APP.processEvents()
            self.assertEqual(load.call_count, 1)
        with patch("ui.main_window.QFileDialog.getExistingDirectory", return_value="") as choose:
            QTest.keyClick(self.preview, Qt.Key_O, Qt.ControlModifier)
            APP.processEvents()
            self.assertEqual(choose.call_count, 1)
        self.assertEqual(self.file_hashes(), self.original_files)

    def test_cancel_main_close_preserves_detached_preview_and_open_windows(self):
        host = self.detach()
        panel = self.window.preview_panel
        with patch.object(self.window.mania_ini_dock, "_confirm_discard_if_dirty", return_value=True), \
                patch.object(self.window, "_confirm_design_navigation", return_value=False):
            self.assertFalse(self.window.close())
        self.assertIs(self.window._preview_window, host)
        self.assertIs(host.centralWidget(), panel)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(host.isVisible())

    def test_accepted_main_close_leaves_no_detached_host_or_running_preview_timer(self):
        host = self.detach()
        self.assertTrue(self.window.close())
        self.flush_deletes()
        self.assertIsNone(self.window._preview_window)
        self.assertFalse(self.window.isVisible())
        self.assertTrue(not isValid(host) or not host.isVisible())
        self.assertFalse(self.preview.timer.isActive())
        self.assertFalse(self.window.std_preview.timer.isActive())


if __name__ == "__main__":
    unittest.main()
