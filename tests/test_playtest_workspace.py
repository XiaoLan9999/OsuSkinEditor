"""User-flow integration checks for the local Mania skin testing workspace"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import warnings

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core import i18n
from ui.main_window import MainWindow


APP = QApplication.instance() or QApplication([])


class PlaytestWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.source = self.directory / "skin"
        self.source.mkdir()
        (self.source / "textures").mkdir()
        (self.source / "skin.ini").write_text(
            "[General]\nName: Playtest fixture\nVersion: 2.7\n"
            "[Fonts]\nComboPrefix: custom-score\nComboOverlap: 2\n"
            "[Mania]\nKeys: 4\nHitPosition: 402\n"
            "NoteImage0: textures/lane-zero\nNoteImage1: textures/lane-one\n"
            "NoteImage2: textures/lane-two\nNoteImage3: textures/not-found\n"
            "Hit300g: custom-max\nHit300: custom-perfect\n"
            "[Mania]\nKeys: 7\nHitPosition: 410\n", encoding="utf-8")
        for name in ("textures/lane-zero", "textures/lane-one", "textures/lane-two",
                     "custom-max", "custom-perfect-0", "custom-perfect-1", "custom-perfect-2",
                     "mania-hit200", "mania-hit100", "mania-hit50", "mania-hit0",
                     "comboburst-mania", *(f"custom-score-{i}" for i in range(10))):
            Image.new("RGBA", (24, 32), (60, 160, 220, 255)).save(self.source / f"{name}.png")
        self.original = self.file_hashes()
        QCoreApplication.setOrganizationName("OsuSkinEditorTests")
        QCoreApplication.setApplicationName("PlaytestWorkspace")
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope,
                          str(self.directory / "settings"))
        QSettings().clear()
        i18n.load_language("zh-CN")
        self.window = MainWindow()
        self.window.setAttribute(Qt.WA_DontShowOnScreen)
        self.window.show()
        self.window.activateWindow()
        # Native window activation is unavailable with WA_DontShowOnScreen
        # Supply Qt's active window so focus transitions remain real events
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            APP.setActiveWindow(self.window)
        APP.processEvents()
        self.assertTrue(self.window.load_skin(str(self.source)))
        self.window.tabs.setCurrentIndex(1)
        APP.processEvents()
        self.preview = self.window.mania_preview
        self.controls = self.window.mania_playback
        self.freeze_timers()

    def tearDown(self):
        self.window.mania_design_dock._dirty = False
        self.window.mania_ini_dock._dirty = False
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def file_hashes(self):
        return {p.relative_to(self.source).as_posix(): sha256(p.read_bytes()).hexdigest()
                for p in self.source.rglob("*") if p.is_file()}

    def freeze_timers(self):
        # Deliberately drive deterministic session time, while still delivering
        # actual Qt mouse/key/focus events through the public widget interfaces
        self.window.std_preview.timer.stop()
        self.preview.timer.stop()
        self.preview._clock.invalidate()

    def choose_mode(self, mode):
        self.controls.test_mode.setCurrentIndex(self.controls.test_mode.findData(mode))
        self.freeze_timers()

    def score_snapshot(self):
        game = self.preview.game
        return (game.score, game.combo, game.max_combo, game.accuracy,
                dict(game.counts), tuple((n.id, n.status) for n in game.notes), game.now_ms)

    def seed_autoplay_score(self):
        self.preview.t = 3500
        self.preview._advance_game()
        self.assertGreater(self.preview.game.score, 0)

    def start_by_canvas_click(self):
        QTest.mouseClick(self.preview, Qt.LeftButton)
        self.freeze_timers()
        self.assertTrue(self.preview.hasFocus())
        self.assertTrue(self.preview._input_armed)
        self.assertTrue(self.preview._playing)

    def test_loaded_mania_defaults_to_auto_with_mixed_notes_and_matching_controls(self):
        self.assertEqual(self.controls.test_mode.currentData(), "auto")
        self.assertEqual(self.preview.test_mode, "auto")
        self.assertEqual(self.controls.test_pattern.currentData(), "mixed")
        self.assertEqual(self.preview.test_pattern, "mixed")
        self.assertTrue(self.preview._playing)
        self.assertFalse(self.preview._input_armed)
        self.assertFalse(self.window.btn_pause.isChecked())
        self.assertEqual(self.preview.keys, 4)
        self.assertEqual(self.controls.keys.currentData(), 4)
        self.assertTrue(any(note.is_hold for note in self.preview.game.notes))
        self.assertEqual({note.lane for note in self.preview.game.notes}, {0, 1, 2, 3})
        self.assertEqual(self.window.preview_badge.text(), i18n.t("playtest.badge"))
        self.assertEqual(self.controls.input_hint.text(), i18n.t("playtest.auto_help"))

    def test_play_mode_waits_for_explicit_click_and_focus_alone_does_not_arm(self):
        self.seed_autoplay_score()
        self.choose_mode("play")
        self.assertFalse(self.preview._playing)
        self.assertFalse(self.preview._input_armed)
        self.assertTrue(self.window.btn_pause.isChecked())
        self.assertEqual(self.preview.game.score, 0)
        self.preview.setFocus()
        QTest.keyClick(self.preview, Qt.Key_D)
        self.assertFalse(self.preview._input_armed)
        self.assertEqual(self.preview.game.pressed_lanes, set())
        self.assertEqual(self.preview.t, 0)
        self.assertIn("D · F · J · K", self.controls.input_hint.text())
        self.start_by_canvas_click()
        self.assertFalse(self.window.btn_pause.isChecked())

    def test_toolbar_resume_focuses_and_starts_play_then_pause_really_pauses(self):
        self.choose_mode("play")
        QTest.mouseClick(self.window.btn_pause, Qt.LeftButton)
        self.freeze_timers()
        self.assertTrue(self.preview.hasFocus())
        self.assertTrue(self.preview._input_armed)
        self.assertTrue(self.preview._playing)
        self.assertFalse(self.window.btn_pause.isChecked())
        QTest.mouseClick(self.window.btn_pause, Qt.LeftButton)
        self.freeze_timers()
        self.assertFalse(self.preview._playing)
        self.assertTrue(self.window.btn_pause.isChecked())
        self.assertEqual(self.preview.game.pressed_lanes, set())
        self.assertEqual(self.window.btn_pause.text(), i18n.t("workspace.resume"))

    def test_escape_disarms_and_synchronizes_toolbar_without_skin_writes(self):
        self.choose_mode("play")
        self.start_by_canvas_click()
        QTest.keyPress(self.preview, Qt.Key_D)
        self.assertIn(0, self.preview.game.pressed_lanes)
        QTest.keyClick(self.preview, Qt.Key_Escape)
        self.freeze_timers()
        self.assertFalse(self.preview._input_armed)
        self.assertFalse(self.preview._playing)
        self.assertTrue(self.window.btn_pause.isChecked())
        self.assertFalse(self.window.std_preview._playing)
        self.assertEqual(self.preview.game.pressed_lanes, set())
        self.assertEqual(self.file_hashes(), self.original)

    def test_edit_control_focus_loss_pauses_without_releasing_a_phantom_hold(self):
        self.choose_mode("play")
        self.start_by_canvas_click()
        self.preview.t = 3000
        self.preview._advance_game()
        QTest.keyPress(self.preview, Qt.Key_D)
        self.freeze_timers()
        self.assertTrue(any(n.status == "holding" for n in self.preview.game.notes))
        before = self.score_snapshot()
        self.controls.speed.setFocus()
        APP.processEvents()
        self.freeze_timers()
        self.assertFalse(self.preview._input_armed)
        self.assertFalse(self.preview._playing)
        self.assertTrue(self.window.btn_pause.isChecked())
        self.assertEqual(self.preview.game.pressed_lanes, set())
        self.assertEqual(self.score_snapshot(), before)

    def test_six_judgement_actions_pause_and_show_assets_without_changing_score(self):
        self.seed_autoplay_score()
        before = self.score_snapshot()
        for kind in ("300g", "300", "200", "100", "50", "0"):
            with self.subTest(kind=kind):
                self.controls.judgement_actions[kind].trigger()
                self.assertTrue(self.window.btn_pause.isChecked())
                self.assertFalse(self.preview._playing)
                self.assertEqual(self.preview._last_judgement, (kind, self.preview.t, True))
                self.assertEqual(self.score_snapshot(), before)
                self.assertTrue(self.preview._inspection_timer.isActive())
        self.assertEqual(self.file_hashes(), self.original)

    def test_combo_inspection_uses_preview_value_and_does_not_inflate_combo(self):
        self.seed_autoplay_score()
        before = self.score_snapshot()
        self.controls.combo_button.click()
        self.assertTrue(self.window.btn_pause.isChecked())
        self.assertFalse(self.preview._playing)
        self.assertEqual(self.preview._combo_inspect, (100, self.preview.t))
        self.assertTrue(self.preview._last_burst[1])
        first_index = self.preview._manual_burst_index
        self.controls.combo_button.click()
        self.assertEqual(self.preview._manual_burst_index, first_index + 1)
        self.assertEqual(self.score_snapshot(), before)
        self.assertEqual(self.file_hashes(), self.original)

    def test_asset_report_includes_columns_custom_paths_frames_and_missing_references(self):
        report = self.window._mania_asset_report()
        for column in range(4):
            self.assertIn(f"NoteImage{column}:", report)
            self.assertIn(f"NoteImage{column}H:", report)
            self.assertIn(f"NoteImage{column}L:", report)
            self.assertIn(f"NoteImage{column}T:", report)
        self.assertIn("NoteImage0: textures/lane-zero.png", report)
        self.assertIn("NoteImage1: textures/lane-one.png", report)
        self.assertIn("NoteImage2: textures/lane-two.png", report)
        self.assertIn("Hit300g: custom-max.png", report)
        self.assertIn("custom-perfect-0.png / custom-perfect-1.png", report)
        self.assertIn(i18n.t("playtest.frames").format(count=3), report)
        self.assertIn("textures/not-found", report)
        self.assertIn("ComboPrefix: custom-score", report)
        self.assertIn("custom-score-9: custom-score-9.png", report)
        self.assertIn("comboburst-mania[0]: comboburst-mania.png", report)
        self.assertNotIn(str(self.source), report)
        self.assertEqual(self.file_hashes(), self.original)

    def test_language_change_preserves_mode_pattern_score_and_localizes_guidance(self):
        self.controls.test_pattern.setCurrentIndex(self.controls.test_pattern.findData("holds"))
        self.seed_autoplay_score()
        self.window.btn_pause.setChecked(True)
        before = self.score_snapshot()
        for action, language in ((self.window.act_lang_en, "en-US"),
                                 (self.window.act_lang_zh, "zh-CN")):
            action.trigger()
            self.freeze_timers()
            self.assertEqual(i18n.lang(), language)
            self.assertEqual(self.controls.test_mode.currentData(), "auto")
            self.assertEqual(self.controls.test_pattern.currentData(), "holds")
            self.assertEqual(self.preview.test_mode, "auto")
            self.assertEqual(self.preview.test_pattern, "holds")
            self.assertEqual(self.controls.test_mode.currentText(), i18n.t("playtest.mode_auto"))
            self.assertEqual(self.controls.test_pattern.currentText(), i18n.t("playtest.pattern_holds"))
            self.assertEqual(self.controls.input_hint.text(), i18n.t("playtest.auto_help"))
            self.assertEqual(self.window.preview_note.text(), i18n.t("playtest.footer"))
            self.assertEqual(self.window.btn_pause.text(), i18n.t("workspace.resume"))
            self.assertEqual(self.score_snapshot(), before)

    def test_switching_modes_resets_practice_and_demo_controls_then_restores_auto(self):
        self.seed_autoplay_score()
        self.choose_mode("demo")
        self.assertEqual(self.preview.test_mode, "demo")
        self.assertTrue(self.preview._playing)
        self.assertFalse(self.controls.test_pattern.isEnabled())
        self.assertEqual(self.preview.game.score, 0)
        self.assertEqual(self.window.preview_badge.text(), i18n.t("workspace.demo"))
        self.assertEqual(self.window.preview_note.text(), i18n.t("workspace.preview_note"))
        self.choose_mode("auto")
        self.assertTrue(self.controls.test_pattern.isEnabled())
        self.assertTrue(self.preview._playing)
        self.assertEqual(self.controls.input_hint.text(), i18n.t("playtest.auto_help"))

    def test_tab_switch_hides_controls_and_returning_to_manual_does_not_start_it(self):
        self.choose_mode("play")
        self.start_by_canvas_click()
        self.window.tabs.setCurrentIndex(0)
        self.freeze_timers()
        self.assertFalse(self.controls.isVisible())
        self.assertFalse(self.preview._playing)
        self.assertFalse(self.preview._input_armed)
        self.window.tabs.setCurrentIndex(1)
        self.freeze_timers()
        self.assertTrue(self.controls.isVisible())
        self.assertFalse(self.preview._playing)
        self.assertTrue(self.window.btn_pause.isChecked())
        self.assertEqual(self.window.btn_pause.text(), i18n.t("workspace.resume"))

    def test_pattern_tempo_restart_and_keycount_changes_rebuild_session_without_writes(self):
        self.choose_mode("play")
        for change in (lambda: self.controls.test_pattern.setCurrentIndex(
                           self.controls.test_pattern.findData("chords")),
                       lambda: self.controls.tempo.setValue(180),
                       lambda: self.controls.restart.click(),
                       lambda: self.controls.keys.setCurrentIndex(self.controls.keys.findData(7))):
            self.start_by_canvas_click()
            self.preview.t = 2000
            self.preview._advance_game()
            change()
            self.freeze_timers()
            self.assertFalse(self.preview._playing)
            self.assertFalse(self.preview._input_armed)
            self.assertTrue(self.window.btn_pause.isChecked())
            self.assertEqual(self.preview.game.score, 0)
            self.assertEqual(self.preview.t, 0)
        self.assertEqual(self.preview.keys, 7)
        self.assertEqual(self.preview.game.keys, 7)
        self.assertIn("S · D · F · Space · J · K · L", self.controls.input_hint.text())
        self.assertEqual(self.preview.game.bpm, 180)
        self.assertEqual(self.preview.game.pattern, "chords")
        self.assertEqual(self.file_hashes(), self.original)


if __name__ == "__main__":
    unittest.main()
