"""Exercise the menu-to-restart workflow without network or executable changes."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, PropertyMock

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from shiboken6 import isValid

from core import i18n
from core.skin_ini import SkinIni
from core.update_launch import write_record
from ui.main_window import MainWindow
from tests.test_online_update_ui import APP, ControlledExecutor, FakeProcess, FakeUpdateService, RELEASE


BETA = dict(RELEASE, version="1.6.0-preview.99", build_number=99, build_id="preview-r99",
            notes_id="preview-r99")
STABLE = dict(RELEASE, version="1.6.0", channel="stable", build_number=98,
              build_id="stable-r98", notes_id="stable-r98")


class OneClickUpdateWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        QCoreApplication.setOrganizationName("OsuSkinEditorTests")
        QCoreApplication.setApplicationName("OneClickUpdateWorkspace")
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(self.root / "settings"))
        QSettings().clear()
        i18n.load_language("zh-CN")
        self.patches = [patch("ui.main_window.UpdateService", FakeUpdateService),
                        patch("ui.update_install_controller.ThreadPoolExecutor", ControlledExecutor),
                        patch("ui.main_window.installed_executable", return_value=self.root / "editor.exe"),
                        patch("ui.software_update.installed_executable", return_value=self.root / "editor.exe")]
        for item in self.patches:
            item.start()
        self.window = MainWindow()
        self.window.setAttribute(Qt.WA_DontShowOnScreen)
        self.window.show()
        for timer in (self.window._network_check_timer, self.window._update_notice_timer,
                      self.window._update_result_timer):
            timer.stop()
        APP.processEvents()
        self.service = self.window.update_service
        self.process = FakeProcess()
        self.controller = self.window._install_controller
        self.controller.launch = lambda prepared: self.process
        self.prepared = {"job": {"nonce": "p" * 64,
                                  "prepared_file": str(self.root / "helper-ready.json"),
                                  "result_file": str(self.root / "result.json"),
                                  "cancel_file": str(self.root / "cancel.json")},
                         "job_path": str(self.root / "job.json"),
                         "helper": str(self.root / "helper.exe")}

    def tearDown(self):
        if isValid(self.window):
            self.process.returncode = 1
            self.window.mania_ini_dock._dirty = False
            self.window.mania_design_dock._dirty = False
            self.controller.cancel()
            self.window.close()
            self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def open_checked(self):
        self.window.act_check_updates.trigger()
        dialog = self.window._software_update_dialog
        self.service.checked_channels({"preview": {"release": BETA, "latest": BETA},
                                       "stable": {"release": STABLE, "latest": STABLE}})
        return dialog

    def click_update(self, dialog, channel="preview"):
        with patch.object(self.window.mania_ini_dock, "_confirm_discard_if_dirty", return_value=True), \
                patch.object(self.window, "_confirm_design_navigation", return_value=True):
            dialog.rows[channel].button.click()

    def download(self, release=BETA):
        payload = self.root / "payload.exe"
        payload.write_bytes(b"not an executable, never launched")
        self.service.downloaded(payload, release)

    def test_about_action_immediately_checks_both_channels_despite_legacy_channel_preference(self):
        self.window.settings.setValue("updates/channel", "stable")
        self.window.act_check_updates.trigger()
        dialog = self.window._software_update_dialog
        self.assertEqual(self.service.checks, [("all", True)])
        self.assertEqual(tuple(dialog.rows), ("preview", "stable"))
        self.assertFalse(hasattr(dialog, "channel"))
        self.assertFalse(hasattr(dialog, "install_button"))
        self.assertFalse(hasattr(dialog, "download_button"))
        self.assertFalse(dialog.isModal())
        self.assertTrue(dialog.isVisible())
        self.assertTrue(dialog.advanced_panel.isHidden())
        self.assertEqual(self.service.source_mode, "auto")
        self.assertEqual(self.window.settings.value("updates/channel", "", str), "stable")

    def test_each_channel_row_starts_its_own_release(self):
        dialog = self.open_checked()
        self.click_update(dialog, "stable")
        self.assertEqual(self.service.downloads, [STABLE])
        self.assertEqual(self.window._update_download_release, STABLE)
        dialog._cancel()
        self.click_update(dialog, "preview")
        self.assertEqual(self.service.downloads, [STABLE, BETA])
        self.assertEqual(self.window._update_download_release, BETA)

    def test_dirty_configuration_cancel_prevents_download_and_edit_lock(self):
        dialog = self.open_checked()
        with patch.object(self.window.mania_ini_dock, "_confirm_discard_if_dirty", return_value=False) as ini, \
                patch.object(self.window, "_confirm_design_navigation") as design:
            dialog.rows["preview"].button.click()
        ini.assert_called_once()
        design.assert_not_called()
        self.assertFalse(self.service.downloads)
        self.assertFalse(self.window._update_edit_locked)
        self.assertFalse(dialog.auto_install_pending)
        self.assertTrue(self.window.centralWidget().isEnabled())

    def test_dirty_design_cancel_prevents_download_and_edit_lock(self):
        dialog = self.open_checked()
        with patch.object(self.window.mania_ini_dock, "_confirm_discard_if_dirty", return_value=True) as ini, \
                patch.object(self.window, "_confirm_design_navigation", return_value=False) as design:
            dialog.rows["preview"].button.click()
        ini.assert_called_once()
        design.assert_called_once()
        self.assertFalse(self.service.downloads)
        self.assertFalse(self.window._update_edit_locked)
        self.assertIsNone(self.controller.future)

    def test_download_start_refusal_restores_editors_without_starting_preparation(self):
        dialog = self.open_checked()
        with patch.object(self.service, "download_release", return_value=False):
            self.click_update(dialog)
        self.assertFalse(self.window._update_edit_locked)
        self.assertIsNone(self.window._update_download_release)
        self.assertFalse(dialog.auto_install_pending)
        self.assertTrue(self.window.centralWidget().isEnabled())
        self.assertTrue(dialog.rows["preview"].button.isEnabled())
        self.assertIsNone(self.controller.future)

    def test_double_click_and_other_channel_click_during_download_do_not_start_second_transfer(self):
        dialog = self.open_checked()
        self.click_update(dialog)
        dialog.rows["preview"].button.click()
        dialog.rows["stable"].button.click()
        self.assertFalse(self.window._start_release_update(STABLE))
        self.assertEqual(self.service.downloads, [BETA])
        self.assertEqual(self.window._update_download_release, BETA)

    def test_download_locks_all_workspace_editors_and_detached_preview(self):
        dialog = self.open_checked()
        self.window._detach_preview()
        self.click_update(dialog)
        self.assertTrue(self.window._update_edit_locked)
        self.assertFalse(self.window._preparing_update)
        for widget in (self.window.centralWidget(), self.window.menuBar(), self.window.mania_ini_dock,
                       self.window.mania_design_dock, self.window.debug_dock, self.window._preview_window):
            self.assertFalse(widget.isEnabled())
        self.assertTrue(dialog.isEnabled())
        self.assertFalse(self.window.act_import_osk.isEnabled())
        self.assertFalse(self.window.btn_import_osk.isEnabled())
        self.assertFalse(self.window.btn_welcome_osk.isEnabled())
        # A late archive-completion notification must not reopen the editors.
        self.window._archive_busy_changed(False)
        self.assertFalse(self.window.centralWidget().isEnabled())
        self.assertFalse(self.window._preview_window.isEnabled())

    def test_download_blocks_programmatic_folder_osk_and_asset_navigation(self):
        dialog = self.open_checked()
        self.click_update(dialog)
        with patch("ui.main_window.QFileDialog.getExistingDirectory") as folder, \
                patch("ui.skin_archive_workflow.QFileDialog.getOpenFileName") as osk, \
                patch("ui.skin_archive_workflow.QFileDialog.getSaveFileName") as export, \
                patch.object(self.window.loader, "load") as load, \
                patch.object(self.window._archive_controller, "import_file") as unpack, \
                patch("ui.main_window.AssetsManagerDialog") as assets:
            self.window.on_open_generic()
            self.assertFalse(self.window.load_skin(str(self.root / "skin")))
            self.assertFalse(self.window.on_import_osk())
            self.assertFalse(self.window.import_osk_file(str(self.root / "skin.osk")))
            self.assertFalse(self.window.on_export_osk())
            self.window._open_assets_manager()
        for action in (folder, osk, export, load, unpack, assets):
            action.assert_not_called()

    def test_download_ready_automatically_starts_helper_once_without_a_second_save_prompt(self):
        dialog = self.open_checked()
        with patch.object(self.window.mania_ini_dock, "_confirm_discard_if_dirty", return_value=True) as ini, \
                patch.object(self.window, "_confirm_design_navigation", return_value=True) as design, \
                patch.object(self.controller, "start", wraps=self.controller.start) as start:
            dialog.rows["preview"].button.click()
            self.download()
            self.service.download_ready.emit(self.service.ready_path, deepcopy(BETA))
        ini.assert_called_once()
        design.assert_called_once()
        start.assert_called_once_with(self.service.ready_path, BETA)
        self.assertTrue(self.window._preparing_update)
        self.assertTrue(self.window._update_edit_locked)
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window._update_exit_authorized)
        self.controller.timer.stop()

    def test_save_choice_writes_pending_ini_once_before_download_and_preserves_original_backup(self):
        skin = self.root / "skin"
        skin.mkdir()
        ini_path = skin / "skin.ini"
        original = b"[General]\nName: Test\n[Mania]\nKeys: 4\nColumnWidth: 30,30,30,30\nHitPosition: 402\n"
        ini_path.write_bytes(original)
        self.assertTrue(self.window.load_skin(str(skin)))
        dock = self.window.mania_ini_dock
        dock.spn_hit_pos.setValue(395)
        self.assertTrue(dock._dirty)
        dialog = self.open_checked()
        save_button, discard_button, cancel_button = object(), object(), object()
        with patch("ui.mania_ini_dock.QMessageBox") as message_box, \
                patch.object(dock, "_on_save_clicked", wraps=dock._on_save_clicked) as save:
            message_box.return_value.addButton.side_effect = [save_button, discard_button, cancel_button]
            message_box.return_value.clickedButton.return_value = save_button
            dialog.rows["preview"].button.click()
            self.assertEqual(SkinIni.read(ini_path).mania_get(4)["HitPosition"], "395")
            self.assertEqual(ini_path.with_suffix(".ini.bak").read_bytes(), original)
            self.assertFalse(dock._dirty)
            self.download()
        save.assert_called_once()
        message_box.return_value.exec.assert_called_once()
        self.assertEqual(self.service.downloads, [BETA])
        self.assertTrue(self.window._preparing_update)
        self.controller.timer.stop()

    def test_cancel_during_download_unlocks_and_ignores_late_ready(self):
        dialog = self.open_checked()
        self.click_update(dialog)
        dialog.cancel_button.click()
        self.assertEqual(self.service.cancelled_downloads, 1)
        self.assertFalse(self.window._update_edit_locked)
        self.assertTrue(self.window.centralWidget().isEnabled())
        self.download()
        self.assertIsNone(self.controller.future)
        self.assertFalse(self.window._preparing_update)
        self.assertFalse(dialog.auto_install_pending)

    def test_close_during_download_unlocks_and_ignores_late_ready(self):
        dialog = self.open_checked()
        self.click_update(dialog)
        dialog.close()
        self.assertIsNone(self.window._software_update_dialog)
        self.assertEqual(self.service.cancelled_downloads, 1)
        self.assertFalse(self.window._update_edit_locked)
        self.download()
        self.assertIsNone(self.controller.future)
        self.assertTrue(self.window.centralWidget().isEnabled())

    def test_download_failure_unlocks_and_allows_explicit_retry(self):
        dialog = self.open_checked()
        self.click_update(dialog)
        self.service.downloading = False
        self.service.state = "idle"
        self.service.state_changed.emit("idle")
        self.service.failed.emit("download", "Connection failed")
        self.assertFalse(self.window._update_edit_locked)
        self.assertTrue(self.window.centralWidget().isEnabled())
        self.assertTrue(dialog.rows["preview"].button.isEnabled())
        self.assertIsNone(self.controller.future)
        self.click_update(dialog)
        self.assertEqual(self.service.downloads, [BETA, BETA])

    def test_preparation_failure_keeps_verified_payload_and_retry_rechecks_unsaved_edits(self):
        dialog = self.open_checked()
        self.click_update(dialog)
        self.download()
        self.controller.timer.stop()
        self.controller.future.set_exception(OSError("Not enough disk space"))
        self.controller.poll()
        self.assertFalse(self.window._preparing_update)
        self.assertFalse(self.window._update_edit_locked)
        self.assertTrue(self.window.centralWidget().isEnabled())
        self.assertTrue(Path(self.service.ready_path).is_file())
        self.assertEqual(self.service.downloads, [BETA])
        self.assertTrue(dialog.rows["preview"].button.isEnabled())
        with patch.object(self.window.mania_ini_dock, "_confirm_discard_if_dirty", return_value=False) as ini:
            dialog.rows["preview"].button.click()
        ini.assert_called_once()
        self.assertFalse(self.window._update_edit_locked)
        self.assertEqual(self.controller.state, "failed")
        with patch.object(self.window.mania_ini_dock, "_confirm_discard_if_dirty", return_value=True) as ini, \
                patch.object(self.window, "_confirm_design_navigation", return_value=True) as design:
            dialog.rows["preview"].button.click()
        ini.assert_called_once()
        design.assert_called_once()
        self.assertTrue(self.window._preparing_update)
        self.assertEqual(self.service.downloads, [BETA])
        self.controller.timer.stop()

    def test_close_preparation_cancels_worker_and_never_launches_helper(self):
        dialog = self.open_checked()
        self.click_update(dialog)
        self.download()
        self.controller.timer.stop()
        dialog.close()
        self.assertTrue(self.controller.cancel_event.is_set())
        with patch.object(self.controller, "launch") as launch:
            self.controller.future.set_result(self.prepared)
            self.controller.poll()
        launch.assert_not_called()
        self.assertFalse(self.window._preparing_update)
        self.assertFalse(self.window._update_edit_locked)
        self.assertTrue(self.window.centralWidget().isEnabled())
        self.assertFalse(self.window._update_exit_authorized)

    def test_old_process_remains_until_helper_ack_then_restarts_without_repeating_dirty_guards(self):
        dialog = self.open_checked()
        with patch.object(self.window.mania_ini_dock, "_confirm_discard_if_dirty", return_value=True) as ini, \
                patch.object(self.window, "_confirm_design_navigation", return_value=True) as design:
            dialog.rows["preview"].button.click()
            self.download()
            self.controller.timer.stop()
            self.controller.future.set_result(self.prepared)
            self.controller.poll()
            self.assertTrue(self.window.isVisible())
            self.assertFalse(self.window._update_exit_authorized)
            write_record(self.prepared["job"]["prepared_file"],
                         {"schema_version": 1, "nonce": self.prepared["job"]["nonce"],
                          "status": "waiting", "pid": self.process.pid})
            with patch.object(APP, "quit") as quit_application:
                self.controller.poll()
            quit_application.assert_called_once()
        ini.assert_called_once()
        design.assert_called_once()
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window._update_exit_authorized)
        self.assertEqual(self.controller.state, "committed")

    def test_active_archive_job_refuses_update_before_guard_or_download(self):
        dialog = self.open_checked()
        with patch.object(type(self.window._archive_controller), "busy", new_callable=PropertyMock,
                          return_value=True), \
                patch.object(self.window.mania_ini_dock, "_confirm_discard_if_dirty") as ini, \
                patch.object(self.window, "_confirm_design_navigation") as design:
            dialog.rows["preview"].button.click()
        ini.assert_not_called()
        design.assert_not_called()
        self.assertFalse(self.service.downloads)
        self.assertFalse(self.window._update_edit_locked)
        self.assertFalse(dialog.auto_install_pending)

    def test_source_download_mode_unlocks_when_package_is_ready_and_never_launches_helper(self):
        dialog = self.open_checked()
        dialog.can_install = False
        self.click_update(dialog)
        self.assertTrue(self.window._update_edit_locked)
        self.download()
        self.assertFalse(self.window._update_edit_locked)
        self.assertTrue(self.window.centralWidget().isEnabled())
        self.assertIsNone(self.controller.future)
        self.assertFalse(dialog.auto_install_pending)
        self.assertTrue(Path(self.service.ready_path).is_file())

    def test_starting_download_stops_startup_background_timer_and_background_check_stays_quiet(self):
        dialog = self.open_checked()
        self.window._network_check_timer.start(2500)
        self.assertTrue(self.window._network_check_timer.isActive())
        self.click_update(dialog)
        self.assertFalse(self.window._network_check_timer.isActive())
        with patch.object(self.service, "check_announcements") as announcements, \
                patch.object(self.service, "check_all_updates") as updates:
            self.window._background_online_check()
        announcements.assert_not_called()
        updates.assert_not_called()
        self.assertTrue(dialog.auto_install_pending)
        self.assertTrue(self.window._update_edit_locked)
        self.assertEqual(self.service.downloads, [BETA])

    def test_startup_background_check_refuses_each_busy_update_stage(self):
        self.window.settings.setValue("updates/auto_check", True)
        cases = (("download", self.service, "downloading", True),
                 ("ready", self.service, "ready_path", str(self.root / "payload.exe")),
                 ("prepare", self.window, "_preparing_update", True),
                 ("intent", self.window, "_update_edit_locked", True))
        for stage, owner, attribute, value in cases:
            with self.subTest(stage=stage), patch.object(owner, attribute, value), \
                    patch.object(self.service, "check_announcements") as announcements, \
                    patch.object(self.service, "check_all_updates") as updates:
                self.window._background_online_check()
                announcements.assert_not_called()
                updates.assert_not_called()
        self.window._background_online_check()
        self.assertEqual(self.service.announcement_checks, [False])
        self.assertEqual(self.service.checks, [("all", False)])

    def cancel_with_announcements_still_checking(self):
        """Model a finished download while an independent announcement request runs."""
        self.service.cancelled_downloads += 1
        self.service.downloading = False
        self.service.state = "checking"
        self.service.state_changed.emit("checking")

    def test_cancel_download_immediately_unlocks_while_announcements_are_still_checking(self):
        dialog = self.open_checked()
        self.click_update(dialog)
        with patch.object(self.service, "cancel_download", self.cancel_with_announcements_still_checking):
            dialog.cancel_button.click()
        self.assertEqual(self.service.state, "checking")
        self.assertEqual(self.service.cancelled_downloads, 1)
        self.assertFalse(self.window._update_edit_locked)
        self.assertIsNone(self.window._update_download_release)
        self.assertTrue(self.window.centralWidget().isEnabled())
        self.assertFalse(dialog.auto_install_pending)
        self.download()
        self.assertIsNone(self.controller.future)

    def test_close_download_immediately_unlocks_while_announcements_are_still_checking(self):
        dialog = self.open_checked()
        self.click_update(dialog)
        with patch.object(self.service, "cancel_download", self.cancel_with_announcements_still_checking):
            dialog.close()
        self.assertEqual(self.service.state, "checking")
        self.assertEqual(self.service.cancelled_downloads, 1)
        self.assertIsNone(self.window._software_update_dialog)
        self.assertFalse(self.window._update_edit_locked)
        self.assertTrue(self.window.centralWidget().isEnabled())
        self.assertFalse(dialog.auto_install_pending)
        self.download()
        self.assertIsNone(self.controller.future)

    def test_download_error_immediately_unlocks_while_announcements_are_still_checking(self):
        dialog = self.open_checked()
        self.click_update(dialog)
        self.service.downloading = False
        self.service.state = "checking"
        self.service.state_changed.emit("checking")
        # An unrelated metadata operation alone must not disarm the update.
        self.assertTrue(self.window._update_edit_locked)
        self.assertTrue(dialog.auto_install_pending)
        self.service.failed.emit("download", "All download sources failed")
        self.assertEqual(self.service.state, "checking")
        self.assertFalse(self.window._update_edit_locked)
        self.assertIsNone(self.window._update_download_release)
        self.assertTrue(self.window.centralWidget().isEnabled())
        self.assertFalse(dialog.auto_install_pending)
        self.assertIsNone(self.controller.future)

    def test_ready_state_keeps_auto_update_intent_locked_until_helper_preparation_starts(self):
        dialog = self.open_checked()
        self.click_update(dialog)
        before_start = []
        real_start = self.controller.start

        def start(payload, release):
            before_start.append((self.window._update_edit_locked,
                                 self.window._update_download_release,
                                 dialog.auto_install_pending,
                                 self.window.centralWidget().isEnabled(),
                                 self.window._preparing_update,
                                 self.service.state))
            return real_start(payload, release)

        with patch.object(self.controller, "start", side_effect=start):
            self.download()
        self.assertEqual(before_start, [(True, BETA, True, False, False, "ready")])
        self.assertTrue(self.window._preparing_update)
        self.assertTrue(self.window._update_edit_locked)
        self.assertEqual(self.window._update_download_release, BETA)
        self.controller.timer.stop()

    def test_previous_failure_result_is_deferred_during_new_update_then_shown_after_cancellation(self):
        dialog = self.open_checked()
        old_job_path = str(self.root / "previous-job.json")
        old_result_path = str(self.root / "previous-result.json")
        old_job = {"nonce": "old-update", "result_file": old_result_path}
        old_result = {"nonce": "old-update", "status": "failed", "message": "Previous update failed"}
        self.window.settings.setValue("updates/pending_job", old_job_path)
        self.click_update(dialog)
        with patch("ui.main_window.read_record", side_effect=[old_job, old_result]) as read, \
                patch.object(self.window, "_show_software_update", wraps=self.window._show_software_update) as show:
            self.window._read_update_result()
            self.assertTrue(self.window._update_result_timer.isActive())
            self.assertEqual(self.window._update_result_timer.interval(), 1000)
            self.assertEqual(self.window.settings.value("updates/pending_job"), old_job_path)
            self.assertTrue(dialog.auto_install_pending)
            self.assertTrue(self.window._update_edit_locked)
            self.assertEqual(self.window._result_checks, 0)
            read.assert_not_called()
            show.assert_not_called()

            self.download()
            self.controller.timer.stop()
            self.assertTrue(self.window._preparing_update)
            self.window._read_update_result()
            read.assert_not_called()
            show.assert_not_called()
            self.assertEqual(self.window.settings.value("updates/pending_job"), old_job_path)
            self.assertTrue(dialog.auto_install_pending)
            self.assertTrue(self.window._update_edit_locked)

            dialog.cancel_button.click()
            self.controller.future.set_result(self.prepared)
            self.controller.poll()
            self.assertFalse(self.window._preparing_update)
            self.assertFalse(self.window._update_edit_locked)
            self.assertFalse(dialog.auto_install_pending)
            self.window._read_update_result()
            self.assertEqual(read.call_count, 2)
            self.assertEqual(read.call_args_list[0].args, (old_job_path,))
            self.assertEqual(read.call_args_list[1].args, (old_result_path,))
            show.assert_called_once()
        self.assertFalse(self.window.settings.contains("updates/pending_job"))
        self.assertIn("Previous update failed", dialog.status.text())
        self.assertTrue(self.window.centralWidget().isEnabled())

    def test_selecting_another_channel_with_a_ready_payload_keeps_new_download_locked(self):
        dialog = self.open_checked()
        self.download(STABLE)
        with patch.object(self.window.mania_ini_dock, "_confirm_discard_if_dirty", return_value=True) as ini, \
                patch.object(self.window, "_confirm_design_navigation", return_value=True) as design:
            dialog.rows["preview"].button.click()
            self.assertEqual(self.service.discarded, 1)
            self.assertEqual(self.service.downloads, [BETA])
            self.assertTrue(self.window._update_edit_locked)
            self.assertEqual(self.window._update_download_release, BETA)
            self.assertFalse(self.window.centralWidget().isEnabled())
            self.download(BETA)
        ini.assert_called_once()
        design.assert_called_once()
        self.assertTrue(self.window._preparing_update)
        self.controller.timer.stop()


if __name__ == "__main__":
    unittest.main()
