import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from core.mania_gameplay import PlayNote
from core.skin_loader import SkinLoader
from ui.preview.mania_preview import ManiaPreview


APP = QApplication.instance() or QApplication([])


class ManiaHudFidelityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        for number in range(10):
            self.png(f"combo-{number}.png", "white", (32, 40))
        self.png("mania-hit300g.png", "#00ff00", (80, 32))
        (self.root / "skin.ini").write_text(
            "[General]\nVersion: 2.7\n[Fonts]\nComboPrefix: combo\nComboOverlap: 0\n"
            "[Mania]\nKeys: 4\nColumnWidth: 60,60,60,60\n"
            "ColumnLineWidth: 0,0,0,0,0\nJudgementLine: 0\n"
            "ComboPosition: 80\nScorePosition: 240\n"
            "ColourHold: 80,160,240,255\nColourBreak: 240,30,60\n", encoding="utf-8")
        self.preview = ManiaPreview()
        self.preview.resize(856, 552)
        self.preview.set_playing(False)
        self.preview.set_skin(SkinLoader().load(str(self.root)))
        self.preview.set_test_mode("auto")
        self.preview.set_playing(False)

    def tearDown(self):
        self.preview.close()
        self.preview.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def png(self, name, colour, size):
        image = QImage(*size, QImage.Format_ARGB32)
        image.fill(QColor(colour))
        image.save(str(self.root / name))

    def chart(self, notes, mode="auto"):
        self.preview.set_test_mode(mode)
        self.preview.restart_demo()
        self.preview.set_playing(False)
        self.preview.game.reset(notes=notes)

    def advance(self, time_ms):
        self.preview.t = time_ms
        self.preview._accept_events(self.preview.game.advance_to(time_ms, autoplay=self.preview.test_mode == "auto"))

    def combo_point(self):
        field, scale, widths, spacing, left, _ = self.preview._geometry()
        return int(left+(sum(widths)+sum(spacing))/2), int(field.top()+80*scale)

    def combo_colour(self):
        image = self.preview.grab().toImage()
        return image.pixelColor(*self.combo_point())

    def assert_rgb_close(self, colour, expected, tolerance=2):
        self.assertTrue(all(abs(a-b) <= tolerance for a, b in zip(colour.getRgb()[:3], expected)),
                        (colour.getRgb(), expected))

    def test_combo_uses_colourhold_during_hold_then_returns_to_white(self):
        self.chart([PlayNote(0, 0, 1000, 2000)])
        self.advance(1200)
        self.assert_rgb_close(self.combo_colour(), (80, 160, 240))
        self.advance(2200)
        self.assert_rgb_close(self.combo_colour(), (255, 255, 255))
        self.assertEqual(self.preview.game.combo, 2)

    def test_colour_transition_is_short_and_render_does_not_advance_it(self):
        self.chart([PlayNote(0, 0, 1000, 2000)])
        self.advance(1060)
        first = self.combo_colour()
        self.assertGreater(first.red(), 80)
        self.assertLess(first.red(), 255)
        state = (self.preview.t, self.preview.game.now_ms, self.preview.game.combo,
                 self.preview._combo_colour_at, set(self.preview._hud_holds))
        for _ in range(3):
            self.assertEqual(self.combo_colour(), first)
        self.assertEqual(state, (self.preview.t, self.preview.game.now_ms, self.preview.game.combo,
                                 self.preview._combo_colour_at, self.preview._hud_holds))

    def test_any_remaining_hold_keeps_colour_when_another_lane_releases(self):
        self.chart([PlayNote(0, 0, 1000, 1500), PlayNote(1, 1, 1100, 2300)])
        self.advance(1700)
        self.assertEqual(self.preview.game.pressed_lanes, {1})
        self.assert_rgb_close(self.combo_colour(), (80, 160, 240))
        self.advance(2450)
        self.assert_rgb_close(self.combo_colour(), (255, 255, 255))

    def test_taps_do_not_tint_combo_and_manual_inspector_does_not_change_scoring(self):
        self.chart([PlayNote(0, 0, 1000)])
        self.advance(1100)
        self.assert_rgb_close(self.combo_colour(), (255, 255, 255))
        before = (self.preview.game.score, self.preview.game.combo, self.preview.game.now_ms)
        self.preview.show_comboburst()
        self.assert_rgb_close(self.combo_colour(), (255, 255, 255))
        self.assertEqual(before, (self.preview.game.score, self.preview.game.combo, self.preview.game.now_ms))

    def test_combo_increment_stretches_height_only_then_settles(self):
        self.chart([PlayNote(0, 0, 1000)])
        bounds = []
        for time_ms in (1000, 1310):
            self.advance(time_ms)
            image = self.preview.grab().toImage()
            center_x, center_y = self.combo_point()
            pixels = [(x, y) for y in range(center_y-30, center_y+31)
                      for x in range(center_x-30, center_x+31)
                      if min(image.pixelColor(x, y).getRgb()[:3]) > 240]
            bounds.append((max(x for x, y in pixels)-min(x for x, y in pixels)+1,
                           max(y for x, y in pixels)-min(y for x, y in pixels)+1))
        self.assertEqual(bounds[0][0], bounds[1][0])
        self.assertAlmostEqual(bounds[0][1]/bounds[1][1], 1.4, delta=.08)

    def test_combo_break_uses_configured_colour_and_disappears_after_200ms(self):
        self.chart([PlayNote(0, 0, 1000), PlayNote(1, 1, 1200), PlayNote(2, 2, 1400)], mode="play")
        for lane, when in ((0, 1000), (1, 1200)):
            self.preview.t = when
            self.preview._accept_events(self.preview.game.key_down(lane, when))
            self.preview._accept_events(self.preview.game.key_up(lane, when+1))
        self.advance(1550)
        colour = self.combo_colour()
        self.assertEqual(self.preview.game.combo, 0)
        self.assertGreater(colour.red(), 80)
        self.assertGreater(colour.red(), colour.green()*4)
        self.assertGreater(colour.blue(), colour.green())
        self.advance(1800)
        self.assert_rgb_close(self.combo_colour(), (0, 0, 0))

    def test_tint_multiplies_texture_colours_without_destroying_source_alpha(self):
        source = QImage(2, 1, QImage.Format_RGBA8888)
        source.setPixelColor(0, 0, QColor(200, 100, 50, 128))
        source.setPixelColor(1, 0, QColor(0, 0, 0, 255))
        tinted = self.preview._multiply_sprite(QPixmap.fromImage(source), QColor(128, 255, 64)).toImage()
        self.assert_rgb_close(tinted.pixelColor(0, 0), (100, 100, 12))
        self.assertEqual(tinted.pixelColor(0, 0).alpha(), 128)
        self.assertEqual(tinted.pixelColor(1, 0), QColor("black"))

    def test_real_judgement_fades_out_but_manual_inspection_remains_visible(self):
        self.chart([PlayNote(0, 0, 1000)])
        field, scale, widths, spacing, left, _ = self.preview._geometry()
        point = (int(left+(sum(widths)+sum(spacing))/2), int(field.top()+240*scale))
        self.advance(1020)
        self.assert_rgb_close(self.preview.grab().toImage().pixelColor(*point), (0, 255, 0))
        self.advance(1221)
        self.assert_rgb_close(self.preview.grab().toImage().pixelColor(*point), (0, 0, 0))
        self.preview.show_judgement("300g")
        self.preview._inspection_time = 10000
        self.assert_rgb_close(self.preview.grab().toImage().pixelColor(*point), (0, 255, 0))

    def test_restart_clears_transient_colour_pulse_and_break_state(self):
        self.chart([PlayNote(0, 0, 1000, 2000)])
        self.advance(1200)
        self.preview.restart_demo()
        self.assertFalse(self.preview._hud_holds)
        self.assertIsNone(self.preview._combo_pulse_at)
        self.assertIsNone(self.preview._combo_break)
        self.assertEqual(self.preview._combo_colour_at_time(0), QColor("white"))


if __name__ == "__main__":
    unittest.main()
