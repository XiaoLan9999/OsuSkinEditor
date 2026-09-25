from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

from core.mania_designer import (
    DesignValidationError, ManiaDesign, build_preview_overlay,
    effective_hit_position, export_skin, lane_width,
)
from core.skin_ini import SkinIni


class ManiaDesignerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / '原皮肤'
        self.source.mkdir()
        self.destination = self.base / 'New Skin'
        self.write_ini()

    def write_ini(self, extra='', encoding='utf-8', keys=4):
        text = ('// 中文原稿\r\n[General]\r\nName: My Skin\r\nVersion: 2.7\r\n'
                f'[Mania]\r\nKeys: {keys}\r\nHitPosition: 425 // target\r\n'
                'CustomSetting: untouched\r\n' + extra +
                '\r\n[Mania]\r\nKeys: 7\r\nHitPosition: 410\r\nStageBottom: shared\r\n')
        (self.source / 'skin.ini').write_bytes(text.encode(encoding))

    def image(self, name, size=(8, 16), colour=(10, 20, 30, 128)):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGBA', size, colour).save(path)
        return path

    def keys(self):
        for suffix in ('1', '2'):
            self.image(f'mania-key{suffix}.png')
            self.image(f'mania-key{suffix}D.png', colour=(100, 80, 40, 255))

    def snapshot(self):
        return {p.relative_to(self.source): sha256(p.read_bytes()).hexdigest()
                for p in self.source.rglob('*') if p.is_file()}

    def test_copy_preserves_source_hashes_other_modes_and_encoding(self):
        self.keys()
        self.write_ini('ColumnWidth: 30,40,50,60\r\n', encoding='gb18030')
        self.image('shared.png')
        (self.source / 'custom.ogg').write_bytes(b'unchanged sound')
        self.image('__conflicts_backup/private.png')
        self.image('.skin_ini_history/old.png')
        before = self.snapshot()
        result = export_skin(self.source, 4, self.destination,
                             ManiaDesign(top_mask_height=30, top_mask_fade=20, receptor_raise=15))
        self.assertEqual(result.root, self.destination)
        self.assertEqual(result.hit_position, 410)
        self.assertEqual(self.snapshot(), before)
        original = SkinIni.read(self.source / 'skin.ini')
        copied = SkinIni.read(result.root / 'skin.ini')
        self.assertEqual(copied._encoding, 'gb18030')
        self.assertEqual(copied._newline, '\r\n')
        self.assertEqual(original.mania_get(7), copied.mania_get(7))
        self.assertEqual(copied.mania_get(4)['CustomSetting'], 'untouched')
        self.assertIn('HitPosition: 410 // target', '\n'.join(copied.lines))
        self.assertEqual((result.root / 'custom.ogg').read_bytes(), b'unchanged sound')
        self.assertFalse((result.root / '__conflicts_backup').exists())
        self.assertFalse((result.root / '.skin_ini_history').exists())
        self.assertFalse((result.root / 'skin.ini.bak').exists())
        self.assertTrue(all((result.root / asset).is_file() for asset in result.assets))
        for path in result.assets:
            self.assertEqual(path.parts[:2], ('editor-assets', 'mania-4'))

    def test_key_padding_retains_original_rgba_pixels_and_moves_bottom_anchor(self):
        self.keys()
        result = export_skin(self.source, 4, self.destination, ManiaDesign(receptor_raise=10))
        config = SkinIni.read(result.root / 'skin.ini').mania_get(4)
        for column in range(4):
            for state in ('', 'D'):
                with Image.open(result.root / (config[f'KeyImage{column}{state}'] + '.png')) as image:
                    self.assertEqual(image.size, (8, 32))
                    self.assertEqual(image.getpixel((0, 31))[3], 0)
                    suffix = '1' if column in (0, 3) else '2'
                    with Image.open(self.source / f'mania-key{suffix}{state}.png') as original:
                        self.assertEqual(image.crop((0, 0, 8, 16)).tobytes(), original.tobytes())
        self.assertNotEqual(config['KeyImage0'], config['KeyImage3'])

    def test_nested_hd_key_assets_and_explicit_extension_are_supported(self):
        extra = ''.join(f'KeyImage{i}{state}: Custom\\Receptor@2x.PNG\r\n'
                        for i in range(4) for state in ('', 'D'))
        self.write_ini(extra)
        original = self.image('Custom/receptor@2x.png', (20, 100), (70, 80, 90, 140))
        result = export_skin(self.source, 4, self.destination, ManiaDesign(receptor_raise=20))
        config = SkinIni.read(result.root / 'skin.ini').mania_get(4)
        with Image.open(result.root / (config['KeyImage0'] + '@2x.png')) as image:
            self.assertEqual(image.size, (20, 164))
            with Image.open(original) as old:
                self.assertEqual(image.crop((0, 0, 20, 100)).tobytes(), old.tobytes())
        self.assertTrue((result.root / (config['KeyImage0'] + '.png')).is_file())

    def test_existing_stage_art_preserved_at_native_bottom_centre(self):
        self.write_ini('ColumnWidth: 30,30,30,30\r\nStageBottom: layers\\Footer\r\n')
        self.image('layers/footer@2x.png', (400, 80), (45, 90, 130, 120))
        overlay = build_preview_overlay(self.source, 4, ManiaDesign(top_mask_height=50, top_mask_fade=20))
        self.assertEqual(overlay.info['skin_density'], 2)
        self.assertEqual(overlay.size, (400, 960))
        # 120 logical lane width = 240 HD px, centred inside original 400px art.
        self.assertEqual(overlay.getpixel((79, 0)), (0, 0, 0, 0))
        self.assertEqual(overlay.getpixel((80, 0)), (0, 0, 0, 255))
        self.assertEqual(overlay.getpixel((319, 99)), (0, 0, 0, 255))
        self.assertEqual(overlay.getpixel((320, 99)), (0, 0, 0, 0))
        self.assertGreater(overlay.getpixel((120, 105))[3], overlay.getpixel((120, 130))[3])
        self.assertEqual(overlay.getpixel((120, 139))[3], 0)
        self.assertEqual(overlay.getpixel((0, 880)), (45, 90, 130, 120))
        self.assertEqual(overlay.getpixel((399, 959)), (45, 90, 130, 120))

    def test_mask_fade_composites_over_existing_alpha_without_erasing_it(self):
        self.image('mania-stage-bottom.png', (120, 480), (255, 0, 0, 255))
        image = build_preview_overlay(self.source, 4, ManiaDesign(top_mask_height=0, top_mask_fade=20))
        red, green, blue, alpha = image.getpixel((100, 20))
        self.assertTrue(100 < red < 160)
        self.assertEqual((green, blue, alpha), (0, 0, 255))
        self.assertEqual(image.getpixel((100, 42)), (255, 0, 0, 255))

    def test_animated_stage_frames_are_all_exported_and_first_previewed(self):
        self.image('mania-stage-bottom.png', (120, 30), 'blue')
        self.image('mania-stage-bottom-0.png', (120, 30), 'red')
        self.image('mania-stage-bottom-1@2x.png', (240, 60), 'green')
        design = ManiaDesign(top_mask_height=5)
        result = export_skin(self.source, 4, self.destination, design)
        config = SkinIni.read(result.root / 'skin.ini').mania_get(4)
        for suffix, colour in (('', (0, 0, 255, 255)), ('-0', (255, 0, 0, 255)), ('-1', (0, 128, 0, 255))):
            for density in ('', '@2x'):
                with Image.open(result.root / f'{config["StageBottom"]}{suffix}{density}.png') as image:
                    self.assertEqual(image.getpixel((0, image.height - 1)), colour)
        preview = build_preview_overlay(self.source, 4, design)
        self.assertEqual(preview.getpixel((0, preview.height - 1)), (255, 0, 0, 255))

    def test_raise_without_link_does_not_change_hitposition_or_stagebottom(self):
        self.keys()
        self.write_ini('StageBottom: source-bottom\r\n')
        result = export_skin(self.source, 4, self.destination,
                             ManiaDesign(receptor_raise=20, link_hit_position=False))
        data = SkinIni.read(result.root / 'skin.ini').mania_get(4)
        self.assertEqual(data['HitPosition'], '425')
        self.assertEqual(data['StageBottom'], 'source-bottom')
        self.assertFalse(any('stage-bottom' in str(p) for p in result.assets))

    def test_noop_preserves_exact_ini_bytes_and_no_generated_assets(self):
        result = export_skin(self.source, 4, self.destination, ManiaDesign())
        self.assertEqual((result.root / 'skin.ini').read_bytes(), (self.source / 'skin.ini').read_bytes())
        self.assertEqual(result.assets, ())
        self.assertFalse((result.root / 'editor-assets').exists())
        self.assertIsNone(build_preview_overlay(self.source, 4, ManiaDesign()))

    def test_reexport_uses_unique_derivatives_and_preserves_prior_assets(self):
        first = export_skin(self.source, 4, self.destination, ManiaDesign(top_mask_height=10))
        old_hashes = {p: sha256((first.root / p).read_bytes()).hexdigest() for p in first.assets}
        second = export_skin(first.root, 4, self.base / 'Another', ManiaDesign(top_mask_height=20))
        self.assertFalse(set(first.assets) & set(second.assets))
        for path, checksum in old_hashes.items():
            self.assertEqual(sha256((second.root / path).read_bytes()).hexdigest(), checksum)

    def test_existing_or_nested_destination_is_rejected_before_writes(self):
        for destination in (self.source, self.source / 'Nested', self.base):
            with self.subTest(destination=destination), self.assertRaises(DesignValidationError):
                export_skin(self.source, 4, destination, ManiaDesign(top_mask_height=10))
        self.assertFalse((self.source / 'Nested').exists())

    def test_missing_key_assets_fails_without_partial_export(self):
        self.keys()
        (self.source / 'mania-key2D.png').unlink()
        before = self.snapshot()
        with self.assertRaisesRegex(DesignValidationError, 'KeyImage1D'):
            export_skin(self.source, 4, self.destination, ManiaDesign(receptor_raise=20))
        self.assertFalse(self.destination.exists())
        self.assertEqual(before, self.snapshot())
        self.assertFalse(list(self.base.glob('.mania-design-*')))

    def test_bad_geometry_and_linked_target_are_rejected(self):
        for design in (ManiaDesign(top_mask_height=-1), ManiaDesign(top_mask_fade=500),
                       ManiaDesign(top_mask_height=400, top_mask_fade=81),
                       ManiaDesign(receptor_raise=200), ManiaDesign(receptor_raise=float('nan')),
                       ManiaDesign(mask_colour='invalid')):
            with self.subTest(design=design), self.assertRaises(DesignValidationError):
                export_skin(self.source, 4, self.destination, design)
        self.assertFalse(self.destination.exists())

    def test_default_preview_preserves_raw_hitposition_and_lowercase_settings(self):
        self.assertEqual(effective_hit_position({'hitposition': '0'}, ManiaDesign()), 0)
        self.assertEqual(effective_hit_position({}, ManiaDesign()), 402)
        self.assertEqual(effective_hit_position({'HITPOSITION': '425'}, ManiaDesign(receptor_raise=30)), 395)
        self.assertEqual(lane_width({'columnwidth': '30,40,50,60', 'ColumnSpacing': '2,3,4'}, 4), 189)

    def test_failed_png_write_cleans_staging_and_preserves_source(self):
        before = self.snapshot()
        with patch.object(Image.Image, 'save', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                export_skin(self.source, 4, self.destination, ManiaDesign(top_mask_height=30))
        self.assertFalse(self.destination.exists())
        self.assertFalse(list(self.base.glob('.mania-design-*')))
        self.assertEqual(before, self.snapshot())

    def test_external_asset_path_is_rejected(self):
        self.write_ini('StageBottom: ../outside\r\n')
        with self.assertRaisesRegex(DesignValidationError, 'within'):
            export_skin(self.source, 4, self.destination, ManiaDesign(top_mask_height=10))
        self.assertFalse(self.destination.exists())

    def test_unsupported_stage_arrangements_fail_clearly(self):
        for setting in ('UpsideDown: 1', 'SplitStages: 1'):
            self.write_ini(setting + '\r\n')
            with self.assertRaisesRegex(DesignValidationError, 'require'):
                export_skin(self.source, 4, self.destination, ManiaDesign(top_mask_height=10))


if __name__ == '__main__':
    unittest.main()
