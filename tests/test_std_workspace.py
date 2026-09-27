"""Standard controls, scene rendering and shared viewport integration"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from core import i18n
from ui.main_window import MainWindow


APP = QApplication.instance() or QApplication([])


class StdWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "skin"
        self.source.mkdir()
        (self.source / "skin.ini").write_text(
            "[General]\nName: Standard fixture\nVersion: 2.7\n"
            "[Colours]\nCombo1: 80,190,220\n[Mania]\nKeys: 4\n", encoding="utf-8")
        Image.new("RGBA", (80, 80), (255, 255, 255, 255)).save(self.source / "hitcircle.png")
        QCoreApplication.setOrganizationName("OsuSkinEditorTests")
        QCoreApplication.setApplicationName("StdWorkspace")
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(self.root / "settings"))
        QSettings().clear()
        i18n.load_language("zh-CN")
        self.window = MainWindow()
        self.window.setAttribute(Qt.WA_DontShowOnScreen)
        self.window.show()
        APP.processEvents()
        self.assertTrue(self.window.load_skin(str(self.source)))
        self.preview = self.window.std_preview
        self.controls = self.window.std_playback
        self.window.tabs.setCurrentIndex(0)
        self.window.btn_pause.setChecked(True)
        self.files = self.file_hashes()

    def tearDown(self):
        self.window.mania_ini_dock._dirty = False
        self.window.mania_design_dock._dirty = False
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def file_hashes(self):
        return {p.relative_to(self.source).as_posix(): sha256(p.read_bytes()).hexdigest()
                for p in self.source.rglob("*") if p.is_file()}

    def select_pattern(self, pattern):
        self.controls.pattern.setCurrentIndex(self.controls.pattern.findData(pattern))

    def test_main_window_defaults_to_mixed_auto_scene_with_matching_controls(self):
        self.assertEqual(self.preview.test_mode, "auto")
        self.assertEqual(self.controls.mode.currentData(), "auto")
        self.assertEqual(self.preview.test_pattern, "mixed")
        self.assertEqual(self.controls.pattern.currentData(), "mixed")
        self.assertEqual({obj.kind for obj in self.preview._scene.objects},
                         {"circle", "slider", "spinner"})
        self.assertTrue(self.controls.isVisible())
        self.assertFalse(self.window.mania_playback.isVisible())
        self.assertFalse(self.window.preview_zoom.isVisible())
        self.assertEqual(self.controls.hint.text(), i18n.t("std_scene.help_auto"))
        self.assertEqual(self.window.preview_note.text(), i18n.t("std_scene.footer"))

    def test_pattern_selector_rebuilds_the_actual_scene_and_restarts_its_clock(self):
        expected = {"circles": {"circle"}, "sliders": {"slider"},
                    "spinner": {"spinner"}, "mixed": {"circle", "slider", "spinner"}}
        for pattern, kinds in expected.items():
            with self.subTest(pattern=pattern):
                self.preview.t = 5200
                self.select_pattern(pattern)
                self.assertEqual(self.preview.test_pattern, pattern)
                self.assertEqual(self.preview._scene.pattern, pattern)
                self.assertEqual({obj.kind for obj in self.preview._scene.objects}, kinds)
                self.assertEqual(self.preview.t, 0)
                self.assertFalse(self.preview.grab().isNull())
        self.assertEqual(self.file_hashes(), self.files)

    def test_cs_and_ar_reach_rendered_circle_scale_and_preempt_without_skin_writes(self):
        self.select_pattern("circles")
        self.preview.t = 800
        scene = self.preview._scene
        calls = []
        original_circle = scene._circle

        def record_circle(*args, **kwargs):
            # Do not retain a QPainter in Mock.call_args: its temporary paint
            # device dies at the end of QWidget.grab()
            calls.append((args[6], args[7]))
            return original_circle(*args, **kwargs)

        with patch.object(scene, "_circle", new=record_circle):
            self.preview.grab()
            self.assertGreater(len(calls), 0)
            original_scale, original_preempt = calls[0]
        self.controls.circle_size.setValue(8)
        self.controls.approach_rate.setValue(10)
        calls.clear()
        with patch.object(scene, "_circle", new=record_circle):
            self.preview.grab()
            self.assertGreater(len(calls), 0)
            changed_scale, changed_preempt = calls[0]
        self.assertEqual(self.preview.circle_size, 8)
        self.assertEqual(self.preview.approach_rate, 10)
        self.assertLess(changed_scale, original_scale)
        self.assertEqual(original_preempt, 1200)
        self.assertEqual(changed_preempt, 450)
        self.assertEqual(self.preview.t, 800)
        self.assertEqual(self.file_hashes(), self.files)

    def test_judgement_and_combo_inspection_pause_without_advancing_scene_statistics(self):
        self.window.btn_pause.setChecked(False)
        self.preview.timer.stop()
        self.preview._clock.invalidate()
        self.preview.t = 5800
        before = self.preview._scene.stats(self.preview.t)
        for kind in ("300", "100", "50", "0"):
            self.controls.judgement_actions[kind].trigger()
            self.assertTrue(self.window.btn_pause.isChecked())
            self.assertFalse(self.preview._playing)
            self.assertEqual(self.preview._manual_judgement, kind)
            self.preview.tick()
            self.preview.grab()
            self.assertEqual(self.preview.t, 5800)
            self.assertEqual(self.preview._scene.stats(self.preview.t), before)
        self.controls.combo_button.click()
        self.assertIsNotNone(self.preview._manual_burst)
        self.assertEqual(self.preview.t, 5800)
        self.assertEqual(self.preview._scene.stats(self.preview.t), before)
        self.controls.restart.click()
        self.assertEqual(self.preview.t, 0)
        self.assertIsNone(self.preview._manual_judgement)
        self.assertIsNone(self.preview._manual_burst)
        self.assertEqual(self.file_hashes(), self.files)

    def test_asset_inspector_enables_zoom_and_disables_scene_only_controls(self):
        self.controls.mode.setCurrentIndex(self.controls.mode.findData("inspect"))
        self.assertEqual(self.preview.test_mode, "inspect")
        self.assertTrue(self.window.preview_zoom.isVisible())
        for widget in (self.controls.pattern, self.controls.circle_size, self.controls.approach_rate,
                       self.controls.judgement_button, self.controls.combo_button):
            self.assertFalse(widget.isEnabled())
        self.window.preview_zoom.setCurrentIndex(self.window.preview_zoom.findData(1.5))
        self.assertEqual(self.preview.preview_scale, 1.5)
        self.assertEqual(self.controls.hint.text(), i18n.t("std_scene.help_inspect"))
        self.controls.mode.setCurrentIndex(self.controls.mode.findData("auto"))
        self.assertEqual(self.preview.test_mode, "auto")
        self.assertFalse(self.window.preview_zoom.isVisible())
        self.assertTrue(self.controls.pattern.isEnabled())
        self.assertTrue(self.controls.judgement_button.isEnabled())
        self.assertEqual(self.file_hashes(), self.files)

    def test_all_scene_aspects_are_shared_with_mania_and_survive_detachment(self):
        for pattern in ("circles", "sliders", "spinner", "mixed"):
            self.select_pattern(pattern)
            for index in range(self.window.preview_aspect.count()):
                self.window.preview_aspect.setCurrentIndex(index)
                ratio = self.window.preview_aspect.currentData()
                rect, _, _ = self.preview._scene_geometry()
                mania_rect = self.window.mania_preview._geometry()[0]
                self.assertAlmostEqual(rect.width()/rect.height(), ratio)
                self.assertAlmostEqual(mania_rect.width()/mania_rect.height(), ratio)
        scene, assets = self.preview._scene, self.preview._scene_assets
        self.preview.t = 6300
        self.window._detach_preview()
        APP.processEvents()
        self.assertIs(self.preview._scene, scene)
        self.assertIs(self.preview._scene_assets, assets)
        self.assertEqual(self.preview.t, 6300)
        rect, _, _ = self.preview._scene_geometry()
        self.assertAlmostEqual(rect.width()/rect.height(), self.window.preview_aspect.currentData())
        self.window._restore_preview()
        self.assertIs(self.preview._scene, scene)
        self.assertEqual(self.preview.t, 6300)
        self.assertEqual(self.file_hashes(), self.files)

    def test_both_languages_localize_controls_and_actual_painted_std_labels(self):
        painted = []

        class RecordingPainter(QPainter):
            def drawText(self, *args):
                if args and isinstance(args[-1], str):
                    painted.append(args[-1])
                return super().drawText(*args)

        for action, language in ((self.window.act_lang_en, "en-US"),
                                 (self.window.act_lang_zh, "zh-CN")):
            action.trigger()
            self.assertEqual(i18n.lang(), language)
            for index, mode in enumerate(("auto", "inspect")):
                self.assertEqual(self.controls.mode.itemText(index), i18n.t("std_scene.mode_"+mode))
            for index, pattern in enumerate(("mixed", "circles", "sliders", "spinner")):
                self.assertEqual(self.controls.pattern.itemText(index), i18n.t("std_scene.pattern_"+pattern))
            self.assertEqual(self.controls.hint.text(), i18n.t("std_scene.help_auto"))
            painted.clear()
            canvas = QImage(self.preview.width(), self.preview.height(), QImage.Format_ARGB32)
            painter = RecordingPainter(canvas)
            try:
                self.preview._paint_scene(painter)
            finally:
                painter.end()
            self.assertTrue(any(i18n.t("std_test.auto") in text for text in painted))
            self.assertTrue(any(i18n.t("std_test.fallback") in text for text in painted))
            self.assertFalse(any("std_test." in text or "std_scene." in text for text in painted))
            self.assertNotEqual(i18n.t("std_test.auto"), "std_test.auto")
            self.assertNotEqual(i18n.t("std_test.fallback"), "std_test.fallback")


if __name__ == "__main__":
    unittest.main()
