"""Exercise OSK import, ordinary editing, and export through the main window."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QSettings, Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from core import i18n
from core.mania_designer import ManiaDesign
from core.skin_ini import SkinIni
from ui.assets_manager import AssetsManagerDialog
from ui.main_window import MainWindow


APP = QApplication.instance() or QApplication([])


def png_bytes(colour="white", size=(16, 16)):
    output = io.BytesIO()
    Image.new("RGBA", size, colour).save(output, format="PNG")
    return output.getvalue()


class OskWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspaces = self.root / "workspaces"
        self.source = self.root / "中文皮肤.OSK"
        self.windows = []
        QCoreApplication.setOrganizationName("OsuSkinEditorTests")
        QCoreApplication.setApplicationName("OskWorkspace")
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope,
                          str(self.root / "settings"))
        QSettings().setValue("ui/language", "zh-CN")
        i18n.load_language("zh-CN")
        self.window = self.new_window()
        self.critical = patch.object(QMessageBox, "critical").start()
        self.warning = patch.object(QMessageBox, "warning").start()
        self.info = patch.object(QMessageBox, "information").start()
        self.addCleanup(patch.stopall)

    def new_window(self):
        window = MainWindow()
        window._archive_workspace_override = self.workspaces
        window.setAttribute(Qt.WA_DontShowOnScreen)
        window.show()
        APP.processEvents()
        self.windows.append(window)
        return window

    def tearDown(self):
        for window in self.windows:
            window._archive_controller.cancel()
            window.mania_ini_dock._dirty = False
            window.mania_design_dock._dirty = False
        self.wait(lambda: all(not window._archive_controller.busy
                              for window in self.windows))
        for window in self.windows:
            window.close()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.windows.clear()
        self.temp.cleanup()

    def wait(self, predicate, timeout=6):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            APP.processEvents()
            QTest.qWait(5)
        self.assertTrue(predicate(), "Asynchronous OSK workspace operation did not finish")

    def archive(self, entries=None):
        if entries is None:
            entries = {
                "skin.ini": ("[General]\nName: OSK fixture\nAuthor: Fixture\n"
                             "[Mania]\nKeys: 4\nHitPosition: 402\n"
                             "ColumnWidth: 30,30,30,30\nHoldLine: 0\n"
                             "NoteImage0H: custom-hold\n").encode(),
                "hitcircle.png": png_bytes(),
                "mania-note1.png": png_bytes("cyan"),
                "mania-key1.png": png_bytes("white"),
                "mania-key1D.png": png_bytes("cyan"),
                "mania-key2.png": png_bytes("white"),
                "mania-key2D.png": png_bytes("cyan"),
                "custom-hold.png": png_bytes("magenta", (12, 24)),
                "nested/unknown.dat": b"keep unknown data exactly",
            }
        with ZipFile(self.source, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return entries, hashlib.sha256(self.source.read_bytes()).hexdigest()

    def imported(self):
        self.assertTrue(self.window.import_osk_file(self.source))
        self.wait(lambda: not self.window._archive_controller.busy)
        self.assertIsNotNone(self.window.skin)
        self.assertFalse(self.critical.called)
        return self.window.skin.root

    def export(self, output, mode="saved"):
        with patch("ui.skin_archive_workflow.QFileDialog.getSaveFileName",
                   return_value=(str(output), "")), \
                patch.object(self.window, "_choose_export_edit_mode", return_value=mode):
            accepted = self.window.on_export_osk()
        if accepted:
            self.wait(lambda: not self.window._archive_controller.busy)
        return accepted

    def test_real_import_asset_ui_ini_save_and_export_round_trip(self):
        entries, original_hash = self.archive()
        work_root = self.imported()
        self.assertNotEqual(work_root, self.source.parent)
        self.assertEqual(work_root.parent, self.workspaces)
        self.assertEqual(self.window.skin_title.text(), "OSK fixture")
        self.assertEqual(self.window.asset_list.count(), 7)
        self.assertIs(self.window.std_preview.skin, self.window.skin)
        self.assertIs(self.window.mania_preview.skin, self.window.skin)

        replacement = self.root / "replacement.png"
        replacement.write_bytes(png_bytes("red", (30, 40)))
        manager = AssetsManagerDialog(work_root, self.window)
        manager.refresh_images(select=work_root / "mania-note1.png")
        changed = []
        manager.assets_changed.connect(lambda: changed.append(True))
        with patch("ui.assets_manager.QFileDialog.getOpenFileName",
                   return_value=(str(replacement), "")):
            manager._replace_image()
        manager.done(0)
        manager.deleteLater()
        self.assertEqual(changed, [True])
        with Image.open(work_root / "mania-note1.png") as image:
            self.assertEqual(image.size, (30, 40))
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))

        dock = self.window.mania_ini_dock
        dock.spn_hit_pos.setValue(385)
        self.assertTrue(dock._dirty)
        self.assertTrue(dock._on_save_clicked())
        self.assertFalse(dock._dirty)
        output = self.root / "edited.osk"
        self.assertTrue(self.export(output))
        with ZipFile(output) as archive:
            names = archive.namelist()
            self.assertNotIn("skin.ini.bak", names)
            self.assertFalse(any(name.startswith(".skin_ini_history/") for name in names))
            self.assertFalse(any(name.startswith("__conflicts_backup/") for name in names))
            self.assertEqual(archive.read("mania-note1.png"),
                             (work_root / "mania-note1.png").read_bytes())
            self.assertEqual(archive.read("custom-hold.png"), entries["custom-hold.png"])
            self.assertEqual(archive.read("nested/unknown.dat"), entries["nested/unknown.dat"])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), original_hash)
        self.assertTrue(self.window.load_skin(str(output)))
        self.wait(lambda: not self.window._archive_controller.busy)
        round_trip = self.window.skin.root
        self.assertNotEqual(round_trip, work_root)
        values = SkinIni.read(round_trip / "skin.ini").mania_get(4)
        self.assertEqual(values["HitPosition"], "385")
        self.assertEqual(values["HoldLine"], "0")
        self.assertEqual(values["NoteImage0H"], "custom-hold")
        self.assertEqual((round_trip / "mania-note1.png").read_bytes(),
                         (work_root / "mania-note1.png").read_bytes())

    def test_corrupt_import_leaves_current_skin_and_unsaved_values(self):
        self.archive()
        self.imported()
        previous = self.window.skin
        self.window.mania_ini_dock.spn_hit_pos.setValue(390)
        self.window.mania_design_dock.top_height.setValue(30)
        broken = self.root / "broken.osk"
        broken.write_bytes(b"This is not a ZIP archive")
        with patch.object(self.window.mania_ini_dock,
                          "_confirm_discard_if_dirty") as guard:
            self.assertTrue(self.window.import_osk_file(broken))
            self.wait(lambda: not self.window._archive_controller.busy)
            guard.assert_not_called()
        self.assertTrue(self.critical.called)
        self.assertIs(self.window.skin, previous)
        self.assertEqual(self.window.mania_ini_dock.spn_hit_pos.value(), 390)
        self.assertTrue(self.window.mania_ini_dock._dirty)
        self.assertEqual(self.window.mania_design_dock.options().top_mask_height, 30)
        self.assertTrue(self.window.mania_design_dock._dirty)
        self.assertEqual([path for path in self.workspaces.iterdir()
                          if path.name != ".osk-origins"], [previous.root])

    def test_refused_navigation_keeps_extracted_copy_and_pending_edit(self):
        self.archive()
        previous_root = self.imported()
        previous = self.window.skin
        self.window.mania_ini_dock.spn_hit_pos.setValue(380)
        with patch.object(self.window.mania_ini_dock,
                          "_confirm_discard_if_dirty", return_value=False) as guard:
            self.assertTrue(self.window.load_skin(str(self.source)))
            self.wait(lambda: not self.window._archive_controller.busy)
            guard.assert_called_once()
        self.assertIs(self.window.skin, previous)
        self.assertEqual(self.window.mania_ini_dock.spn_hit_pos.value(), 380)
        self.assertTrue(self.window.mania_ini_dock._dirty)
        roots = [path for path in self.workspaces.iterdir()
                 if path.name != ".osk-origins"]
        self.assertEqual(len(roots), 2)
        other = next(root for root in roots if root != previous_root)
        self.assertTrue((other / "skin.ini").is_file())
        self.assertEqual(self.window._archive_origin(other), str(self.source.resolve()))

    def test_export_cannot_overwrite_imported_source(self):
        _entries, original_hash = self.archive()
        self.imported()
        self.assertFalse(self.export(self.source))
        self.warning.assert_called_once()
        self.assertFalse(self.window._archive_controller.busy)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), original_hash)

    def test_lazer_native_json_without_ini_is_preserved_and_explained(self):
        entries, original_hash = self.archive({
            "skininfo.json": json.dumps({"Name": "Lazer skin", "Creator": "Player"}).encode(),
            "MainHUDComponents.json": b'{"Components":[],"Unknown":[1,2,3]}',
            "Playfield.json": b'{"Layers":[],"Keep":"yes"}',
            "cursor.png": png_bytes("cyan"),
        })
        work_root = self.imported()
        self.assertEqual(self.window.skin_title.text(), "Lazer skin")
        self.assertIn("Name: Lazer skin", (work_root / "skin.ini").read_text())
        self.assertIn(i18n.t("osk.native_notice"), self.window.archive_note.text())
        self.assertFalse(self.window.archive_note.isHidden())
        output = self.root / "lazer-edited.osk"
        self.assertTrue(self.export(output))
        with ZipFile(output) as archive:
            for name, data in entries.items():
                self.assertEqual(archive.read(name), data)
            self.assertIn("skin.ini", archive.namelist())
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), original_hash)

    def test_language_switch_updates_archive_actions_and_buttons(self):
        for language in ("en-US", "zh-CN"):
            self.window.on_change_language(language)
            for control, key in ((self.window.act_import_osk, "osk.import"),
                                 (self.window.act_export_osk, "osk.export"),
                                 (self.window.btn_import_osk, "osk.button_import"),
                                 (self.window.btn_welcome_osk, "osk.button_import"),
                                 (self.window.btn_export_osk, "osk.button_export")):
                self.assertEqual(control.text(), i18n.t(key))
            self.assertTrue(self.window.btn_import_osk.isEnabled())
            self.assertFalse(self.window.btn_export_osk.isEnabled())
        self.assertEqual(self.window.act_import_osk.shortcut().toString(), "Ctrl+Shift+O")
        self.assertEqual(self.window.act_export_osk.shortcut().toString(), "Ctrl+Shift+E")

    def test_header_welcome_and_file_import_controls_use_archive_workflow(self):
        self.archive()
        roots = []
        for control in (self.window.btn_welcome_osk, self.window.btn_import_osk,
                        self.window.act_import_osk):
            with patch("ui.skin_archive_workflow.QFileDialog.getOpenFileName",
                       return_value=(str(self.source), "")) as chooser:
                if control is self.window.act_import_osk:
                    control.trigger()
                else:
                    control.click()
                self.wait(lambda: not self.window._archive_controller.busy)
                chooser.assert_called_once()
            self.assertIsNotNone(self.window.skin)
            self.assertEqual(self.window._archive_origin(), str(self.source.resolve()))
            roots.append(self.window.skin.root)
        self.assertEqual(len(set(roots)), 3)
        self.assertTrue(all(root.is_dir() for root in roots))

    def test_header_and_file_export_controls_add_osk_extension(self):
        self.archive()
        self.imported()
        for control, name in ((self.window.btn_export_osk, "button-output"),
                              (self.window.act_export_osk, "action-output")):
            output = self.root / name
            with patch("ui.skin_archive_workflow.QFileDialog.getSaveFileName",
                       return_value=(str(output), "")) as chooser:
                if control is self.window.act_export_osk:
                    control.trigger()
                else:
                    control.click()
                self.wait(lambda: not self.window._archive_controller.busy)
                chooser.assert_called_once()
            self.assertTrue(output.with_suffix(".osk").is_file())
            self.assertFalse(output.exists())
            with ZipFile(output.with_suffix(".osk")) as archive:
                self.assertIn("skin.ini", archive.namelist())

    def test_drag_accepts_single_local_osk_and_rejects_remote_or_multiple(self):
        self.archive()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.source))])
        self.assertEqual(self.window._dropped_skin(mime), self.source)
        mime.setUrls([QUrl("https://example.com/skin.osk")])
        self.assertIsNone(self.window._dropped_skin(mime))
        mime.setUrls([QUrl.fromLocalFile(str(self.source))] * 2)
        self.assertIsNone(self.window._dropped_skin(mime))

    def test_reopen_last_persistent_workspace_retains_origin_protection(self):
        self.archive()
        work_root = self.imported()
        self.window.settings.sync()
        self.assertEqual(self.window.settings.value("paths/last_skin", "", str), str(work_root))
        self.assertTrue(self.window.close())
        reopened = self.new_window()
        reopened.on_open_last_skin()
        self.assertEqual(reopened.skin.root, work_root)
        self.assertEqual(reopened._archive_origin(), str(self.source.resolve()))
        self.assertIn(self.source.name, reopened.archive_note.text())
        with patch("ui.skin_archive_workflow.QFileDialog.getSaveFileName",
                   return_value=(str(self.source), "")):
            self.assertFalse(reopened.on_export_osk())
        self.assertTrue(self.warning.called)

    def test_evicted_source_cache_still_protects_old_workspace_after_reopen(self):
        _entries, original_hash = self.archive()
        first_root = self.imported()
        for index in range(35):
            other_root = self.workspaces / ("later-workspace-" + str(index))
            other_root.mkdir()
            self.window._remember_archive_origin(other_root,
                                                 self.root / ("later-source-" + str(index) + ".osk"))
        self.assertNotIn(str(first_root.resolve()), self.window._archive_sources)
        self.window.settings.sync()
        self.assertTrue(self.window.close())
        reopened = self.new_window()
        self.assertNotIn(str(first_root.resolve()), reopened._archive_sources)
        self.assertTrue(reopened.load_skin(str(first_root)))
        self.assertEqual(reopened._archive_origin(), str(self.source.resolve()))
        self.assertIn(self.source.name, reopened.archive_note.text())
        with patch("ui.skin_archive_workflow.QFileDialog.getSaveFileName",
                   return_value=(str(self.source), "")), \
                patch.object(reopened._archive_controller, "export_folder") as exporter:
            self.assertFalse(reopened.on_export_osk())
            exporter.assert_not_called()
        self.warning.assert_called_once()
        self.assertFalse(reopened._archive_controller.busy)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), original_hash)

        # Origin records belong beside the workspaces, never to the skin export.
        origin_record = self.workspaces / ".osk-origins" / (first_root.name + ".json")
        self.assertTrue(origin_record.is_file())
        self.assertFalse(origin_record.is_relative_to(first_root))
        output = self.root / "old-workspace-edited.osk"
        self.window = reopened
        self.assertTrue(self.export(output))
        with ZipFile(output) as archive:
            self.assertFalse(any(".osk-origins" in name for name in archive.namelist()))
            source_spellings = (str(self.source.resolve()), self.source.resolve().as_posix(),
                                json.dumps(str(self.source.resolve()), ensure_ascii=False)[1:-1])
            for name in archive.namelist():
                data = archive.read(name)
                for spelling in source_spellings:
                    self.assertNotIn(spelling.encode("utf-8"), data)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), original_hash)

    def test_apply_export_persists_pending_ini_and_design_once(self):
        _entries, original_hash = self.archive()
        work_root = self.imported()
        self.window.mania_ini_dock.spn_hit_pos.setValue(395)
        self.window.mania_design_dock.top_height.setValue(40)
        self.window.mania_design_dock.receptor_raise.setValue(20)
        output = self.root / "designed.osk"
        self.assertTrue(self.export(output, mode="apply"))
        designed_root = self.window.skin.root
        self.assertNotEqual(designed_root, work_root)
        self.assertEqual(SkinIni.read(work_root / "skin.ini").mania_get(4)["HitPosition"], "395")
        designed = SkinIni.read(designed_root / "skin.ini").mania_get(4)
        self.assertEqual(designed["HitPosition"], "375")
        self.assertIn("StageBottom", designed)
        self.assertFalse(self.window.mania_ini_dock._dirty)
        self.assertFalse(self.window.mania_design_dock._dirty)
        self.assertEqual(self.window.mania_design_dock.options(), ManiaDesign())
        self.assertEqual(self.window.mania_preview.design_options, ManiaDesign())
        self.assertEqual(self.window._archive_origin(), str(self.source.resolve()))
        second = self.root / "designed-second.osk"
        self.assertTrue(self.export(second))
        with ZipFile(output) as first, ZipFile(second) as later:
            self.assertEqual({name: first.read(name) for name in first.namelist()},
                             {name: later.read(name) for name in later.namelist()})
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), original_hash)

    def test_saved_export_uses_disk_and_retains_pending_preview_edits(self):
        self.archive()
        work_root = self.imported()
        self.window.mania_ini_dock.spn_hit_pos.setValue(380)
        self.window.mania_design_dock.top_height.setValue(40)
        self.assertTrue(self.export(self.root / "saved.osk", mode="saved"))
        self.assertEqual(self.window.skin.root, work_root)
        self.assertTrue(self.window.mania_ini_dock._dirty)
        self.assertTrue(self.window.mania_design_dock._dirty)
        with ZipFile(self.root / "saved.osk") as archive:
            self.assertIn(b"HitPosition: 402", archive.read("skin.ini"))
            self.assertNotIn(b"StageBottom", archive.read("skin.ini"))

    def test_cancelled_export_choice_creates_no_output_and_retains_edits(self):
        self.archive()
        work_root = self.imported()
        original = (work_root / "skin.ini").read_bytes()
        self.window.mania_ini_dock.spn_hit_pos.setValue(380)
        output = self.root / "cancelled.osk"
        self.assertFalse(self.export(output, mode=None))
        self.assertFalse(output.exists())
        self.assertTrue(self.window.mania_ini_dock._dirty)
        self.assertEqual((work_root / "skin.ini").read_bytes(), original)

    def test_busy_isolates_ui_and_close_waits_for_cancelled_worker(self):
        self.archive()
        self.imported()
        previous = self.window.skin
        started = threading.Event()
        release = threading.Event()

        def importer(source, dest, *, progress, cancelled):
            (dest / "partial.png").write_bytes(b"partial")
            started.set()
            while not cancelled():
                time.sleep(0.005)
            release.wait(3)
            raise InterruptedError("Cancelled import")

        self.window._archive_controller._importer = importer
        self.assertTrue(self.window.import_osk_file(self.source))
        self.wait(started.is_set)
        self.assertFalse(self.window.centralWidget().isEnabled())
        self.assertFalse(self.window.menuBar().isEnabled())
        self.assertFalse(self.window.mania_ini_dock.isEnabled())
        self.assertFalse(self.window.act_import_osk.isEnabled())
        self.assertFalse(self.window.act_export_osk.isEnabled())
        self.assertFalse(self.window.load_skin(str(self.source)))
        self.assertFalse(self.window.import_osk_file(self.source))
        self.assertFalse(self.window.on_export_osk())
        with patch("ui.main_window.AssetsManagerDialog") as manager:
            self.window._open_assets_manager()
            manager.assert_not_called()
        self.assertFalse(self.window.close())
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window._archive_controller.busy)
        self.assertFalse(self.window._closing)
        release.set()
        self.wait(lambda: not self.window._archive_controller.busy)
        self.wait(lambda: self.window._closing)
        self.assertFalse(self.window.isVisible())
        self.assertIs(self.window.skin, previous)
        self.assertEqual([path for path in self.workspaces.iterdir()
                          if path.name != ".osk-origins"], [previous.root])


if __name__ == "__main__":
    unittest.main()
