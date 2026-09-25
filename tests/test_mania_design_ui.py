import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from core.mania_designer import ManiaDesign
from core.skin_loader import SkinLoader
from ui.mania_design_dock import ManiaDesignDock, _ExportCopyDialog
from ui.widgets.wheel_guard import ClickWheelSpinBox


APP = QApplication.instance() or QApplication([])


class ManiaDesignUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.parent = Path(self.temp.name)
        self.source = self.parent / "Original skin"
        self.source.mkdir()
        self.original_ini = b"[General]\nName: Original\n[Mania]\nKeys: 4\nHitPosition: 402\n"
        (self.source / "skin.ini").write_bytes(self.original_ini)
        self.skin = SkinLoader().load(str(self.source))
        self.dock = ManiaDesignDock()
        self.widgets = [self.dock]

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def load(self):
        self.assertTrue(self.dock.set_skin(self.skin, 4))

    def test_no_skin_disables_design_and_export(self):
        self.assertFalse(self.dock.controls.isEnabled())
        self.assertFalse(self.dock.export_button.isEnabled())
        with patch.object(self.dock, "_choose_export_destination") as choose:
            self.assertFalse(self.dock._export_copy())
        choose.assert_not_called()

    def test_edits_emit_full_design_and_never_write_source(self):
        self.load()
        changes = []
        self.dock.design_changed.connect(changes.append)
        self.dock.top_height.setValue(80)
        self.dock.top_fade.setValue(25)
        self.dock.receptor_raise.setValue(20)
        self.dock.link_hit_position.setChecked(False)
        self.assertEqual(changes[-1], ManiaDesign(80, 25, 20, False, "#000000"))
        self.assertTrue(self.dock._dirty)
        self.assertEqual((self.source / "skin.ini").read_bytes(), self.original_ini)
        self.assertEqual([p.name for p in self.source.iterdir()], ["skin.ini"])
        for spin in (self.dock.top_height, self.dock.top_fade, self.dock.receptor_raise):
            self.assertIsInstance(spin, ClickWheelSpinBox)
            self.assertFalse(spin._wheel_edit_armed)

    def test_reset_restores_original_preview_and_clears_virtual_changes(self):
        self.load()
        self.dock.top_height.setValue(70)
        changes = []
        self.dock.design_changed.connect(changes.append)
        self.dock.reset_preview()
        self.assertEqual(changes, [ManiaDesign()])
        self.assertFalse(self.dock._dirty)
        self.assertEqual((self.source / "skin.ini").read_bytes(), self.original_ini)

    def test_refresh_same_skin_keeps_live_design(self):
        self.load()
        self.dock.top_height.setValue(33)
        refreshed = SkinLoader().load(str(self.source))
        with patch.object(QMessageBox, "question") as question:
            self.assertTrue(self.dock.set_skin(refreshed, 4))
        question.assert_not_called()
        self.assertEqual(self.dock.options().top_mask_height, 33)
        self.assertTrue(self.dock._dirty)

    def test_cancel_key_change_preserves_design_and_keys(self):
        self.load()
        self.dock.top_height.setValue(35)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Cancel):
            self.assertFalse(self.dock.set_keys(7))
        self.assertEqual(self.dock._keys, 4)
        self.assertEqual(self.dock.options().top_mask_height, 35)
        self.assertTrue(self.dock._dirty)

    def test_discard_allows_key_change_and_emits_default_once(self):
        self.load()
        self.dock.top_height.setValue(35)
        changes = []
        self.dock.design_changed.connect(changes.append)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Discard):
            self.assertTrue(self.dock.set_keys(7))
        self.assertEqual(self.dock._keys, 7)
        self.assertEqual(changes, [ManiaDesign()])
        self.assertFalse(self.dock._dirty)

    def test_cancel_export_from_unsaved_guard_cancels_transition(self):
        self.load()
        self.dock.top_height.setValue(35)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Save), \
                patch.object(self.dock, "_choose_export_destination", return_value=None):
            self.assertFalse(self.dock.confirm_discard_if_dirty())
        self.assertTrue(self.dock._dirty)

    def test_export_passes_current_k_and_options_emits_copy_after_success(self):
        self.load()
        self.dock.top_height.setValue(40)
        self.dock.receptor_raise.setValue(12)
        target = self.parent / "Saved copy"
        changes = []
        self.dock.exported.connect(changes.append)
        self.dock.before_export = Mock(return_value=True)
        with patch.object(self.dock, "_choose_export_destination", return_value=target), \
                patch("ui.mania_design_dock.export_skin", return_value=SimpleNamespace(root=target)) as export:
            self.assertTrue(self.dock._export_copy())
        export.assert_called_once_with(self.source.resolve(), 4, target, ManiaDesign(40, 0, 12, True, "#000000"))
        self.dock.before_export.assert_called_once_with()
        self.assertEqual(changes, [str(target)])
        self.assertFalse(self.dock._dirty)
        self.assertTrue(self.dock.export_button.isEnabled())
        self.assertEqual((self.source / "skin.ini").read_bytes(), self.original_ini)

    def test_pending_ini_guard_can_cancel_export_without_engine_call(self):
        self.load()
        self.dock.top_height.setValue(25)
        self.dock.before_export = Mock(return_value=False)
        with patch.object(self.dock, "_choose_export_destination", return_value=self.parent / "Copy"), \
                patch("ui.mania_design_dock.export_skin") as export:
            self.assertFalse(self.dock._export_copy())
        export.assert_not_called()
        self.assertTrue(self.dock._dirty)
        self.assertTrue(self.dock.export_button.isEnabled())

    def test_real_mask_export_from_dock_creates_loadable_copy_and_keeps_original(self):
        self.load()
        self.dock.top_height.setValue(45)
        self.dock.top_fade.setValue(20)
        target = self.parent / "Real copy"
        emitted = []
        self.dock.exported.connect(emitted.append)
        with patch.object(self.dock, "_choose_export_destination", return_value=target):
            self.assertTrue(self.dock._export_copy())
        copy = SkinLoader().load(str(target))
        self.assertEqual(copy.mode_keys, 4)
        self.assertIn("StageBottom", copy.mania_variants[4])
        stage = target / copy.mania_variants[4]["StageBottom"]
        self.assertTrue(stage.with_suffix(".png").exists())
        self.assertEqual((self.source / "skin.ini").read_bytes(), self.original_ini)
        self.assertEqual([p.name for p in self.source.iterdir()], ["skin.ini"])
        self.assertEqual(emitted, [str(target)])
        self.assertFalse(self.dock._dirty)

    def test_failed_export_keeps_design_and_enables_retry(self):
        self.load()
        self.dock.receptor_raise.setValue(180)
        emitted = []
        self.dock.exported.connect(emitted.append)
        with patch.object(self.dock, "_choose_export_destination", return_value=self.parent / "Copy"), \
                patch("ui.mania_design_dock.export_skin", side_effect=ValueError("Unsupported settings")), \
                patch.object(QMessageBox, "warning") as warning:
            self.assertFalse(self.dock._export_copy())
        warning.assert_called_once()
        self.assertEqual(emitted, [])
        self.assertEqual(self.dock.options().receptor_raise, 180)
        self.assertTrue(self.dock._dirty)
        self.assertTrue(self.dock.export_button.isEnabled())

    def test_load_exported_copy_force_resets_virtual_transform(self):
        self.load()
        self.dock.receptor_raise.setValue(25)
        copy = self.parent / "Exported skin"
        copy.mkdir()
        (copy / "skin.ini").write_bytes(self.original_ini)
        with patch.object(QMessageBox, "question") as question:
            self.assertTrue(self.dock.set_skin(SkinLoader().load(str(copy)), 4, force=True))
        question.assert_not_called()
        self.assertEqual(self.dock.options(), ManiaDesign())
        self.assertFalse(self.dock._dirty)

    def test_copy_dialog_suggests_unique_name_and_rejects_existing_or_unsafe_names(self):
        (self.parent / "Original skin - Designer 4K").mkdir()
        dialog = _ExportCopyDialog(self.source, 4)
        self.widgets.append(dialog)
        self.assertEqual(dialog.name_edit.text(), "Original skin - Designer 4K (2)")
        self.assertTrue(dialog.save_button.isEnabled())
        for name in ("Original skin", "../bad", "bad/name", "CON", "CON.txt", "bad.", "", "bad:name"):
            with self.subTest(name=name):
                dialog.name_edit.setText(name)
                self.assertIsNone(dialog.destination)
                self.assertFalse(dialog.save_button.isEnabled())
        dialog.name_edit.setText("My custom 4K")
        self.assertEqual(dialog.destination, self.parent / "My custom 4K")
        self.assertTrue(dialog.save_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
