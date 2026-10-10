"""Archive work runs asynchronously and never changes the imported source OSK."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from zipfile import ZipFile

from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PIL import Image

from core.mania_designer import ManiaDesign
from core.skin_ini import SkinIni

from ui.skin_archive_controller import ArchiveController


APP = QApplication.instance() or QApplication([])


class ArchiveControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspaces"
        self.source = self.root / "中文皮肤.osk"
        self.controllers = []

    def tearDown(self):
        for controller in self.controllers:
            controller.cancel()
        self.wait(lambda: all(not controller.busy for controller in self.controllers))
        for controller in self.controllers:
            self.assertTrue(controller.close())
        self.temp.cleanup()

    def controller(self, **kwargs):
        controller = ArchiveController(**kwargs)
        self.controllers.append(controller)
        controller.results = []
        controller.progresses = []
        controller.transitions = []
        controller.imported.connect(lambda path, info, source:
                                    controller.results.append(("import", path, info, source,
                                                               controller.busy)))
        controller.exported.connect(lambda path:
                                    controller.results.append(("export", path, controller.busy)))
        controller.cancelled.connect(lambda kind:
                                     controller.results.append(("cancelled", kind, controller.busy)))
        controller.failed.connect(lambda kind, message:
                                  controller.results.append(("failed", kind, message,
                                                             controller.busy)))
        controller.progress.connect(lambda kind, done, total:
                                    controller.progresses.append((kind, done, total)))
        controller.busy_changed.connect(controller.transitions.append)
        return controller

    def wait(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            APP.processEvents()
            QTest.qWait(5)
        self.assertTrue(predicate(), "Asynchronous archive task did not complete")

    def archive(self, entries):
        with ZipFile(self.source, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return hashlib.sha256(self.source.read_bytes()).hexdigest()

    def test_real_import_and_export_preserve_nested_files_and_original(self):
        entries = {"skin.ini": b"[General]\nName: Test\n",
                   "sounds/hit.wav": bytes(range(256)),
                   "cursor.png": b"uninterpreted texture bytes"}
        original_hash = self.archive(entries)
        controller = self.controller()
        self.assertTrue(controller.import_file(self.source, self.workspace))
        self.wait(lambda: bool(controller.results))
        result = controller.results[0]
        self.assertEqual(result[0], "import")
        self.assertFalse(result[-1])
        self.assertEqual(result[3], str(self.source.resolve()))
        work_root = Path(result[1])
        self.assertEqual(work_root.parent, self.workspace.resolve())
        self.assertTrue(work_root.name.startswith("中文皮肤-"))
        self.assertTrue(result[2].has_skin_ini)
        for name, content in entries.items():
            self.assertEqual((work_root / name).read_bytes(), content)
        out = self.root / "edited.osk"
        self.assertTrue(controller.export_folder(work_root, out))
        self.wait(lambda: len(controller.results) == 2)
        self.assertEqual(controller.results[1], ("export", str(out.resolve()), False))
        with ZipFile(out) as archive:
            self.assertEqual({name: archive.read(name) for name in archive.namelist()}, entries)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), original_hash)
        self.assertEqual(controller.transitions, [True, False, True, False])

    def test_lazer_metadata_adds_ini_without_modifying_json_or_injecting_sections(self):
        metadata = json.dumps({"Name": "中文\n[Mania]\nKeys: 9",
                               "Creator": "Author\r\nVersion: injected"},
                              ensure_ascii=False).encode("utf-8")
        entries = {"skininfo.json": metadata,
                   "Playfield.json": b'{"Widgets": [], "Unknown": "retain me"}',
                   "cursor.png": b"texture"}
        original_hash = self.archive(entries)
        controller = self.controller()
        self.assertTrue(controller.import_file(self.source, self.workspace))
        self.wait(lambda: bool(controller.results))
        result = controller.results[0]
        self.assertEqual(result[0], "import")
        self.assertFalse(result[2].has_skin_ini)
        self.assertTrue(result[2].has_lazer_metadata)
        root = Path(result[1])
        ini = (root / "skin.ini").read_text(encoding="utf-8")
        self.assertEqual(ini.splitlines(), ["[General]", "Name: 中文 [Mania] Keys: 9",
                                           "Author: Author Version: injected", "Version: latest"])
        for name, content in entries.items():
            self.assertEqual((root / name).read_bytes(), content)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), original_hash)

    def test_busy_and_gui_thread_delivery(self):
        started = threading.Event()
        release = threading.Event()
        worker_threads = []
        callback_threads = []

        def importer(source, dest, *, progress, cancelled):
            worker_threads.append(threading.get_ident())
            progress(5, 10)
            started.set()
            release.wait(3)
            (dest / "skin.ini").write_text("[General]", encoding="utf-8")
            return SimpleNamespace(has_skin_ini=True)

        controller = self.controller(importer=importer)
        controller.progress.connect(lambda *_args: callback_threads.append(QThread.currentThread()))
        controller.imported.connect(lambda *_args: callback_threads.append(QThread.currentThread()))
        self.assertTrue(controller.import_file(self.source, self.workspace))
        self.wait(started.is_set)
        self.assertTrue(controller.busy)
        self.assertFalse(controller.import_file(self.source, self.workspace))
        self.assertFalse(controller.export_folder(self.root, self.root / "output.osk"))
        self.wait(lambda: bool(controller.progresses))
        self.assertNotEqual(worker_threads, [threading.get_ident()])
        release.set()
        self.wait(lambda: bool(controller.results))
        self.assertEqual(controller.progresses, [("import", 5, 10)])
        self.assertTrue(all(thread == APP.thread() for thread in callback_threads))
        self.assertEqual(len(controller.results), 1)

    def test_cancel_cleans_only_created_workspace_and_emits_once(self):
        existing = self.workspace / "keep"
        existing.mkdir(parents=True)
        (existing / "original.txt").write_bytes(b"keep")
        started = threading.Event()

        def importer(source, dest, *, progress, cancelled):
            (dest / "partial.png").write_bytes(b"partial")
            started.set()
            while not cancelled():
                time.sleep(0.005)
            raise InterruptedError("cancelled")

        controller = self.controller(importer=importer)
        self.assertTrue(controller.import_file(self.source, self.workspace))
        self.wait(started.is_set)
        controller.cancel()
        controller.cancel()
        self.assertTrue(controller.busy)
        self.wait(lambda: bool(controller.results))
        self.assertEqual(controller.results, [("cancelled", "import", False)])
        self.assertEqual(list(self.workspace.iterdir()), [existing])
        self.assertEqual((existing / "original.txt").read_bytes(), b"keep")
        QTest.qWait(60)
        self.assertEqual(len(controller.results), 1)

    def test_failed_import_removes_partial_workspace_and_preserves_archive(self):
        source_hash = self.archive({"skin.ini": "[General]"})

        def importer(source, dest, **kwargs):
            (dest / "partial").write_bytes(b"partial")
            raise ValueError("Broken archive")

        controller = self.controller(importer=importer)
        self.assertTrue(controller.import_file(self.source, self.workspace))
        self.wait(lambda: bool(controller.results))
        self.assertEqual(controller.results, [("failed", "import", "Broken archive", False)])
        self.assertEqual(list(self.workspace.iterdir()), [])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), source_hash)

    def test_success_handler_can_start_next_task_without_old_progress(self):
        reports = []

        def importer(source, dest, *, progress, cancelled):
            reports.append(progress)
            (dest / "skin.ini").write_text("[General]", encoding="utf-8")
            return SimpleNamespace(has_skin_ini=True)

        def exporter(source, out, **kwargs):
            out.write_bytes(b"exported")

        controller = self.controller(importer=importer, exporter=exporter)
        second_started = []
        controller.imported.connect(lambda path, *_args:
                                    second_started.append(controller.export_folder(path, self.root / "next.osk")))
        self.assertTrue(controller.import_file(self.source, self.workspace))
        self.wait(lambda: len(controller.results) == 2)
        reports[0](999, 999)
        APP.processEvents()
        self.assertEqual(second_started, [True])
        self.assertEqual(controller.progresses, [])
        self.assertEqual(controller.transitions, [True, False, True, False])

    def test_late_cancel_does_not_claim_completed_export_was_cancelled(self):
        completed = threading.Event()

        def exporter(source, out, **kwargs):
            out.write_bytes(b"final archive")
            completed.set()

        controller = self.controller(exporter=exporter)
        self.assertTrue(controller.export_folder(self.root, self.root / "done.osk"))
        self.assertTrue(completed.wait(2))
        controller.cancel()
        self.wait(lambda: bool(controller.results))
        self.assertEqual(controller.results[0][0], "export")
        self.assertEqual((self.root / "done.osk").read_bytes(), b"final archive")

    def test_default_ini_metadata_read_is_bounded_and_preserves_bytes(self):
        huge_metadata = b"{" + b" " * (128 * 1024) + b"}"

        def importer(source, dest, **kwargs):
            (dest / "skininfo.json").write_bytes(huge_metadata)
            return SimpleNamespace(has_skin_ini=False, display_name="Inspection Name",
                                   creator="Inspection Creator")

        controller = self.controller(importer=importer)
        self.assertTrue(controller.import_file(self.source, self.workspace))
        self.wait(lambda: bool(controller.results))
        result = controller.results[0]
        self.assertEqual(result[0], "import")
        root = Path(result[1])
        self.assertEqual((root / "skininfo.json").read_bytes(), huge_metadata)
        self.assertIn("Name: Inspection Name\nAuthor: Inspection Creator\n",
                      (root / "skin.ini").read_text(encoding="utf-8"))

    def test_two_imports_have_distinct_persistent_workspaces(self):
        self.archive({"skin.ini": "[General]"})
        controller = self.controller()
        for count in (1, 2):
            self.assertTrue(controller.import_file(self.source, self.workspace))
            self.wait(lambda: len(controller.results) == count)
        first, second = (Path(result[1]) for result in controller.results)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir())
        self.assertTrue(second.is_dir())

    def design_source(self):
        source = self.root / "设计源"
        source.mkdir()
        (source / "skin.ini").write_text(
            "[General]\nName: Designer\n[Mania]\nKeys: 4\nHitPosition: 402\n"
            "ColumnWidth: 30,30,30,30\n", encoding="utf-8")
        for name in ("mania-key1", "mania-key1D", "mania-key2", "mania-key2D"):
            Image.new("RGBA", (16, 16), "cyan").save(source / (name + ".png"))
        return source

    def test_design_export_keeps_original_and_second_plain_export_does_not_apply_twice(self):
        source = self.design_source()
        original = {path.name: path.read_bytes() for path in source.iterdir()}
        out = self.root / "designed.osk"
        controller = self.controller()
        self.assertTrue(controller.export_folder(source, out,
                                                design=ManiaDesign(top_mask_height=40,
                                                                   receptor_raise=20),
                                                working_parent=self.workspace))
        self.wait(lambda: bool(controller.results))
        self.assertEqual(controller.results[0][0], "export")
        derived = Path(controller.last_export_root)
        self.assertEqual(derived.parent, self.workspace)
        ini = SkinIni.read(derived / "skin.ini").mania_get(4)
        self.assertEqual(ini["HitPosition"], "382")
        key_path = derived / (ini["KeyImage0"] + ".png")
        with Image.open(key_path) as image:
            self.assertEqual(image.height, 48)
        self.assertIn("StageBottom", ini)
        self.assertEqual({path.name: path.read_bytes() for path in source.iterdir()}, original)
        second = self.root / "second.osk"
        self.assertTrue(controller.export_folder(derived, second))
        self.wait(lambda: len(controller.results) == 2)
        self.assertIsNone(controller.last_export_root)
        with ZipFile(out) as first_archive, ZipFile(second) as second_archive:
            first_files = {name: first_archive.read(name) for name in first_archive.namelist()}
            second_files = {name: second_archive.read(name) for name in second_archive.namelist()}
            self.assertEqual(first_files, second_files)
        self.assertEqual(SkinIni.read(derived / "skin.ini").mania_get(4)["HitPosition"], "382")

    def test_cancel_during_design_cleans_derived_copy_and_keeps_previous_osk(self):
        source = self.design_source()
        original_ini = (source / "skin.ini").read_bytes()
        started = threading.Event()
        release = threading.Event()
        out = self.root / "existing.osk"
        out.write_bytes(b"previous archive")

        def designer(source, keys, destination, design):
            started.set()
            release.wait(3)
            destination.mkdir()
            (destination / "skin.ini").write_bytes(b"new design")
            return SimpleNamespace(root=destination)

        controller = self.controller(design_exporter=designer)
        self.assertTrue(controller.export_folder(source, out, design=ManiaDesign(),
                                                working_parent=self.workspace))
        self.wait(started.is_set)
        self.assertFalse(controller.close())
        self.assertTrue(controller.busy)
        release.set()
        self.wait(lambda: bool(controller.results))
        self.assertEqual(controller.results, [("cancelled", "export", False)])
        self.assertEqual(list(self.workspace.iterdir()), [])
        self.assertEqual((source / "skin.ini").read_bytes(), original_ini)
        self.assertEqual(out.read_bytes(), b"previous archive")
        self.assertIsNone(controller.last_export_root)
        self.assertTrue(controller.close())
        self.assertFalse(controller.import_file(self.source, self.workspace))

    def test_design_detects_source_changes_and_discards_mixed_copy(self):
        source = self.design_source()
        out = self.root / "existing.osk"
        out.write_bytes(b"previous archive")

        def designer(source, keys, destination, design):
            destination.mkdir()
            (destination / "skin.ini").write_bytes(b"new design")
            (source / "cursor.png").write_bytes(b"external change")
            return SimpleNamespace(root=destination)

        controller = self.controller(design_exporter=designer)
        self.assertTrue(controller.export_folder(source, out, design=ManiaDesign(),
                                                working_parent=self.workspace))
        self.wait(lambda: bool(controller.results))
        self.assertEqual(controller.results[0][:2], ("failed", "export"))
        self.assertIn("skin changed", controller.results[0][2])
        self.assertEqual(list(self.workspace.iterdir()), [])
        self.assertEqual(out.read_bytes(), b"previous archive")
        self.assertIsNone(controller.last_export_root)

    def test_file_cannot_be_exported_as_design_skin(self):
        source = self.root / "skin-file.ini"
        source.write_bytes(b"original")
        controller = self.controller()
        self.assertTrue(controller.export_folder(source, self.root / "out.osk",
                                                design=ManiaDesign(), working_parent=self.workspace))
        self.wait(lambda: bool(controller.results))
        self.assertEqual(controller.results[0][:2], ("failed", "export"))
        self.assertEqual(source.read_bytes(), b"original")
        self.assertFalse((self.root / "out.osk").exists())


if __name__ == "__main__":
    unittest.main()
