# -*- coding: utf-8 -*-
"""Skin preview and a local, synthetic Mania playtest with no skin writes."""
from pathlib import Path
import math
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPixmap, QLinearGradient, QImage
from PySide6.QtCore import Qt, QRectF, QTimer, QElapsedTimer, Signal
from core.skin_ini import SkinIni
from core.mania_designer import ManiaDesign, build_preview_overlay, effective_hit_position, DesignValidationError
from core.mania_timeline import bounded_number, travel_time_ms, sample_note_ages
from core.mania_gameplay import ManiaGame
from ui.preview.mania_skin_assets import ManiaSkinAssets
from core import i18n


class ManiaPreview(QWidget):
    play_state_changed = Signal(str)
    session_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 300)
        self.skin = None
        self.skin_ini = None
        self.keys = 7
        self.layout = {}
        self._settings = {}
        self._note_images = []
        self._key_images = []
        self._image_density = {}
        self._stage_hint = None
        self._stage_bottom = None
        self.viewport_aspect = None
        self._scene_rect = QRectF()
        self._overlay_ink_bounds = None
        self._design_overlay = None
        self._overlay_density = 2
        self.design_options = ManiaDesign()
        self.scroll_speed = 20.0
        self.demo_bpm = 120.0
        self._show_hit_guide = False
        self.test_mode = "demo"
        self.test_pattern = "mixed"
        self.game = ManiaGame(keys=self.keys, bpm=self.demo_bpm, pattern=self.test_pattern)
        self._assets = None
        self._input_armed = False
        self._last_judgement = None
        self._last_burst = None
        self._manual_burst_index = -1
        self._combo_inspect = None
        self._reset_hud_effects()
        self._lane_flashes = {}
        self._inspection_time = 0
        self._inspection_clock = QElapsedTimer()
        self._inspection_timer = QTimer(self)
        self._inspection_timer.setInterval(33)
        self._inspection_timer.timeout.connect(self._tick_inspection)
        self.setFocusPolicy(Qt.StrongFocus)
        self.t = 0
        self._playing = True
        self._clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.tick)
        self._load_layout_for_keys(self.keys)

    def set_playing(self, playing: bool):
        awaiting_click = (bool(playing) and self.test_mode == "play" and
                          (not self._input_armed or not self.hasFocus()))
        self._playing = bool(playing) and not awaiting_click
        if self._playing:
            if self._last_judgement and self._last_judgement[2]:
                self._last_judgement = None
            if self._last_burst and self._last_burst[1]:
                self._last_burst = None
            self._combo_inspect = None
        if not self._playing:
            self.game.release_all(self.t)
            self._lane_flashes.clear()
        if self._playing and self.isVisible():
            if not self.timer.isActive():
                self._clock.start()
            self.timer.start()
        else:
            self.timer.stop()
            self._clock.invalidate()
        self.play_state_changed.emit("ready" if awaiting_click else "playing" if self._playing else "paused")
        self._sync_inspection_timer()
        self.update()

    def _sync_inspection_timer(self):
        inspecting = ((self._last_judgement and self._last_judgement[2]) or
                      (self._last_burst and self._last_burst[1]))
        if inspecting and not self._playing and self.isVisible():
            if not self._inspection_timer.isActive():
                self._inspection_clock.start()
            self._inspection_timer.start()
        else:
            self._inspection_timer.stop()
            self._inspection_clock.invalidate()

    def _tick_inspection(self):
        if self._playing or not self.isVisible():
            self._sync_inspection_timer()
            return
        if self._inspection_clock.isValid():
            self._inspection_time += self._inspection_clock.restart()
        self.update()

    def _effect_age(self, when, manual):
        return max(0, self._inspection_time if manual and not self._playing else self.t-when)

    @property
    def key_labels(self):
        return tuple(label for label, _ in self._bindings())

    def _bindings(self):
        # These are local to this focused canvas, never application shortcuts.
        layouts = {1: ["Space"], 2: ["F", "J"], 3: ["F", "Space", "J"],
                   4: ["D", "F", "J", "K"], 5: ["D", "F", "Space", "J", "K"],
                   6: ["S", "D", "F", "J", "K", "L"],
                   7: ["S", "D", "F", "Space", "J", "K", "L"],
                   8: ["A", "S", "D", "F", "J", "K", "L", ";"],
                   9: ["A", "S", "D", "F", "Space", "J", "K", "L", ";"]}
        labels = layouts.get(self.keys, list("QWERTYUIOPASDFGHJK")[:self.keys])
        return tuple((label, int(Qt.Key_Space) if label == "Space" else
                      int(Qt.Key_Semicolon) if label == ";" else ord(label)) for label in labels)

    def set_test_mode(self, mode="demo"):
        if mode not in ("demo", "play", "auto"):
            raise ValueError("Unknown Mania test mode")
        if mode == self.test_mode:
            return
        self.test_mode = mode
        self._input_armed = False
        self.restart_demo()
        self.set_playing(mode != "play")
        if mode == "play":
            self.play_state_changed.emit("ready")

    def set_test_pattern(self, pattern="mixed"):
        if pattern not in ("mixed", "taps", "holds", "chords"):
            raise ValueError("Unknown Mania test pattern")
        if pattern != self.test_pattern:
            self.test_pattern = pattern
            self.restart_demo()

    def show_judgement(self, kind):
        aliases = {"max": "300g", "perfect": "300", "great": "200", "good": "100",
                   "bad": "50", "miss": "0"}
        kind = aliases.get(str(kind).lower(), str(kind).lower())
        if kind not in ("300g", "300", "200", "100", "50", "0"):
            raise ValueError("Unknown Mania judgement")
        self._last_judgement = (kind, self.t, True)
        self._inspection_time = 0
        self._sync_inspection_timer()
        self.update()

    def show_comboburst(self):
        self._manual_burst_index += 1
        self._last_burst = (self.t, True)
        self._combo_inspect = (100, self.t)
        self._inspection_time = 0
        self._sync_inspection_timer()
        self.update()

    def set_comboburst_preview(self, enabled=True):
        if enabled:
            self.show_comboburst()
        else:
            self._last_burst = None
            self._combo_inspect = None
            self._sync_inspection_timer()
            self.update()

    def _accept_events(self, events):
        for event in events:
            self._accept_hud_event(event)
            self._last_judgement = (event.judgement, event.time_ms, False)
            if event.judgement != "0":
                self._lane_flashes[event.lane] = event.time_ms
                if event.combo and event.combo % 50 == 0:
                    self._last_burst = (event.time_ms, False)
        if events:
            self.session_changed.emit(self.game)

    def _advance_game(self):
        if self.test_mode == "demo":
            return
        self._accept_events(self.game.advance_to(self.t, autoplay=self.test_mode == "auto"))
        if self.game.finished:
            if self.test_mode == "auto":
                self.restart_demo()
            else:
                self.set_playing(False)
                self.play_state_changed.emit("finished")

    def _sync_time(self):
        if self._clock.isValid():
            self.t += self._clock.restart()
        else:
            self._clock.start()
        self._advance_game()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.test_mode == "play" and self.skin:
            self.start_test()
            event.accept()
            return
        super().mousePressEvent(event)

    def start_test(self):
        """Explicit canvas/toolbar action; config focus alone never arms input."""
        if self.test_mode != "play" or not self.skin:
            return
        self.setFocus(Qt.OtherFocusReason)
        if self.game.finished:
            self.restart_demo()
        self._input_armed = True
        self.set_playing(True)

    def keyPressEvent(self, event):
        if self.test_mode != "play" or not self._input_armed or not self.hasFocus():
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key_Escape:
            self.set_playing(False)
            self._input_armed = False
            event.accept()
            return
        lane = next((i for i, (_, key) in enumerate(self._bindings()) if key == event.key()), None)
        if lane is None or event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            super().keyPressEvent(event)
            return
        if self._playing and not event.isAutoRepeat():
            self._sync_time()
            self._accept_events(self.game.key_down(lane, self.t))
            self.update()
        event.accept()

    def keyReleaseEvent(self, event):
        lane = next((i for i, (_, key) in enumerate(self._bindings()) if key == event.key()), None)
        if self.test_mode == "play" and self._input_armed and lane is not None:
            if self._playing and not event.isAutoRepeat():
                self._sync_time()
                self._accept_events(self.game.key_up(lane, self.t))
                self.update()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        if self.test_mode == "play":
            self._input_armed = False
            self.set_playing(False)
        super().focusOutEvent(event)

    def set_scroll_speed(self, value):
        self.scroll_speed = bounded_number(value, 1, 40, 20)
        self.update()

    def set_demo_bpm(self, value):
        value = bounded_number(value, 40, 300, 120)
        if value != self.demo_bpm:
            self.demo_bpm = value
            if self.test_mode != "demo":
                self.restart_demo()
        self.update()

    def restart_demo(self):
        self.t = 0
        self.game.reset(keys=self.keys, bpm=self.demo_bpm, pattern=self.test_pattern)
        self._last_judgement = None
        self._last_burst = None
        self._combo_inspect = None
        self._reset_hud_effects()
        self._lane_flashes.clear()
        self._sync_inspection_timer()
        if self.test_mode == "play":
            self._input_armed = False
            self.set_playing(False)
            self.play_state_changed.emit("ready")
        if self.timer.isActive():
            self._clock.start()
        else:
            self._clock.invalidate()
        self.session_changed.emit(self.game)
        self.update()

    def set_show_hit_guide(self, show):
        self._show_hit_guide = bool(show)
        self.update()

    def set_design_options(self, design: ManiaDesign):
        # Build first so an invalid design cannot replace the working preview.
        effective_hit_position(self._settings, design)
        overlay = build_preview_overlay(self._skin_root(), self.keys, design) if self.skin else None
        self.design_options = design
        self._set_overlay_image(overlay)
        self.update()

    @property
    def effective_hit_position(self):
        values = dict(self._settings)
        # Keep the ordinary preview tolerant of malformed optional INI values;
        # exporting or explicitly applying a design still validates the source.
        hit = self.layout.get("HitPosition")
        values["hitposition"] = 402 if hit is None else hit
        return effective_hit_position(values, self.design_options)

    @property
    def travel_time_ms(self):
        return travel_time_ms(self.scroll_speed, self.effective_hit_position)

    def _set_overlay_image(self, image):
        self._design_overlay = None
        self._overlay_ink_bounds = None
        if image is not None:
            rgba = image.convert("RGBA")
            # QImage must own its bytes after the local PIL image is released.
            qt_image = QImage(rgba.tobytes(), rgba.width, rgba.height,
                             rgba.width*4, QImage.Format_RGBA8888).copy()
            self._design_overlay = QPixmap.fromImage(qt_image)
            self._design_overlay.setDevicePixelRatio(1.0)
            self._overlay_density = image.info.get("skin_density", 2)
            self._overlay_ink_bounds = rgba.getchannel("A").getbbox()

    def showEvent(self, event):
        super().showEvent(event)
        self.set_playing(self._playing)

    def hideEvent(self, event):
        if self.test_mode == "play":
            self._input_armed = False
            self.set_playing(False)
        self.timer.stop()
        self._clock.invalidate()
        self._inspection_timer.stop()
        self._inspection_clock.invalidate()
        super().hideEvent(event)

    def tick(self):
        if not self._playing or not self.isVisible():
            return
        self._sync_time()
        self.update()

    def set_skin(self, skin):
        self.design_options = ManiaDesign()
        self.skin = skin
        self.set_keys(getattr(skin, "mode_keys", self.keys))
        self.restart_demo()

    def set_keys(self, k: int):
        previous = self.keys
        try:
            self.keys = max(1, min(18, int(k)))
        except (TypeError, ValueError):
            self.keys = 7
        if previous != self.keys:
            self.design_options = ManiaDesign()
            self.restart_demo()
        # The editor emits this after saving as well as when switching keycount.
        self._load_skin_ini()
        self._load_layout_for_keys(self.keys)
        self.update()

    def _skin_root(self):
        return Path(self.skin.root) if self.skin else None

    def _load_skin_ini(self):
        self.skin_ini = None
        root = self._skin_root()
        if root:
            try:
                self.skin_ini = SkinIni.read(root / "skin.ini")
            except (OSError, ValueError):
                pass

    @staticmethod
    def _int_or_none(value):
        try:
            result = float(value)
            return result if math.isfinite(result) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _list_of_ints(value, fallback_len=0, fill=0):
        values = value if isinstance(value, (list, tuple)) else str(value or "").split(",")
        result = []
        for item in values[:fallback_len]:
            try:
                number = float(item)
                result.append(max(0, min(4096, number)) if math.isfinite(number) else fill)
            except (TypeError, ValueError):
                result.append(fill)
        return result + [fill] * (fallback_len-len(result))

    @staticmethod
    def _bool(value, default=False):
        if value is None or str(value).strip() == "":
            return default
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _colour(value, default):
        try:
            channels = [int(part.strip()) for part in str(value).split(",")]
            if len(channels) == 3:
                channels.append(255)
            if len(channels) != 4 or any(not 0 <= n <= 255 for n in channels):
                raise ValueError
            return QColor(*channels)
        except (TypeError, ValueError):
            return QColor(default)

    def _load_layout_for_keys(self, k):
        data = self.skin_ini.mania_get(k) if self.skin_ini else {}
        self._settings = {key.lower(): value for key, value in data.items()}
        d = self._settings
        self.layout = {
            "ColumnStart": self._int_or_none(d.get("columnstart")),
            "ColumnRight": self._int_or_none(d.get("columnright")),
            "ColumnWidth": self._list_of_ints(d.get("columnwidth"), k, 30),
            "ColumnSpacing": self._list_of_ints(d.get("columnspacing"), max(k-1, 0), 0),
            "ColumnLineWidth": self._list_of_ints(d.get("columnlinewidth"), k+1, 2),
            "HitPosition": self._int_or_none(d.get("hitposition")),
            "LightPosition": self._int_or_none(d.get("lightposition")),
            "LightingNWidth": self._list_of_ints(d.get("lightingnwidth"), k, 0),
            "LightingLWidth": self._list_of_ints(d.get("lightinglwidth"), k, 0),
            "WidthForNoteHeightScale": self._int_or_none(d.get("widthfornoteheightscale")),
        }
        self._note_images = []
        self._key_images = []
        self._image_density = {}
        self._assets = ManiaSkinAssets(self._skin_root(), d,
                                       config=getattr(self.skin, "ini", None), keys=k)
        for i in range(k):
            for sprites, images in ((self._assets.notes, self._note_images),
                                    (self._assets.keys_up, self._key_images)):
                sprite = sprites[i]
                image = sprite.frame_at(0) if sprite else None
                images.append(image)
                if image is not None:
                    self._image_density[image.cacheKey()] = sprite.density_at(0)
        self._stage_hint = self._pix(d.get("stagehint") or "mania-stage-hint")
        self._stage_bottom = self._pix(d.get("stagebottom") or "mania-stage-bottom")
        try:
            overlay = build_preview_overlay(self._skin_root(), k, self.design_options) if self.skin else None
        except (OSError, DesignValidationError):
            # Existing unusual INI files can still use the ordinary renderer;
            # explicit design changes surface validation errors in the editor.
            overlay = None
        self._set_overlay_image(overlay)
        self._keys_under_notes = self._bool(d.get("keysundernotes"))
        self._judgement_line = self._bool(d.get("judgementline"), True)
        self._judgement_colour = self._colour(d.get("colourjudgementline"), "white")
        self._lane_colours = [self._colour(d.get(f"colour{i+1}"), "black") for i in range(k)]
        self._line_colour = self._colour(d.get("colourcolumnline"), "white")

    def _pix(self, name):
        root = self._skin_root()
        if not root or not name:
            return None
        relative = Path(str(name).replace("\\", "/"))
        if relative.suffix.lower() != ".png":
            relative = Path(str(relative) + ".png")
        try:
            path = (root / relative).resolve()
            path.relative_to(root.resolve())
        except (OSError, ValueError):
            return None
        for candidate in [path.with_name(path.stem + "@2x.png"), path]:
            if not candidate.is_file():
                continue
            pm = QPixmap()
            try:
                pm.loadFromData(candidate.read_bytes())
            except OSError:
                continue
            if not pm.isNull():
                pm.setDevicePixelRatio(1.0)
                self._image_density[pm.cacheKey()] = 2 if candidate.stem.lower().endswith("@2x") else 1
                return pm
        return None

    def set_viewport_aspect(self, aspect=None):
        """Set the virtual game's width/height ratio, preserving skin units.

        None retains the flexible editor viewport. Fixed aspects letterbox the
        complete scene, including artwork extending outside the game screen.
        """
        if aspect is not None:
            aspect = float(aspect)
            if not math.isfinite(aspect) or aspect <= 0:
                raise ValueError("Viewport aspect must be positive and finite")
        self.viewport_aspect = aspect
        self.update()

    def _logical_scene_bounds(self, screen_width, start, total):
        bounds = QRectF(0, 0, screen_width, 480).united(QRectF(start, 0, total, 480))
        assets = self._assets
        if assets is None:
            return bounds
        for side, sprite, ink_frames in (
                ("left", assets.stage_left, assets.stage_left_bounds),
                ("right", assets.stage_right, assets.stage_right_bounds)):
            if sprite is None:
                continue
            for index, ink in enumerate(ink_frames):
                if ink is None:
                    continue
                width = sprite.frames[index].width() / sprite.densities[index]
                height = sprite.frames[index].height() / sprite.densities[index]
                origin = start-width/1.6 if side == "left" else start+total
                x1, y1, x2, y2 = ink
                bounds = bounds.united(QRectF(origin+x1/1.6, y1*480/height,
                                              (x2-x1)/1.6, (y2-y1)*480/height))
        if self._design_overlay is not None and self._has_design_mask():
            density = self._overlay_density
            bottom_frames = ((self._design_overlay.width()/density,
                              self._design_overlay.height()/density,
                              tuple(v/density for v in self._overlay_ink_bounds)
                              if self._overlay_ink_bounds else None),)
        elif assets.stage_bottom:
            bottom_frames = tuple((image.width()/density, image.height()/density, ink)
                                  for image, density, ink in zip(assets.stage_bottom.frames,
                                  assets.stage_bottom.densities, assets.stage_bottom_bounds))
        else:
            bottom_frames = ()
        for width, height, ink in bottom_frames:
            if ink is None:
                continue
            x1, y1, x2, y2 = ink
            rect = QRectF(start+total/2-width/2+x1, 480-height+y1, x2-x1, y2-y1)
            if self._bool(self._settings.get("upsidedown")):
                rect.moveTop(480-rect.bottom())
            bounds = bounds.united(rect)
        style = str(self._settings.get("comboburststyle", "1")).strip().casefold()
        sides = (False,) if style in ("0", "left") else ((False, True) if style in ("2", "both") else (True,))
        for sprite, ink in zip(assets.combo_bursts, assets.combo_burst_bounds):
            if ink is None:
                continue
            width, height = sprite.logical_size()
            x1, y1, x2, y2 = ink
            for right in sides:
                x = start+total+(width-x2)/1.6 if right else start-width/1.6+x1/1.6
                bounds = bounds.united(QRectF(x, 480-height/1.6+y1/1.6,
                                              (x2-x1)/1.6, (y2-y1)/1.6))
        return bounds

    def _geometry(self):
        """Fit one 480-high virtual screen and its artwork with one scale."""
        available = QRectF(self.rect().adjusted(28, 44, -28, -28))
        start = self.layout["ColumnStart"]
        start = 136 if start is None else start
        column_widths = [max(1, n) for n in self.layout["ColumnWidth"]]
        total_width = sum(column_widths) + sum(self.layout["ColumnSpacing"])
        right = self.layout["ColumnRight"]
        right = 19 if right is None else max(0, right)
        logical_width = (480*self.viewport_aspect if self.viewport_aspect is not None else
                         max(640, available.width()/max(1, available.height())*480))
        if self.viewport_aspect is None:
            logical_width = max(logical_width, start+total_width+right)
        if self.layout["ColumnStart"] is None and not self.skin:
            start = (logical_width-total_width)/2
        bounds = self._logical_scene_bounds(logical_width, start, total_width)
        scale = max(0.001, min(max(1, available.height())/bounds.height(),
                               max(1, available.width())/bounds.width()))
        origin_x = available.center().x()-bounds.width()*scale/2-bounds.left()*scale
        origin_y = available.center().y()-bounds.height()*scale/2-bounds.top()*scale
        field = QRectF(origin_x, origin_y, logical_width*scale, 480*scale)
        self._scene_rect = QRectF(origin_x+bounds.left()*scale, origin_y+bounds.top()*scale,
                                 bounds.width()*scale, bounds.height()*scale)
        widths = [max(1, n)*scale for n in self.layout["ColumnWidth"]]
        spacing = [n*scale for n in self.layout["ColumnSpacing"]]
        left = field.left() + start*scale
        hit_y = field.top() + self.effective_hit_position*scale
        return field, scale, widths, spacing, left, hit_y

    def _stage_side_rect(self, sprite, side, field, scale, left, total):
        width, _ = sprite.logical_size(self.t)
        width = width/1.6*scale
        return QRectF(left-width if side == "left" else left+total,
                      field.top(), width, field.height())

    def _draw_stage_sides(self, painter, field, scale, left, total):
        for side, sprite in (("left", self._assets.stage_left), ("right", self._assets.stage_right)):
            if sprite is not None:
                self._paint_sprite(painter, sprite,
                                   self._stage_side_rect(sprite, side, field, scale, left, total), self.t)

    def _key_rect(self, column, lane_x, width, field, scale, key=None, density=None):
        key = self._key_images[column] if key is None else key
        # Legacy keys stretch horizontally only. SD textures are in a 768-high
        # image space, while skin.ini uses 480: 480 / 768 = 0.625.
        # Their transparent padding is authored for a bottom-of-screen anchor.
        if density is None:
            density = self._image_density.get(key.cacheKey(), 1) if key is not None else 1
        height = key.height()/density/1.6*scale if key is not None else 64*scale
        # Export bakes an integer number of transparent image pixels below each
        # receptor. Match that rounding per density, including sub-unit lifts.
        padding = round(float(self.design_options.receptor_raise)*1.6*density)
        lift = padding/density/1.6*scale
        return QRectF(lane_x, field.bottom()-height-lift, width, height)

    def _stage_bottom_rect(self, left, total, field, scale):
        image, density = self._active_stage_bottom()
        if image is None:
            return QRectF()
        width, height = image.width()/density*scale, image.height()/density*scale
        return QRectF(left+total/2-width/2, field.bottom()-height, width, height)

    def _draw_stage_bottom(self, painter, field, scale, left, total):
        image, _ = self._active_stage_bottom()
        if image is not None:
            painter.drawPixmap(self._stage_bottom_rect(left, total, field, scale), image, QRectF(image.rect()))

    def _has_design_mask(self):
        return bool(self.design_options.top_mask_height or self.design_options.top_mask_fade)

    def _active_stage_bottom(self):
        if self._design_overlay is not None and self._has_design_mask():
            return self._design_overlay, self._overlay_density
        if self._assets and self._assets.stage_bottom:
            sprite = self._assets.stage_bottom
            return sprite.frame_at(self.t), sprite.density_at(self.t)
        return self._stage_bottom, self._image_density.get(self._stage_bottom.cacheKey(), 1) if self._stage_bottom else 1

    def _stage_hint_rect(self, left, total, hit_y, scale):
        if self._stage_hint is None:
            return QRectF()
        # LegacyHitTarget uses a fixed vertical scale, independent of stage width.
        # See ppy/osu LegacyHitTarget.cs (0.9 * 1.6025 in the 768-high space).
        height = (self._stage_hint.height()/self._image_density.get(self._stage_hint.cacheKey(), 1)
                  * (0.9*1.6025/1.6) * scale)
        return QRectF(left, hit_y-height/2, total, height)

    def _note_rect(self, column, lane_x, width, bottom, scale, widths):
        note = self._note_images[column]
        height_width = self.layout["WidthForNoteHeightScale"]
        reference_width = height_width*scale if height_width and height_width > 0 else min(widths)
        height = reference_width*note.height()/note.width() if note is not None else 10*scale
        return QRectF(lane_x, bottom-height, width, height)

    def _draw_keys(self, painter, field, scale, widths, spacing, left):
        lane_x = left
        for index, width in enumerate(widths):
            key = self._key_images[index]
            pressed = (index in self.game.pressed_lanes or
                       (self.test_mode == "auto" and 0 <= self.t-self._lane_flashes.get(index, -1000) < 85))
            sprite = self._assets.keys_down[index] if pressed else self._assets.keys_up[index]
            density = None
            if sprite is not None:
                key, density = sprite.frame_at(self.t), sprite.density_at(self.t)
            rect = self._key_rect(index, lane_x, width, field, scale, key, density)
            if key is not None:
                flags = self._assets.key_down_flips if pressed else self._assets.key_flips
                flip = self._bool(self._settings.get("upsidedown")) and not flags[index]
                self._paint_pixmap(painter, key, rect, flip)
            else:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor("#66ddeb" if pressed else "#29364a"))
                painter.drawRoundedRect(rect.adjusted(3, 0, -3, 0), 3, 3)
            lane_x += width + (spacing[index] if index < self.keys-1 else 0)

    def _draw_notes(self, painter, field, scale, widths, spacing, left, hit_y):
        if self.test_mode != "demo":
            self._draw_game_notes(painter, field, scale, widths, spacing, left, hit_y)
            return
        lane_x = left
        duration = self.travel_time_ms
        for index, width in enumerate(widths):
            note = self._note_images[index]
            for age in sample_note_ages(self.t, index, self.keys, self.demo_bpm, duration):
                progress = age/duration
                bottom = field.top() + progress*(hit_y-field.top())
                rect = self._note_rect(index, lane_x, width, bottom, scale, widths)
                if note is not None:
                    flip = self._bool(self._settings.get("upsidedown")) and not self._assets.note_flips[index]
                    self._paint_pixmap(painter, note, rect, flip)
                else:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor("#70d7e0"))
                    painter.drawRoundedRect(rect.adjusted(3, 0, -3, 0), 3, 3)
            lane_x += width + (spacing[index] if index < self.keys-1 else 0)

    def _sprite_note_rect(self, sprite, lane_x, width, bottom, scale, widths):
        image = sprite.frame_at(self.t) if sprite else None
        height_width = self.layout["WidthForNoteHeightScale"]
        reference = height_width*scale if height_width and height_width > 0 else min(widths)
        height = reference*image.height()/image.width() if image else 10*scale
        return QRectF(lane_x, bottom-height, width, height)

    @staticmethod
    def _paint_pixmap(painter, image, rect, flip=False):
        if flip:
            painter.save()
            painter.translate(0, rect.top()+rect.bottom())
            painter.scale(1, -1)
        painter.drawPixmap(rect, image, QRectF(image.rect()))
        if flip:
            painter.restore()

    @staticmethod
    def _paint_sprite(painter, sprite, rect, elapsed, flip=False):
        if sprite is None or rect.isEmpty():
            return False
        ManiaPreview._paint_pixmap(painter, sprite.frame_at(elapsed), rect, flip)
        return True

    def _draw_hold_body(self, painter, sprite, rect, style):
        if rect.isEmpty():
            return
        if sprite is None:
            painter.fillRect(rect.adjusted(rect.width()*.2, 0, -rect.width()*.2, 0), QColor("#4ba6b8"))
            return
        if not style:
            self._paint_sprite(painter, sprite, rect, self.t)
            return
        image = sprite.frame_at(self.t)
        unit = rect.width()/image.width()
        texture_height = image.height()*unit
        painter.save()
        painter.setClipRect(rect, Qt.IntersectClip)
        visible = rect.intersected(painter.clipBoundingRect())

        def draw_texture(origin):
            top = max(visible.top(), origin)
            bottom = min(visible.bottom(), origin+texture_height)
            if bottom > top:
                # Explicit source cropping avoids painting a 40000px texture
                # into a huge destination just to retain a few visible rows.
                painter.drawPixmap(QRectF(rect.left(), top, rect.width(), bottom-top), image,
                                   QRectF(0, (top-origin)/unit, image.width(), (bottom-top)/unit))

        if style == 1:
            first = rect.top()+math.floor((visible.top()-rect.top())/texture_height)*texture_height
            count = min(2048, max(0, math.ceil((visible.bottom()-first)/texture_height)))
            for index in range(count):
                draw_texture(first+index*texture_height)
        else:
            # RepeatBottom preserves the TOP of the artwork and extends its
            # bottom edge, not a bottom-aligned repeated copy of the image.
            # See osu!dev's explanation: forums/topics/341098. This preserves
            # the transparent lead-in and rounded cap of Percy-style LN art.
            origin = (rect.bottom()-texture_height if style == 2 else
                      rect.center().y()-texture_height/2 if style == 4 else rect.top())
            draw_texture(origin)
            if visible.top() < origin:
                bottom = min(origin, visible.bottom())
                painter.drawPixmap(QRectF(rect.left(), visible.top(), rect.width(), bottom-visible.top()),
                                   image, QRectF(0, 0, image.width(), 1))
            if visible.bottom() > origin+texture_height:
                top = max(origin+texture_height, visible.top())
                painter.drawPixmap(QRectF(rect.left(), top, rect.width(), visible.bottom()-top),
                                   image, QRectF(0, image.height()-1, image.width(), 1))
        painter.restore()

    def _draw_game_notes(self, painter, field, scale, widths, spacing, left, hit_y):
        positions = []
        x = left
        for index, width in enumerate(widths):
            positions.append(x)
            x += width+(spacing[index] if index < len(spacing) else 0)
        speed = (hit_y-field.top())/self.travel_time_ms
        for note in self.game.notes:
            if note.status in ("hit", "missed"):
                continue
            index = note.lane
            x, width = positions[index], widths[index]
            raw_head_y = hit_y-(note.start_ms-self.t)*speed
            head_y = raw_head_y
            if note.status == "holding":
                # Early holds keep falling until the head reaches the target.
                head_y = min(head_y, hit_y)
            tail_y = hit_y-((note.end_ms or note.start_ms)-self.t)*speed
            if head_y < field.top()-width*2 or tail_y > field.bottom()+width*2:
                continue
            head_sprite = self._assets.hold_heads[index] if note.is_hold else self._assets.notes[index]
            head = self._sprite_note_rect(head_sprite, x, width, head_y, scale, widths)
            upside_down = self._bool(self._settings.get("upsidedown"))
            if note.is_hold:
                tail_sprite = self._assets.hold_tails[index]
                tail = self._sprite_note_rect(tail_sprite, x, width, tail_y, scale, widths)
                flip_tail = upside_down or self._assets.tail_flips[index]
                if flip_tail:
                    # A reversed tail uses the opposite anchor, not just a
                    # mirrored texture inside the head's bottom-anchored box.
                    tail.moveTop(tail_y)
                # Legacy DrawableHoldNote uses duration - headHeight/2 +
                # tailHeight/2 for the body, independently of the tail sprite's
                # reversed anchor. Transparent tail pixels still count.
                body_top = tail_y-tail.height()/2
                body_bottom = raw_head_y-head.height()/2
                body = QRectF(x, body_top, width, max(0, body_bottom-body_top))
                painter.save()
                # A held LN is clipped at the head instead of rescaling its
                # whole texture every frame as the remaining duration shrinks.
                painter.setClipRect(QRectF(x, field.top(), width,
                                          max(0, head.center().y()-field.top())), Qt.IntersectClip)
                painter.save()
                if upside_down and not self._assets.body_flips[index]:
                    painter.translate(0, body.top()+body.bottom())
                    painter.scale(1, -1)
                self._draw_hold_body(painter, self._assets.hold_bodies[index], body,
                                     self._assets.body_styles[index])
                painter.restore()
                if not self._paint_sprite(painter, tail_sprite, tail, self.t, flip_tail):
                    painter.fillRect(tail.adjusted(3, 0, -3, 0), QColor("#b0f0ec"))
                painter.restore()
            head_flags = self._assets.head_flips if note.is_hold else self._assets.note_flips
            if not self._paint_sprite(painter, head_sprite, head, self.t, upside_down and not head_flags[index]):
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor("#70d7e0"))
                painter.drawRoundedRect(head.adjusted(3, 0, -3, 0), 3, 3)

    def _draw_lighting(self, painter, field, scale, widths, spacing, left, hit_y, stage_only=False):
        if self.test_mode == "demo":
            return
        x = left
        for lane, width in enumerate(widths):
            held = lane in self.game.pressed_lanes
            recent = 0 <= self.t-self._lane_flashes.get(lane, -1000) < 160
            if held or recent:
                holding_note = any(note.lane == lane and note.status == "holding"
                                   for note in self.game.notes)
                if stage_only:
                    sprite = self._assets.stage_light if held else None
                    if sprite:
                        image = sprite.frame_at(self.t)
                        height = image.height()/sprite.density_at(self.t)/1.6*scale
                        light_position = self.layout["LightPosition"]
                        bottom = field.top()+(413 if light_position is None else light_position)*scale
                        self._paint_sprite(painter, sprite, QRectF(x, bottom-height, width, height), self.t)
                elif recent or held and holding_note:
                    sprite = self._assets.lighting_l if holding_note else self._assets.lighting_n
                    if sprite:
                        authored_width = self.layout["LightingLWidth" if holding_note else "LightingNWidth"][lane]
                        size_factor = ((authored_width or width/scale)/30
                                       if self._assets.version >= 2.5 else 1)
                        age = max(0, self.t-self._lane_flashes.get(lane, self.t))
                        logical_w, logical_h = sprite.logical_size(age)
                        size = size_factor*scale/1.6
                        rect = QRectF(x+width/2-logical_w*size/2, hit_y-logical_h*size/2,
                                      logical_w*size, logical_h*size)
                        self._paint_sprite(painter, sprite, rect, age)
            x += width+(spacing[lane] if lane < len(spacing) else 0)

    def _hud_rect(self, sprite, center_x, y, scale, elapsed=0, loop=True):
        width, height = sprite.logical_size(elapsed, loop=loop)
        factor = getattr(self._assets, "hud_scale", .625)*scale
        return QRectF(center_x-width*factor/2, y-height*factor/2, width*factor, height*factor)

    def _reset_hud_effects(self):
        self._hud_holds = set()
        self._hud_combo = 0
        self._combo_pulse_at = None
        self._combo_break = None
        self._combo_colour_from = QColor("white")
        self._combo_colour_target = QColor("white")
        self._combo_colour_at = 0

    def _combo_colour_at_time(self, time_ms):
        # ColourHold's meaning is documented, but stable's exact transition
        # curve is not public. This short linear transition is a tester-only
        # approximation; the configured colour itself is used without changes.
        progress = max(0, min(1, (time_ms-self._combo_colour_at)/120))
        start, end = self._combo_colour_from.getRgb(), self._combo_colour_target.getRgb()
        return QColor(*(round(a+(b-a)*progress) for a, b in zip(start, end)))

    def _accept_hud_event(self, event):
        note = next((note for note in self.game.notes if note.id == event.note_id), None)
        had_hold = bool(self._hud_holds)
        if event.hold_end or event.judgement == "0":
            self._hud_holds.discard(event.note_id)
        elif note is not None and note.is_hold:
            self._hud_holds.add(event.note_id)
        if had_hold != bool(self._hud_holds):
            self._combo_colour_from = self._combo_colour_at_time(event.time_ms)
            self._combo_colour_target = (QColor(self._assets.hold_colour) if self._hud_holds
                                         else QColor("white"))
            self._combo_colour_at = event.time_ms
        if event.judgement == "0" and self._hud_combo > 0:
            self._combo_break = (self._hud_combo, event.time_ms)
        elif event.combo == self._hud_combo+1:
            self._combo_pulse_at = event.time_ms
        self._hud_combo = event.combo

    @staticmethod
    def _judgement_transform(kind, age):
        """Stable-compatible scale/fade envelope used by ppy's legacy piece."""
        if age < 0 or age >= 220:
            return 0.0, 1.0, 0.0
        if age < 20:
            progress = age/20
            alpha = 1-(1-progress)**2
        elif age < 180:
            alpha = 1.0
        else:
            alpha = 1-((age-180)/40)**2
        if kind == "0":
            # A fixed tilt keeps the test reproducible. Stable chooses a small
            # random angle; its 1.2 -> 1 scale and duration are reproduced.
            progress = min(1, age/100)
            eased = 1-(1-progress)**2
            return alpha, 1.2-.2*eased, 3*eased
        if age < 40:
            size = .8+.2*age/40
        elif age < 80:
            size = .85-.15*(age-40)/40
        elif age < 180:
            size = .7
        else:
            size = .7-.3*((age-180)/40)**2
        return alpha, size, 0.0

    def _draw_judgement(self, painter, field, scale, left, total):
        if not self._last_judgement:
            return
        kind, when, manual = self._last_judgement
        age = self._effect_age(when, manual)
        alpha, size, rotation = (1, 1, 0) if manual else self._judgement_transform(kind, age)
        if alpha <= 0:
            return
        position = getattr(self._assets, "score_position", 300)
        y = (field.bottom()-position*scale if self._bool(self._settings.get("upsidedown"))
             else field.top()+position*scale)
        sprite = self._assets.judgements.get(kind)
        painter.save()
        painter.setOpacity(painter.opacity()*alpha)
        painter.translate(left+total/2, y)
        painter.scale(size, size)
        painter.rotate(rotation)
        painter.translate(-left-total/2, -y)
        if sprite:
            rect = self._hud_rect(sprite, left+total/2, y, scale, age, loop=manual)
            image = sprite.frame_at(age, loop=manual)
            painter.drawPixmap(rect, image, QRectF(image.rect()))
        else:
            labels = {"300g": "MAX", "300": "PERFECT", "200": "GREAT", "100": "GOOD", "50": "BAD", "0": "MISS"}
            colours = {"300g": "#efffff", "300": "#ffe77a", "200": "#6cf3b5", "100": "#6ecbff", "50": "#c294ff", "0": "#ff6b83"}
            painter.setFont(QFont("Segoe UI", max(8, int(20*scale)), QFont.Bold))
            painter.setPen(QColor(colours[kind]))
            painter.drawText(QRectF(left-100*scale, y-24*scale, total+200*scale, 48*scale),
                             Qt.AlignCenter, labels[kind])
        painter.restore()

    def _draw_combo(self, painter, field, scale, left, total):
        combo = self.game.combo if self.test_mode != "demo" else 0
        inspecting = self._combo_inspect is not None
        if inspecting:
            combo = self._combo_inspect[0]
        position = getattr(self._assets, "combo_position", 111)
        y = (field.bottom()-position*scale if self._bool(self._settings.get("upsidedown"))
             else field.top()+position*scale)
        if self._combo_break and not inspecting:
            previous, when = self._combo_break
            progress = max(0, (self.t-when)/200)
            if progress < 1:
                painter.save()
                painter.setCompositionMode(QPainter.CompositionMode_Plus)
                painter.setOpacity(painter.opacity()*.8*(1-progress))
                self._draw_combo_value(painter, previous, left+total/2, y, scale,
                                       self._assets.break_colour, 1+3*progress, 1+3*progress)
                painter.restore()
        if combo <= 0:
            return
        pulse = 1
        if not inspecting and self._combo_pulse_at is not None:
            progress = max(0, min(1, (self.t-self._combo_pulse_at)/300))
            pulse += .4*(1-progress)**2
        colour = QColor("white") if inspecting else self._combo_colour_at_time(self.t)
        self._draw_combo_value(painter, combo, left+total/2, y, scale, colour, 1, pulse)

    @staticmethod
    def _multiply_sprite(image, colour):
        """Multiply RGB while retaining texture shading and source alpha.

        Only a small temporary glyph is allocated; there is no per-frame cache
        that could grow throughout a long preview session.
        """
        if colour.red() == colour.green() == colour.blue() == 255:
            return image
        from PIL import Image, ImageChops
        source = image.toImage().convertToFormat(QImage.Format_RGBA8888)
        rgba = Image.frombytes("RGBA", (source.width(), source.height()), bytes(source.constBits()))
        overlay = Image.new("RGBA", rgba.size, (colour.red(), colour.green(), colour.blue(), 255))
        tinted = ImageChops.multiply(rgba, overlay)
        qt_image = QImage(tinted.tobytes(), tinted.width, tinted.height,
                          tinted.width*4, QImage.Format_RGBA8888).copy()
        return QPixmap.fromImage(qt_image)

    def _draw_combo_value(self, painter, combo, center_x, y, scale, colour, stretch_x=1, stretch_y=1):
        digits = [self._assets.digits[int(value)] for value in str(combo)]
        factor = getattr(self._assets, "hud_scale", .625)*scale
        painter.save()
        painter.setOpacity(painter.opacity()*colour.alphaF())
        painter.translate(center_x, y)
        painter.scale(stretch_x, stretch_y)
        painter.translate(-center_x, -y)
        if all(digits):
            sizes = [sprite.logical_size(self.t) for sprite in digits]
            overlap = self._assets.combo_overlap*factor
            width = sum(size[0]*factor for size in sizes)-overlap*(len(digits)-1)
            x = center_x-width/2
            for sprite, (digit_width, digit_height) in zip(digits, sizes):
                rect = QRectF(x, y-digit_height*factor/2, digit_width*factor, digit_height*factor)
                image = self._multiply_sprite(sprite.frame_at(self.t), colour)
                painter.drawPixmap(rect, image, QRectF(image.rect()))
                x += digit_width*factor-overlap
        else:
            painter.setFont(QFont("Segoe UI", max(8, int(22*scale)), QFont.Bold))
            painter.setPen(QColor(colour.red(), colour.green(), colour.blue()))
            painter.drawText(QRectF(center_x-200*scale, y-28*scale, 400*scale, 56*scale), Qt.AlignCenter, str(combo))
        painter.restore()

    def _draw_comboburst(self, painter, field, scale, left, total):
        if not self._last_burst or not self._assets.combo_bursts:
            return
        when, manual = self._last_burst
        age = self._effect_age(when, manual)
        if not manual and age > 1500:
            return
        index = self._manual_burst_index if manual else max(0, self.game.combo//50-1)
        sprite = self._assets.combo_bursts[index % len(self._assets.combo_bursts)]
        style = str(self._settings.get("comboburststyle", "1")).strip().lower()
        right_side = style not in ("0", "left")
        if style in ("2", "both"):
            # Stable uses a random side; deterministic alternation makes both
            # placements inspectable without changing the skin's settings.
            right_side = index % 2 == 0
        width, height = sprite.logical_size(age, loop=manual)
        width, height = width*scale/1.6, height*scale/1.6
        rect = QRectF(left+total if right_side else left-width, field.bottom()-height, width, height)
        image = sprite.frame_at(age, loop=manual)
        if right_side:
            painter.save()
            painter.translate(rect.left()+rect.right(), 0)
            painter.scale(-1, 1)
        painter.drawPixmap(rect, image, QRectF(image.rect()))
        if right_side:
            painter.restore()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        background = QLinearGradient(0, 0, self.width(), self.height())
        background.setColorAt(0, QColor("#101d30"))
        background.setColorAt(1, QColor("#08111e"))
        p.fillRect(self.rect(), background)
        p.setPen(QColor("#8090aa"))
        p.setFont(QFont("Segoe UI", 9))
        if self.test_mode == "demo":
            summary = f"{self.keys}K  /  " + i18n.t("preview.sample_pattern", "示例音符")
        else:
            mode = i18n.t("playtest.auto", "自动演示") if self.test_mode == "auto" else i18n.t("playtest.play", "键盘试玩")
            summary = (f"{self.keys}K  /  {mode}    {self.game.accuracy:.2f}%    "
                       f"{i18n.t('playtest.combo', '连击')} {self.game.combo}    "
                       f"{i18n.t('playtest.score', '分数')} {self.game.score}")
        p.drawText(QRectF(24, 4, self.width()-48, 32), Qt.AlignLeft | Qt.AlignVCenter, summary)
        field, scale, widths, spacing, left, hit_y = self._geometry()
        total = sum(widths)+sum(spacing)
        p.save()
        p.setClipRect(self._scene_rect)
        self._draw_stage_sides(p, field, scale, left, total)
        p.restore()
        p.save()
        p.setClipRect(field)
        if str(self._settings.get("upsidedown", "0")).lower() in ("1", "true"):
            p.translate(0, field.top()+field.bottom())
            p.scale(1, -1)
        lane_x = left
        # Adjacent fractional lane edges must share full pixel coverage. AA
        # creates hairline gaps revealing the blue editor behind a black stage.
        p.setRenderHint(QPainter.Antialiasing, False)
        for i, width in enumerate(widths):
            lane = QRectF(lane_x, field.top(), width, field.height())
            p.fillRect(lane, self._lane_colours[i])
            line_width = self.layout["ColumnLineWidth"][i]*scale
            if line_width > 0:
                p.setPen(QPen(self._line_colour, line_width))
                p.drawLine(lane.topLeft(), lane.bottomLeft())
            lane_x += width + (spacing[i] if i < self.keys-1 else 0)
        line_width = self.layout["ColumnLineWidth"][-1]*scale
        if line_width > 0:
            p.setPen(QPen(self._line_colour, line_width))
            p.drawLine(int(lane_x), int(field.top()), int(lane_x), int(field.bottom()))
        p.setRenderHint(QPainter.Antialiasing, True)
        # The hit target is a background element, behind receptors and notes.
        if self._stage_hint is not None:
            p.drawPixmap(self._stage_hint_rect(left, total, hit_y, scale), self._stage_hint,
                         QRectF(self._stage_hint.rect()))
        if self._judgement_line:
            height = scale/1.6
            p.fillRect(QRectF(left, hit_y-height/2, total, height), self._judgement_colour)
        self._draw_lighting(p, field, scale, widths, spacing, left, hit_y, stage_only=True)
        if self._keys_under_notes:
            self._draw_keys(p, field, scale, widths, spacing, left)
        self._draw_notes(p, field, scale, widths, spacing, left, hit_y)
        if not self._keys_under_notes:
            self._draw_keys(p, field, scale, widths, spacing, left)
        self._draw_lighting(p, field, scale, widths, spacing, left, hit_y)
        p.restore()
        p.save()
        p.setClipRect(self._scene_rect)
        # StageBottom can extend beyond the virtual game's edges. Its entire
        # visible bounds participate in the same scene fit as the side art.
        p.save()
        if self._bool(self._settings.get("upsidedown")):
            p.translate(0, field.top()+field.bottom())
            p.scale(1, -1)
        self._draw_stage_bottom(p, field, scale, left, total)
        p.restore()
        self._draw_comboburst(p, field, scale, left, total)
        self._draw_combo(p, field, scale, left, total)
        self._draw_judgement(p, field, scale, left, total)
        p.restore()
        if self._show_hit_guide:
            guide_y = (field.top()+field.bottom()-hit_y
                       if self._bool(self._settings.get("upsidedown")) else hit_y)
            p.save()
            p.setClipRect(field)
            p.setPen(QPen(QColor("#68e5ee"), 1.5, Qt.DashLine))
            p.drawLine(int(left), int(guide_y), int(left+total), int(guide_y))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(QRectF(left, guide_y-24, max(total, 180), 20),
                       Qt.AlignLeft | Qt.AlignVCenter, f"HitPosition {self.effective_hit_position:g}")
            p.restore()
        if self.test_mode == "play" and self.skin:
            p.setPen(QColor("#94cadf"))
            p.setFont(QFont("Segoe UI", 9))
            hint = (i18n.t("playtest.finished_hint", "本轮结束，点击轨道重新开始") if self.game.finished else
                    i18n.t("playtest.click_hint", "点击轨道开始 / 继续") if not self._playing or not self._input_armed else
                    i18n.t("playtest.escape_hint", "Esc 暂停"))
            labels = "  ".join(self.key_labels)
            p.drawText(QRectF(20, self.height()-26, self.width()-40, 24), Qt.AlignCenter, f"{hint}  ·  {labels}")
        elif not self.skin:
            p.setPen(QColor("#94a3bd"))
            p.drawText(self.rect().adjusted(20, 0, -20, -8), Qt.AlignBottom | Qt.AlignHCenter,
                       i18n.t("preview.open_hint", "打开皮肤文件夹或导入 .osk，开始预览"))
