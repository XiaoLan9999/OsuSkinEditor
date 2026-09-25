import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from configparser import ConfigParser
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from core.skin_loader import SkinLoader
from core.skin_ini import SkinIni
from ui.preview.std_preview import StdPreview, _alpha_center
from ui.preview.mania_preview import ManiaPreview
from ui.mania_ini_dock import ManiaIniDock


APP = QApplication.instance() or QApplication([])


class PreviewFixture(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        # processEvents() alone does not deliver DeferredDelete without exec().
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.widgets.clear()
        self.temp.cleanup()

    def widget(self, cls):
        widget = cls()
        self.widgets.append(widget)
        return widget

    def skin(self, text="[General]\nName: Fixture\n"):
        (self.root / "skin.ini").write_text(text, encoding="utf-8")
        return SkinLoader().load(str(self.root))

    def png(self, filename, width=16, height=16, color=QColor("white")):
        target = self.root / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        image = QImage(width, height, QImage.Format_ARGB32)
        image.fill(color)
        self.assertTrue(image.save(str(target)))


class PreviewTests(PreviewFixture):
    def test_std_tint_preserves_shading_and_partial_alpha(self):
        preview = self.widget(StdPreview)
        image = QImage(2, 1, QImage.Format_ARGB32)
        image.setPixelColor(0, 0, QColor(128, 128, 128, 128))
        image.setPixelColor(1, 0, QColor(255, 255, 255, 255))
        tinted = preview._tint(QPixmap.fromImage(image), (0, 200, 100)).toImage()
        dark, light = tinted.pixelColor(0, 0), tinted.pixelColor(1, 0)
        self.assertEqual(dark.alpha(), 128)
        self.assertAlmostEqual(dark.green(), 100, delta=2)
        self.assertAlmostEqual(dark.blue(), 50, delta=2)
        self.assertEqual((light.red(), light.green(), light.blue()), (0, 200, 100))

    def test_std_tint_handles_qt_high_dpi_without_solid_rectangles(self):
        preview = self.widget(StdPreview)
        image = QImage(8, 8, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        image.setPixelColor(5, 5, QColor(255, 255, 255, 255))
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(2)
        tinted = preview._tint(pixmap, (255, 100, 180)).toImage()
        self.assertEqual(tinted.pixelColor(7, 7).alpha(), 0)
        self.assertEqual(tinted.pixelColor(0, 0).alpha(), 0)
        self.assertEqual(tinted.pixelColor(5, 5), QColor(255, 100, 180))

    def test_std_preserves_authored_padding_and_custom_font_hd(self):
        self.png("hitcircle.png", 12, 12, QColor(Qt.transparent))
        image = QImage(str(self.root / "hitcircle.png"))
        image.setPixelColor(1, 1, QColor("white"))
        image.save(str(self.root / "hitcircle.png"))
        self.png("numbers/custom-6@2x.png", 24, 32)
        preview = self.widget(StdPreview)
        preview.set_skin(self.skin("[Fonts]\nHitCirclePrefix: numbers/custom\n"))
        self.assertEqual(preview.off_circle, (0, 0))
        self.assertEqual((preview.pm_digits[6].width(), preview.pm_digits[6].height()), (12, 16))
        self.assertEqual(preview.pm_digits[6].devicePixelRatio(), 1)

    def test_std_new_skin_does_not_inherit_previous_combo_color(self):
        preview = self.widget(StdPreview)
        preview.set_skin(self.skin("[Colours]\nCombo1: 255,0,0\n"))
        self.assertEqual(preview.combo_color, (255, 0, 0))
        preview.set_skin(self.skin())
        self.assertEqual(preview.combo_color, (0, 255, 255))

    def test_std_approach_keeps_native_size_with_blank_one_pixel_hitcircle(self):
        self.png("hitcircle.png", 1, 1, QColor(Qt.transparent))
        self.png("approachcircle.png", 126, 126)
        preview = self.widget(StdPreview)
        preview.set_skin(self.skin())
        preview.resize(600, 520)
        preview.set_playing(False)
        images = []
        original_draw = preview._draw_centered

        def record_image(painter, cx, cy, pixmap, *args, **kwargs):
            # Retaining QPainter in Mock.call_args outlives its paint device.
            images.append(pixmap)
            return original_draw(painter, cx, cy, pixmap, *args, **kwargs)

        with patch.object(preview, "_draw_centered", new=record_image):
            preview.grab()
        approach = images[0]
        self.assertEqual(approach.width(), 378)

    def test_std_overlay_and_number_follow_ini_order(self):
        for asset in ("hitcircle.png", "hitcircleoverlay.png", "default-6.png"):
            self.png(asset)
        preview = self.widget(StdPreview)
        preview.resize(600, 520)
        images = []
        original_draw = preview._draw_centered

        def record_image(painter, cx, cy, pixmap, *args, **kwargs):
            images.append(pixmap)
            return original_draw(painter, cx, cy, pixmap, *args, **kwargs)

        for rule in (0, 1):
            preview.set_skin(self.skin(f"[General]\nHitCircleOverlayAboveNumber: {rule}\n"))
            images.clear()
            with patch.object(preview, "_draw_centered", new=record_image):
                preview.grab()
            last = images[-1]
            self.assertIs(last, preview.pm_overlay if rule else preview.pm_digits[6])

    def test_std_cursor_configuration_and_zoom(self):
        self.png("cursor@2x.png", 48, 48)
        preview = self.widget(StdPreview)
        preview.set_skin(self.skin("[General]\nCursorCentre: 0\nCursorRotate: 0\n"))
        self.assertFalse(preview.cursor_center)
        self.assertFalse(preview.cursor_rotate)
        self.assertEqual(preview.pm_cursor.width(), 24)
        preview.set_preview_scale(2)
        self.assertEqual(preview.preview_scale, 2)
        self.assertFalse(preview.grab().isNull())

    def test_replaced_images_reload_even_when_metadata_matches(self):
        self.png("hitcircle.png", 32, 32, QColor("red"))
        self.png("mania-note1.png", 32, 32, QColor("red"))
        skin = self.skin("[Mania]\nKeys: 4\n")
        std = self.widget(StdPreview)
        mania = self.widget(ManiaPreview)
        for preview in (std, mania):
            preview.set_skin(skin)
        for name in ("hitcircle.png", "mania-note1.png"):
            path = self.root / name
            info = path.stat()
            self.png(name, 32, 32, QColor("blue"))
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        for preview in (std, mania):
            preview.set_skin(skin)
        self.assertEqual(std.pm_circle.toImage().pixelColor(0, 0).name(), "#0000ff")
        self.assertEqual(mania._note_images[0].toImage().pixelColor(0, 0).name(), "#0000ff")

    def test_alpha_center_even_and_odd_images_are_zero_when_symmetric(self):
        for size in (2, 3, 4, 5):
            image = QImage(size, size, QImage.Format_ARGB32)
            image.fill(Qt.white)
            self.assertEqual(_alpha_center(QPixmap.fromImage(image)), (0, 0))

    def test_previews_pause_when_hidden_and_retain_user_pause(self):
        for cls in (StdPreview, ManiaPreview):
            preview = self.widget(cls)
            self.assertFalse(preview.timer.isActive())
            preview.show()
            APP.processEvents()
            self.assertTrue(preview.timer.isActive())
            preview.hide()
            self.assertFalse(preview.timer.isActive())
            preview.set_playing(False)
            preview.show()
            APP.processEvents()
            self.assertFalse(preview.timer.isActive())

    def test_mania_reloads_saved_settings_and_preserves_zero(self):
        preview = self.widget(ManiaPreview)
        preview.resize(800, 520)
        preview.set_skin(self.skin("[Mania]\nKeys: 4\nHitPosition: 0\nColumnStart: 0\nColumnWidth: 60,60,60,60\n"))
        self.assertEqual(preview.layout["ColumnWidth"], [60]*4)
        field, _, _, _, left, hit_y = preview._geometry()
        self.assertEqual(left, field.left())
        self.assertEqual(hit_y, field.top())
        (self.root / "skin.ini").write_text("[Mania]\nKeys: 4\nColumnWidth: 22\n", encoding="utf-8")
        preview.set_keys(4)
        self.assertEqual(preview.layout["ColumnWidth"], [22, 30, 30, 30])
        self.assertIsNone(preview.layout["HitPosition"])

    def test_mania_loads_custom_note_images_and_renders(self):
        self.png("notes/blue@2x.png", 80, 24, QColor("#70d7e0"))
        self.png("mania-key1.png", 32, 32)
        preview = self.widget(ManiaPreview)
        preview.set_skin(self.skin("[Mania]\nKeys: 4\nNoteImage0: notes/blue\n"))
        self.assertIsNotNone(preview._note_images[0])
        self.assertEqual(preview._note_images[0].width(), 80)
        preview.resize(800, 520)
        self.assertFalse(preview.grab().isNull())


class ManiaEditorTests(PreviewFixture):
    def setUp(self):
        super().setUp()
        self.messages = [patch.object(QMessageBox, method, return_value=QMessageBox.Ok)
                         for method in ("information", "warning", "critical")]
        for message in self.messages:
            message.start()

    def tearDown(self):
        for message in self.messages:
            message.stop()
        super().tearDown()

    def editor(self, text):
        self.skin(text)
        dock = self.widget(ManiaIniDock)
        dock.set_skin_root(self.root)
        return dock

    def test_valid_zero_fields_and_unsupported_values_preserved(self):
        dock = self.editor("[Mania]\nKeys: 4\nHitPosition: 0\nBarlineHeight: 0\nLightingNWidth: 10,20,30,40\n")
        self.assertEqual(dock.spn_hit_pos.value(), 0)
        self.assertEqual(dock.spn_barline_h.value(), 0)
        dock.spn_col_start.setValue(100)
        self.assertTrue(dock._on_save_clicked())
        saved = SkinIni.read(self.root / "skin.ini").mania_get(4)
        self.assertEqual(saved["LightingNWidth"], "10,20,30,40")
        self.assertEqual(saved["ColumnStart"], "100")
        self.assertNotIn("ColourHold", saved)

    def test_invalid_list_does_not_silently_drop_tokens_or_touch_disk(self):
        dock = self.editor("[Mania]\nKeys: 4\nColumnWidth: 30,30,30,30\n")
        before = (self.root / "skin.ini").read_bytes()
        dock.le_col_width.setText("40,not-a-number,40,40")
        self.assertFalse(dock._on_save_clicked())
        self.assertTrue(dock._dirty)
        self.assertEqual((self.root / "skin.ini").read_bytes(), before)

    def test_save_failure_blocks_key_switch_and_preserves_dirty_form(self):
        dock = self.editor("[Mania]\nKeys: 4\nColumnWidth: 30,30,30,30\n[Mania]\nKeys: 7\n")
        dock.le_col_width.setText("45,45,45,45")
        with patch.object(SkinIni, "save", side_effect=OSError("disk full")), \
                patch.object(dock, "_confirm_discard_if_dirty", side_effect=dock._on_save_clicked):
            dock.cmb_keys.setCurrentIndex(dock.cmb_keys.findData(7))
        self.assertEqual(dock._current_view_k, 4)
        self.assertEqual(dock.cmb_keys.currentData(), 4)
        self.assertEqual(dock.le_col_width.text(), "45,45,45,45")
        self.assertTrue(dock._dirty)

    def test_save_during_switch_reaches_requested_keys(self):
        dock = self.editor("[Mania]\nKeys: 4\n[Mania]\nKeys: 7\n")
        dock.spn_hit_pos.setValue(375)
        with patch.object(dock, "_confirm_discard_if_dirty", side_effect=dock._on_save_clicked):
            dock.cmb_keys.setCurrentIndex(dock.cmb_keys.findData(7))
        self.assertEqual(dock._current_view_k, 7)
        self.assertEqual(dock.cmb_keys.currentData(), 7)
        self.assertEqual(SkinIni.read(self.root / "skin.ini").mania_get(4)["HitPosition"], "375")

    def test_snapshots_are_unique_and_preserve_source_bytes(self):
        dock = self.editor("[Mania]\nKeys: 4\n")
        data = "[General]\r\nName: 中文\r\n[Mania]\r\nKeys: 4\r\n".encode("utf-16")
        (self.root / "skin.ini").write_bytes(data)
        first, second = dock._archive_snapshot(), dock._archive_snapshot()
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), data)
        self.assertEqual(second.read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
