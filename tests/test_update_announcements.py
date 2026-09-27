"""Local announcement policy and nonmodal viewer lifecycle integration"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import warnings

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from shiboken6 import isValid

from core import i18n
from core.update_announcements import (
    CURRENT_BUILD_ID, load_announcements, mark_announcements_seen, should_show_announcement,
)
from ui.main_window import MainWindow
from ui.update_announcements import UpdateAnnouncementsDialog


APP = QApplication.instance() or QApplication([])


class AnnouncementPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / "settings.ini"), QSettings.IniFormat)
        self.entries = load_announcements()

    def tearDown(self):
        self.settings.sync()
        self.temp.cleanup()

    def test_unread_current_build_only_shows_when_present_in_local_catalog(self):
        self.assertIsInstance(self.entries, tuple)
        self.assertTrue(any(entry["id"] == CURRENT_BUILD_ID for entry in self.entries))
        self.assertTrue(should_show_announcement(self.settings, self.entries))
        self.assertFalse(should_show_announcement(self.settings, (), CURRENT_BUILD_ID))
        self.assertFalse(should_show_announcement(self.settings, self.entries, "not-bundled"))
        self.assertNotEqual(CURRENT_BUILD_ID, "v1.5")

    def test_disabled_auto_display_accepts_persisted_false_boolean_and_string(self):
        for disabled in (False, "false"):
            self.settings.setValue("updates/show_on_start", disabled)
            self.assertFalse(should_show_announcement(self.settings, self.entries))
        self.settings.setValue("updates/show_on_start", True)
        self.assertTrue(should_show_announcement(self.settings, self.entries))

    def test_seen_ids_are_deduplicated_and_survive_relaunch_and_downgrade(self):
        mark_announcements_seen(self.settings, [CURRENT_BUILD_ID, "older-build", CURRENT_BUILD_ID])
        self.assertFalse(should_show_announcement(self.settings, self.entries))
        self.settings.sync()
        reopened = QSettings(self.settings.fileName(), QSettings.IniFormat)
        self.assertEqual(set(reopened.value("updates/seen_ids", [], list)),
                         {CURRENT_BUILD_ID, "older-build"})
        old = deepcopy(self.entries[0])
        old["id"] = "older-build"
        self.assertFalse(should_show_announcement(reopened, (old,), "older-build"))

    def test_new_bundled_id_shows_again_without_forgetting_earlier_read_ids(self):
        mark_announcements_seen(self.settings, [CURRENT_BUILD_ID])
        upcoming = deepcopy(next(entry for entry in self.entries if entry["id"] == CURRENT_BUILD_ID))
        upcoming["id"] = "preview-next-test"
        catalog = (*self.entries, upcoming)
        self.assertTrue(should_show_announcement(self.settings, catalog, upcoming["id"]))
        mark_announcements_seen(self.settings, [upcoming["id"]])
        self.assertFalse(should_show_announcement(self.settings, catalog, upcoming["id"]))
        self.assertFalse(should_show_announcement(self.settings, catalog, CURRENT_BUILD_ID))

    def test_missing_or_malformed_bundled_catalog_does_not_break_startup(self):
        path = Path(self.temp.name) / "updates.json"
        self.assertEqual(load_announcements(path), ())
        for invalid in ("{broken", "[]", '{"schema_version":99,"entries":[]}',
                        '{"schema_version":1,"entries":[null,{"id":"invalid"}]}'):
            path.write_text(invalid, encoding="utf-8")
            self.assertEqual(load_announcements(path), ())


class AnnouncementWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "skin"
        self.source.mkdir()
        (self.source / "skin.ini").write_text(
            "[General]\nName: Announcement fixture\nVersion: 2.7\n[Mania]\nKeys: 4\n",
            encoding="utf-8")
        self.original = self.file_hashes()
        QCoreApplication.setOrganizationName("OsuSkinEditorTests")
        QCoreApplication.setApplicationName("UpdateAnnouncements")
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(self.root / "settings"))
        QSettings().clear()
        i18n.load_language("zh-CN")
        self.windows = []
        self.window = self.new_window()

    def tearDown(self):
        for window in self.windows:
            if isValid(window):
                window.mania_ini_dock._dirty = False
                window.mania_design_dock._dirty = False
                window.close()
                window.deleteLater()
        self.flush()
        self.temp.cleanup()

    @staticmethod
    def flush():
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    @staticmethod
    def activate(window):
        window.activateWindow()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            APP.setActiveWindow(window)

    def new_window(self):
        window = MainWindow()
        self.windows.append(window)
        window.setAttribute(Qt.WA_DontShowOnScreen)
        window.show()
        self.activate(window)
        APP.processEvents()
        return window

    def file_hashes(self):
        return {p.relative_to(self.source).as_posix(): sha256(p.read_bytes()).hexdigest()
                for p in self.source.rglob("*") if p.is_file()}

    @staticmethod
    def startup_check(window):
        # Simulate the owned timer firing instead of introducing wall time
        window._update_notice_timer.stop()
        window._maybe_show_update_announcement()

    def test_explicit_startup_shows_current_build_once_then_close_persists_read_state(self):
        self.assertIsNone(self.window._update_dialog)
        self.window.schedule_update_announcement()
        self.assertTrue(self.window._update_notice_timer.isActive())
        self.startup_check(self.window)
        dialog = self.window._update_dialog
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.isVisible())
        self.assertFalse(dialog.isModal())
        self.assertIn(CURRENT_BUILD_ID, dialog.viewed_ids)
        dialog.close()
        self.assertIsNone(self.window._update_dialog)
        self.assertIn(CURRENT_BUILD_ID, QSettings().value("updates/seen_ids", [], list))
        second = self.new_window()
        self.startup_check(second)
        self.assertIsNone(second._update_dialog)

    def test_hidden_dialog_does_not_mark_unseen_announcements_as_viewed(self):
        dialog = UpdateAnnouncementsDialog(self.window.settings, parent=self.window)
        self.assertEqual(dialog.viewed_ids, set())
        if dialog.version_list.count() > 1:
            dialog.version_list.setCurrentRow(dialog.version_list.count()-1)
        self.assertEqual(dialog.viewed_ids, set())
        dialog.deleteLater()
        self.flush()
        self.assertEqual(QSettings().value("updates/seen_ids", [], list), [])

    def test_empty_catalog_has_a_closable_manual_viewer_without_marking_read(self):
        dialog = UpdateAnnouncementsDialog(self.window.settings, entries=(), parent=self.window)
        dialog.show()
        self.assertEqual(dialog.entries, ())
        self.assertEqual(dialog.version_list.count(), 0)
        self.assertTrue(dialog.body.toPlainText())
        self.assertFalse(dialog.viewed_ids)
        dialog.close_button.click()
        self.assertFalse(dialog.isVisible())
        self.assertEqual(QSettings().value("updates/seen_ids", [], list), [])
        dialog.deleteLater()

    def test_disable_automatic_display_still_allows_manual_open_and_persists_preference(self):
        self.window.settings.setValue("updates/show_on_start", False)
        self.startup_check(self.window)
        self.assertIsNone(self.window._update_dialog)
        self.window.act_update_announcements.trigger()
        dialog = self.window._update_dialog
        self.assertTrue(dialog.isVisible())
        self.assertFalse(dialog.show_on_start.isChecked())
        dialog.show_on_start.setChecked(True)
        dialog.show_on_start.setChecked(False)
        dialog.close_button.click()
        self.assertFalse(QSettings().value("updates/show_on_start", True, bool))
        second = self.new_window()
        self.startup_check(second)
        self.assertIsNone(second._update_dialog)

    def test_repeated_manual_requests_and_detached_preview_reuse_one_viewer(self):
        self.window._show_update_announcements()
        first = self.window._update_dialog
        self.window._show_update_announcements()
        self.assertIs(self.window._update_dialog, first)
        self.window._detach_preview()
        self.window._show_update_announcements()
        self.assertIs(self.window._update_dialog, first)
        self.assertEqual(len([d for d in self.window.findChildren(UpdateAnnouncementsDialog)
                              if d.isVisible()]), 1)
        first.close()
        # Reopen immediately, before DeferredDelete is processed
        self.window._show_update_announcements()
        self.assertIsNotNone(self.window._update_dialog)
        self.assertIsNot(self.window._update_dialog, first)
        self.assertTrue(self.window._update_dialog.isVisible())

    def test_language_change_preserves_selection_viewed_ids_and_read_history(self):
        mark_announcements_seen(self.window.settings, ["older-read-entry"])
        self.window._show_update_announcements()
        dialog = self.window._update_dialog
        if dialog.version_list.count() > 1:
            dialog.version_list.setCurrentRow(dialog.version_list.count()-1)
        selected = dialog.version_list.currentItem().data(Qt.UserRole)
        viewed = set(dialog.viewed_ids)
        previous_body = dialog.body.toPlainText()
        self.window.act_lang_en.trigger()
        self.assertIs(self.window._update_dialog, dialog)
        self.assertEqual(dialog.version_list.currentItem().data(Qt.UserRole), selected)
        self.assertEqual(dialog.viewed_ids, viewed)
        self.assertNotEqual(dialog.body.toPlainText(), previous_body)
        self.assertIn("older-read-entry", QSettings().value("updates/seen_ids", [], list))
        dialog.close()
        read = set(QSettings().value("updates/seen_ids", [], list))
        self.assertTrue(viewed.issubset(read))
        self.window.act_lang_zh.trigger()
        self.assertEqual(set(QSettings().value("updates/seen_ids", [], list)), read)

    def test_active_manual_play_defers_auto_notice_and_manual_viewer_never_resets_session(self):
        self.assertTrue(self.window.load_skin(str(self.source)))
        self.window.tabs.setCurrentIndex(1)
        controls = self.window.mania_playback
        preview = self.window.mania_preview
        controls.test_mode.setCurrentIndex(controls.test_mode.findData("play"))
        self.activate(self.window)
        QTest.mouseClick(preview, Qt.LeftButton)
        preview.timer.stop()
        preview._clock.invalidate()
        preview.t = 2000
        preview._advance_game()
        QTest.keyPress(preview, Qt.Key_D)
        preview.timer.stop()
        preview._clock.invalidate()
        game = preview.game
        before = (game.score, game.combo, dict(game.counts), game.now_ms, preview.t)
        self.assertGreater(game.score, 0)
        self.startup_check(self.window)
        self.assertIsNone(self.window._update_dialog)
        self.assertTrue(self.window._update_notice_timer.isActive())
        self.assertTrue(preview._playing)
        self.window._show_update_announcements()
        dialog = self.window._update_dialog
        self.activate(dialog)
        APP.processEvents()
        self.assertFalse(preview._playing)
        self.assertFalse(preview._input_armed)
        self.assertFalse(game.pressed_lanes)
        for action in (self.window.act_lang_en, self.window.act_lang_zh):
            action.trigger()
            self.assertIs(self.window._update_dialog, dialog)
            self.assertFalse(preview._playing)
            self.assertFalse(preview._input_armed)
            self.assertEqual((game.score, game.combo, dict(game.counts), game.now_ms, preview.t), before)
        dialog.close()
        self.assertIs(preview.game, game)
        self.assertEqual((game.score, game.combo, dict(game.counts), game.now_ms, preview.t), before)
        self.assertFalse(preview._playing)
        self.assertEqual(self.file_hashes(), self.original)

    def test_modal_and_fullscreen_windows_defer_automatic_notice_until_safe(self):
        modal = QDialog(self.window)
        modal.setWindowModality(Qt.ApplicationModal)
        modal.show()
        APP.processEvents()
        self.startup_check(self.window)
        self.assertIsNone(self.window._update_dialog)
        self.assertTrue(self.window._update_notice_timer.isActive())
        modal.close()
        modal.deleteLater()
        self.flush()
        self.window._detach_preview()
        host = self.window._preview_window
        host.toggle_fullscreen()
        self.startup_check(self.window)
        self.assertIsNone(self.window._update_dialog)
        self.assertTrue(self.window._update_notice_timer.isActive())
        host.toggle_fullscreen()
        self.startup_check(self.window)
        self.assertIsNotNone(self.window._update_dialog)

    def test_cancel_main_close_keeps_viewer_but_successful_close_cleans_timer_and_dialog(self):
        self.window._show_update_announcements()
        dialog = self.window._update_dialog
        with patch.object(self.window, "_confirm_design_navigation", return_value=False):
            self.assertFalse(self.window.close())
        self.assertIs(self.window._update_dialog, dialog)
        self.assertTrue(dialog.isVisible())
        self.assertTrue(self.window.close())
        self.assertIsNone(self.window._update_dialog)
        self.assertFalse(self.window._update_notice_timer.isActive())
        self.flush()
        self.assertTrue(not isValid(dialog) or not dialog.isVisible())
        self.startup_check(self.window)
        self.assertIsNone(self.window._update_dialog)

    def test_closing_before_startup_timer_fires_does_not_create_a_late_popup(self):
        self.window.schedule_update_announcement()
        self.assertTrue(self.window._update_notice_timer.isActive())
        self.assertTrue(self.window.close())
        self.assertFalse(self.window._update_notice_timer.isActive())
        self.startup_check(self.window)
        self.assertIsNone(self.window._update_dialog)
        self.assertEqual(QSettings().value("updates/seen_ids", [], list), [])


if __name__ == "__main__":
    unittest.main()
