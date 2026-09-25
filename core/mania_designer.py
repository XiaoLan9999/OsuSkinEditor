"""Destructive-looking Mania designs exported safely into a new skin copy.

Coordinates are skin.ini's 480-high coordinate space. StageBottom is an
un-stretched, bottom-centred foreground: one SD image pixel is one logical
unit (LegacyStageForeground's 1.6 scale cancels the 768/480 playfield scale).
Keys instead use SD pixels / 1.6 and stretch horizontally to each column.

Primary references:
https://osu.ppy.sh/wiki/en/Skinning/osu!mania
https://github.com/ppy/osu/blob/master/osu.Game.Rulesets.Mania/Skinning/Legacy/LegacyStageForeground.cs
https://github.com/ppy/osu/blob/master/osu.Game.Rulesets.Mania/Skinning/Legacy/LegacyKeyArea.cs
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Mapping
from uuid import uuid4

from PIL import Image, ImageColor, ImageDraw

from core.skin_ini import SkinIni


class DesignValidationError(ValueError):
    """A design cannot be exported without changing its advertised meaning."""


@dataclass(frozen=True)
class ManiaDesign:
    top_mask_height: float = 0
    top_mask_fade: float = 0
    receptor_raise: float = 0
    link_hit_position: bool = True
    mask_colour: str = '#000000'


@dataclass(frozen=True)
class ExportResult:
    root: Path
    assets: tuple[Path, ...]
    hit_position: float


def _number(value, label: str) -> float:
    try:
        result = float(value)
        if math.isfinite(result):
            return result
    except (ValueError, TypeError):
        pass
    raise DesignValidationError(f'{label} must be a finite number')


def _settings(values: Mapping[str, str]) -> dict[str, str]:
    return {key.casefold(): value for key, value in values.items()}


def _is_true(value) -> bool:
    return str(value).strip().casefold() in ('1', 'true', 'yes', 'on')


def validate_design(design: ManiaDesign) -> None:
    top = _number(design.top_mask_height, 'Upper mask height')
    fade = _number(design.top_mask_fade, 'Upper mask fade')
    lift = _number(design.receptor_raise, 'Receptor raise')
    if top < 0 or fade < 0 or top + fade > 480:
        raise DesignValidationError('Upper mask height + fade must be between 0 and 480')
    if not 0 <= lift <= 240:
        raise DesignValidationError('Receptor raise must be between 0 and 240')
    try:
        ImageColor.getrgb(design.mask_colour)
    except (ValueError, TypeError) as exc:
        raise DesignValidationError('Choose a valid mask colour') from exc


def effective_hit_position(values: Mapping[str, str], design: ManiaDesign) -> float:
    """Return the gameplay target; receptor art only moves when raised separately."""
    validate_design(design)
    data = _settings(values)
    old = _number(data.get('hitposition', 402), 'HitPosition')
    new = old - float(design.receptor_raise) if design.link_hit_position else old
    if design.receptor_raise and design.link_hit_position and not 240 <= new <= 480:
        raise DesignValidationError(
            f'Linked HitPosition would be {new:g}; osu! supports 240–480; '
            'reduce the receptor raise or disable linking')
    return new


def lane_width(values: Mapping[str, str], keys: int) -> float:
    """Active lanes only; ColumnStart/ColumnRight are outside this width."""
    data = _settings(values)

    def numbers(name, count, default):
        raw = str(data.get(name, '')).split(',') if data.get(name) else []
        result = [_number(raw[i], name) if i < len(raw) else default for i in range(count)]
        if any(n < 0 or n > 4096 for n in result):
            raise DesignValidationError(f'{name} contains an unsupported width')
        return result

    widths = numbers('columnwidth', keys, 30)
    if any(n <= 0 for n in widths):
        raise DesignValidationError('ColumnWidth must be greater than zero')
    return sum(widths) + sum(numbers('columnspacing', keys - 1, 0))


def _ini(root: Path) -> SkinIni:
    if not root.is_dir():
        raise DesignValidationError(f'Skin folder does not exist: {root}')
    path = next((p for p in root.iterdir() if p.name.casefold() == 'skin.ini' and p.is_file()), None)
    if path is None:
        raise DesignValidationError('The source folder has no skin.ini')
    return SkinIni.read(path)


def _asset_base(root: Path, name: str) -> Path:
    """Resolve Windows-style, case-insensitive relative skin paths safely."""
    name = str(name).strip().replace('\\', '/')
    if name.lower().endswith('.png'):
        name = name[:-4]
    if name.lower().endswith('@2x'):
        name = name[:-3]
    parts = name.split('/')
    if not name or any(p in ('', '.', '..') or ':' in p for p in parts):
        raise DesignValidationError(f'Image path must stay within the skin folder: {name}')
    current = root
    for part in parts[:-1]:
        candidate = current / part
        if current.is_dir():
            candidate = next((p for p in current.iterdir() if p.name.casefold() == part.casefold()), candidate)
        current = candidate
    result = current / parts[-1]
    if not result.resolve().is_relative_to(root.resolve()):
        raise DesignValidationError(f'Image path leaves the skin folder: {name}')
    return result


def _variants(base: Path) -> dict[int, Path]:
    if not base.parent.is_dir():
        return {}
    desired = {f'{base.name}.png'.casefold(): 1, f'{base.name}@2x.png'.casefold(): 2}
    found = {}
    for path in base.parent.iterdir():
        density = desired.get(path.name.casefold())
        if density is not None and path.is_file():
            if path.is_symlink():
                raise DesignValidationError(f'Linked image files are not supported: {path.name}')
            found[density] = path
    return found


def _read_image(path: Path) -> Image.Image:
    try:
        with Image.open(path) as source:
            source.load()
            return source.convert('RGBA')
    except (OSError, ValueError) as exc:
        raise DesignValidationError(f'Cannot read image: {path.name}') from exc


def _stage_frames(root: Path, values: Mapping[str, str]) -> dict[str, dict[int, Path]]:
    data = _settings(values)
    base = _asset_base(root, data.get('stagebottom') or 'mania-stage-bottom')
    frames = {}
    static = _variants(base)
    if static:
        frames[''] = static
    if base.parent.is_dir():
        pattern = re.compile(re.escape(base.name) + r'-(\d+)(?:@2x)?\.png$', re.IGNORECASE)
        for path in base.parent.iterdir():
            match = pattern.fullmatch(path.name)
            if match:
                suffix = '-' + match.group(1)
                frames[suffix] = _variants(base.with_name(base.name + suffix))
    return frames or {'': {}}


def _at_density(variants: Mapping[int, Path], density: int) -> Image.Image | None:
    if not variants:
        return None
    source_density = density if density in variants else max(variants)
    image = _read_image(variants[source_density])
    if source_density != density:
        size = tuple(max(1, round(n * density / source_density)) for n in image.size)
        image = image.resize(size, Image.Resampling.NEAREST if density > source_density else Image.Resampling.LANCZOS)
    return image


def _compose_overlay(variants, width: float, design: ManiaDesign, density: int) -> Image.Image:
    old = _at_density(variants, density)
    lane_pixels = max(1, round(width * density))
    stage_pixels = 480 * density
    canvas_width = max(lane_pixels, old.width if old else 0)
    # Keep the original image centre on exactly the same pixel grid after
    # widening its canvas; otherwise odd/even widths move the artwork 0.5px.
    if old and (canvas_width - old.width) % 2:
        canvas_width += 1
    size = (canvas_width, max(stage_pixels, old.height if old else 0))
    if size[0] > 16384 or size[1] > 16384 or size[0] * size[1] > 40_000_000:
        raise DesignValidationError('StageBottom is too large to edit safely; reduce the image dimensions')
    output = Image.new('RGBA', size)
    if old:
        output.alpha_composite(old, ((size[0] - old.width) // 2, size[1] - old.height))
    top = round(float(design.top_mask_height) * density)
    fade = round(float(design.top_mask_fade) * density)
    if top or fade:
        # Build the cover separately and composite it, retaining original graphics
        # underneath partial-alpha fade pixels and everywhere outside the cover.
        cover = Image.new('RGBA', (lane_pixels, stage_pixels))
        painter = ImageDraw.Draw(cover)
        rgb = ImageColor.getrgb(design.mask_colour)[:3]
        if top:
            painter.rectangle((0, 0, lane_pixels - 1, top - 1), fill=(*rgb, 255))
        for y in range(fade):
            alpha = round(255 * (1 - (y + 1) / fade))
            painter.line((0, top + y, lane_pixels - 1, top + y), fill=(*rgb, alpha))
        output.alpha_composite(cover, ((size[0] - lane_pixels) // 2, size[1] - stage_pixels))
    output.info['skin_density'] = density
    return output


def _validate_layout(values: Mapping[str, str], keys: int, design: ManiaDesign) -> None:
    if not isinstance(keys, int) or not 1 <= keys <= 18:
        raise DesignValidationError('Choose a key count between 1 and 18')
    validate_design(design)
    effective_hit_position(values, design)
    lane_width(values, keys)
    data = _settings(values)
    if design.top_mask_height or design.top_mask_fade:
        if _is_true(data.get('upsidedown', '0')):
            raise DesignValidationError('Savable upper masks currently require downscroll (UpsideDown: 0)')
        if _is_true(data.get('splitstages', '0')) or keys > 9:
            raise DesignValidationError('Savable upper masks currently require a single stage with 1–9 keys')


def build_preview_overlay(root: Path, keys: int, design: ManiaDesign) -> Image.Image | None:
    """Return first StageBottom frame with mask, RGBA at density 2.

    Native logical rectangle: width=image.width/2, height=image.height/2;
    x=lane_centre-width/2, y=480-height. Never stretch to lane width. This
    includes original StageBottom art and must replace, not cover, that layer.
    image.info['skin_density'] is provided for callers not assuming density 2.
    """
    root = Path(root).resolve()
    values = _ini(root).mania_get(keys)
    _validate_layout(values, keys, design)
    frames = _stage_frames(root, values)
    # Animations override the static texture, just as in the skin loader.
    frame = frames.get('-0', frames.get('', next(iter(frames.values()))))
    if not design.top_mask_height and not design.top_mask_fade:
        image = _at_density(frame, 2)
        if image is not None:
            image.info['skin_density'] = 2
        return image
    return _compose_overlay(frame, lane_width(values, keys), design, 2)


def _key_fallback(keys: int, column: int, data: Mapping[str, str]) -> str:
    # For even keycounts using a special side key, refuse guessing when the
    # author did not specify KeyImage paths: stable/lazer differ here.
    if keys % 2 == 0 and data.get('specialstyle', '0') not in ('', '0'):
        raise DesignValidationError('Set explicit KeyImage paths before raising special-side-key layouts')
    suffix = 'S' if keys % 2 and column == keys // 2 else str(1 + min(column, keys - column - 1) % 2)
    return f'mania-key{suffix}'


def _prepare_key_images(root: Path, keys: int, values: Mapping[str, str], design: ManiaDesign):
    if not design.receptor_raise:
        return {}
    data = _settings(values)
    result = {}
    for column in range(keys):
        for state in ('', 'D'):
            option = f'KeyImage{column}{state}'
            name = data.get(option.casefold())
            if not name:
                name = _key_fallback(keys, column, data) + state
            variants = _variants(_asset_base(root, name))
            if not variants:
                raise DesignValidationError(
                    f'{option}: image "{name}" is missing; add the actual idle/pressed '
                    'key image to the skin before raising receptors; game defaults cannot be edited')
            images = {}
            for density, path in variants.items():
                source = _read_image(path)
                padding = round(float(design.receptor_raise) * 1.6 * density)
                if source.height + padding > 16384:
                    raise DesignValidationError(f'{option}: the resulting image would be too tall')
                image = Image.new('RGBA', (source.width, source.height + padding))
                image.paste(source, (0, 0))  # no mask: preserve every authored RGBA byte.
                images[density] = image
            # Also provide an SD fallback when the author supplied HD only.
            if 1 not in images:
                high = images[2]
                images[1] = high.resize((max(1, round(high.width / 2)), max(1, round(high.height / 2))), Image.Resampling.LANCZOS)
            result[option] = images
    return result


def _copy_ignore(folder, names):
    excluded = {'.skin_ini_history', '__conflicts_backup', '.git', '__pycache__'}
    return [name for name in names if name.casefold() in excluded or
            name.casefold() == 'skin.ini.bak' or
            (name.startswith('.skin-') and name.endswith('.tmp'))]


def export_skin(source_root: Path, keys: int, destination: Path, design: ManiaDesign) -> ExportResult:
    """Copy, edit only derivatives, then publish the completed new directory.

    Existing destinations and destinations inside the source are rejected.
    All validation/image work happens before copying; failed writes remove
    only our known staging directory. No source file is ever opened for write.
    """
    source = Path(source_root).expanduser().resolve()
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise DesignValidationError('The destination already exists; choose a new folder name')
    destination = destination.resolve()
    if destination == source or destination.is_relative_to(source):
        raise DesignValidationError('Choose a destination outside the source skin folder')
    ini = _ini(source)
    values = ini.mania_get(keys)
    _validate_layout(values, keys, design)
    hit = effective_hit_position(values, design)
    key_images = _prepare_key_images(source, keys, values, design)
    stage_images = {}
    if design.top_mask_height or design.top_mask_fade:
        for suffix, variants in _stage_frames(source, values).items():
            for density in (1, 2):
                stage_images[(suffix, density)] = _compose_overlay(variants, lane_width(values, keys), design, density)

    # Reject linked directories/files rather than copying external data by accident.
    for folder, directories, files in os.walk(source, followlinks=False):
        ignored = set(_copy_ignore(folder, directories + files))
        directories[:] = [name for name in directories if name not in ignored]
        for name in directories + [n for n in files if n not in ignored]:
            path = Path(folder) / name
            if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
                raise DesignValidationError(f'Linked files/folders cannot be exported: {path.relative_to(source)}')

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.mania-design-', dir=destination.parent))
    assets = []
    try:
        shutil.copytree(source, staging, dirs_exist_ok=True, ignore=_copy_ignore)
        copied_ini = SkinIni.read(staging / ini.path.name)
        if copied_ini._original_bytes != ini._original_bytes:
            raise OSError('skin.ini changed during export; reload the skin and try again')
        updates = {}
        # Unique paths also preserve earlier exports when designing an exported skin.
        relative_dir = Path('editor-assets') / f'mania-{keys}' / uuid4().hex[:10]
        if stage_images or key_images:
            (staging / relative_dir).mkdir(parents=True, exist_ok=False)
        for (suffix, density), image in stage_images.items():
            relative = relative_dir / f'stage-bottom{suffix}{"@2x" if density == 2 else ""}.png'
            image.save(staging / relative, format='PNG')
            assets.append(relative)
        if stage_images:
            updates['StageBottom'] = (relative_dir / 'stage-bottom').as_posix()
        for option, variants in key_images.items():
            basename = option.lower()
            for density, image in variants.items():
                relative = relative_dir / f'{basename}{"@2x" if density == 2 else ""}.png'
                image.save(staging / relative, format='PNG')
                assets.append(relative)
            updates[option] = (relative_dir / basename).as_posix()
        if design.receptor_raise and design.link_hit_position:
            updates['HitPosition'] = f'{hit:g}'
        if updates:
            copied_ini.mania_set_values(keys, updates)
            copied_ini.save(create_backup=False)
        if destination.exists():
            raise DesignValidationError('The destination was created during export; choose a new folder')
        # rename, not replace: on Windows this fails if a destination appeared.
        staging.rename(destination)
        return ExportResult(destination, tuple(assets), hit)
    finally:
        # This path came directly from mkdtemp and is never an input directory.
        if staging.exists() and staging.parent.resolve() == destination.parent.resolve() and staging.name.startswith('.mania-design-'):
            shutil.rmtree(staging)


export_mania_design = export_skin
