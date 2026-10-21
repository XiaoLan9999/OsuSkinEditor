"""Image repair uses real worker threads while Qt stays responsive and owns its lifetime."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import io
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import delete, isValid

from core.image_compat import (ImageIssue, ImageRepairCancelled, ImageRepairError,
                               RepairResult, ScanResult)
from ui.image_compat_dialog import ImageCompatibilityDialog


APP = QApplication.instance() or QApplication([])
MODULE = "ui.image_compat_dialog."


def issue(name="Mania/LN_Body.png", *, repairable=True, actual="TIFF", status="mismatch"):
    return ImageIssue(name, "PNG", actual, 32, 64, status, repairable,
                      "PNG extension with TIFF content", "a" * 64)


class ImageCompatibilityDialogTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dialogs = []
        self.releases = []
        self.language = patch(MODULE + "i18n.lang", return_value="en-US")
        self.text = patch(MODULE + "i18n.t", side_effect=lambda key, default=None: default or key)
        self.language.start()
        self.text.start()

    def tearDown(self):
        for event in self.releases:
            event.set()
        for dialog in self.dialogs:
            if not isValid(dialog):
                continue
            dialog.cancel()
            self.wait_for(lambda: not dialog.busy)
            dialog.reject()
            dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.text.stop()
        self.language.stop()
        self.temp.cleanup()

    def wait_for(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            APP.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Timed out waiting for image dialog worker")

    def make_dialog(self, issues=None):
        if issues is not None:
            scanner = patch(MODULE + "scan_skin_images", return_value=ScanResult(len(issues), tuple(issues)))
            scanner.start()
            self.addCleanup(scanner.stop)
        dialog = ImageCompatibilityDialog(self.root)
        self.dialogs.append(dialog)
        dialog.show()
        self.wait_for(lambda: not dialog._start_timer.isActive() and not dialog.busy)
        return dialog

    def blocked_repair(self, terminal):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.releases.append(release)
        observed = {}

        def repair(root, issues, *, cancelled, progress):
            observed["thread"] = threading.get_ident()
            observed["issues"] = issues
            entered.set()
            try:
                release.wait(3)
                observed["cancelled"] = cancelled()
                progress(1, 2, "Mania/LN_Body.png")
                if isinstance(terminal, Exception):
                    raise terminal
                return terminal
            finally:
                finished.set()

        patched = patch(MODULE + "repair_skin_images", side_effect=repair)
        patched.start()
        self.addCleanup(patched.stop)
        return entered, release, finished, observed

    def test_read_only_scan_reports_disguised_png_and_never_creates_backup(self):
        source = self.root / "Mania" / "LN_Body.png"
        source.parent.mkdir()
        Image.new("RGBA", (7, 11), (70, 80, 90, 110)).save(source, format="TIFF")
        original = source.read_bytes()
        dialog = self.make_dialog()
        self.assertEqual(source.read_bytes(), original)
        self.assertFalse((self.root / "__conflicts_backup").exists())
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 1).text(), "Mania/LN_Body.png")
        self.assertEqual(dialog.table.item(0, 2).text(), "TIFF / .png")
        self.assertEqual(dialog.table.item(0, 3).text(), "7 × 11")
        self.assertTrue(dialog.repair_button.isEnabled())

    def test_real_repair_keeps_filename_pixels_and_original_backup(self):
        source = self.root / "LN.png"
        Image.new("RGBA", (4, 3), (23, 48, 92, 100)).save(source, format="TIFF")
        original = source.read_bytes()
        with Image.open(io.BytesIO(original)) as image:
            pixels = image.convert("RGBA").tobytes()
        dialog = self.make_dialog()
        changes = []
        dialog.assets_changed.connect(lambda: changes.append(True))
        self.assertTrue(dialog.repair_selected())
        self.wait_for(lambda: not dialog.busy)
        self.assertEqual(changes, [True])
        with Image.open(source) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.convert("RGBA").tobytes(), pixels)
        self.assertEqual((dialog._backup_dir / "LN.png").read_bytes(), original)
        self.assertEqual(dialog.table.item(0, 0).checkState(), Qt.Unchecked)
        self.assertFalse(dialog.table.item(0, 0).flags() & Qt.ItemIsUserCheckable)
        self.assertFalse(dialog.repair_button.isEnabled())
        self.assertIn(str(dialog._backup_dir), dialog.backup_label.text())
        self.assertTrue(dialog.open_backup_button.isVisible())

    def test_multiframe_and_unreadable_rows_cannot_be_selected(self):
        broken = issue("broken.png", repairable=False, actual="", status="unreadable")
        multi = issue("animation.png", repairable=False, actual="GIF")
        unsupported = issue("extra.tif", repairable=False, status="unsupported")
        dialog = self.make_dialog([issue(), broken, multi, unsupported])
        self.assertEqual(len(dialog._selected()), 1)
        dialog._select_repairable()
        self.assertEqual(len(dialog._selected()), 1)
        for row in (1, 2, 3):
            self.assertFalse(dialog.table.item(row, 0).flags() & Qt.ItemIsUserCheckable)
        self.assertIn("replace", dialog.table.item(1, 4).text())
        self.assertIn("Manual", dialog.table.item(2, 4).text())
        self.assertIn("references", dialog.table.item(3, 4).text())

    def test_repair_only_submits_checked_repairable_rows(self):
        dialog = self.make_dialog([issue("first.png"), issue("second.png"), issue("bad.png", repairable=False)])
        dialog.table.item(1, 0).setCheckState(Qt.Unchecked)
        terminal = RepairResult(("first.png",), self.root / "__conflicts_backup" / "backup", ())
        entered, release, _, observed = self.blocked_repair(terminal)
        self.assertTrue(dialog.repair_selected())
        self.wait_for(entered.is_set)
        self.assertEqual([item.relative_path for item in observed["issues"]], ["first.png"])
        release.set()
        self.wait_for(lambda: not dialog.busy)
        self.assertEqual(observed["thread"] != threading.get_ident(), True)

    def test_no_selection_cannot_start_repair(self):
        dialog = self.make_dialog([issue()])
        dialog.table.item(0, 0).setCheckState(Qt.Unchecked)
        with patch(MODULE + "repair_skin_images") as repair:
            self.assertFalse(dialog.repair_selected())
        repair.assert_not_called()
        self.assertFalse(dialog.repair_button.isEnabled())

    def test_cancel_keeps_partial_repairs_backup_and_emits_refresh_once(self):
        dialog = self.make_dialog([issue("first.png"), issue("second.png")])
        backup = self.root / "__conflicts_backup" / "saved"
        terminal = ImageRepairCancelled("cancelled", partial_result=RepairResult(("first.png",), backup, ()))
        entered, release, _, observed = self.blocked_repair(terminal)
        changes = []
        dialog.assets_changed.connect(lambda: changes.append(True))
        dialog.repair_selected()
        self.wait_for(entered.is_set)
        dialog.cancel()
        self.assertTrue(dialog.busy)
        self.assertFalse(dialog.cancel_button.isEnabled())
        release.set()
        self.wait_for(lambda: not dialog.busy)
        self.assertTrue(observed["cancelled"])
        self.assertEqual(changes, [True])
        self.assertIn("1 repaired", dialog.status.text())
        self.assertIn(str(backup), dialog.backup_label.text())
        self.assertFalse(dialog.table.item(0, 0).flags() & Qt.ItemIsUserCheckable)
        self.assertTrue(dialog.table.item(1, 0).flags() & Qt.ItemIsUserCheckable)

    def test_partial_failure_reports_completed_count_and_refreshes(self):
        dialog = self.make_dialog([issue("first.png"), issue("second.png")])
        terminal = ImageRepairError("disk unavailable", partial_result=RepairResult(("first.png",), self.root / "backup", ()))
        entered, release, _, _ = self.blocked_repair(terminal)
        changes = []
        dialog.assets_changed.connect(lambda: changes.append(True))
        dialog.repair_selected()
        self.wait_for(entered.is_set)
        release.set()
        self.wait_for(lambda: not dialog.busy)
        self.assertIn("1 repaired", dialog.status.text())
        self.assertIn("disk unavailable", dialog.status.text())
        self.assertEqual(changes, [True])
        self.assertTrue(dialog.repair_button.isEnabled())

    def test_close_waits_for_worker_completion_without_blocking_gui(self):
        dialog = self.make_dialog([issue()])
        entered, release, _, _ = self.blocked_repair(ImageRepairCancelled("cancelled"))
        pulses, rejected = [], []
        dialog.rejected.connect(lambda: rejected.append(True))
        timer = QTimer(dialog)
        timer.setInterval(5)
        timer.timeout.connect(lambda: pulses.append(True))
        timer.start()
        dialog.repair_selected()
        self.wait_for(entered.is_set)
        dialog.close()
        self.assertTrue(dialog.isVisible())
        self.assertTrue(dialog._close_pending)
        self.assertEqual(rejected, [])
        self.wait_for(lambda: len(pulses) >= 2)
        release.set()
        self.wait_for(lambda: bool(rejected))
        self.assertEqual(rejected, [True])
        self.assertFalse(dialog.isVisible())
        self.assertTrue(dialog._executor_closed)

    def test_escape_cancels_worker_and_defers_reject(self):
        dialog = self.make_dialog([issue()])
        entered, release, _, observed = self.blocked_repair(ImageRepairCancelled("cancelled"))
        dialog.repair_selected()
        self.wait_for(entered.is_set)
        QTest.keyClick(dialog, Qt.Key_Escape)
        self.assertTrue(dialog.isVisible())
        self.assertTrue(dialog._close_pending)
        release.set()
        self.wait_for(lambda: not dialog.isVisible())
        self.assertTrue(observed["cancelled"])

    def test_parent_style_destruction_cancels_without_worker_qt_access(self):
        dialog = self.make_dialog([issue()])
        entered, release, finished, observed = self.blocked_repair(ImageRepairCancelled("cancelled"))
        dialog.repair_selected()
        self.wait_for(entered.is_set)
        future = dialog._future
        delete(dialog)
        self.assertFalse(isValid(dialog))
        release.set()
        self.wait_for(finished.is_set)
        self.assertTrue(observed["cancelled"])
        self.assertIsInstance(future.exception(timeout=1), ImageRepairCancelled)

    def test_empty_scan_explains_healthy_images_and_disables_repair(self):
        dialog = self.make_dialog([])
        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertIn("no format compatibility", dialog.status.text())
        self.assertFalse(dialog.repair_button.isEnabled())
        self.assertFalse(dialog.open_backup_button.isVisible())

    def test_scan_failure_allows_rescan_and_does_not_emit_assets_changed(self):
        with patch(MODULE + "scan_skin_images", side_effect=ImageRepairError("read failure")):
            dialog = self.make_dialog()
        self.assertIn("read failure", dialog.status.text())
        changes = []
        dialog.assets_changed.connect(lambda: changes.append(True))
        with patch(MODULE + "scan_skin_images", return_value=ScanResult(7, ())):
            self.assertTrue(dialog.start_scan())
            self.wait_for(lambda: not dialog.busy)
        self.assertEqual(changes, [])
        self.assertIn("Checked 7", dialog.status.text())

    def test_open_backup_uses_file_url_for_recorded_path(self):
        dialog = self.make_dialog([])
        dialog._backup_dir = self.root / "__conflicts_backup" / "backup with spaces"
        dialog._render_backup()
        with patch(MODULE + "QDesktopServices.openUrl", return_value=True) as open_url:
            dialog.open_backup_button.click()
        self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()), dialog._backup_dir)

    def test_busy_controls_prevent_duplicate_repair_and_rescan(self):
        dialog = self.make_dialog([issue()])
        entered, release, _, _ = self.blocked_repair(RepairResult((), None, ()))
        self.assertTrue(dialog.repair_selected())
        self.wait_for(entered.is_set)
        self.assertFalse(dialog.repair_selected())
        self.assertFalse(dialog.start_scan())
        self.assertFalse(dialog.table.isEnabled())
        self.assertFalse(dialog.rescan_button.isEnabled())
        release.set()
        self.wait_for(lambda: not dialog.busy)
        self.assertTrue(dialog.table.isEnabled())


if __name__ == "__main__":
    unittest.main()
