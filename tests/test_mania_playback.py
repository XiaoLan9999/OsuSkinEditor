import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from core.mania_designer import ManiaDesign, build_preview_overlay, export_skin
from core.mania_timeline import beat_interval_ms, sample_note_ages, travel_time_ms
from core.skin_loader import SkinLoader
from ui.preview.mania_preview import ManiaPreview


APP = QApplication.instance() or QApplication([])


class ManiaTimelineTests(unittest.TestCase):
    def test_speed_changes_distance_and_visible_count_without_changing_rhythm(self):
        slow, fast = travel_time_ms(10), travel_time_ms(20)
        self.assertAlmostEqual(slow, fast*2)
        slow_ages = sample_note_ages(100, 0, 4, 120, slow)
        fast_ages = sample_note_ages(100, 0, 4, 120, fast)
        self.assertEqual(slow_ages, (100, 600, 1100))
        self.assertEqual(fast_ages, (100,))
        self.assertAlmostEqual(fast_ages[0]/fast, slow_ages[0]/slow*2)
        self.assertEqual(beat_interval_ms(120), 500)
        for speed in (1, 10, 20, 40):
            self.assertEqual(sample_note_ages(100, 0, 4, 120, travel_time_ms(speed))[0], 100)

    def test_rhythm_repeats_each_beat_with_even_lane_stagger(self):
        for lane in range(4):
            offset = lane*125
            self.assertEqual(sample_note_ages(offset, lane, 4, 120, 1200)[0], 0)
            self.assertEqual(sample_note_ages(offset+500, lane, 4, 120, 1200)[0], 0)
        self.assertEqual(beat_interval_ms(240), 250)

    def test_linked_hit_position_changes_travel_length_but_not_velocity(self):
        for speed in (1, 20, 40):
            self.assertAlmostEqual(402/travel_time_ms(speed, 402), 350/travel_time_ms(speed, 350))
        self.assertEqual(travel_time_ms(0), travel_time_ms(1))
        self.assertEqual(travel_time_ms(100), travel_time_ms(40))


class ManiaPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "source"
        self.root.mkdir()
        self.widgets = []
        self.write_ini()
        self.preview = self.new_preview()
        self.preview.set_skin(SkinLoader().load(str(self.root)))

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.widgets.clear()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def new_preview(self):
        preview = ManiaPreview()
        preview.resize(856, 552)
        preview.set_playing(False)
        self.widgets.append(preview)
        return preview

    def write_ini(self, extra=""):
        (self.root / "skin.ini").write_text(
            "[Mania]\nKeys: 4\nColumnWidth: 60,60,60,60\nHitPosition: 402\n"
            "JudgementLine: 0\n" + extra, encoding="utf-8")

    def png(self, name, size, colour):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", size, colour).save(path)

    def hashes(self):
        return {path.relative_to(self.root): sha256(path.read_bytes()).hexdigest()
                for path in self.root.rglob("*") if path.is_file()}

    def test_elapsed_clock_catches_delayed_frames_and_does_not_wrap(self):
        self.preview.show()
        APP.processEvents()
        self.preview.set_playing(True)
        self.preview.restart_demo()
        self.preview.t = 5000
        time.sleep(0.035)  # Deliberately delayed event-loop delivery, not 16 ms.
        self.preview.tick()
        self.assertGreaterEqual(self.preview.t, 5025)
        self.assertLess(self.preview.t, 7000)

    def test_pause_and_hide_discard_elapsed_wall_time(self):
        self.preview.show()
        APP.processEvents()
        self.preview.set_playing(True)
        self.preview.t = 9000
        self.preview.set_playing(False)
        time.sleep(0.035)
        self.preview.tick()
        self.assertEqual(self.preview.t, 9000)
        self.assertFalse(self.preview._clock.isValid())
        self.preview.set_playing(True)
        self.preview.tick()
        self.assertLess(self.preview.t, 9020)
        self.preview.hide()
        frozen = self.preview.t
        time.sleep(0.035)
        self.preview.tick()
        self.assertEqual(self.preview.t, frozen)
        self.preview.show()
        self.preview.tick()
        self.assertLess(self.preview.t-frozen, 20)

    def test_controls_and_design_are_preview_only_and_restart_keeps_settings(self):
        before = self.hashes()
        self.preview.set_scroll_speed(28)
        self.preview.set_demo_bpm(170)
        self.preview.set_show_hit_guide(True)
        self.preview.set_design_options(ManiaDesign(top_mask_height=60, top_mask_fade=20, receptor_raise=20))
        self.preview.t = 42345
        self.preview.restart_demo()
        self.assertEqual(self.preview.t, 0)
        self.assertEqual((self.preview.scroll_speed, self.preview.demo_bpm), (28, 170))
        self.assertEqual(self.preview.effective_hit_position, 382)
        self.assertFalse(self.preview.grab().isNull())
        self.assertEqual(before, self.hashes())
        self.preview.set_keys(7)
        self.assertEqual(self.preview.design_options, ManiaDesign())

    def test_malformed_optional_hit_position_keeps_baseline_preview_usable(self):
        self.write_ini("HitPosition: invalid\n")
        self.preview.set_skin(SkinLoader().load(str(self.root)))
        self.assertEqual(self.preview.effective_hit_position, 402)
        self.assertFalse(self.preview.grab().isNull())

    def test_receptor_lift_preserves_native_dimensions_and_optional_hit_link(self):
        self.png("mania-key1@2x.png", (160, 320), "red")
        self.preview.set_skin(SkinLoader().load(str(self.root)))
        field, scale, widths, _, left, old_hit = self.preview._geometry()
        original = self.preview._key_rect(0, left, widths[0], field, scale)
        self.preview.set_design_options(ManiaDesign(receptor_raise=25, link_hit_position=False))
        raised = self.preview._key_rect(0, left, widths[0], field, scale)
        self.assertEqual(raised.size(), original.size())
        self.assertAlmostEqual(original.top()-raised.top(), 25*scale)
        self.assertEqual(self.preview._geometry()[-1], old_hit)
        self.preview.set_design_options(ManiaDesign(receptor_raise=25, link_hit_position=True))
        self.assertAlmostEqual(old_hit-self.preview._geometry()[-1], 25*scale)

    def test_bottom_art_is_native_size_bottom_centred_and_over_notes(self):
        self.png("custom-bottom@2x.png", (400, 160), "#b31be2")
        self.write_ini("StageBottom: custom-bottom\n")
        self.preview.set_skin(SkinLoader().load(str(self.root)))
        field, scale, widths, spacing, left, _ = self.preview._geometry()
        total = sum(widths)+sum(spacing)
        rect = self.preview._stage_bottom_rect(left, total, field, scale)
        self.assertAlmostEqual(rect.width()/scale, 200)
        self.assertAlmostEqual(rect.height()/scale, 80)
        self.assertAlmostEqual(rect.center().x(), left+total/2)
        self.assertEqual(rect.bottom(), field.bottom())
        image = self.preview.grab().toImage()
        self.assertEqual(image.pixelColor(int(rect.center().x()), int(rect.bottom()-30)).name(), "#b31be2")

    def test_one_unit_lift_matches_export_pixel_rounding_for_sd_and_hd(self):
        for name, density in (("low", 1), ("high", 2)):
            suffix = "@2x" if density == 2 else ""
            for state in ("", "D"):
                self.png(f"{name}{state}{suffix}.png", (60*density, 160*density), "red")
        settings = "".join(f"KeyImage{column}: {name}\nKeyImage{column}D: {name}D\n"
                           for column, name in enumerate(("low", "high", "low", "high")))
        self.write_ini(settings)
        self.preview.set_skin(SkinLoader().load(str(self.root)))
        field, scale, widths, spacing, left, _ = self.preview._geometry()
        baseline = [self.preview._key_rect(column, left, widths[column], field, scale)
                    for column in range(2)]
        self.preview.set_design_options(ManiaDesign(receptor_raise=1))
        result = export_skin(self.root, 4, self.base / "rounded-export", ManiaDesign(receptor_raise=1))
        reopened = self.new_preview()
        reopened.set_skin(SkinLoader().load(str(result.root)))
        reopened_field, reopened_scale, reopened_widths, _, reopened_left, _ = reopened._geometry()
        pending_image = self.preview.grab().toImage()
        exported_image = reopened.grab().toImage()
        lane_x, exported_lane_x = left, reopened_left
        for column, density in enumerate((1, 2)):
            pending_rect = self.preview._key_rect(column, lane_x, widths[column], field, scale)
            exported_rect = reopened._key_rect(column, exported_lane_x, reopened_widths[column],
                                               reopened_field, reopened_scale)
            expected_lift = round(1.6*density)/(1.6*density)*scale
            self.assertAlmostEqual(baseline[column].top()-pending_rect.top(), expected_lift)
            self.assertAlmostEqual(pending_rect.top(), exported_rect.top())
            self.assertAlmostEqual(pending_rect.left(), exported_rect.left())
            self.assertAlmostEqual(pending_rect.width(), exported_rect.width())
            self.assertAlmostEqual(exported_rect.height()-pending_rect.height(), expected_lift)
            # Compare the visible art boundary rather than requiring identical
            # interpolation alpha values from differently padded textures.
            x = int(pending_rect.center().x())
            pending_rows = [y for y in range(int(field.top()), int(field.bottom()))
                            if pending_image.pixelColor(x, y).red() > 200
                            and pending_image.pixelColor(x, y).green() < 20]
            exported_rows = [y for y in range(int(field.top()), int(field.bottom()))
                             if exported_image.pixelColor(x, y).red() > 200
                             and exported_image.pixelColor(x, y).green() < 20]
            self.assertTrue(pending_rows)
            self.assertEqual(pending_rows[0], exported_rows[0])
            lane_x += widths[column]+spacing[column]
            exported_lane_x += reopened_widths[column]+spacing[column]

    def test_exported_design_matches_preview_pixels_and_overlay_dimensions(self):
        for suffix in ("1", "2"):
            self.png(f"mania-key{suffix}.png", (60, 160), "#ee7755")
            self.png(f"mania-key{suffix}D.png", (60, 160), "#ee5577")
        self.png("mania-stage-bottom@2x.png", (520, 24), "#336699")
        self.preview.set_skin(SkinLoader().load(str(self.root)))
        design = ManiaDesign(top_mask_height=45, top_mask_fade=20, receptor_raise=20, mask_colour="#331155")
        overlay = build_preview_overlay(self.root, 4, design)
        self.preview.set_design_options(design)
        field, scale, widths, spacing, left, _ = self.preview._geometry()
        rect = self.preview._stage_bottom_rect(left, sum(widths)+sum(spacing), field, scale)
        self.assertAlmostEqual(rect.width()/scale, overlay.width/2)
        self.assertAlmostEqual(rect.height()/scale, overlay.height/2)
        self.preview.t = 180
        before = self.hashes()
        result = export_skin(self.root, 4, self.base / "exported", design)
        exported_preview = self.new_preview()
        exported_preview.set_skin(SkinLoader().load(str(result.root)))
        exported_preview.t = 180
        image = self.preview.grab().toImage()
        exported = exported_preview.grab().toImage()
        self.assertEqual(image.size(), exported.size())
        # Include full field, receptor art, note targets, original StageBottom,
        # solid mask and fade. Do not retain any QPainter beyond paintEvent.
        for y in range(int(field.top()), int(field.bottom())):
            for x in range(int(left-8), int(left+sum(widths)+sum(spacing)+8)):
                self.assertEqual(image.pixel(x, y), exported.pixel(x, y), (x, y))
        self.assertEqual(before, self.hashes())


if __name__ == "__main__":
    unittest.main()
