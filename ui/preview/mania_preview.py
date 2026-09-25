# -*- coding: utf-8 -*-
"""A lightweight skin preview with sample notes, not a beatmap simulation."""
from pathlib import Path
import math
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QPixmap, QLinearGradient, QImage
from PySide6.QtCore import Qt, QRectF, QTimer, QElapsedTimer
from core.skin_ini import SkinIni
from core.mania_designer import ManiaDesign, build_preview_overlay, effective_hit_position, DesignValidationError
from core.mania_timeline import bounded_number, travel_time_ms, sample_note_ages
from core import i18n


class ManiaPreview(QWidget):
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
        self._design_overlay = None
        self._overlay_density = 2
        self.design_options = ManiaDesign()
        self.scroll_speed = 20.0
        self.demo_bpm = 120.0
        self._show_hit_guide = False
        self.t = 0
        self._playing = True
        self._clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.tick)
        self._load_layout_for_keys(self.keys)

    def set_playing(self, playing: bool):
        self._playing = bool(playing)
        if self._playing and self.isVisible():
            if not self.timer.isActive():
                self._clock.start()
            self.timer.start()
        else:
            self.timer.stop()
            self._clock.invalidate()

    def set_scroll_speed(self, value):
        self.scroll_speed = bounded_number(value, 1, 40, 20)
        self.update()

    def set_demo_bpm(self, value):
        self.demo_bpm = bounded_number(value, 40, 300, 120)
        self.update()

    def restart_demo(self):
        self.t = 0
        if self.timer.isActive():
            self._clock.start()
        else:
            self._clock.invalidate()
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
        if image is not None:
            rgba = image.convert("RGBA")
            # QImage must own its bytes after the local PIL image is released.
            qt_image = QImage(rgba.tobytes(), rgba.width, rgba.height,
                             rgba.width*4, QImage.Format_RGBA8888).copy()
            self._design_overlay = QPixmap.fromImage(qt_image)
            self._design_overlay.setDevicePixelRatio(1.0)
            self._overlay_density = image.info.get("skin_density", 2)

    def showEvent(self, event):
        super().showEvent(event)
        self.set_playing(self._playing)

    def hideEvent(self, event):
        self.timer.stop()
        self._clock.invalidate()
        super().hideEvent(event)

    def tick(self):
        if not self._playing or not self.isVisible():
            return
        if self._clock.isValid():
            self.t += self._clock.restart()
        else:
            self._clock.start()
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
            "WidthForNoteHeightScale": self._int_or_none(d.get("widthfornoteheightscale")),
        }
        self._note_images = []
        self._key_images = []
        self._image_density = {}
        for i in range(k):
            suffix = "S" if k % 2 and i == k//2 else str(1 + min(i, k-i-1) % 2)
            self._note_images.append(self._pix(d.get(f"noteimage{i}", f"mania-note{suffix}")))
            self._key_images.append(self._pix(d.get(f"keyimage{i}", f"mania-key{suffix}")))
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

    def _geometry(self):
        """Use the same 480-high skin coordinate system for all positions."""
        available = QRectF(self.rect().adjusted(28, 44, -28, -28))
        start = self.layout["ColumnStart"]
        start = 136 if start is None else start
        logical_width = max(640, start + sum(self.layout["ColumnWidth"]) + sum(self.layout["ColumnSpacing"]) + 19)
        scale = max(0.01, min(available.height()/480.0, available.width()/logical_width))
        field = QRectF(available.left(), available.top() + (available.height()-480*scale)/2,
                       available.width(), 480*scale)
        widths = [max(1, n)*scale for n in self.layout["ColumnWidth"]]
        spacing = [n*scale for n in self.layout["ColumnSpacing"]]
        total = sum(widths) + sum(spacing)
        start = self.layout["ColumnStart"]
        if start is None:
            left = field.left() + (136*scale if self.skin else (field.width()-total)/2)
        else:
            left = field.left() + start*scale
        hit_y = field.top() + self.effective_hit_position*scale
        return field, scale, widths, spacing, left, hit_y

    def _key_rect(self, column, lane_x, width, field, scale):
        key = self._key_images[column]
        # Legacy keys stretch horizontally only. SD textures are in a 768-high
        # image space, while skin.ini uses 480: 480 / 768 = 0.625.
        # Their transparent padding is authored for a bottom-of-screen anchor.
        density = self._image_density.get(key.cacheKey(), 1) if key is not None else 1
        height = key.height()/density/1.6*scale if key is not None else 64*scale
        # Export bakes an integer number of transparent image pixels below each
        # receptor. Match that rounding per density, including sub-unit lifts.
        padding = round(float(self.design_options.receptor_raise)*1.6*density)
        lift = padding/density/1.6*scale
        return QRectF(lane_x, field.bottom()-height-lift, width, height)

    def _stage_bottom_rect(self, left, total, field, scale):
        image = self._design_overlay if self._design_overlay is not None else self._stage_bottom
        if image is None:
            return QRectF()
        density = (self._overlay_density if self._design_overlay is not None
                   else self._image_density.get(image.cacheKey(), 1))
        width, height = image.width()/density*scale, image.height()/density*scale
        return QRectF(left+total/2-width/2, field.bottom()-height, width, height)

    def _draw_stage_bottom(self, painter, field, scale, left, total):
        image = self._design_overlay if self._design_overlay is not None else self._stage_bottom
        if image is not None:
            painter.drawPixmap(self._stage_bottom_rect(left, total, field, scale), image, QRectF(image.rect()))

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
            rect = self._key_rect(index, lane_x, width, field, scale)
            if key is not None:
                painter.drawPixmap(rect, key, QRectF(key.rect()))
            else:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor("#29364a"))
                painter.drawRoundedRect(rect.adjusted(3, 0, -3, 0), 3, 3)
            lane_x += width + (spacing[index] if index < self.keys-1 else 0)

    def _draw_notes(self, painter, field, scale, widths, spacing, left, hit_y):
        lane_x = left
        duration = self.travel_time_ms
        for index, width in enumerate(widths):
            note = self._note_images[index]
            for age in sample_note_ages(self.t, index, self.keys, self.demo_bpm, duration):
                progress = age/duration
                bottom = field.top() + progress*(hit_y-field.top())
                rect = self._note_rect(index, lane_x, width, bottom, scale, widths)
                if note is not None:
                    painter.drawPixmap(rect, note, QRectF(note.rect()))
                else:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor("#70d7e0"))
                    painter.drawRoundedRect(rect.adjusted(3, 0, -3, 0), 3, 3)
            lane_x += width + (spacing[index] if index < self.keys-1 else 0)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        background = QLinearGradient(0, 0, self.width(), self.height())
        background.setColorAt(0, QColor("#101d30"))
        background.setColorAt(1, QColor("#08111e"))
        p.fillRect(self.rect(), background)
        p.setPen(QColor("#8090aa"))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(24, 27, f"{self.keys}K  /  " + i18n.t("preview.sample_pattern", "示例音符"))
        field, scale, widths, spacing, left, hit_y = self._geometry()
        total = sum(widths)+sum(spacing)
        p.save()
        p.setClipRect(field)
        if str(self._settings.get("upsidedown", "0")).lower() in ("1", "true"):
            p.translate(0, field.top()+field.bottom())
            p.scale(1, -1)
        lane_x = left
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
        # The hit target is a background element, behind receptors and notes.
        if self._stage_hint is not None:
            p.drawPixmap(self._stage_hint_rect(left, total, hit_y, scale), self._stage_hint,
                         QRectF(self._stage_hint.rect()))
        if self._judgement_line:
            height = scale/1.6
            p.fillRect(QRectF(left, hit_y-height/2, total, height), self._judgement_colour)
        if self._keys_under_notes:
            self._draw_keys(p, field, scale, widths, spacing, left)
        self._draw_notes(p, field, scale, widths, spacing, left, hit_y)
        if not self._keys_under_notes:
            self._draw_keys(p, field, scale, widths, spacing, left)
        # StageBottom is a foreground texture. The composite already includes
        # the original artwork; painting it twice would darken alpha pixels.
        self._draw_stage_bottom(p, field, scale, left, total)
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
        if not self.skin:
            p.setPen(QColor("#94a3bd"))
            p.drawText(self.rect().adjusted(20, 0, -20, -8), Qt.AlignBottom | Qt.AlignHCenter,
                       i18n.t("preview.open_hint", "打开皮肤文件夹或导入 .osk，开始预览"))
