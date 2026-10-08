"""Update UI/handshake tests without network access or executable replacement."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from concurrent.futures import Future
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch, Mock

from PySide6.QtCore import QObject, QCoreApplication, QEvent, QSettings, Qt, Signal
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from core import i18n
from core.update_launch import prepare_install_job, acknowledge_update_startup, read_record, write_record, launch_helper
from ui.software_update import SoftwareUpdateDialog
from ui.update_install_controller import UpdateInstallController
from ui.main_window import MainWindow


APP = QApplication.instance() or QApplication([])
RELEASE = {"version": "1.6.0-preview.6", "build_id": "preview-r6", "build_number": 6,
           "channel": "preview", "platform": "windows-x64", "notes_id": "preview-r6",
           "size": 1024*1024, "sha256": "a"*64,
           "url": "https://github.com/XiaoLan9999/OsuSkinEditor/releases/download/preview-r6/editor.exe"}


class FakeUpdateService(QObject):
    announcements_ready = Signal(object)
    update_ready = Signal(object)
    failed = Signal(str, str)
    download_progress = Signal(int, int)
    download_ready = Signal(str, object)
    state_changed = Signal(str)
    source_changed = Signal(str, str)

    def __init__(self, parent=None, **_kwargs):
        super().__init__(parent)
        self.ready_path = None
        self.ready_release = None
        self.downloading = False
        self.state = "idle"
        self.last_result_from_cache = {"announcements": False, "updates": False}
        self.source_mode = "auto"
        self.last_source = {}
        self.source_modes = []
        self.checks, self.downloads = [], []
        self.cancelled_downloads = 0
        self.discarded = 0
        self.closed = False
        self.announcement_checks = []

    def set_source_mode(self, mode):
        if mode not in ("auto", "github", "ghfast", "ghproxy"):
            return False
        if self.downloading or self.state == "checking":
            return mode == self.source_mode
        self.source_mode = mode
        self.source_modes.append(mode)
        return True

    def check_announcements(self, force=False):
        self.announcement_checks.append(force)
        return True

    def check_updates(self, channel="preview", force=False):
        self.checks.append((channel, force))
        self.state = "checking"
        self.state_changed.emit(self.state)
        return True

    def checked(self, release):
        self.state = "idle"
        self.state_changed.emit(self.state)
        self.update_ready.emit(deepcopy(release))

    def download_release(self, release):
        self.downloads.append(deepcopy(release))
        self.downloading = True
        self.state = "downloading"
        self.state_changed.emit(self.state)
        return True

    def downloaded(self, path, release=RELEASE):
        self.downloading = False
        self.ready_path = str(path)
        self.ready_release = deepcopy(release)
        self.state = "ready"
        self.state_changed.emit(self.state)
        self.download_ready.emit(self.ready_path, deepcopy(release))

    def cancel_download(self):
        self.cancelled_downloads += 1
        self.downloading = False
        self.state = "idle"
        self.state_changed.emit(self.state)

    def discard_ready(self):
        self.discarded += 1
        self.ready_path = None
        self.ready_release = None
        self.state = "idle"
        self.state_changed.emit(self.state)

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.pid = 123456

    def poll(self):
        return self.returncode


class ControlledExecutor:
    def __init__(self, **_kwargs):
        self.future = Future()
        self.submitted = None
        self.shutdown_calls = []

    def submit(self, function, *args):
        self.submitted = (function, args)
        return self.future

    def shutdown(self, **kwargs):
        self.shutdown_calls.append(kwargs)


class SoftwareUpdateDialogTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.IniFormat)
        self.settings.setValue("updates/channel", "preview")
        i18n.load_language("zh-CN")
        self.service = FakeUpdateService()
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.preparing = False
            dialog.close()
            dialog.deleteLater()
        self.service.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def dialog(self, can_install=True):
        dialog = SoftwareUpdateDialog(self.service, self.settings, can_install=can_install)
        dialog.setAttribute(Qt.WA_DontShowOnScreen)
        dialog.show()
        self.dialogs.append(dialog)
        return dialog

    def test_explicit_check_available_download_progress_and_ready_controls(self):
        dialog = self.dialog()
        self.assertFalse(dialog.download_button.isEnabled())
        self.assertFalse(dialog.install_button.isEnabled())
        dialog.check_button.click()
        self.assertEqual(self.service.checks, [("preview", True)])
        self.assertFalse(dialog.channel.isEnabled())
        self.assertFalse(dialog.source_mode.isEnabled())
        self.service.checked(RELEASE)
        self.assertTrue(dialog.download_button.isEnabled())
        self.assertIn(RELEASE["version"], dialog.release_info.text())
        dialog.download_button.click()
        self.assertEqual(self.service.downloads, [RELEASE])
        self.assertFalse(dialog.check_button.isEnabled())
        self.assertFalse(dialog.source_mode.isEnabled())
        self.service.download_progress.emit(512*1024, 1024*1024)
        self.assertEqual(dialog.progress.value(), 50)
        self.service.downloaded(self.root / "payload.exe")
        self.assertTrue(dialog.install_button.isEnabled())
        self.assertFalse(dialog.download_button.isEnabled())
        installs = []
        dialog.install_requested.connect(lambda: installs.append(True))
        dialog.install_button.click()
        self.assertEqual(installs, [True])

    def test_source_mode_allows_check_but_never_offers_in_place_install(self):
        dialog = self.dialog(can_install=False)
        self.service.checked(RELEASE)
        self.service.downloaded(self.root / "payload.exe")
        self.assertFalse(dialog.install_button.isEnabled())
        self.assertIn(i18n.t("updater.source_mode"), dialog.help_text.text())

    def test_error_does_not_claim_up_to_date_and_update_text_is_plain(self):
        dialog = self.dialog()
        dialog.check()
        self.service.state = "idle"
        self.service.failed.emit("updates", "TLS certificate validation failed")
        self.assertIn("TLS", dialog.status.text())
        self.assertNotEqual(dialog.status.text(), i18n.t("updater.up_to_date"))
        self.assertEqual(dialog.status.textFormat(), Qt.PlainText)
        self.assertEqual(dialog.release_info.textFormat(), Qt.PlainText)
        before = dialog.status.text()
        self.service.failed.emit("announcements", "Separate feed failed")
        self.assertEqual(dialog.status.text(), before)

    def test_failed_refresh_does_not_reenable_download_for_previous_release(self):
        dialog = self.dialog()
        self.service.checked(RELEASE)
        self.assertTrue(dialog.download_button.isEnabled())
        dialog.check()
        self.service.state = "idle"
        self.service.failed.emit("updates", "Network unavailable")
        self.assertFalse(dialog.download_button.isEnabled())
        self.assertIsNone(dialog.release)

    def test_cancel_download_and_prepared_payload_use_distinct_service_operations(self):
        dialog = self.dialog()
        self.service.checked(RELEASE)
        dialog.download_button.click()
        dialog.cancel_button.click()
        self.assertEqual(self.service.cancelled_downloads, 1)
        payload = self.root / "payload.exe"
        payload.write_bytes(b"preserved verified bytes")
        self.service.downloaded(payload)
        dialog.cancel_button.click()
        self.assertEqual(self.service.discarded, 1)
        self.assertTrue(payload.exists())
        self.assertFalse(dialog.install_button.isEnabled())

    def test_preparing_close_and_cancel_only_request_handshake_cancellation(self):
        dialog = self.dialog()
        self.service.downloaded(self.root / "payload.exe")
        cancelled = []
        dialog.cancel_install_requested.connect(lambda: cancelled.append(True))
        dialog.set_preparing(True)
        self.assertFalse(dialog.install_button.isEnabled())
        self.assertFalse(dialog.channel.isEnabled())
        self.assertFalse(dialog.source_mode.isEnabled())
        dialog.cancel_button.click()
        self.assertEqual(cancelled, [True])
        self.assertEqual(self.service.discarded, 0)
        dialog.close()
        self.assertGreaterEqual(len(cancelled), 2)

    def test_existing_ready_payload_restores_dialog_state_and_preferences(self):
        self.service.downloaded(self.root / "payload.exe")
        dialog = self.dialog()
        self.assertEqual(dialog.release, RELEASE)
        self.assertTrue(dialog.install_button.isEnabled())
        dialog.auto_check.setChecked(False)
        self.assertFalse(self.settings.value("updates/auto_check", True, bool))
        dialog.retranslate()
        self.assertEqual(dialog.release, RELEASE)

    def test_connection_preference_restores_and_changes_trigger_fresh_check(self):
        self.settings.setValue("updates/source_mode", "ghfast")
        dialog = self.dialog()
        self.assertEqual(dialog.source_mode.currentData(), "ghfast")
        self.assertEqual(self.service.source_mode, "ghfast")
        self.service.checked(RELEASE)
        dialog.source_mode.setCurrentIndex(dialog.source_mode.findData("ghproxy"))
        self.assertEqual(self.settings.value("updates/source_mode", "", str), "ghproxy")
        self.assertEqual(self.service.source_mode, "ghproxy")
        self.assertEqual(self.service.checks, [("preview", True)])
        self.assertIsNone(dialog.release)
        self.assertFalse(dialog.source_mode.isEnabled())

    def test_invalid_connection_preference_returns_to_auto(self):
        self.settings.setValue("updates/source_mode", "unknown-mirror")
        dialog = self.dialog()
        self.assertEqual(dialog.source_mode.currentData(), "auto")
        self.assertEqual(self.settings.value("updates/source_mode", "", str), "auto")

    def test_current_route_updates_on_fallback_and_stays_plain_across_language_change(self):
        self.service.last_source["updates"] = "raw.githubusercontent.com"
        dialog = self.dialog()
        self.assertIn("raw.githubusercontent.com", dialog.current_source.text())
        self.service.source_changed.emit("download", "ghfast.top")
        self.assertIn("ghfast.top", dialog.current_source.text())
        self.service.download_progress.emit(800*1024, 1024*1024)
        self.service.source_changed.emit("download", "gh-proxy.org")
        self.service.download_progress.emit(0, 1024*1024)
        self.assertIn("gh-proxy.org", dialog.current_source.text())
        self.assertEqual(dialog.progress.value(), 0)
        self.service.source_changed.emit("announcements", "ghfast.top")
        self.assertIn("gh-proxy.org", dialog.current_source.text())
        i18n.load_language("en-US")
        dialog.retranslate()
        self.assertIn("gh-proxy.org", dialog.current_source.text())
        self.assertIn("Download", dialog.current_source.text())
        self.assertEqual(dialog.current_source.textFormat(), Qt.PlainText)

    def test_connection_change_rejected_during_background_check_restores_choice(self):
        dialog = self.dialog()
        self.service.check_updates()
        dialog.source_mode.setCurrentIndex(dialog.source_mode.findData("github"))
        self.assertEqual(dialog.source_mode.currentData(), "auto")
        self.assertEqual(self.service.source_mode, "auto")
        self.assertEqual(self.settings.value("updates/source_mode", "", str), "auto")


class UpdateInstallControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.process = FakeProcess()
        self.launches = []
        self.patch_executor = patch("ui.update_install_controller.ThreadPoolExecutor", ControlledExecutor)
        self.patch_executor.start()
        self.controller = UpdateInstallController(prepare=lambda *args: None, launch=self.launch)
        self.ready, self.errors, self.cancelled = [], [], []
        self.controller.ready_to_exit.connect(self.ready.append)
        self.controller.failed.connect(self.errors.append)
        self.controller.cancelled.connect(lambda: self.cancelled.append(True))
        self.prepared = {"job": {"nonce": "n"*64,
                                 "prepared_file": str(self.root / "helper-ready.json"),
                                 "result_file": str(self.root / "result.json"),
                                 "cancel_file": str(self.root / "cancel.json")},
                         "job_path": str(self.root / "job.json"), "helper": str(self.root / "helper.exe")}

    def tearDown(self):
        try:
            self.controller.close()
        finally:
            self.controller.deleteLater()
            self.patch_executor.stop()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()
            self.temp.cleanup()

    def launch(self, prepared):
        self.launches.append(prepared)
        return self.process

    def start(self, complete=True):
        self.assertTrue(self.controller.start(str(self.root / "payload.exe"), RELEASE))
        self.controller.timer.stop()
        if complete:
            self.controller.future.set_result(self.prepared)
            self.controller.poll()

    def waiting_notice(self, nonce=None):
        write_record(self.prepared["job"]["prepared_file"],
                     {"schema_version": 1, "nonce": nonce or self.prepared["job"]["nonce"],
                      "status": "waiting", "pid": self.process.pid})

    def test_preparation_stays_asynchronous_and_handshake_requires_matching_nonce(self):
        self.start(complete=False)
        self.assertEqual(self.controller.state, "preparing")
        self.assertFalse(self.launches)
        self.assertFalse(self.controller.start("another.exe", RELEASE))
        self.controller.future.set_result(self.prepared)
        self.controller.poll()
        self.assertEqual(self.controller.state, "waiting")
        self.waiting_notice("wrong-nonce")
        self.controller.poll()
        self.assertFalse(self.ready)
        self.waiting_notice()
        self.controller.poll()
        self.assertEqual(self.ready, [self.prepared])
        self.assertEqual(self.controller.state, "ready")
        self.controller.commit()
        self.assertEqual(self.controller.state, "committed")

    def test_helper_refusal_keeps_existing_application_running(self):
        self.start()
        write_record(self.prepared["job"]["result_file"],
                     {"nonce": self.prepared["job"]["nonce"], "status": "failed", "message": "Target is not writable"})
        self.controller.poll()
        self.assertFalse(self.ready)
        self.assertEqual(self.errors, ["Target is not writable"])
        self.assertEqual(self.controller.state, "failed")

    def test_exited_helper_cannot_authorize_exit_using_a_leftover_waiting_record(self):
        self.start()
        self.waiting_notice()
        self.process.returncode = 1
        self.controller.poll()
        self.assertFalse(self.ready)
        self.assertEqual(self.controller.state, "failed")

    def test_cancel_before_prepare_completion_never_launches_helper(self):
        self.start(complete=False)
        self.controller.cancel()
        self.controller.poll()
        self.assertEqual(self.controller.state, "preparing")
        self.controller.future.set_result(self.prepared)
        self.controller.poll()
        self.assertFalse(self.launches)
        self.assertEqual(self.controller.state, "cancelled")
        self.assertTrue(self.cancelled)

    def test_cancel_while_waiting_writes_nonce_bound_marker_and_never_emits_ready(self):
        self.start()
        self.controller.cancel()
        self.controller.poll()
        record = read_record(self.prepared["job"]["cancel_file"])
        self.assertEqual(record["nonce"], self.prepared["job"]["nonce"])
        self.assertEqual(record["status"], "cancel")
        self.assertFalse(self.ready)
        self.assertEqual(self.controller.state, "cancelled")

    def test_timeout_requests_cancellation_and_never_authorizes_exit(self):
        self.start()
        self.controller.started -= 121
        self.controller.poll()
        self.assertFalse(self.ready)
        self.assertTrue(read_record(self.prepared["job"]["cancel_file"]))
        self.assertEqual(self.controller.state, "failed")

    def test_worker_exception_is_reported_without_launch_or_exit(self):
        self.start(complete=False)
        self.controller.future.set_exception(OSError("No disk space"))
        self.controller.poll()
        self.assertFalse(self.launches)
        self.assertFalse(self.ready)
        self.assertIn("No disk space", self.errors[0])

    def test_cancel_marker_failure_after_error_keeps_polling_until_helper_exits(self):
        self.start()
        self.controller._fail("Prior preparation error")
        with patch("ui.update_install_controller.write_record", side_effect=OSError("Disk full")):
            self.assertFalse(self.controller.close())
            self.assertTrue(self.controller.timer.isActive())
            self.controller.poll()
            self.assertFalse(self.cancelled)
            self.process.returncode = 1
            self.controller.poll()
        self.assertTrue(self.cancelled)
        self.assertFalse(self.ready)


class OnlineUpdateWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        QCoreApplication.setOrganizationName("OsuSkinEditorTests")
        QCoreApplication.setApplicationName("OnlineUpdateWorkspace")
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(self.root / "settings"))
        QSettings().clear()
        self.patches = [patch("ui.main_window.UpdateService", FakeUpdateService),
                        patch("ui.update_install_controller.ThreadPoolExecutor", ControlledExecutor),
                        patch("ui.main_window.installed_executable", return_value=self.root / "editor.exe"),
                        patch("ui.software_update.installed_executable", return_value=self.root / "editor.exe")]
        for item in self.patches:
            item.start()
        self.window = MainWindow()
        self.window.setAttribute(Qt.WA_DontShowOnScreen)
        self.window.show()
        self.window._network_check_timer.stop()
        self.window._update_notice_timer.stop()
        self.window._update_result_timer.stop()
        APP.processEvents()
        self.process = FakeProcess()
        self.window._install_controller.launch = lambda prepared: self.process
        self.prepared = {"job": {"nonce": "w"*64,
                                 "prepared_file": str(self.root / "helper-ready.json"),
                                 "result_file": str(self.root / "result.json"),
                                 "cancel_file": str(self.root / "cancel.json")},
                         "job_path": str(self.root / "job.json"), "helper": str(self.root / "helper.exe")}

    def tearDown(self):
        if isValid(self.window):
            self.process.returncode = 1
            self.window.mania_ini_dock._dirty = False
            self.window.mania_design_dock._dirty = False
            self.window._install_controller.cancel()
            self.window.close()
            self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def ready_payload(self):
        payload = self.root / "payload.exe"
        payload.write_bytes(b"not an executable, never launched in these tests")
        self.window.update_service.downloaded(payload, RELEASE)

    def start_waiting(self):
        self.ready_payload()
        self.window._request_update_install()
        controller = self.window._install_controller
        controller.timer.stop()
        controller.future.set_result(self.prepared)
        controller.poll()
        return controller

    def test_menu_opens_single_nonmodal_update_dialog_and_checks_explicitly(self):
        self.window.act_check_updates.trigger()
        first = self.window._software_update_dialog
        self.assertIsInstance(first, SoftwareUpdateDialog)
        self.assertFalse(first.isModal())
        self.assertTrue(first.isVisible())
        self.assertTrue(self.window.update_service.checks)
        self.window.act_check_updates.trigger()
        self.assertIs(self.window._software_update_dialog, first)

    def test_update_availability_changes_menu_without_downloading_or_exiting(self):
        self.window.update_service.checked(RELEASE)
        self.assertEqual(self.window.available_release, RELEASE)
        self.assertFalse(self.window.update_service.downloads)
        self.assertFalse(self.window._preparing_update)
        self.assertFalse(self.window._update_exit_authorized)
        self.assertTrue(self.window.isVisible())

    def test_dirty_configuration_cancel_blocks_install_before_worker_starts(self):
        self.ready_payload()
        with patch.object(self.window.mania_ini_dock, "_confirm_discard_if_dirty", return_value=False), \
                patch.object(self.window, "_confirm_design_navigation") as design_guard:
            self.window._request_update_install()
            design_guard.assert_not_called()
        self.assertIsNone(self.window._install_controller.future)
        self.assertFalse(self.window._preparing_update)
        self.assertTrue(self.window.centralWidget().isEnabled())
        self.assertTrue(self.window.isVisible())

    def test_dirty_design_cancel_and_source_mode_both_refuse_install(self):
        self.ready_payload()
        with patch.object(self.window.mania_ini_dock, "_confirm_discard_if_dirty", return_value=True), \
                patch.object(self.window, "_confirm_design_navigation", return_value=False):
            self.window._request_update_install()
        self.assertIsNone(self.window._install_controller.future)
        with patch("ui.main_window.installed_executable", return_value=None):
            self.window._request_update_install()
        self.assertIsNone(self.window._install_controller.future)

    def test_preparing_disables_editing_then_helper_refusal_restores_window(self):
        controller = self.start_waiting()
        self.assertTrue(self.window._preparing_update)
        self.assertFalse(self.window.centralWidget().isEnabled())
        self.assertFalse(self.window.menuBar().isEnabled())
        self.assertTrue(self.window.isVisible())
        write_record(self.prepared["job"]["result_file"],
                     {"nonce": self.prepared["job"]["nonce"], "status": "failed", "message": "Target permission denied"})
        controller.poll()
        self.assertFalse(self.window._preparing_update)
        self.assertTrue(self.window.centralWidget().isEnabled())
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window._update_exit_authorized)

    def test_old_application_exits_only_after_live_nonce_bound_helper_handshake(self):
        controller = self.start_waiting()
        self.assertFalse(self.window._update_exit_authorized)
        write_record(self.prepared["job"]["prepared_file"],
                     {"schema_version": 1, "nonce": self.prepared["job"]["nonce"],
                      "status": "waiting", "pid": self.process.pid})
        with patch.object(APP, "quit") as quit_application:
            controller.poll()
            quit_application.assert_called_once()
        self.assertTrue(self.window._update_exit_authorized)
        self.assertEqual(controller.state, "committed")
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window.update_service.closed)
        self.assertEqual(self.window.settings.value("updates/pending_job"), self.prepared["job_path"])

    def test_failed_cancel_marker_defers_close_until_helper_has_exited(self):
        controller = self.start_waiting()
        controller._fail("Preparation failed")
        with patch("ui.update_install_controller.write_record", side_effect=OSError("No space for cancellation")):
            self.assertFalse(self.window.close())
            self.assertTrue(self.window.isVisible())
            self.assertTrue(self.window._quit_after_cancel)
            self.assertFalse(self.window.update_service.closed)
            self.assertTrue(controller.timer.isActive())
            self.process.returncode = 1
            controller.poll()
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window.update_service.closed)

    def test_auto_check_preference_and_close_stop_future_network_work(self):
        self.window.settings.setValue("updates/auto_check", False)
        self.window._background_online_check()
        self.assertFalse(self.window.update_service.checks)
        self.window.settings.setValue("updates/auto_check", True)
        self.window._background_online_check()
        self.assertEqual(self.window.update_service.announcement_checks, [False])
        self.assertTrue(self.window.update_service.checks)
        self.window.close()
        self.assertTrue(self.window.update_service.closed)
        self.assertFalse(self.window._network_check_timer.isActive())

    def test_background_check_applies_saved_connection_mode(self):
        self.window.settings.setValue("updates/source_mode", "ghproxy")
        self.window._background_online_check()
        self.assertEqual(self.window.update_service.source_mode, "ghproxy")
        self.assertEqual(self.window.update_service.announcement_checks, [False])
        self.assertTrue(self.window.update_service.checks)

    def test_window_restores_connection_mode_before_network_work(self):
        self.window.close()
        self.window.deleteLater()
        APP.processEvents()
        QSettings().setValue("updates/source_mode", "ghfast")
        self.window = MainWindow()
        self.window.setAttribute(Qt.WA_DontShowOnScreen)
        self.window.show()
        APP.processEvents()
        self.assertEqual(self.window.update_service.source_mode, "ghfast")
        self.assertFalse(self.window.update_service.checks)
        self.assertEqual(self.window.update_service.announcement_checks, [])


class UpdateLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.payload = self.root / "download" / "payload.exe"
        self.payload.parent.mkdir()
        self.payload.write_bytes(b"downloaded fixture bytes")
        self.target = self.root / "installed" / "editor.exe"
        self.target.parent.mkdir()
        self.target.write_bytes(b"existing program must stay intact")
        self.helper = self.root / "helper.exe"
        self.helper.write_bytes(b"bundled helper fixture")
        self.release = dict(RELEASE, size=self.payload.stat().st_size,
                            sha256=hashlib.sha256(self.payload.read_bytes()).hexdigest())

    def tearDown(self):
        self.temp.cleanup()

    def test_source_run_refuses_in_place_preparation(self):
        with patch("core.update_launch.installed_executable", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Windows packaged"):
                prepare_install_job(self.payload, self.release, threading.Event(), helper=self.helper)

    def test_preparation_copies_into_isolated_stage_without_changing_target_or_source(self):
        before = self.target.read_bytes(), self.payload.read_bytes(), self.helper.read_bytes()
        with patch("core.update_launch.process_image_path", return_value=None), \
                patch("core.update_launch.capture_process_identity", return_value={"creation_time": 1234}):
            result = prepare_install_job(self.payload, self.release, threading.Event(),
                                         target=self.target, helper=self.helper)
        job = result["job"]
        self.assertNotEqual(Path(job["payload_exe"]), self.payload)
        self.assertEqual(Path(job["payload_exe"]).read_bytes(), before[1])
        self.assertEqual(Path(result["helper"]).read_bytes(), before[2])
        self.assertEqual(before, (self.target.read_bytes(), self.payload.read_bytes(), self.helper.read_bytes()))
        self.assertEqual(read_record(result["job_path"])["nonce"], job["nonce"])
        self.assertEqual(job["parent_creation_times"][str(os.getpid())], 1234)

    def test_startup_acknowledgement_is_bound_to_nonce_target_and_expected_filename(self):
        stage = self.root / "stage"
        stage.mkdir()
        ready = stage / "ready.json"
        nonce = "n"*64
        write_record(stage / "job.json", {"nonce": nonce, "ready_file": str(ready), "target_exe": str(self.target)})
        with patch("core.update_launch.installed_executable", return_value=self.target), \
                patch("core.update_launch.capture_process_identity", return_value={"creation_time": 1234}):
            self.assertFalse(acknowledge_update_startup(str(ready), "wrong"))
            self.assertFalse(acknowledge_update_startup(str(stage / "foreign.json"), nonce))
            self.assertFalse(ready.exists())
            self.assertTrue(acknowledge_update_startup(str(ready), nonce))
            self.assertEqual(read_record(ready)["creation_time"], 1234)
            self.assertFalse(acknowledge_update_startup(str(ready), nonce))

    def test_malformed_handshake_record_is_ignored(self):
        record = self.root / "record.json"
        for raw in (b"broken", b"[]", b"["*2000+b"]"*2000):
            record.write_bytes(raw)
            self.assertIsNone(read_record(record))

    @unittest.skipUnless(os.name == "nt", "Packaged update helpers run on Windows")
    def test_helper_launch_is_hidden_argument_safe_and_resets_onefile_environment(self):
        import subprocess
        prepared = {"helper": str(self.root / "helper with spaces.exe"),
                    "job_path": str(self.root / "folder with spaces" / "job.json")}
        with patch.dict(os.environ, {"_PYI_APPLICATION_HOME_DIR": "old-bundle", "_MEIPASS2": "old-path"}), \
                patch("core.update_launch.subprocess.Popen") as start:
            launch_helper(prepared)
        args, kwargs = start.call_args
        self.assertEqual(args[0], [prepared["helper"], "--job", prepared["job_path"]])
        self.assertEqual(kwargs["cwd"], str(Path(prepared["job_path"]).parent))
        self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertEqual(kwargs["startupinfo"].wShowWindow, 0)
        self.assertFalse(any(key.startswith("_PYI_") for key in kwargs["env"]))
        self.assertNotIn("_MEIPASS2", kwargs["env"])
        self.assertEqual(kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")


if __name__ == "__main__":
    unittest.main()
