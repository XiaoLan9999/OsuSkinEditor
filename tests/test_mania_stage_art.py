import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PySide6.QtCore import QCoreApplication, QEvent, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from core.skin_loader import SkinLoader
from ui.preview.mania_preview import ManiaPreview


APP = QApplication.instance() or QApplication([])


class ManiaStageArtTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.preview = ManiaPreview()
        self.preview.resize(856, 552)
        self.preview.set_playing(False)

    def tearDown(self):
        self.preview.close()
        self.preview.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def png(self, name, size, colour="transparent", ink=None):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        image = QImage(*size, QImage.Format_ARGB32)
        image.fill(QColor(colour) if ink is None else Qt.transparent)
        if ink is not None:
            painter = QPainter(image)
            painter.fillRect(QRect(*ink), QColor(colour))
            painter.end()
        self.assertTrue(image.save(str(target)))

    def load(self, settings=""):
        (self.root / "skin.ini").write_text(
            "[General]\nVersion: 2.7\n[Mania]\nKeys: 4\nColumnStart: 240\n"
            "ColumnWidth: 72,72,72,72\nColumnLineWidth: 0,0,0,0,0\nJudgementLine: 0\n"
            + settings, encoding="utf-8")
        self.preview.set_skin(SkinLoader().load(str(self.root)))
        self.preview.set_playing(False)
        return self.preview._geometry()

    def sample(self, x, y):
        return self.preview.grab().toImage().pixelColor(round(x), round(y)).name()

    def test_custom_side_paths_are_case_insensitive_and_hd_density_is_respected(self):
        self.png("Side/Left@2x.PNG", (128, 192), "red")
        self.png("Side/Right@2x.PNG", (192, 128), "blue")
        field, scale, widths, spacing, left, _ = self.load("StageLeft: side\\LEFT\nStageRight: SIDE/right\n")
        total = sum(widths)+sum(spacing)
        lrect = self.preview._stage_side_rect(self.preview._assets.stage_left, "left", field, scale, left, total)
        rrect = self.preview._stage_side_rect(self.preview._assets.stage_right, "right", field, scale, left, total)
        self.assertAlmostEqual(lrect.width()/scale, 64/1.6)
        self.assertAlmostEqual(rrect.width()/scale, 96/1.6)
        self.assertEqual(lrect.right(), left)
        self.assertEqual(rrect.left(), left+total)
        self.assertEqual(lrect.height(), field.height())
        self.assertEqual(rrect.height(), field.height())
        self.assertEqual(self.sample(lrect.center().x(), lrect.center().y()), "#ff0000")
        self.assertEqual(self.sample(rrect.center().x(), rrect.center().y()), "#0000ff")

    def test_capoo_sized_transparent_canvas_retains_avatar_position_and_size(self):
        # Only synthetic geometry from the user's side asset; no artwork.
        self.png("mania-stage-right.png", (1000, 1000), "#ff00ff", (52, 320, 180, 217))
        self.preview.set_viewport_aspect(1.6)
        field, scale, widths, _, left, _ = self.load()
        x = left+sum(widths)+(52+90)/1.6*scale
        y = field.top()+(320+108)*480/1000*scale
        self.assertEqual(self.sample(x, y), "#ff00ff")
        self.assertAlmostEqual(scale, 1)
        self.assertAlmostEqual(field.width(), 768)
        self.assertAlmostEqual((left-field.left())/scale, 240)

    def test_fixed_viewport_preserves_columnstart_and_centres_virtual_screen(self):
        self.preview.set_viewport_aspect(16/9)
        field, scale, _, _, left, _ = self.load()
        self.assertAlmostEqual(field.width()/field.height(), 16/9)
        self.assertAlmostEqual(field.center().x(), self.preview.width()/2)
        self.assertAlmostEqual((left-field.left())/scale, 240)
        self.preview.set_viewport_aspect(1.6)
        field, scale, _, _, left, _ = self.preview._geometry()
        self.assertAlmostEqual(field.width()/field.height(), 1.6)
        self.assertAlmostEqual((left-field.left())/scale, 240)

    def test_opaque_wide_art_scales_entire_scene_and_keeps_all_edges(self):
        self.png("mania-stage-left.png", (800, 768), "red")
        self.png("mania-stage-right.png", (800, 768), "blue")
        self.preview.set_viewport_aspect(1.6)
        field, scale, widths, spacing, left, _ = self.load()
        self.assertLess(scale, 1)
        self.assertAlmostEqual(widths[0], 72*scale)
        self.assertAlmostEqual((left-field.left())/scale, 240)
        lrect = self.preview._stage_side_rect(self.preview._assets.stage_left, "left", field, scale, left, sum(widths))
        rrect = self.preview._stage_side_rect(self.preview._assets.stage_right, "right", field, scale, left, sum(widths))
        self.assertAlmostEqual(lrect.width(), 500*scale)
        self.assertAlmostEqual(rrect.width(), 500*scale)
        self.assertGreaterEqual(lrect.left(), 28-1e-8)
        self.assertLessEqual(rrect.right(), self.preview.width()-28+1e-8)
        self.assertEqual(self.sample(lrect.left()+2, field.center().y()), "#ff0000")
        self.assertEqual(self.sample(rrect.right()-2, field.center().y()), "#0000ff")

    def test_narrow_window_uses_same_scale_for_lanes_and_side_art(self):
        self.png("mania-stage-right.png", (640, 768), "blue")
        self.preview.resize(400, 400)
        self.preview.set_viewport_aspect(1.6)
        field, scale, widths, _, left, _ = self.load()
        rect = self.preview._stage_side_rect(self.preview._assets.stage_right, "right", field, scale, left, sum(widths))
        self.assertAlmostEqual(rect.width()/widths[0], 400/72)
        self.assertLessEqual(rect.right(), self.preview.width()-28+1e-8)
        self.assertEqual(self.sample(rect.right()-2, field.center().y()), "#0000ff")

    def test_transparent_side_images_do_not_force_extra_zoom_or_generic_art(self):
        self.preview.set_viewport_aspect(1.6)
        before = self.load()[1]
        self.png("mania-stage-right.png", (2000, 800))
        after = self.load()[1]
        self.assertEqual(before, after)
        self.assertIsNotNone(self.preview._assets.stage_right)
        self.assertEqual(self.preview._assets.stage_right_bounds, (None,))

    def test_custom_missing_side_image_does_not_use_default_image(self):
        self.png("mania-stage-right.png", (100, 768), "red")
        self.load("StageRight: missing\n")
        self.assertIsNone(self.preview._assets.stage_right)
        self.assertEqual(self.preview._assets.missing["stageright"], "missing")

    def test_all_animation_frame_bounds_are_fitted_without_zoom_jumps(self):
        self.png("side-0.png", (64, 64), "red")
        self.png("side-1@2x.png", (1600, 1536), "blue")
        self.preview.set_viewport_aspect(1.6)
        field, scale, widths, _, left, _ = self.load("StageRight: side\n")
        self.preview.t = 0
        self.assertEqual(self.sample(left+sum(widths)+10*scale, field.center().y()), "#ff0000")
        self.preview.t = 17
        self.assertEqual(self.preview._geometry()[1], scale)
        self.assertEqual(self.sample(left+sum(widths)+400*scale, field.center().y()), "#0000ff")

    def test_stage_sides_keep_upright_orientation_when_notes_scroll_up(self):
        self.png("mania-stage-right.png", (80, 80), "red", (0, 0, 80, 40))
        field, scale, widths, _, left, _ = self.load("UpsideDown: 1\n")
        x = left+sum(widths)+20*scale
        self.assertEqual(self.sample(x, field.top()+100*scale), "#ff0000")
        self.assertNotEqual(self.sample(x, field.top()+400*scale), "#ff0000")

    def test_stage_bottom_can_cover_side_art_and_preserves_foreground_layer(self):
        self.png("mania-stage-left.png", (80, 768), "red")
        self.png("mania-stage-bottom.png", (500, 480), "blue")
        field, scale, widths, _, left, _ = self.load()
        self.assertEqual(self.sample(left-10*scale, field.bottom()-10*scale), "#0000ff")

    def test_stage_bottom_animation_uses_current_frame_and_includes_full_bounds(self):
        self.png("mania-stage-bottom-0.png", (1000, 100), "red")
        self.png("mania-stage-bottom-1.png", (1000, 100), "blue")
        field, scale, widths, _, left, _ = self.load()
        rect = self.preview._stage_bottom_rect(left, sum(widths), field, scale)
        self.assertGreaterEqual(rect.left(), 28-1e-8)
        self.assertLessEqual(rect.right(), self.preview.width()-28+1e-8)
        self.preview.t = 0
        self.assertEqual(self.sample(rect.left()+2, rect.center().y()), "#ff0000")
        self.preview.t = 17
        self.assertEqual(self.sample(rect.right()-2, rect.center().y()), "#0000ff")

    def test_comboburst_uses_scene_scale_instead_of_shrinking_to_sidebar(self):
        self.png("comboburst-mania.png", (800, 768), "blue")
        self.preview.set_viewport_aspect(1.6)
        field, scale, widths, _, left, _ = self.load()
        self.preview.show_comboburst()
        self.assertLess(scale, 1)
        self.assertEqual(self.sample(left+sum(widths)+450*scale, field.center().y()), "#0000ff")

    def test_viewport_rejects_nonpositive_or_nonfinite_ratios(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.preview.set_viewport_aspect(value)
        self.preview.set_viewport_aspect(None)
        self.assertIsNone(self.preview.viewport_aspect)


if __name__ == "__main__":
    unittest.main()
