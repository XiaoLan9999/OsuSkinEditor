import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from core.skin_loader import SkinLoader
from ui.preview.mania_preview import ManiaPreview


APP = QApplication.instance() or QApplication([])


class ManiaRenderingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.preview = ManiaPreview()
        self.preview.resize(856, 552)  # 800 x 480 field: one screen unit per INI unit.
        self.preview.set_playing(False)

    def tearDown(self):
        self.preview.close()
        self.preview.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def png(self, name, size, colour):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        image = QImage(*size, QImage.Format_ARGB32)
        image.fill(QColor(colour))
        image.save(str(target))

    def load(self, settings="", keys=4):
        (self.root / "skin.ini").write_text(f"[Mania]\nKeys: {keys}\n{settings}", encoding="utf-8")
        self.preview.set_skin(SkinLoader().load(str(self.root)))
        return self.preview._geometry()

    def test_capoo_key_padding_is_visible_at_screen_bottom_not_below_hitline(self):
        # Reproduce Capoo's dimensions without including any third-party artwork.
        self.png("key.png", (100, 284), "white")
        field, scale, widths, _, left, hit_y = self.load(
            "ColumnWidth: 72,72,72,72\nHitPosition: 425\nKeyImage0: key\n")
        rect = self.preview._key_rect(0, left, widths[0], field, scale)
        self.assertAlmostEqual(rect.height()/scale, 177.5)
        self.assertAlmostEqual(rect.bottom(), field.bottom())
        self.assertAlmostEqual((rect.top()-field.top())/scale + 195/1.6, 424.375)
        self.assertLess(rect.top()+195/1.6*scale, hit_y)
        before = rect
        field, scale, widths, _, left, _ = self.load(
            "ColumnWidth: 72,72,72,72\nHitPosition: 380\nKeyImage0: key\n")
        self.assertEqual(self.preview._key_rect(0, left, widths[0], field, scale), before)

    def test_key_height_is_independent_of_column_width_and_hd_density(self):
        self.png("keys/skin@2x.png", (200, 568), "white")
        field, scale, widths, _, left, _ = self.load(
            "ColumnWidth: 40,80,40,80\nKeyImage0: keys/skin\nKeyImage1: keys/skin\n")
        first = self.preview._key_rect(0, left, widths[0], field, scale)
        second = self.preview._key_rect(1, left+widths[0], widths[1], field, scale)
        self.assertEqual(first.height(), second.height())
        self.assertAlmostEqual(first.height()/scale, 177.5)
        self.assertAlmostEqual(second.width(), first.width()*2)

    def test_note_bottom_meets_hitposition(self):
        self.png("note.png", (150, 150), "white")
        _, scale, widths, _, left, hit_y = self.load(
            "NoteImage0: note\nColumnWidth: 72,72,72,72\nHitPosition: 425\n")
        rect = self.preview._note_rect(0, left, widths[0], hit_y, scale, widths)
        self.assertEqual(rect.bottom(), hit_y)
        self.assertAlmostEqual(rect.height()/scale, 72)

    def test_stage_hint_is_centered_at_hit_position_and_hd_scaled(self):
        self.png("custom/hint@2x.png", (80, 40), "#00ff00")
        _, scale, widths, spacing, left, hit_y = self.load(
            "StageHint: custom/hint.png\nHitPosition: 410\nJudgementLine: 0\n")
        total = sum(widths)+sum(spacing)
        rect = self.preview._stage_hint_rect(left, total, hit_y, scale)
        self.assertEqual(rect.center().y(), hit_y)
        self.assertEqual(rect.width(), total)
        self.assertAlmostEqual(rect.height()/scale, 20*0.9*1.6025/1.6)
        self.assertFalse(self.preview._judgement_line)
        self.assertIsNotNone(self.preview._stage_hint)

    def test_disabled_judgement_line_does_not_add_a_pink_bar_or_glow(self):
        self.png("key.png", (10, 10), Qt.transparent)
        self.png("note.png", (10, 10), Qt.transparent)
        _, _, widths, _, left, hit_y = self.load(
            "JudgementLine: 0\nKeyImage0: key\nNoteImage0: note\nColumnLineWidth: 0,0\n", keys=1)
        image = self.preview.grab().toImage()
        for dy in (-1, 0, 1, 5):
            self.assertEqual(image.pixelColor(int(left+widths[0]/2), int(hit_y+dy)).name(), "#000000")

    def test_enabled_judgement_line_uses_skin_colour(self):
        _, _, widths, _, left, hit_y = self.load("JudgementLine: 1\nColourJudgementLine: 12,230,45\n", keys=1)
        image = self.preview.grab().toImage()
        colour = image.pixelColor(int(left+widths[0]/2), int(hit_y))
        # The subpixel legacy line is antialiased against the black lane.
        self.assertGreater(colour.green(), colour.red()*5)
        self.assertGreater(colour.green(), colour.blue()*3)

    def test_missing_explicit_stage_hint_does_not_resurrect_default_graphic(self):
        self.png("mania-stage-hint.png", (80, 20), "green")
        self.load("StageHint: intentionally-missing\nJudgementLine: 0\n")
        self.assertIsNone(self.preview._stage_hint)
        self.load("JudgementLine: 0\n")
        self.assertIsNotNone(self.preview._stage_hint)

    def test_stage_hint_and_judgement_line_are_behind_key_images(self):
        self.png("key.png", (80, 200), "red")
        self.png("note.png", (10, 10), Qt.transparent)
        self.png("hint.png", (80, 20), "green")
        for under in (0, 1):
            _, _, widths, _, left, hit_y = self.load(
                f"KeysUnderNotes: {under}\nJudgementLine: 1\nStageHint: hint\n"
                "KeyImage0: key\nNoteImage0: note\nColumnWidth: 60\n", keys=1)
            image = self.preview.grab().toImage()
            self.assertEqual(image.pixelColor(int(left+widths[0]/2), int(hit_y)).name(), "#ff0000")

    def test_keys_under_notes_changes_real_pixel_layer_order(self):
        self.png("key.png", (80, 200), "red")
        self.png("note.png", (80, 80), "blue")
        for under, expected in ((0, "#ff0000"), (1, "#0000ff")):
            _, _, widths, _, left, hit_y = self.load(
                f"KeysUnderNotes: {under}\nJudgementLine: 0\nKeyImage0: key\nNoteImage0: note\nColumnWidth: 60\n",
                keys=1)
            self.preview.t = self.preview.travel_time_ms - 1
            image = self.preview.grab().toImage()
            self.assertEqual(image.pixelColor(int(left+widths[0]/2), int(hit_y-4)).name(), expected)

    def test_lane_colours_use_one_based_indices(self):
        self.load("Colour1: 1,2,3\nColour2: 4,5,6\nColourColumnLine: 7,8,9\n")
        self.assertEqual(self.preview._lane_colours[0].name(), "#010203")
        self.assertEqual(self.preview._lane_colours[1].name(), "#040506")
        self.assertEqual(self.preview._line_colour.name(), "#070809")

    def test_default_judgement_line_is_enabled(self):
        self.load()
        self.assertTrue(self.preview._judgement_line)

    def test_narrow_preview_keeps_four_columns_inside_field(self):
        self.preview.resize(380, 640)
        field, _, widths, spacing, left, _ = self.load("ColumnStart: 240\nColumnWidth: 72,72,72,72\n")
        self.assertLessEqual(left+sum(widths)+sum(spacing), field.right())


if __name__ == "__main__":
    unittest.main()
