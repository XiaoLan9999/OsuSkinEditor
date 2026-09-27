import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from PySide6.QtCore import Qt, QCoreApplication, QEvent, QPoint
from PySide6.QtGui import QColor, QImage, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLineEdit

from core.mania_gameplay import PlayNote
from core.skin_loader import SkinLoader
from ui.preview.mania_preview import ManiaPreview


APP = QApplication.instance() or QApplication([])


class ManiaPlaytestUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.host = QWidget()
        layout = QVBoxLayout(self.host)
        self.preview = ManiaPreview()
        self.preview.setFixedSize(856, 552)
        self.editor = QLineEdit()
        layout.addWidget(self.preview)
        layout.addWidget(self.editor)
        self.host.show()
        self.host.activateWindow()
        APP.processEvents()
        self.preview.set_playing(False)
        self.load()

    def tearDown(self):
        self.host.close()
        self.host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def png(self, name, colour, size=(80, 80)):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        image = QImage(*size, QImage.Format_ARGB32)
        image.fill(QColor(colour))
        image.save(str(path))

    def load(self, settings="", fonts=""):
        (self.root / "skin.ini").write_text(
            "[General]\nVersion: 2.7\n[Fonts]\n"+fonts+
            "[Mania]\nKeys: 4\nColumnWidth: 60,60,60,60\nColumnLineWidth: 0,0,0,0,0\n"
            "JudgementLine: 0\n"+settings, encoding="utf-8")
        self.preview.set_skin(SkinLoader().load(str(self.root)))

    def start_play(self, notes):
        self.preview.set_test_mode("play")
        self.preview.game.reset(notes=notes)
        QTest.mouseClick(self.preview, Qt.LeftButton, pos=QPoint(220, 220))
        self.preview.timer.stop()  # Tests supply monotonic timestamps explicitly.
        self.preview._clock.invalidate()
        self.assertTrue(self.preview.hasFocus())

    def at(self, milliseconds):
        self.preview.t = milliseconds
        self.preview._clock.invalidate()

    def test_keyboard_requires_canvas_click_and_does_not_capture_text_edits(self):
        self.preview.set_test_mode("play")
        self.preview.game.reset(notes=[PlayNote(0, 0, 2000)])
        self.at(2000)
        self.preview.setFocus(Qt.TabFocusReason)
        QTest.keyPress(self.preview, Qt.Key_D)
        self.assertEqual(self.preview.game.combo, 0)
        self.assertFalse(self.preview.game.pressed_lanes)
        self.editor.setFocus()
        QTest.keyClicks(self.editor, "dfjk")
        self.assertEqual(self.editor.text(), "dfjk")
        self.assertEqual(self.preview.game.combo, 0)
        QTest.mouseClick(self.preview, Qt.LeftButton, pos=QPoint(220, 220))
        self.preview.timer.stop()
        self.at(2000)
        QTest.keyPress(self.preview, Qt.Key_D)
        self.assertEqual(self.preview.game.combo, 1)
        self.assertEqual(self.preview.game.last_event.judgement, "300g")

    def test_auto_repeat_and_modified_shortcuts_do_not_judge_notes(self):
        self.start_play([PlayNote(0, 0, 2000)])
        self.at(2000)
        QTest.keyPress(self.preview, Qt.Key_D, Qt.ControlModifier)
        self.assertEqual(self.preview.game.combo, 0)
        repeated = QKeyEvent(QEvent.KeyPress, Qt.Key_D, Qt.NoModifier, "d", True, 1)
        QApplication.sendEvent(self.preview, repeated)
        self.assertEqual(self.preview.game.combo, 0)
        QTest.keyPress(self.preview, Qt.Key_D)
        self.assertEqual(self.preview.game.combo, 1)

    def test_explicit_toolbar_start_focuses_and_arms_canvas_only(self):
        self.preview.set_test_mode("play")
        self.editor.setFocus()
        self.preview.set_playing(True)
        self.assertFalse(self.preview._playing)
        self.assertFalse(self.preview._input_armed)
        self.preview.start_test()
        self.assertTrue(self.preview.hasFocus())
        self.assertTrue(self.preview._input_armed)
        self.assertTrue(self.preview._playing)
        self.editor.setFocus()
        APP.processEvents()
        self.assertFalse(self.preview._input_armed)
        self.assertFalse(self.preview._playing)

    def test_input_settles_elapsed_clock_before_judging(self):
        self.start_play([PlayNote(0, 0, 2020)])
        self.at(2000)
        self.preview._clock.start()
        time.sleep(.020)
        QTest.keyPress(self.preview, Qt.Key_D)
        self.assertGreaterEqual(self.preview.game.last_event.time_ms, 2015)
        self.assertEqual(self.preview.game.last_event.judgement, "300g")

    def test_hold_release_scores_and_focus_loss_freezes_without_miss(self):
        self.start_play([PlayNote(0, 0, 2000, 2600)])
        self.at(2000)
        QTest.keyPress(self.preview, Qt.Key_D)
        self.assertEqual(self.preview.game.notes[0].status, "holding")
        self.editor.setFocus()
        APP.processEvents()
        self.assertFalse(self.preview._playing)
        self.assertFalse(self.preview.game.pressed_lanes)
        self.assertEqual(self.preview.game.counts["0"], 0)
        frozen = self.preview.t
        time.sleep(.020)
        self.preview.tick()
        self.assertEqual(self.preview.t, frozen)
        QTest.mouseClick(self.preview, Qt.LeftButton, pos=QPoint(220, 220))
        self.preview.timer.stop()
        self.at(2200)
        QTest.keyPress(self.preview, Qt.Key_D)
        self.at(2600)
        QTest.keyRelease(self.preview, Qt.Key_D)
        self.assertEqual(self.preview.game.notes[0].status, "hit")
        self.assertEqual(self.preview.game.combo, 2)

    def test_escape_pause_hide_and_restart_clear_input(self):
        self.start_play([PlayNote(0, 0, 2000, 3000)])
        self.at(2000)
        QTest.keyPress(self.preview, Qt.Key_D)
        QTest.keyPress(self.preview, Qt.Key_Escape)
        self.assertFalse(self.preview._playing)
        self.assertFalse(self.preview.game.pressed_lanes)
        self.assertFalse(self.preview._input_armed)
        self.preview.restart_demo()
        self.assertEqual(self.preview.game.combo, 0)
        self.assertEqual(self.preview.t, 0)
        self.assertFalse(self.preview._playing)
        QTest.mouseClick(self.preview, Qt.LeftButton, pos=QPoint(220, 220))
        self.preview.hide()
        self.assertFalse(self.preview._playing)
        self.assertFalse(self.preview._clock.isValid())
        self.preview.show()
        self.assertFalse(self.preview._playing)

    def test_all_six_judgement_images_are_visible_without_mutating_score(self):
        colours = {"300g": "#ff0000", "300": "#00ff00", "200": "#0000ff",
                   "100": "#ffff00", "50": "#ff00ff", "0": "#00ffff"}
        for kind, colour in colours.items():
            self.png(f"hit-{kind}@2x.png", colour, (160, 64))
        self.load("ScorePosition: 210\n"+"".join(f"Hit{kind}: hit-{kind}\n" for kind in colours))
        self.preview.set_test_mode("play")
        field, scale, widths, spacing, left, _ = self.preview._geometry()
        x, y = left+(sum(widths)+sum(spacing))/2, field.top()+210*scale
        for kind, colour in colours.items():
            self.preview.show_judgement(kind)
            image = self.preview.grab().toImage()
            self.assertEqual(image.pixelColor(int(x), int(y)).name(), colour)
            self.assertEqual(self.preview.game.combo, 0)
            self.assertEqual(self.preview.game.score, 0)
        self.assertEqual(self.preview.t, 0)

    def test_animated_judgement_inspection_uses_separate_clock_and_hd_dimensions(self):
        self.png("mania-hit300g-0@2x.png", "red", (160, 64))
        self.png("mania-hit300g-1@2x.png", "blue", (160, 64))
        self.load()
        self.preview.show_judgement("300g")
        self.preview._inspection_time = 25
        field, scale, widths, spacing, left, _ = self.preview._geometry()
        x, y = left+sum(widths)/2, field.top()+300*scale
        image = self.preview.grab().toImage()
        self.assertEqual(image.pixelColor(int(x), int(y)).name(), "#0000ff")
        self.assertEqual(image.pixelColor(int(x+26*scale), int(y)).name(), "#000000")
        time.sleep(.015)
        self.preview._tick_inspection()
        self.assertGreater(self.preview._inspection_time, 25)
        self.assertEqual(self.preview.t, 0)
        self.assertEqual(self.preview.game.now_ms, 0)
        self.preview.hide()
        self.assertFalse(self.preview._inspection_timer.isActive())
        self.preview.show()
        self.assertTrue(self.preview._inspection_timer.isActive())
        self.preview.set_playing(True)
        self.assertFalse(self.preview._inspection_timer.isActive())
        self.assertIsNone(self.preview._last_judgement)

    def test_coloured_hold_head_body_tail_and_all_columns_render_real_assets(self):
        colours = ("#aa0000", "#00aa00", "#0000aa", "#aaaa00")
        settings = ""
        for i, colour in enumerate(colours):
            self.png(f"note-{i}.png", colour)
            settings += f"NoteImage{i}: note-{i}\n"
        self.png("hold-head.png", "#cc11cc")
        self.png("hold-body.png", "#11cccc", (80, 30))
        self.png("hold-tail.png", "#ff8811")
        self.load(settings+"NoteImage0H: hold-head\nNoteImage0L: hold-body\nNoteImage0T: hold-tail\n")
        self.preview.set_test_mode("auto")
        self.preview.set_playing(False)
        self.preview.game.reset(notes=[PlayNote(0, 0, 3000, 3300),
                                      *[PlayNote(i, i, 3000) for i in range(1, 4)]])
        self.preview.t = 2900
        field, scale, widths, spacing, left, hit = self.preview._geometry()
        velocity = (hit-field.top())/self.preview.travel_time_ms
        head_y, tail_y = hit-100*velocity, hit-400*velocity
        image = self.preview.grab().toImage()
        self.assertEqual(image.pixelColor(int(left+30), int(head_y-30)).name(), "#cc11cc")
        self.assertEqual(image.pixelColor(int(left+30), int(tail_y+30)).name(), "#ff8811")
        self.assertEqual(image.pixelColor(int(left+30), int((head_y+tail_y)/2)).name(), "#11cccc")
        for i in range(1, 4):
            self.assertEqual(image.pixelColor(int(left+i*60+30), int(head_y-30)).name(), colours[i])

    def test_pressed_receptor_uses_keyimage_d(self):
        self.png("up.png", "#223344", (80, 160))
        self.png("down@2x.png", "#ee5511", (160, 320))
        self.load("KeyImage0: up\nKeyImage0D: down\n")
        self.start_play([PlayNote(0, 0, 5000)])
        self.at(100)
        QTest.keyPress(self.preview, Qt.Key_D)
        field, scale, widths, spacing, left, hit = self.preview._geometry()
        image = self.preview.grab().toImage()
        self.assertEqual(image.pixelColor(int(left+30), int(field.bottom()-25)).name(), "#ee5511")
        self.at(110)
        QTest.keyRelease(self.preview, Qt.Key_D)
        image = self.preview.grab().toImage()
        self.assertEqual(image.pixelColor(int(left+30), int(field.bottom()-25)).name(), "#223344")

    def test_repeat_bottom_preserves_top_of_tall_hold_art_without_stretching(self):
        body = QImage(60, 600, QImage.Format_ARGB32)
        body.fill(QColor("red"))
        for y in range(540, 600):
            for x in range(60):
                body.setPixelColor(x, y, QColor("blue"))
        body.save(str(self.root / "body.png"))
        self.png("head.png", "white", (60, 60))
        self.load("NoteImage0H: head\nNoteImage0L: body\nNoteBodyStyle0: 3\n")
        self.preview.set_test_mode("auto")
        self.preview.set_playing(False)
        self.preview.game.reset(notes=[PlayNote(0, 0, 3000, 3300)])
        self.preview.t = 2900
        field, scale, widths, spacing, left, hit = self.preview._geometry()
        head_bottom = hit-100*(hit-field.top())/self.preview.travel_time_ms
        image = self.preview.grab().toImage()
        # RepeatBottom preserves the top then extends the last pixel row.
        # A short hold samples the red TOP of this tall image, not the blue
        # bottom as the former bottom-aligned whole-texture tiling did.
        self.assertEqual(image.pixelColor(int(left+30), int(head_bottom-70)).name(), "#ff0000")

    def test_upside_down_moves_hud_positions_without_flipping_judgement_art(self):
        self.png("top.png", "#44ff22", (80, 32))
        self.load("UpsideDown: 1\nScorePosition: 120\nHit300g: top\n")
        self.preview.show_judgement("300g")
        field, scale, widths, spacing, left, hit = self.preview._geometry()
        image = self.preview.grab().toImage()
        x, y = left+sum(widths)/2, field.bottom()-120*scale
        self.assertEqual(image.pixelColor(int(x), int(y)).name(), "#44ff22")

    def test_stage_light_bottom_uses_light_position_and_empty_press_has_no_hit_effect(self):
        self.png("stage.png", "#11ee77", (80, 80))
        self.png("lightingN.png", "#ee1177", (16, 16))
        self.png("lightingL.png", "#ee1177", (16, 16))
        self.load("StageLight: stage\nLightPosition: 380\n")
        self.start_play([PlayNote(0, 0, 5000)])
        self.at(100)
        QTest.keyPress(self.preview, Qt.Key_D)
        field, scale, widths, spacing, left, hit = self.preview._geometry()
        image = self.preview.grab().toImage()
        x = int(left+30)
        self.assertEqual(image.pixelColor(x, int(field.top()+375)).name(), "#11ee77")
        self.assertEqual(image.pixelColor(x, int(field.top()+385)).name(), "#000000")
        self.assertEqual(image.pixelColor(x, int(hit)).name(), "#000000")

    def test_hit_lighting_honours_per_column_width(self):
        self.png("lightingN.png", "#ee1177", (16, 16))
        self.load("LightingNWidth: 30,0,0,0\n")
        self.preview.set_test_mode("auto")
        self.preview.set_playing(False)
        self.preview._lane_flashes[0] = 0
        field, scale, widths, spacing, left, hit = self.preview._geometry()
        image = self.preview.grab().toImage()
        self.assertEqual(image.pixelColor(int(left+30), int(hit)).name(), "#ee1177")
        self.assertEqual(image.pixelColor(int(left+36), int(hit)).name(), "#000000")

    def test_combo_burst_styles_stay_outside_lanes_and_cycle_both_sides(self):
        self.png("comboburst-mania.png", "#5511cc", (80, 80))
        for style, side in (("Left", "left"), ("Right", "right"), ("Both", "right")):
            self.load(f"ComboBurstStyle: {style}\n")
            self.preview._manual_burst_index = -1
            self.preview.show_comboburst()
            field, scale, widths, spacing, left, hit = self.preview._geometry()
            x = left-20 if side == "left" else left+sum(widths)+20
            image = self.preview.grab().toImage()
            self.assertEqual(image.pixelColor(int(x), int(field.bottom()-20)).name(), "#5511cc")
            self.assertNotEqual(image.pixelColor(int(left+30), int(field.bottom()-20)).name(), "#5511cc")
            if style == "Both":
                self.preview.show_comboburst()
                image = self.preview.grab().toImage()
                self.assertEqual(image.pixelColor(int(left-20), int(field.bottom()-20)).name(), "#5511cc")

    def test_manual_combo_burst_and_custom_font_preserve_engine_combo(self):
        self.png("comboburst-mania.png", "#4411ee", (300, 120))
        self.png("numbers-0.png", "#11ee44", (32, 48))
        self.png("numbers-1.png", "#ee1144", (32, 48))
        self.load("ComboPosition: 120\n", "ComboPrefix: numbers\nComboOverlap: 4\n")
        self.preview.show_comboburst()
        field, scale, widths, spacing, left, hit = self.preview._geometry()
        center = left+sum(widths)/2
        image = self.preview.grab().toImage()
        self.assertEqual(image.pixelColor(int(center), int(field.top()+120)).name(), "#11ee44")
        self.assertEqual(image.pixelColor(int(left+sum(widths)+20), int(field.bottom()-20)).name(), "#4411ee")
        self.assertNotEqual(image.pixelColor(int(center), int(field.bottom()-20)).name(), "#4411ee")
        self.assertEqual(self.preview.game.combo, 0)
        self.assertEqual(self.preview.game.score, 0)

    def test_pattern_keys_and_speed_are_read_only_and_restart_preserves_mode(self):
        before = {path.name: sha256(path.read_bytes()).hexdigest() for path in self.root.iterdir()}
        self.preview.set_test_mode("auto")
        self.preview.set_test_pattern("holds")
        self.preview.set_scroll_speed(32)
        self.preview.set_demo_bpm(180)
        self.preview.restart_demo()
        self.assertTrue(all(note.is_hold for note in self.preview.game.notes[:-4]))
        self.assertEqual(self.preview.test_mode, "auto")
        self.assertEqual(self.preview.key_labels, ("D", "F", "J", "K"))
        self.preview.set_keys(7)
        self.assertEqual(self.preview.key_labels, ("S", "D", "F", "Space", "J", "K", "L"))
        self.preview.set_keys(18)
        self.assertEqual(len(set(self.preview.key_labels)), 18)
        self.assertFalse(self.preview.grab().isNull())
        after = {path.name: sha256(path.read_bytes()).hexdigest() for path in self.root.iterdir()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
