"""Case-insensitive, bounded legacy Mania sprite loading for the skin tester.

All sizes are SD image pixels, not the 480-high stage coordinates. Keys and
HUD artwork are drawn at 1 / 1.6 of this size in the stage; notes instead scale
to their column width. An existing transparent image is still a valid image.

Animation lookup follows ppy/osu's LegacySkinExtensions: a contiguous -0,
-1, ... sequence wins over the static PNG, and stops at its first missing
frame. Numeric padding is not guessed. Recreate the collection on skin reload
to avoid QPixmap's filename cache returning an old edited image.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from pathlib import Path, PureWindowsPath

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage, QImageReader, QPixmap
from PIL import Image


MAX_ANIMATION_FRAMES = 256
MAX_IMAGE_PIXELS = 24_000_000
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_DECODED_BYTES = 192 * 1024 * 1024

# The named legacy enumeration is not the wiki's shortened 0/1/2 table.
# ppy/osu's LegacyManiaSkinDecoder parses these names/numbers directly. Keep
# the old Repeat value (1) for skins which deliberately tile the entire image.
NOTE_BODY_STYLES = {
    "stretch": 0,
    "repeat": 1,
    "repeattop": 2,
    "repeatbottom": 3,
    "repeattopandbottom": 4,
}


def _number(value, default, low=None, high=None):
    try:
        result = float(value)
        if not math.isfinite(result):
            return default
    except (TypeError, ValueError):
        return default
    if low is not None:
        result = max(low, result)
    if high is not None:
        result = min(high, result)
    return result


def _bool(value, default=False):
    if value is None or not str(value).strip():
        return default
    return str(value).strip().casefold() in ("1", "true", "yes", "on")


def _direction_flag(value, default):
    """Invalid/empty optional overrides keep the inherited direction flag."""
    word = str(value).strip().casefold()
    if word in ("1", "true", "yes", "on"):
        return True
    if word in ("0", "false", "no", "off"):
        return False
    return default


def note_body_style(value, default=3):
    """Return a legacy body-style value without truncating malformed numbers."""
    word = str(value).strip().casefold()
    if word in NOTE_BODY_STYLES:
        return NOTE_BODY_STYLES[word]
    number = _number(value, None)
    if number is not None and number.is_integer() and 0 <= number <= 4:
        return int(number)
    return default


def _colour(value, default):
    try:
        channels = [int(part.strip()) for part in str(value).split(",")]
        if len(channels) == 3:
            channels.append(255)
        if len(channels) != 4 or any(not 0 <= channel <= 255 for channel in channels):
            raise ValueError
        return QColor(*channels)
    except (ValueError, TypeError):
        return QColor(*default)


def _folded(values):
    return {str(key).casefold(): value for key, value in (values or {}).items()}


def _section(config, name):
    if config is None:
        return {}
    if hasattr(config, "sections"):
        for section in config.sections():
            if section.casefold() == name.casefold():
                return _folded(dict(config.items(section)))
    elif isinstance(config, Mapping):
        for section, values in config.items():
            if str(section).casefold() == name.casefold() and isinstance(values, Mapping):
                return _folded(values)
    return {}


def sprite_ink_bounds(sprite):
    """Logical alpha bounds per frame, computed once rather than while painting.

    Empty margins retain their authored positions. They simply do not force a
    whole-stage zoom-out when an otherwise small decoration has a huge canvas.
    """
    if sprite is None:
        return ()
    result = []
    for pixmap, density in zip(sprite.frames, sprite.densities):
        image = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
        rgba = Image.frombytes("RGBA", (image.width(), image.height()), bytes(image.constBits()),
                               "raw", "RGBA", image.bytesPerLine())
        bounds = rgba.getchannel("A").getbbox()
        result.append(tuple(value / density for value in bounds) if bounds else None)
    return tuple(result)


@dataclass(frozen=True)
class Sprite:
    """Immutable frame collection; QPixmap DPR is always one.

    Density is tracked per frame because skins sometimes mix SD and HD frames.
    Use frame_at and density_at with the same time and loop arguments.
    """

    frames: tuple[QPixmap, ...]
    densities: tuple[int, ...]
    frame_rate: float = 60.0
    paths: tuple[Path, ...] = ()

    def _index(self, elapsed_ms=0, loop=True):
        elapsed = _number(elapsed_ms, 0, 0)
        index = int(elapsed * self.frame_rate / 1000)
        return index % len(self.frames) if loop else min(index, len(self.frames) - 1)

    def frame_at(self, elapsed_ms=0, loop=True):
        return self.frames[self._index(elapsed_ms, loop)]

    def density_at(self, elapsed_ms=0, loop=True):
        return self.densities[self._index(elapsed_ms, loop)]

    def logical_size(self, elapsed_ms=0, loop=True):
        index = self._index(elapsed_ms, loop)
        return (self.frames[index].width() / self.densities[index],
                self.frames[index].height() / self.densities[index])

    @property
    def density(self):
        return self.densities[0]

    @property
    def animated(self):
        return len(self.frames) > 1


def default_column_suffixes(keys, special_style=0, split_stages=False):
    """Official 1-9K mirrored column pattern, repeated for split stages.

    SpecialStyle moves the special key to a side on even stages above 4K.
    The regular keys retain the even pattern after removing that side key.
    Explicit per-column filenames always take precedence over this fallback.
    """
    keys = int(_number(keys, 4, 1, 18))
    style = str(special_style).strip().casefold()
    side = {"1": "left", "left": "left", "2": "right", "right": "right"}.get(style)
    stage_counts = [keys]
    if (split_stages or keys > 9) and keys > 1:
        stage_counts = [(keys + 1) // 2, keys // 2]
    result = []
    for stage_index, count in enumerate(stage_counts):
        regular = ["S" if count % 2 and i == count // 2
                   else str(1 + min(i, count - i - 1) % 2)
                   for i in range(count)]
        if side and count > 4 and count % 2 == 0:
            # The remaining odd group has regular keys at its centre. The
            # special lane is outer/inner on the right half of a split stage.
            regular = [str(1 + min(i, count - 2 - i) % 2) for i in range(count - 1)]
            actual_side = side
            if len(stage_counts) == 2 and stage_index == 1:
                actual_side = "right" if side == "left" else "left"
            if actual_side == "left":
                regular = ["S"] + regular
            else:
                regular = regular + ["S"]
        result.extend(regular)
    return tuple(result)


class ManiaSkinAssets:
    """Read-only assets and drawing metadata for one keycount.

    Missing optional assets are None. `missing` records requested paths so the
    caller can distinguish a generic fallback from an author-supplied texture.
    No disk reads are necessary while painting.
    """

    def __init__(self, root, settings=None, config=None, keys=4):
        self.root = Path(root).resolve() if root is not None else None
        self.settings = _folded(settings)
        self.keys = int(_number(keys, 4, 1, 18))
        self._directories = {}
        self._images = {}
        self._sprites = {}
        self._decoded_bytes = 0
        self.missing = {}
        self.warnings = []
        general = _section(config, "General")
        fonts = _section(config, "Fonts")
        version = str(general.get("version", "1.0")).strip().casefold()
        self.version = 2.7 if version == "latest" else _number(version, 1.0)
        self.hud_scale = 1.0 / 1.6
        self.score_position = _number(self.settings.get("scoreposition"), 300, 0, 480)
        self.combo_position = _number(self.settings.get("comboposition"), 111, 0, 480)
        self.combo_overlap = _number(fonts.get("combooverlap"), 0, -256, 256)
        self.combo_prefix = str(fonts.get("comboprefix") or "score").strip()
        self.hold_colour = _colour(self.settings.get("colourhold"), (255, 191, 51, 255))
        self.break_colour = _colour(self.settings.get("colourbreak"), (255, 0, 0, 255))
        self.column_suffixes = default_column_suffixes(
            self.keys, self.settings.get("specialstyle", 0),
            _bool(self.settings.get("splitstages")))

        self.notes = []
        self.hold_heads = []
        self.hold_bodies = []
        self.hold_tails = []
        self.keys_up = []
        self.keys_down = []
        self.body_styles = []
        # Flags describe permission to flip with an element's effective
        # direction, not an unconditional image transform. Notes/heads/bodies
        # and keys face up only during upscroll; a hold tail faces the inverse
        # direction, so its default image flip occurs during downscroll.
        # Geometry anchors must be handled separately by the renderer.
        self.note_flips = []
        self.head_flips = []
        self.body_flips = []
        self.key_flips = []
        self.key_down_flips = []
        self.tail_flips = []
        default_style = 3 if self.version >= 2.5 else 0
        global_style = note_body_style(self.settings.get("notebodystyle"), default_style)
        modern_flips = self.version >= 2.5
        note_flip = _direction_flag(self.settings.get("noteflipwhenupsidedown"), modern_flips)
        key_flip = _direction_flag(self.settings.get("keyflipwhenupsidedown"), modern_flips)

        def flip(prefix, column, suffix, inherited):
            # The optional suffix-wide forms (notably ...T) appear in the
            # official Mania image documentation, while the INI reference
            # lists the global and column-specific forms.
            part_default = _direction_flag(self.settings.get(prefix + suffix), inherited)
            return _direction_flag(self.settings.get(f"{prefix}{column}{suffix}"), part_default)

        for i, suffix in enumerate(self.column_suffixes):
            note = self._configured(f"noteimage{i}", f"mania-note{suffix}")
            head = self._configured(f"noteimage{i}h", f"mania-note{suffix}H") or note
            body = self._configured(f"noteimage{i}l", f"mania-note{suffix}L")
            tail = self._configured(f"noteimage{i}t", f"mania-note{suffix}T") or head
            up = self._configured(f"keyimage{i}", f"mania-key{suffix}", animated=False)
            down = self._configured(f"keyimage{i}d", f"mania-key{suffix}D", animated=False) or up
            self.notes.append(note)
            self.hold_heads.append(head)
            self.hold_bodies.append(body)
            self.hold_tails.append(tail)
            self.keys_up.append(up)
            self.keys_down.append(down)
            self.body_styles.append(note_body_style(self.settings.get(f"notebodystyle{i}"), global_style))
            self.note_flips.append(flip("noteflipwhenupsidedown", i, "", note_flip))
            self.head_flips.append(flip("noteflipwhenupsidedown", i, "h", note_flip))
            self.body_flips.append(flip("noteflipwhenupsidedown", i, "l", note_flip))
            self.tail_flips.append(flip("noteflipwhenupsidedown", i, "t", note_flip))
            self.key_flips.append(flip("keyflipwhenupsidedown", i, "", key_flip))
            self.key_down_flips.append(flip("keyflipwhenupsidedown", i, "d", key_flip))

        self.judgements = {kind: self._configured(f"hit{kind}", f"mania-hit{kind}")
                           for kind in ("300g", "300", "200", "100", "50", "0")}
        self.digits = [self.load(f"{self.combo_prefix}-{i}", animated=False) for i in range(10)]
        for digit, sprite in enumerate(self.digits):
            if sprite is None:
                self.missing[f"combo-{digit}"] = f"{self.combo_prefix}-{digit}"

        burst = self.load("comboburst-mania")
        self.combo_bursts = ([] if burst is None else
                             [Sprite((frame,), (density,), paths=(path,))
                              for frame, density, path in zip(burst.frames, burst.densities, burst.paths)])
        light_rate = _number(self.settings.get("lightframepersecond"), 60, 1, 240)
        self.stage_light = self._configured("stagelight", "mania-stage-light", frame_rate=light_rate)
        self.lighting_n = self._configured("lightingn", "lightingN")
        self.lighting_l = self._configured("lightingl", "lightingL")
        self.stage_hint = self._configured("stagehint", "mania-stage-hint", animated=False)
        self.stage_bottom = self._configured("stagebottom", "mania-stage-bottom")
        # Official legacy side artwork is static. Also accept frame sequences
        # in the tester so editing an animated variant remains inspectable.
        self.stage_left = self._configured("stageleft", "mania-stage-left")
        self.stage_right = self._configured("stageright", "mania-stage-right")
        self.stage_left_bounds = sprite_ink_bounds(self.stage_left)
        self.stage_right_bounds = sprite_ink_bounds(self.stage_right)
        self.stage_bottom_bounds = sprite_ink_bounds(self.stage_bottom)
        self.combo_burst_bounds = tuple(sprite_ink_bounds(sprite)[0] for sprite in self.combo_bursts)

    def _configured(self, option, default, **kwargs):
        name = self.settings.get(option) or default
        sprite = self.load(name, **kwargs)
        if sprite is None:
            self.missing[option] = str(name)
        return sprite

    @staticmethod
    def _normal_name(name):
        text = str(name or "").strip().replace("\\", "/")
        if not text or "\0" in text or text.startswith("/") or PureWindowsPath(text).drive:
            return None
        parts = text.split("/")
        if any(part in ("", ".", "..") or ":" in part for part in parts):
            return None
        if text.casefold().endswith(".png"):
            text = text[:-4]
        if text.casefold().endswith("@2x"):
            text = text[:-3]
        return text or None

    def _resolve(self, name):
        if self.root is None:
            return None
        current = self.root
        for part in name.split("/"):
            if current not in self._directories:
                try:
                    children = sorted(current.iterdir(), key=lambda p: (p.name.casefold(), p.name))
                except OSError:
                    return None
                folded = {}
                for child in children:
                    folded.setdefault(child.name.casefold(), child)
                self._directories[current] = folded
            current = self._directories[current].get(part.casefold())
            if current is None:
                return None
            try:
                current.resolve().relative_to(self.root)
            except (OSError, ValueError, RuntimeError):
                return None
        return current if current.is_file() else None

    def _image(self, name):
        """Load fresh bytes and preserve density without Qt filename caching."""
        for density, suffix in ((2, "@2x.png"), (1, ".png")):
            path = self._resolve(name + suffix)
            if path is None:
                continue
            if path in self._images:
                cached = self._images[path]
                if cached is not None:
                    return cached, density, path
                continue
            self._images[path] = None
            try:
                if path.stat().st_size > MAX_IMAGE_BYTES:
                    self.warnings.append(f"Image file exceeds preview size limit: {path.name}")
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            # Some widely-used skins contain a TIFF/BMP renamed to .png. Qt
            # detects the actual format while still checking header dimensions
            # before decoding (Capoo's LN body is a 138 x 40000 TIFF).
            buffer = QBuffer()
            buffer.setData(QByteArray(data))
            buffer.open(QIODevice.ReadOnly)
            reader = QImageReader(buffer)
            reader.setDecideFormatFromContent(True)
            size = reader.size()
            width, height = size.width(), size.height()
            allocation = width * height * 4
            if (width <= 0 or height <= 0):
                continue
            if (width * height > MAX_IMAGE_PIXELS or
                    self._decoded_bytes + allocation > MAX_DECODED_BYTES):
                self.warnings.append(f"Image dimensions exceed preview size limit: {path.name}")
                continue
            image = reader.read()
            if image.isNull():
                continue
            pixmap = QPixmap.fromImage(image)
            pixmap.setDevicePixelRatio(1.0)
            self._images[path] = pixmap
            self._decoded_bytes += allocation
            return pixmap, density, path
        return None

    def load(self, name, *, animated=True, frame_rate=60):
        normal = self._normal_name(name)
        if normal is None:
            return None
        rate = _number(frame_rate, 60, 1, 240)
        cache_key = (normal.casefold(), bool(animated), rate)
        if cache_key in self._sprites:
            return self._sprites[cache_key]
        frames = []
        if animated:
            for frame in range(MAX_ANIMATION_FRAMES):
                loaded = self._image(f"{normal}-{frame}")
                if loaded is None:
                    break
                frames.append(loaded)
        if not frames:
            single = self._image(normal)
            if single is not None:
                frames = [single]
        result = None
        if frames:
            images, densities, paths = zip(*frames)
            result = Sprite(images, densities, rate, paths)
        self._sprites[cache_key] = result
        return result
