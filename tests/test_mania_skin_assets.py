import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from configparser import ConfigParser
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from ui.preview.mania_skin_assets import ManiaSkinAssets, default_column_suffixes, note_body_style


APP = QApplication.instance() or QApplication([])


class ManiaSkinAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def png(self, name, colour="white", size=(20, 10)):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        image = QImage(*size, QImage.Format_ARGB32)
        image.fill(QColor(colour))
        self.assertTrue(image.save(str(target)))
        return target

    @staticmethod
    def colour(sprite, elapsed=0, loop=True):
        return sprite.frame_at(elapsed, loop).toImage().pixelColor(0, 0).name()

    def test_each_default_column_loads_its_note_head_body_tail_and_pressed_key(self):
        colours = {"1": "red", "2": "green", "S": "blue"}
        for suffix, colour in colours.items():
            for part in ("", "H", "L", "T"):
                self.png(f"mania-note{suffix}{part}.png", colour)
            for state in ("", "D"):
                self.png(f"mania-key{suffix}{state}.png", colour)
        assets = ManiaSkinAssets(self.root, keys=7)
        self.assertEqual(assets.column_suffixes, ("1", "2", "1", "S", "1", "2", "1"))
        for collection in (assets.notes, assets.hold_heads, assets.hold_bodies,
                           assets.hold_tails, assets.keys_up, assets.keys_down):
            self.assertEqual([self.colour(sprite) for sprite in collection],
                             [QColor(colours[suffix]).name() for suffix in assets.column_suffixes])
        self.assertEqual(default_column_suffixes(4), ("1", "2", "2", "1"))

    def test_column_customisations_are_independent_and_case_insensitive(self):
        settings = {}
        for column in range(4):
            colour = QColor.fromHsv(column * 70, 255, 255).name()
            for field, ending in (("NoteImage", ""), ("NoteImage", "H"),
                                  ("NoteImage", "L"), ("NoteImage", "T"),
                                  ("KeyImage", ""), ("KeyImage", "D")):
                path = f"Custom/Column{column}/{field}{ending}"
                self.png(path + "@2x.PNG", colour, (40, 20))
                settings[f"{field}{column}{ending}"] = path.lower().replace("/", "\\")
        assets = ManiaSkinAssets(self.root, settings, keys=4)
        for column in range(4):
            for collection in (assets.notes, assets.hold_heads, assets.hold_bodies,
                               assets.hold_tails, assets.keys_up, assets.keys_down):
                sprite = collection[column]
                self.assertEqual(self.colour(sprite), QColor.fromHsv(column * 70, 255, 255).name())
                self.assertEqual(sprite.logical_size(), (20, 10))
                self.assertEqual(sprite.density, 2)
                self.assertEqual(sprite.frame_at().devicePixelRatio(), 1)

    def test_fallback_preserves_an_explicit_transparent_head_or_key(self):
        self.png("mania-note1.png", "red")
        self.png("empty.png", "transparent")
        assets = ManiaSkinAssets(self.root, {"NoteImage0H": "empty", "KeyImage0": "empty"})
        self.assertIs(assets.hold_tails[0], assets.hold_heads[0])
        self.assertIs(assets.keys_down[0], assets.keys_up[0])
        self.assertEqual(assets.hold_heads[0].frame_at().toImage().pixelColor(0, 0).alpha(), 0)
        self.assertIs(assets.hold_heads[3], assets.notes[3])
        self.assertIs(assets.hold_tails[3], assets.notes[3])
        self.assertIn("noteimage0t", assets.missing)

    def test_judgement_custom_paths_and_default_names_cover_all_six_results(self):
        settings = {}
        for number, kind in enumerate(("300g", "300", "200", "100", "50", "0")):
            name = f"judgements/custom-{kind}" if number % 2 else f"mania-hit{kind}"
            self.png(name + ".png", QColor.fromHsv(number * 40, 255, 255).name())
            if number % 2:
                settings[f"Hit{kind}"] = name
        assets = ManiaSkinAssets(self.root, settings)
        self.assertEqual(set(assets.judgements), {"300g", "300", "200", "100", "50", "0"})
        self.assertEqual(len({self.colour(sprite) for sprite in assets.judgements.values()}), 6)

    def test_animation_precedes_static_and_uses_each_frames_density(self):
        self.png("mania-note1.png", "blue")
        self.png("mania-note1-0.png", "red")
        self.png("mania-note1-0@2x.png", "green", (40, 20))
        self.png("mania-note1-1.png", "yellow")
        assets = ManiaSkinAssets(self.root)
        sprite = assets.notes[0]
        self.assertEqual(len(sprite.frames), 2)
        self.assertEqual(self.colour(sprite, 0), QColor("green").name())
        self.assertEqual(self.colour(sprite, 17), QColor("yellow").name())
        self.assertEqual(sprite.density_at(0), 2)
        self.assertEqual(sprite.density_at(17), 1)
        self.assertEqual(sprite.logical_size(0), sprite.logical_size(17))
        self.assertEqual(self.colour(sprite, 34), QColor("green").name())
        self.assertEqual(self.colour(sprite, 1000, False), QColor("yellow").name())
        self.assertEqual(self.colour(sprite, -100), QColor("green").name())

    def test_animation_stops_at_gap_and_never_aliases_padded_frame_names(self):
        self.png("mania-note1-0.png", "red")
        self.png("mania-note1-00.png", "blue")
        self.png("mania-note1-01.png", "yellow")
        self.png("mania-note1-2.png", "green")
        sprite = ManiaSkinAssets(self.root).notes[0]
        self.assertEqual(len(sprite.frames), 1)
        self.assertEqual(self.colour(sprite), QColor("red").name())

    def test_missing_frame_zero_falls_back_static_and_keys_remain_static(self):
        self.png("mania-note1.png", "red")
        self.png("mania-note1-1.png", "green")
        self.png("mania-key1.png", "yellow")
        self.png("mania-key1-0.png", "blue")
        assets = ManiaSkinAssets(self.root)
        self.assertEqual(self.colour(assets.notes[0]), QColor("red").name())
        self.assertEqual(self.colour(assets.keys_up[0]), QColor("yellow").name())

    def test_explicit_png_and_hd_suffix_are_normalised_once(self):
        self.png("Notes/Blue@2x.PNG", "blue", (40, 20))
        assets = ManiaSkinAssets(self.root, {"NoteImage0": "notes\\blue@2X.PNG"})
        self.assertEqual(assets.notes[0].logical_size(), (20, 10))
        self.assertEqual(assets.notes[0].density, 2)
        self.assertIs(assets.load("NOTES/BLUE"), assets.notes[0])

    def test_font_prefix_and_overlap_use_fonts_section_not_score_prefix(self):
        config = ConfigParser()
        config.optionxform = str
        config.read_string("[fOnTs]\nComboPrefix: FONT\\Combo\nComboOverlap: 4\nScorePrefix: other\n")
        for digit in range(10):
            self.png(f"font/combo-{digit}@2x.png", "red", (20 + digit * 2, 40))
        assets = ManiaSkinAssets(self.root, config=config)
        self.assertEqual(assets.combo_overlap, 4)
        self.assertEqual([sprite.logical_size()[0] for sprite in assets.digits], list(range(10, 20)))
        self.assertTrue(all(sprite.density == 2 for sprite in assets.digits))

    def test_font_defaults_to_score_and_combobursts_are_independent_images(self):
        for digit in range(10):
            self.png(f"score-{digit}.png")
        self.png("comboburst-mania-0.png", "red")
        self.png("comboburst-mania-1@2x.png", "blue", (40, 20))
        assets = ManiaSkinAssets(self.root)
        self.assertTrue(all(assets.digits))
        self.assertEqual(assets.combo_overlap, 0)
        self.assertEqual([self.colour(sprite) for sprite in assets.combo_bursts],
                         [QColor("red").name(), QColor("blue").name()])
        self.assertTrue(all(not sprite.animated for sprite in assets.combo_bursts))

    def test_fresh_asset_collection_reflects_external_image_edits(self):
        self.png("mania-note1.png", "red")
        before = ManiaSkinAssets(self.root)
        self.png("mania-note1.png", "blue")
        after = ManiaSkinAssets(self.root)
        self.assertEqual(self.colour(before.notes[0]), QColor("red").name())
        self.assertEqual(self.colour(after.notes[0]), QColor("blue").name())

    def test_invalid_hd_falls_back_sd_and_missing_custom_asset_is_identified(self):
        (self.root / "mania-note1@2x.png").write_bytes(b"broken png")
        self.png("mania-note1.png", "red")
        assets = ManiaSkinAssets(self.root, {"NoteImage1": "custom/not-here"})
        self.assertEqual(assets.notes[0].density, 1)
        self.assertIsNone(assets.notes[1])
        self.assertEqual(assets.missing["noteimage1"], "custom/not-here")

    def test_capoo_style_tiff_renamed_png_is_read_by_content_and_keeps_full_body(self):
        # Actual Capoo uses a 40000-high TIFF under its .png filename. Use a
        # narrower generated sample to exercise that format without user art.
        image = QImage(2, 40000, QImage.Format_ARGB32)
        image.fill(QColor("magenta"))
        self.assertTrue(image.save(str(self.root / "long-body.png"), "TIFF"))
        assets = ManiaSkinAssets(self.root, {"NoteImage0L": "long-body"})
        self.assertEqual(assets.hold_bodies[0].logical_size(), (2, 40000))
        self.assertEqual(self.colour(assets.hold_bodies[0]), QColor("magenta").name())
        self.assertFalse(assets.warnings)

    def test_path_traversal_absolute_and_alternate_stream_paths_are_rejected(self):
        self.png("valid.png")
        assets = ManiaSkinAssets(self.root)
        for name in ("../valid", "sub/../valid", "/valid", "C:/valid", "C:valid",
                     "\\\\server\\valid", "valid:stream", "./valid", "sub//valid", "bad\0name"):
            with self.subTest(name=name):
                self.assertIsNone(assets.load(name))
        self.assertIsNotNone(assets.load("valid"))

    def test_animation_and_memory_limits_stop_loading_without_losing_prior_frames(self):
        for frame in range(4):
            self.png(f"mania-note1-{frame}.png", "red")
        with patch("ui.preview.mania_skin_assets.MAX_ANIMATION_FRAMES", 2):
            limited = ManiaSkinAssets(self.root)
        self.assertEqual(len(limited.notes[0].frames), 2)
        with patch("ui.preview.mania_skin_assets.MAX_DECODED_BYTES", 20 * 10 * 4):
            limited = ManiaSkinAssets(self.root)
        self.assertEqual(len(limited.notes[0].frames), 1)
        self.assertTrue(limited.warnings)

    def test_layout_metadata_is_finite_and_preserves_zero_positions(self):
        settings = {"ScorePosition": "0", "ComboPosition": "nan", "NoteBodyStyle": "2",
                    "NoteBodyStyle1": "0", "NoteFlipWhenUpsideDownT": "0",
                    "NoteFlipWhenUpsideDown1T": "1"}
        assets = ManiaSkinAssets(self.root, settings, {"General": {"Version": "latest"}})
        self.assertEqual(assets.score_position, 0)
        self.assertEqual(assets.combo_position, 111)
        self.assertEqual(assets.body_styles, [2, 0, 2, 2])
        self.assertEqual(assets.tail_flips, [False, True, False, False])
        self.assertEqual(assets.hud_scale, 0.625)
        defaults = ManiaSkinAssets(self.root, config={"General": {"Version": "2.7"}})
        self.assertEqual(defaults.body_styles, [3] * 4)
        self.assertEqual(defaults.tail_flips, [True] * 4)
        old = ManiaSkinAssets(self.root)
        self.assertEqual(old.body_styles, [0] * 4)
        self.assertEqual(old.tail_flips, [False] * 4)

    def test_named_body_styles_match_legacy_numeric_values(self):
        for name, value in (("Stretch", 0), ("Repeat", 1), ("RepeatTop", 2),
                            ("RepeatBottom", 3), ("RepeatTopAndBottom", 4)):
            with self.subTest(name=name):
                self.assertEqual(note_body_style(name), value)
                self.assertEqual(note_body_style(name.swapcase()), value)
                self.assertEqual(note_body_style(str(value)), value)
                self.assertEqual(note_body_style(f" {value}.0 "), value)

    def test_invalid_body_style_does_not_silently_clamp_or_truncate(self):
        for value in (None, "", "nonsense", "1.7", "-1", "5", "nan", "inf"):
            with self.subTest(value=value):
                self.assertEqual(note_body_style(value, 2), 2)

    def test_per_column_named_styles_override_global_and_invalid_values_inherit(self):
        settings = {"NoteBodyStyle": "RepeatTop", "NoteBodyStyle0": "Stretch",
                    "NoteBodyStyle1": "RepeatBottom", "NoteBodyStyle2": "garbage"}
        assets = ManiaSkinAssets(self.root, settings, {"General": {"Version": "2.7"}})
        self.assertEqual(assets.body_styles, [0, 3, 2, 2])

    def test_all_direction_flags_have_version_appropriate_defaults(self):
        for version, default in (("2.4", False), ("2.5", True), ("latest", True)):
            with self.subTest(version=version):
                assets = ManiaSkinAssets(self.root, config={"General": {"Version": version}})
                for name in ("note_flips", "head_flips", "body_flips", "tail_flips",
                             "key_flips", "key_down_flips"):
                    self.assertEqual(getattr(assets, name), [default] * 4)

    def test_note_direction_flags_apply_global_part_and_exact_column_precedence(self):
        settings = {"NoteFlipWhenUpsideDown": "0", "NoteFlipWhenUpsideDown0": "1",
                    "NoteFlipWhenUpsideDown1H": "1", "NoteFlipWhenUpsideDown2L": "1",
                    "NoteFlipWhenUpsideDownT": "1", "NoteFlipWhenUpsideDown3T": "0"}
        assets = ManiaSkinAssets(self.root, settings, {"General": {"Version": "2.7"}})
        self.assertEqual(assets.note_flips, [True, False, False, False])
        self.assertEqual(assets.head_flips, [False, True, False, False])
        self.assertEqual(assets.body_flips, [False, False, True, False])
        self.assertEqual(assets.tail_flips, [True, True, True, False])
        self.assertEqual(assets.key_flips, [True] * 4)

    def test_key_up_and_down_flags_are_independent_and_inherit_global(self):
        settings = {"KeyFlipWhenUpsideDown": "0", "KeyFlipWhenUpsideDown0": "1",
                    "KeyFlipWhenUpsideDown1D": "1", "KeyFlipWhenUpsideDown2D": "bad"}
        assets = ManiaSkinAssets(self.root, settings, {"General": {"Version": "2.7"}})
        self.assertEqual(assets.key_flips, [True, False, False, False])
        self.assertEqual(assets.key_down_flips, [False, True, False, False])
        self.assertEqual(assets.note_flips, [True] * 4)

    def test_invalid_direction_override_preserves_inherited_value(self):
        settings = {"NoteFlipWhenUpsideDown": "1", "NoteFlipWhenUpsideDown0H": "invalid",
                    "NoteFlipWhenUpsideDown1T": "", "KeyFlipWhenUpsideDown1": "?"}
        assets = ManiaSkinAssets(self.root, settings, {"General": {"Version": "2.7"}})
        self.assertTrue(assets.head_flips[0])
        self.assertTrue(assets.tail_flips[1])
        self.assertTrue(assets.key_flips[1])

    def test_direction_metadata_is_independent_of_current_scroll_direction(self):
        # The tail has an effective direction opposite to notes. Callers apply
        # its flag in downscroll, and apply ordinary flags in upscroll.
        down = ManiaSkinAssets(self.root, {"UpsideDown": "0"}, {"General": {"Version": "2.7"}})
        up = ManiaSkinAssets(self.root, {"UpsideDown": "1"}, {"General": {"Version": "2.7"}})
        self.assertEqual(down.note_flips, up.note_flips)
        self.assertEqual(down.tail_flips, up.tail_flips)

    def test_hold_and_break_colours_preserve_rgba_and_default_gold_red(self):
        assets = ManiaSkinAssets(self.root, {"ColourHold": "246,171,172,210", "ColourBreak": "12,34,56"})
        self.assertEqual(assets.hold_colour.getRgb(), (246, 171, 172, 210))
        self.assertEqual(assets.break_colour.getRgb(), (12, 34, 56, 255))
        for value in (None, "", "red", "256,0,0", "1,2,3,4,5", "1.5,2,3"):
            with self.subTest(value=value):
                defaults = ManiaSkinAssets(self.root, {"ColourHold": value, "ColourBreak": value})
                self.assertEqual(defaults.hold_colour.getRgb(), (255, 191, 51, 255))
                self.assertEqual(defaults.break_colour.getRgb(), (255, 0, 0, 255))

    def test_default_layouts_cover_one_to_eighteen_keys_and_split_stages(self):
        for keys in range(1, 19):
            self.assertEqual(len(default_column_suffixes(keys)), keys)
        self.assertEqual(default_column_suffixes(14), default_column_suffixes(7) * 2)
        self.assertEqual(default_column_suffixes(8, split_stages=True), default_column_suffixes(4) * 2)
        self.assertEqual(default_column_suffixes(8, 1), ("S", "1", "2", "1", "2", "1", "2", "1"))
        self.assertEqual(default_column_suffixes(8, 2), ("1", "2", "1", "2", "1", "2", "1", "S"))
        self.assertEqual(default_column_suffixes(4, 1), default_column_suffixes(4))

    def test_light_frame_rate_is_used_only_for_stage_light(self):
        for name in ("mania-stage-light", "lightingN", "lightingL"):
            for index, colour in enumerate(("red", "blue")):
                self.png(f"{name}-{index}.png", colour)
        assets = ManiaSkinAssets(self.root, {"LightFramePerSecond": "10"})
        self.assertEqual(self.colour(assets.stage_light, 50), QColor("red").name())
        self.assertEqual(self.colour(assets.stage_light, 100), QColor("blue").name())
        self.assertEqual(self.colour(assets.lighting_n, 17), QColor("blue").name())


if __name__ == "__main__":
    unittest.main()
