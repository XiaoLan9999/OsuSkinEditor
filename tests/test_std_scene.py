import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from PySide6.QtCore import Qt, QCoreApplication, QEvent, QPointF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from core.skin_loader import SkinLoader
from ui.preview.std_preview import StdPreview
from ui.preview.std_scene import make_scene
from ui.preview.std_skin_assets import StdSkinAssets


APP = QApplication.instance() or QApplication([])


class StdSceneTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.preview = StdPreview()
        self.preview.resize(1000, 640)
        self.preview.set_playing(False)
        self.load()
        self.preview.set_test_mode("auto")

    def tearDown(self):
        self.preview.close()
        self.preview.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def png(self, name, colour, size=(128, 128)):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        image = QImage(*size, QImage.Format_ARGB32)
        image.fill(QColor(colour))
        image.save(str(path))

    def load(self, general="", colours="", fonts=""):
        (self.root / "skin.ini").write_text("[General]\nVersion: 2.7\n"+general+
                                           "[Colours]\nCombo1: 80,160,240\n"+colours+
                                           "[Fonts]\n"+fonts, encoding="utf-8")
        self.preview.set_skin(SkinLoader().load(str(self.root)))

    def point(self, x, y):
        rect, scale, width = self.preview._scene_geometry()
        bounds = self.preview._scene.viewport_bounds(width)
        return int(rect.left()+(x-bounds.left())*scale), int(rect.top()+(y-bounds.top())*scale)

    def object_point(self, obj):
        _, _, width = self.preview._scene_geometry()
        return self.point((width-512)/2+obj.x, 48+obj.y)

    def test_scene_contains_continuous_numbered_circles_slider_and_spinner(self):
        scene = self.preview._scene
        self.assertEqual({o.kind for o in scene.objects}, {"circle", "slider", "spinner"})
        self.assertEqual([o.number for o in scene.objects[:4]], [1, 2, 3, 4])
        self.assertEqual({o.result for o in scene.objects}, {"300", "100", "50", "0"})
        self.assertGreaterEqual(len(scene.visible(1700, 1200)), 3)
        for pattern, kind in (("circles", "circle"), ("sliders", "slider"), ("spinner", "spinner")):
            self.preview.set_test_pattern(pattern)
            self.assertEqual({o.kind for o in self.preview._scene.objects}, {kind})
        self.assertEqual(StdPreview().test_mode, "inspect")

    def test_viewport_aspect_reserves_full_frame_and_all_elements_share_scale(self):
        for ratio in (4/3, 16/10, 16/9):
            self.preview.set_viewport_aspect(ratio)
            rect, scale, width = self.preview._scene_geometry()
            self.assertAlmostEqual(width/480, ratio)
            self.assertAlmostEqual(rect.width()/rect.height(), ratio)
            self.assertAlmostEqual(rect.height()/480, scale)
            self.assertTrue(self.preview.rect().contains(rect.toAlignedRect()))
        self.preview.set_preview_scale(4)
        self.assertTrue(self.preview.rect().contains(self.preview._scene_geometry()[0].toAlignedRect()))

    def test_custom_hd_circle_number_and_overlay_order_render_real_pixels(self):
        self.png("hitcircle.png", "white")
        self.png("hitcircleoverlay.png", "red")
        self.png("approachcircle.png", Qt.transparent)
        self.png("fonts/digit-1@2x.png", "#00ff00", (80, 100))
        for rule, expected in ((0, "#00ff00"), (1, "#ff0000")):
            self.load(general=f"HitCircleOverlayAboveNumber: {rule}\n", fonts="HitCirclePrefix: fonts/digit\n")
            self.preview.t = 900
            point = self.object_point(self.preview._scene.objects[0])
            self.assertEqual(self.preview.grab().toImage().pixelColor(*point).name(), expected)
            self.assertEqual(self.preview._scene_assets.fonts["hitcircle"]["1"].logical_size(), (40, 50))

    def test_cs_uses_native_padding_and_ar_changes_preempt_without_mutating_files(self):
        self.png("hitcircle.png", "white")
        self.png("approachcircle.png", Qt.transparent)
        self.load()
        before = {p.name: sha256(p.read_bytes()).digest() for p in self.root.iterdir()}
        self.preview.set_circle_size(10)
        self.preview.set_approach_rate(10)
        self.assertEqual(self.preview.approach_preempt, 450)
        self.preview.set_approach_rate(0)
        self.assertEqual(self.preview.approach_preempt, 1800)
        self.preview.set_circle_size(4)
        self.preview.t = 900
        image = self.preview.grab().toImage()
        x, y = self.object_point(self.preview._scene.objects[0])
        _, scale, _ = self.preview._scene_geometry()
        # Native 128px artwork scales to 72.96 logical units at CS4. Do not
        # derive this from a skin image's alpha bounds or another layer's size.
        self.assertEqual(image.pixelColor(int(x+30*scale), y).name(), "#50a0f0")
        self.assertNotEqual(image.pixelColor(int(x+39*scale), y).name(), "#50a0f0")
        after = {p.name: sha256(p.read_bytes()).digest() for p in self.root.iterdir()}
        self.assertEqual(before, after)

    def test_animated_slider_ball_hd_and_tint_option(self):
        self.png("sliderb0@2x.png", "white", (80, 80))
        self.png("sliderb1@2x.png", "#00ffff", (80, 80))
        self.png("sliderfollowcircle.png", Qt.transparent)
        self.load(general="AnimationFramerate: 10\nAllowSliderBallTint: 0\n")
        self.preview.set_test_pattern("sliders")
        obj = self.preview._scene.objects[0]
        self.preview.t = obj.start+100
        _, _, width = self.preview._scene_geometry()
        point = self.preview._scene.cursor_position(self.preview.t, QPointF((width-512)/2, 48))
        # Prevent the fallback cursor from touching the exact ball centre.
        self.png("cursor.png", Qt.transparent)
        self.load(general="AnimationFramerate: 10\nAllowSliderBallTint: 0\n")
        self.preview.t = obj.start+100
        self.assertEqual(self.preview.grab().toImage().pixelColor(*self.point(point.x(), point.y())).name(), "#00ffff")
        self.load(general="AnimationFramerate: 10\nAllowSliderBallTint: 1\n")
        self.preview.t = obj.start+100
        self.assertEqual(self.preview.grab().toImage().pixelColor(*self.point(point.x(), point.y())).name(), "#00a0f0")

    def test_slider_track_and_border_use_configured_colours(self):
        self.load(colours="SliderTrackOverride: 20,40,60\nSliderBorder: 240,20,30\n")
        self.preview.set_test_pattern("sliders")
        obj = self.preview._scene.objects[0]
        self.preview.t = obj.start-100
        _, _, width = self.preview._scene_geometry()
        path = self.preview._scene.slider_path(obj, QPointF((width-512)/2, 48))
        middle = path.pointAtPercent(.5)
        image = self.preview.grab().toImage()
        self.assertEqual(image.pixelColor(*self.point(middle.x(), middle.y())).name(), "#14283c")

    def test_spinner_uses_layered_new_art_and_old_version_art(self):
        for name, color, size in (("spinner-bottom", "blue", 240), ("spinner-top", "green", 180),
                                  ("spinner-middle2", "red", 100), ("spinner-middle", "yellow", 40)):
            self.png(name+".png", color, (size, size))
        self.load()
        self.preview.set_test_pattern("spinner")
        self.preview.t = 2000
        _, _, width = self.preview._scene_geometry()
        self.assertEqual(self.preview.grab().toImage().pixelColor(*self.point(width/2, 240)).name(), "#ffff00")
        self.png("spinner-circle.png", "#ff00ff", (150, 150))
        self.load(general="Version: 1.0\n")
        self.preview.t = 2000
        self.assertEqual(self.preview.grab().toImage().pixelColor(*self.point(width/2, 240)).name(), "#ff00ff")

    def test_manual_judgements_are_real_assets_and_do_not_change_score_or_time(self):
        colours = {"300": "#ff0000", "100": "#00ff00", "50": "#0000ff", "0": "#ffff00"}
        for kind, color in colours.items():
            self.png("hit"+kind+".png", color, (80, 32))
        self.load()
        self.preview.t = 600
        before = self.preview._scene.stats(self.preview.t)
        _, _, width = self.preview._scene_geometry()
        for kind, expected in colours.items():
            self.preview.show_judgement(kind)
            self.assertEqual(self.preview.grab().toImage().pixelColor(*self.point(width/2, 225)).name(), expected)
            self.assertEqual(self.preview._scene.stats(self.preview.t), before)
            self.assertEqual(self.preview.t, 600)

    def test_font_prefix_hd_combo_digits_survive_missing_x_symbol(self):
        for digit in range(10):
            self.png(f"numbers/count-{digit}@2x.png", "#ff3300", (32, 48))
        self.load(fonts="ScorePrefix: numbers/count\nComboPrefix: numbers/count\n")
        self.preview.t = 1100
        image = self.preview.grab().toImage()
        self.assertEqual(image.pixelColor(*self.point(20, 451)).name(), "#ff3300")
        self.assertEqual(self.preview._scene_assets.fonts["score"]["3"].logical_size(), (16, 24))

    def test_comboburst_remains_inside_virtual_window_beside_playfield(self):
        self.png("comboburst-0.png", "#5511cc", (400, 600))
        self.load()
        for ratio in (4/3, 16/10, 16/9):
            self.preview.set_viewport_aspect(ratio)
            self.preview.show_comboburst()
            self.assertEqual(self.preview.grab().toImage().pixelColor(*self.point(20, 455)).name(), "#5511cc")

    def test_missing_images_report_fallback_and_explicit_transparent_sprite_is_valid(self):
        self.png("hitcircle.png", Qt.transparent, (1, 1))
        self.load()
        self.assertNotIn("hitcircle", self.preview._scene_assets.missing)
        self.assertIn("sliderb", self.preview._scene_assets.missing)
        self.assertFalse(self.preview.grab().isNull())

    def test_oversized_burst_scales_entire_scene_without_squeezing_character(self):
        self.png("comboburst.png", "#5511cc", (600, 1400))
        self.png("hitcircle.png", "#00ff00")
        self.png("approachcircle.png", Qt.transparent)
        self.load(colours="Combo1: 255,255,255\n")
        self.preview.t = 900
        rect, scale, width = self.preview._scene_geometry()
        bounds = self.preview._scene.viewport_bounds(width)
        self.assertEqual(bounds.top(), 480-1400*.625)
        self.assertEqual(bounds.height(), 1400*.625)
        # Both the full character and a native CS4 circle use the same stage
        # scale; the character's aspect ratio cannot depend on side margin.
        self.preview.show_comboburst()
        image = self.preview.grab().toImage()
        self.assertEqual(image.pixelColor(*self.point(300, -250)).name(), "#5511cc")
        sprite = self.preview._scene_assets.combo_bursts[0]
        character = self.preview._scene.sprite_rect(sprite, 0, 0, .625)
        circle = self.preview._scene.sprite_rect(self.preview._scene_assets.images["hitcircle"], 0, 0, .57)
        self.assertAlmostEqual(character.width()/circle.width(), (600*.625)/(128*.57))
        self.assertTrue(self.preview.rect().contains(rect.toAlignedRect()))

    def test_health_uses_native_fill_crop_marker_priority_and_real_accuracy_digits(self):
        self.png("scorebar-bg.png", "#333333", (400, 60))
        self.png("scorebar-colour-0.png", "#00ff00", (200, 20))
        self.png("scorebar-marker.png", "#ff0000", (10, 10))
        self.png("scorebar-ki.png", "#0000ff", (30, 30))
        self.load()
        image = self.preview.grab().toImage()
        self.assertEqual(image.pixelColor(*self.point(20, 12)).name(), "#00ff00")
        # 80% of200SD at.625 =100; native fill begins at(7.5,7.8).
        self.assertEqual(image.pixelColor(*self.point(120, 12)).name(), "#333333")
        marker = image.pixelColor(*self.point(107.5, 14))
        self.assertEqual(marker.red(), 255)
        self.assertGreaterEqual(marker.green(), 51)  # Marker blends additively.
        self.assertLess(marker.blue(), 100)  # The blue scorebar-ki was not used.

    def test_health_background_visible_overflow_expands_global_bounds(self):
        self.png("scorebar-bg.png", "#884422", (1800, 20))
        self.load()
        _, _, width = self.preview._scene_geometry()
        self.assertEqual(self.preview._scene.viewport_bounds(width).right(), 1800*.625)

    def test_score_digit_cells_use_five_width_but_keep_each_texture_native_size(self):
        self.png("score-5.png", "white", (40, 50))
        self.png("score-1.png", "#00ff00", (10, 50))
        self.load()
        image = QImage(180, 100, QImage.Format_ARGB32)
        image.fill(Qt.black)
        painter = QPainter(image)
        self.preview._scene.number(painter, "111", "score", 120, 40, .6, align="right", fixed_width=True)
        painter.end()
        runs = []
        started = None
        for x in range(image.width()):
            green = image.pixelColor(x, 40).green() > 240
            if green and started is None:
                started = x
            elif not green and started is not None:
                runs.append((started, x))
                started = None
        self.assertEqual([b-a for a, b in runs], [6, 6, 6])
        self.assertEqual([runs[i+1][0]-runs[i][0] for i in range(2)], [24, 24])

    def test_score_and_accuracy_use_shared_prefix_at_distinct_official_scales(self):
        for digit in range(10):
            self.png(f"score-{digit}.png", "#00ff00", (40, 50))
        self.load()
        image = self.preview.grab().toImage()
        _, _, width = self.preview._scene_geometry()
        # Source50px high -> score30 and accuracy18 logical pixels. Accuracy
        # starts below the score plus9/1.6, keeping authored transparent padding.
        self.assertEqual(image.pixelColor(*self.point(width-20, 28)).name(), "#00ff00")
        self.assertNotEqual(image.pixelColor(*self.point(width-20, 32)).name(), "#00ff00")
        self.assertEqual(image.pixelColor(*self.point(width-25, 40)).name(), "#00ff00")
        self.assertNotEqual(image.pixelColor(*self.point(width-25, 57)).name(), "#00ff00")

    def test_asset_reload_ignores_stale_pixmap_cache_and_rejects_escaping_prefix(self):
        self.png("hitcircle.png", "red")
        self.load()
        info = (self.root / "hitcircle.png").stat()
        self.png("hitcircle.png", "blue")
        os.utime(self.root / "hitcircle.png", ns=(info.st_atime_ns, info.st_mtime_ns))
        self.load(fonts="HitCirclePrefix: ../outside\n")
        self.assertEqual(self.preview._scene_assets.images["hitcircle"].frame_at().toImage().pixelColor(0, 0), QColor("blue"))
        self.assertIsNone(self.preview._scene_assets.load("../outside"))

    def test_elapsed_clock_respects_pause_hide_and_restart_settings(self):
        self.preview.show()
        APP.processEvents()
        self.preview.set_playing(True)
        self.preview.restart_demo()
        time.sleep(.025)
        self.preview.tick()
        self.assertGreaterEqual(self.preview.t, 20)
        self.preview.set_playing(False)
        frozen = self.preview.t
        time.sleep(.020)
        self.preview.tick()
        self.assertEqual(self.preview.t, frozen)
        self.preview.set_playing(True)
        self.preview.hide()
        time.sleep(.020)
        self.preview.show()
        self.preview.tick()
        self.assertLess(self.preview.t-frozen, 20)
        self.preview.set_circle_size(6)
        self.preview.set_test_pattern("sliders")
        self.preview.restart_demo()
        self.assertEqual(self.preview.circle_size, 6)
        self.assertEqual(self.preview.test_pattern, "sliders")
        self.assertEqual(self.preview.t, 0)


if __name__ == "__main__":
    unittest.main()
