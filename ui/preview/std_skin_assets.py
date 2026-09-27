"""Read-only legacy osu!standard artwork and configuration for the test scene.

Sprite bounds retain authored padding, @2x density, and transparent placeholders.
The shared Sprite value type is used without loading any Mania resources.
"""
from pathlib import Path, PureWindowsPath
from collections import OrderedDict
import math

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF
from PySide6.QtGui import QImageReader, QPixmap, QColor
from ui.preview.mania_skin_assets import Sprite


def number(value, default, low=-10000, high=10000):
    try:
        result = float(value)
        return max(low, min(high, result)) if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def boolean(value, default=False):
    return default if value is None else str(value).strip().lower() in ("1", "true", "yes", "on")


def colour(value, default):
    try:
        channels = [int(v.strip()) for v in str(value).split(",")]
        if len(channels) not in (3, 4) or any(not 0 <= v <= 255 for v in channels):
            raise ValueError
        return QColor(*channels)
    except (ValueError, TypeError):
        return QColor(default)


class StdSkinAssets:
    def __init__(self, skin):
        self.root = Path(skin.root).resolve() if skin else None
        self._dirs, self._images, self._sprites = {}, {}, {}
        self._decoded = 0
        self._tinted = OrderedDict()
        self._visible_bounds = {}
        self.missing = []
        sections = {}
        if skin and skin.ini:
            sections = {s.casefold(): {k.casefold(): v for k, v in skin.ini.items(s)} for s in skin.ini.sections()}
        general, fonts = sections.get("general", {}), sections.get("fonts", {})
        colours = sections.get("colours", sections.get("colors", {}))
        version = str(general.get("version", "1")).strip().lower()
        self.version = 2.7 if version == "latest" else number(version, 1)
        self.frame_rate = number(general.get("animationframerate"), -1, -1, 240)
        self.overlay_above = boolean(general.get("hitcircleoverlayabovenumber"), True)
        self.slider_ball_tint = boolean(general.get("allowsliderballtint"), False)
        self.slider_ball_flip = boolean(general.get("sliderballflip"), False)
        self.slider_ball_colour = colour(colours.get("sliderball"), "white")
        self.slider_border = colour(colours.get("sliderborder"), "white")
        self.slider_track = colour(colours.get("slidertrackoverride"), "#333333") if "slidertrackoverride" in colours else None
        self.spinner_background = colour(colours.get("spinnerbackground"), "#646464")
        self.cursor_center = boolean(general.get("cursorcentre"), True)
        self.cursor_rotate = boolean(general.get("cursorrotate"), True)
        self.cursor_expand = boolean(general.get("cursorexpand"), True)
        self.cursor_trail_rotate = boolean(general.get("cursortrailrotate"), False)
        self.combo_colours = [colour(colours[f"combo{i}"], "#6bd5ed") for i in range(1, 9) if f"combo{i}" in colours]
        if not self.combo_colours:
            self.combo_colours = [QColor("#ffbe00"), QColor("#00ca00"), QColor("#127cff"), QColor("#f21839")]
        # Legacy combo colours start at Combo2 and wrap back to Combo1.
        if len(self.combo_colours) > 1:
            self.combo_colours = self.combo_colours[1:]+self.combo_colours[:1]
        self.overlaps = {key: number(fonts.get(key+"overlap"), 0, -256, 256) for key in ("hitcircle", "score", "combo")}
        self.fonts = {}
        for font, fallback in (("hitcircle", "default"), ("score", "score"), ("combo", "score")):
            prefix = str(fonts.get(font+"prefix") or fallback).strip()
            self.fonts[font] = {str(i): self.load(f"{prefix}-{i}", animated=False) for i in range(10)}
            for token in ("x", "dot", "percent"):
                self.fonts[font][{"dot": ".", "percent": "%"}.get(token, token)] = self.load(f"{prefix}-{token}", animated=False)
        self.images = {}
        static = ("hitcircle", "approachcircle", "cursor", "cursormiddle", "cursortrail", "reversearrow",
                  "sliderscorepoint", "sliderb-nd", "sliderb-spec", "spinner-background", "spinner-circle", "spinner-metre",
                  "spinner-bottom", "spinner-top", "spinner-middle", "spinner-middle2", "spinner-glow",
                  "spinner-approachcircle", "spinner-spin", "spinner-clear", "spinner-rpm", "scorebar-bg",
                  "scorebar-marker", "scorebar-ki", "scorebar-kidanger", "scorebar-kidanger2")
        for name in static:
            self.images[name] = self.load(name, animated=False)
        for name in ("hitcircleoverlay", "sliderstartcircle", "sliderstartcircleoverlay", "sliderendcircle",
                     "sliderendcircleoverlay", "sliderfollowcircle", "followpoint", "hit300", "hit100", "hit50", "hit0",
                     "scorebar-colour"):
            self.images[name] = self.load(name)
        self.images["sliderb"] = self.load("sliderb", separator="")
        burst = self.load("comboburst")
        self.combo_bursts = [] if burst is None else [Sprite((im,), (density,), paths=(path,))
                                                    for im, density, path in zip(burst.frames, burst.densities, burst.paths)]
        for name in ("hitcircle", "approachcircle", "sliderb", "sliderfollowcircle", "cursor"):
            if self.images[name] is None:
                self.missing.append(name)
        for name in ("hit300", "hit100", "hit50", "hit0"):
            if self.images[name] is None:
                self.missing.append(name)
        for font in self.fonts:
            if any(self.fonts[font][str(i)] is None for i in range(10)):
                self.missing.append(font+" digits")

    def _resolve(self, name):
        if not self.root:
            return None
        relative = str(name).replace("\\", "/")
        if PureWindowsPath(relative).drive or relative.startswith("/") or any(p in ("", ".", "..") or ":" in p for p in relative.split("/")):
            return None
        current = self.root
        for part in relative.split("/"):
            if current not in self._dirs:
                try:
                    self._dirs[current] = {p.name.casefold(): p for p in current.iterdir()}
                except OSError:
                    return None
            current = self._dirs[current].get(part.casefold())
            if current is None:
                return None
        try:
            current.resolve().relative_to(self.root)
        except (OSError, ValueError, RuntimeError):
            return None
        return current if current.is_file() else None

    def _image(self, name):
        for density, ending in ((2, "@2x.png"), (1, ".png")):
            path = self._resolve(name+ending)
            if path is None:
                continue
            if path in self._images:
                return self._images[path], density, path
            try:
                if path.stat().st_size > 32*1024*1024:
                    continue
                raw = QByteArray(path.read_bytes())
            except OSError:
                continue
            buffer = QBuffer(raw)
            buffer.open(QIODevice.ReadOnly)
            reader = QImageReader(buffer)
            reader.setDecideFormatFromContent(True)
            size = reader.size()
            allocation = size.width()*size.height()*4
            if size.width() <= 0 or size.height() <= 0 or allocation > 96*1024*1024 or self._decoded+allocation > 192*1024*1024:
                continue
            image = reader.read()
            if image.isNull():
                continue
            pix = QPixmap.fromImage(image)
            pix.setDevicePixelRatio(1)
            self._images[path] = pix
            self._decoded += allocation
            return pix, density, path
        return None

    def load(self, name, animated=True, separator="-"):
        name = str(name).strip().replace("\\", "/")
        if name.lower().endswith(".png"):
            name = name[:-4]
        cache_key = (name, animated, separator)
        if cache_key in self._sprites:
            return self._sprites[cache_key]
        frames = []
        if animated:
            for index in range(128):
                frame = self._image(f"{name}{separator}{index}")
                if frame is None:
                    break
                frames.append(frame)
        if not frames:
            single = self._image(name)
            if single:
                frames.append(single)
        sprite = None
        if frames:
            images, densities, paths = zip(*frames)
            sprite = Sprite(images, densities, self.frame_rate if self.frame_rate > 0 else max(1, len(frames)), paths)
        self._sprites[cache_key] = sprite
        return sprite

    def tinted(self, pixmap, tint):
        """Small bounded cache; alpha and authored texture shading are retained."""
        from PIL import Image, ImageChops
        from PySide6.QtGui import QImage
        if tint is None or tint == QColor("white"):
            return pixmap
        key = (pixmap.cacheKey(), tint.rgba())
        if key in self._tinted:
            self._tinted.move_to_end(key)
            return self._tinted[key]
        source = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
        image = Image.frombytes("RGBA", (source.width(), source.height()), bytes(source.constBits()))
        result = ImageChops.multiply(image, Image.new("RGBA", image.size, tint.getRgb()))
        output = QPixmap.fromImage(QImage(result.tobytes(), result.width, result.height,
                                          result.width*4, QImage.Format_RGBA8888).copy())
        # Do not retain enormous skin images or an unbounded animation history.
        if result.width*result.height*4 <= 1024*1024:
            self._tinted[key] = output
            while len(self._tinted) > 24:
                self._tinted.popitem(last=False)
        return output

    def visible_bounds(self, sprite):
        """Visible bounds in SD pixels, for fitting a whole oversized scene."""
        from PIL import Image
        from PySide6.QtGui import QImage
        if sprite is None:
            return QRectF()
        key = tuple(im.cacheKey() for im in sprite.frames)
        if key not in self._visible_bounds:
            bounds = QRectF()
            for pixmap, density in zip(sprite.frames, sprite.densities):
                image = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
                box = Image.frombytes("RGBA", (image.width(), image.height()), bytes(image.constBits())).getchannel("A").getbbox()
                if box:
                    left, top, right, bottom = box
                    bounds = bounds.united(QRectF(left/density, top/density, (right-left)/density, (bottom-top)/density))
            self._visible_bounds[key] = bounds
        return QRectF(self._visible_bounds[key])
