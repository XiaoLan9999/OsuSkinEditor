"""Pixel/geometry regression fixtures for legacy Mania long-note artwork

These synthetic textures contain no third-party skin artwork
References:
https://osu.ppy.sh/community/forums/topics/341098 (osu!dev: repeated bottom row)
ppy/osu LegacyNotePiece.cs / LegacyHoldNoteTailPiece.cs (note sizing/direction)
ppy/osu DrawableHoldNote.cs (early-hit movement and held-note masking)
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from core.mania_gameplay import PlayNote
from core.skin_loader import SkinLoader
from ui.preview.mania_preview import ManiaPreview
from ui.preview.mania_skin_assets import Sprite


APP = QApplication.instance() or QApplication([])


class ManiaLongNoteFidelityTests(unittest.TestCase):
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

    @staticmethod
    def image(width, height, colour=Qt.transparent):
        image = QImage(width, height, QImage.Format_ARGB32)
        image.fill(QColor(colour))
        return image

    @staticmethod
    def sprite(image):
        return Sprite((QPixmap.fromImage(image),), (1,))

    def save(self, name, image):
        image.save(str(self.root / f"{name}.png"))

    def stripes(self, width=20, height=20):
        image = self.image(width, height)
        painter = QPainter(image)
        painter.fillRect(0, 0, width, int(height*.3), QColor("red"))
        painter.fillRect(0, int(height*.3), width, int(height*.4), QColor("green"))
        painter.fillRect(0, int(height*.7), width, height-int(height*.7), QColor("blue"))
        painter.end()
        return image

    def draw_body(self, source, style, rect=QRectF(10, 10, 20, 60), clip=None):
        output = self.image(120, 260)
        painter = QPainter(output)
        if clip is not None:
            painter.setClipRect(clip)
        self.preview._draw_hold_body(painter, self.sprite(source), rect, style)
        painter.end()
        return output

    def assert_colour(self, image, x, y, expected):
        self.assertEqual(image.pixelColor(int(x), int(y)), QColor(expected), (x, y))

    def test_repeat_bottom_keeps_original_top_then_extends_bottom_row(self):
        output = self.draw_body(self.stripes(), 3)
        for y, colour in ((12, "red"), (20, "green"), (28, "blue"),
                          (32, "blue"), (40, "blue"), (68, "blue")):
            self.assert_colour(output, 20, y, colour)
        self.assertEqual(output.pixelColor(20, 9).alpha(), 0)
        self.assertEqual(output.pixelColor(20, 70).alpha(), 0)

    def test_repeat_top_keeps_original_bottom_and_extends_top_row(self):
        output = self.draw_body(self.stripes(), 2)
        for y, colour in ((12, "red"), (28, "red"), (48, "red"),
                          (52, "red"), (60, "green"), (68, "blue")):
            self.assert_colour(output, 20, y, colour)

    def test_repeat_tiles_the_entire_texture_without_stretching(self):
        output = self.draw_body(self.stripes(), 1)
        for base in (10, 30, 50):
            for offset, colour in ((2, "red"), (10, "green"), (18, "blue")):
                self.assert_colour(output, 20, base+offset, colour)

    def test_repeat_bottom_and_top_centres_texture_and_extends_both_edges(self):
        output = self.draw_body(self.stripes(), 4)
        for y, colour in ((12, "red"), (28, "red"), (32, "red"),
                          (40, "green"), (48, "blue"), (52, "blue"), (68, "blue")):
            self.assert_colour(output, 20, y, colour)

    def test_stretch_maps_whole_texture_onto_full_body(self):
        output = self.draw_body(self.stripes(), 0)
        for y, colour in ((12, "red"), (26, "red"), (32, "green"),
                          (48, "green"), (54, "blue"), (68, "blue")):
            self.assert_colour(output, 20, y, colour)

    def test_tall_capoo_style_body_preserves_transparent_lead_in_and_round_cap(self):
        # Only the first 200 of 3000 authored rows should be visible
        source = self.image(40, 3000)
        painter = QPainter(source)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#9f969f"))
        painter.drawRoundedRect(QRectF(4, 20, 32, 48), 16, 16)
        painter.fillRect(4, 44, 32, 2956, QColor("#9f969f"))
        painter.fillRect(0, 2960, 40, 40, QColor("red"))
        painter.end()
        output = self.draw_body(source, 3, QRectF(10, 10, 40, 200))
        self.assertEqual(output.pixelColor(30, 20).alpha(), 0)
        self.assertEqual(output.pixelColor(14, 31).alpha(), 0)
        self.assertGreater(output.pixelColor(30, 32).alpha(), 240)
        self.assert_colour(output, 30, 75, "#9f969f")
        self.assert_colour(output, 30, 195, "#9f969f")
        self.assertEqual(output.pixelColor(12, 195).alpha(), 0)
        self.assertEqual(output.pixelColor(30, 211).alpha(), 0)

    def test_parent_clip_does_not_change_repeat_texture_origin(self):
        source = self.stripes()
        full = self.draw_body(source, 1)
        clipped = self.draw_body(source, 1, clip=QRectF(10, 35, 20, 25))
        for y in range(35, 60):
            self.assertEqual(clipped.pixelColor(20, y), full.pixelColor(20, y))
        self.assertEqual(clipped.pixelColor(20, 34).alpha(), 0)
        self.assertEqual(clipped.pixelColor(20, 60).alpha(), 0)

    def load(self, extra="", head=None, tail=None, body=None):
        self.save("head", head if head is not None else self.stripes())
        self.save("tail", tail if tail is not None else self.stripes(20, 40))
        self.save("body", body if body is not None else self.stripes(20, 100))
        (self.root / "skin.ini").write_text(
            "[General]\nVersion: 2.7\n[Mania]\nKeys: 2\n"
            "ColumnWidth: 60,100\nWidthForNoteHeightScale: 40\n"
            "HitPosition: 402\nJudgementLine: 0\n"
            "NoteImage0: head\nNoteImage0H: head\nNoteImage0T: tail\n"
            "NoteImage0L: body\nNoteImage1: head\n"
            "NoteImage1H: head\nNoteImage1T: tail\nNoteImage1L: body\n"+extra,
            encoding="utf-8")
        self.preview.set_skin(SkinLoader().load(str(self.root)))
        self.preview.set_test_mode("play")
        self.preview.set_playing(False)
        self.preview.game.reset(notes=[PlayNote(0, 0, 1000, 1250)])
        return self.preview._geometry()

    def draw_game(self, time, status="pending"):
        self.preview.t = time
        self.preview.game.notes[0].status = status
        output = self.image(self.preview.width(), self.preview.height())
        painter = QPainter(output)
        geometry = self.preview._geometry()
        field, _, _, _, _, _ = geometry
        painter.setClipRect(field)
        if self.preview._bool(self.preview._settings.get("upsidedown")):
            painter.translate(0, field.top()+field.bottom())
            painter.scale(1, -1)
        bodies, sprites = [], []
        original_body = self.preview._draw_hold_body
        original_sprite = self.preview._paint_sprite

        def record_body(target, sprite, rect, style, *args, **kwargs):
            bodies.append((QRectF(rect), QRectF(target.clipBoundingRect())))
            return original_body(target, sprite, rect, style, *args, **kwargs)

        def record_sprite(target, sprite, rect, elapsed, *args, **kwargs):
            sprites.append((sprite, QRectF(rect)))
            return original_sprite(target, sprite, rect, elapsed, *args, **kwargs)

        with patch.object(self.preview, "_draw_hold_body", side_effect=record_body), \
                patch.object(self.preview, "_paint_sprite", side_effect=record_sprite):
            self.preview._draw_game_notes(painter, *geometry)
        painter.end()
        return output, bodies, sprites

    def rect_for(self, sprites, target):
        return next(rect for sprite, rect in sprites if sprite is target)

    def screen_rect(self, rect):
        if self.preview._bool(self.preview._settings.get("upsidedown")):
            field = self.preview._geometry()[0]
            return QRectF(rect.left(), field.top()+field.bottom()-rect.bottom(),
                          rect.width(), rect.height())
        return rect

    def assert_stripe_direction(self, output, rect, flipped):
        self.assert_colour(output, rect.center().x(), rect.top()+rect.height()*.15,
                           "blue" if flipped else "red")
        self.assert_colour(output, rect.center().x(), rect.top()+rect.height()*.85,
                           "red" if flipped else "blue")

    def test_explicit_height_reference_applies_to_note_head_and_tail_independent_of_lane_width(self):
        field, scale, widths, _, left, hit_y = self.load()
        for lane, x in ((0, left), (1, left+widths[0])):
            note = self.preview._sprite_note_rect(self.preview._assets.notes[lane], x,
                                                 widths[lane], hit_y, scale, widths)
            head = self.preview._sprite_note_rect(self.preview._assets.hold_heads[lane], x,
                                                 widths[lane], hit_y, scale, widths)
            tail = self.preview._sprite_note_rect(self.preview._assets.hold_tails[lane], x,
                                                 widths[lane], hit_y, scale, widths)
            self.assertAlmostEqual(note.height(), 40*scale)
            self.assertAlmostEqual(head.height(), 40*scale)
            self.assertAlmostEqual(tail.height(), 80*scale)
            self.assertAlmostEqual(note.width(), widths[lane])
            self.assertAlmostEqual(note.bottom(), hit_y)

    def test_early_held_head_keeps_falling_before_hitting_the_target(self):
        field, _, _, _, _, hit_y = self.load()
        velocity = (hit_y-field.top())/self.preview.travel_time_ms
        _, _, sprites = self.draw_game(900, "holding")
        head = self.rect_for(sprites, self.preview._assets.hold_heads[0])
        self.assertAlmostEqual(head.bottom(), hit_y-100*velocity)
        self.assertLess(head.bottom(), hit_y)
        _, _, sprites = self.draw_game(1000, "holding")
        self.assertAlmostEqual(self.rect_for(sprites, self.preview._assets.hold_heads[0]).bottom(), hit_y)
        _, _, sprites = self.draw_game(1100, "holding")
        self.assertAlmostEqual(self.rect_for(sprites, self.preview._assets.hold_heads[0]).bottom(), hit_y)

    def test_held_body_retains_original_texture_length_and_only_visible_clip_shrinks(self):
        self.load("NoteBodyStyle: 0\n", head=self.image(20, 20), tail=self.image(20, 40))
        first, first_bodies, _ = self.draw_game(1010, "holding")
        second, second_bodies, _ = self.draw_game(1050, "holding")
        first_rect, first_clip = first_bodies[0]
        second_rect, second_clip = second_bodies[0]
        self.assertAlmostEqual(first_rect.height(), second_rect.height())
        self.assertGreater(second_rect.top(), first_rect.top())
        self.assertGreater(second_rect.bottom(), first_rect.bottom())
        self.assertAlmostEqual(first_clip.bottom(), second_clip.bottom())
        self.assertGreater(first_rect.intersected(first_clip).height(),
                           second_rect.intersected(second_clip).height())
        self.assertEqual(first.pixelColor(int(first_rect.center().x()), int(first_rect.top()+20)),
                         second.pixelColor(int(second_rect.center().x()), int(second_rect.top()+20)))

    def test_default_downscroll_tail_has_top_anchor_and_opposite_art_orientation_to_head(self):
        field, _, _, _, _, hit_y = self.load()
        velocity = (hit_y-field.top())/self.preview.travel_time_ms
        output, _, sprites = self.draw_game(900)
        head = self.rect_for(sprites, self.preview._assets.hold_heads[0])
        tail = self.rect_for(sprites, self.preview._assets.hold_tails[0])
        self.assertAlmostEqual(tail.top(), hit_y-(1250-900)*velocity)
        self.assert_colour(output, head.center().x(), head.top()+5, "red")
        self.assert_colour(output, head.center().x(), head.bottom()-5, "blue")
        self.assert_colour(output, tail.center().x(), tail.top()+5, "blue")
        self.assert_colour(output, tail.center().x(), tail.bottom()-5, "red")

    def test_transparent_tail_keeps_its_authored_height_and_body_geometry(self):
        self.load(tail=self.image(20, 60, "yellow"))
        _, opaque_bodies, opaque_sprites = self.draw_game(900)
        opaque_tail = self.rect_for(opaque_sprites, self.preview._assets.hold_tails[0])
        self.load(tail=self.image(20, 60))
        _, transparent_bodies, transparent_sprites = self.draw_game(900)
        transparent_tail = self.rect_for(transparent_sprites, self.preview._assets.hold_tails[0])
        self.assertEqual(transparent_tail, opaque_tail)
        self.assertEqual(transparent_bodies, opaque_bodies)
        self.assertGreater(transparent_tail.height(), 0)
        self.assertEqual(self.preview._assets.hold_tails[0].frame_at(0).toImage().pixelColor(5, 5).alpha(), 0)

    def test_body_geometry_uses_duration_minus_head_half_plus_tail_half(self):
        field, _, _, _, _, hit_y = self.load()
        velocity = (hit_y-field.top())/self.preview.travel_time_ms
        for timestamp, status in ((900, "pending"), (990, "holding"), (1100, "holding")):
            with self.subTest(timestamp=timestamp, status=status):
                _, bodies, sprites = self.draw_game(timestamp, status)
                body, _ = bodies[0]
                head = self.rect_for(sprites, self.preview._assets.hold_heads[0])
                tail = self.rect_for(sprites, self.preview._assets.hold_tails[0])
                raw_head_y = hit_y-(1000-timestamp)*velocity
                raw_tail_y = hit_y-(1250-timestamp)*velocity
                self.assertAlmostEqual(body.height(), 250*velocity-head.height()/2+tail.height()/2)
                self.assertAlmostEqual(body.top(), raw_tail_y-tail.height()/2)
                self.assertAlmostEqual(body.bottom(), raw_head_y-head.height()/2)

    def test_normal_note_upscroll_flag_preserves_or_flips_actual_art(self):
        for upside, flag in ((0, 0), (0, 1), (1, 0), (1, 1)):
            with self.subTest(upside=upside, flag=flag):
                self.load(f"UpsideDown: {upside}\nNoteFlipWhenUpsideDown0: {flag}\n")
                self.preview.game.reset(notes=[PlayNote(0, 0, 1000)])
                output, _, sprites = self.draw_game(900)
                rect = self.rect_for(sprites, self.preview._assets.notes[0])
                self.assert_stripe_direction(output, self.screen_rect(rect), bool(upside and flag))

    def test_hold_head_upscroll_flag_is_separate_from_normal_note_art(self):
        for upside, flag in ((0, 0), (0, 1), (1, 0), (1, 1)):
            with self.subTest(upside=upside, flag=flag):
                self.load(f"UpsideDown: {upside}\nNoteFlipWhenUpsideDown0: {1-flag}\n"
                          f"NoteFlipWhenUpsideDown0H: {flag}\n")
                output, _, sprites = self.draw_game(900)
                rect = self.rect_for(sprites, self.preview._assets.hold_heads[0])
                self.assert_stripe_direction(output, self.screen_rect(rect), bool(upside and flag))

    def test_key_and_pressed_key_upscroll_flags_preserve_or_flip_art(self):
        self.save("key", self.stripes(20, 80))
        for pressed in (False, True):
            for flag in (0, 1):
                with self.subTest(pressed=pressed, flag=flag):
                    # The unpressed flag deliberately differs from the pressed
                    # flag so using the wrong key metadata fails visibly
                    self.load("UpsideDown: 1\nKeyImage0: key\nKeyImage0D: key\n"
                              f"KeyFlipWhenUpsideDown0: {flag if not pressed else 1-flag}\n"
                              f"KeyFlipWhenUpsideDown0D: {flag if pressed else 1-flag}\n")
                    if pressed:
                        self.preview.game.pressed_lanes.add(0)
                    output = self.image(self.preview.width(), self.preview.height())
                    painter = QPainter(output)
                    field, scale, widths, spacing, left, _ = self.preview._geometry()
                    painter.setClipRect(field)
                    painter.translate(0, field.top()+field.bottom())
                    painter.scale(1, -1)
                    self.preview._draw_keys(painter, field, scale, widths, spacing, left)
                    painter.end()
                    rect = self.preview._key_rect(0, left, widths[0], field, scale)
                    self.assert_stripe_direction(output, self.screen_rect(rect), bool(flag))

    def test_stretched_body_upscroll_flag_preserves_or_flips_whole_art(self):
        for flag in (0, 1):
            with self.subTest(flag=flag):
                self.load(f"UpsideDown: 1\nNoteBodyStyle: 0\nNoteFlipWhenUpsideDown0L: {flag}\n",
                          head=self.image(20, 20), tail=self.image(20, 40),
                          body=self.stripes(80, 20))
                output, bodies, _ = self.draw_game(900)
                self.assert_stripe_direction(output, self.screen_rect(bodies[0][0]), bool(flag))


if __name__ == "__main__":
    unittest.main()
