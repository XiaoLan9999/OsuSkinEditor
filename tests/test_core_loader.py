from pathlib import Path
import tempfile
import unittest

from core.skin_loader import SkinLoader, _read_ini_robust


class SkinLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'skin.ini'

    def test_preamble_percent_urls_comments_and_mania_variants(self):
        self.path.write_text('Author: preamble\n====\n[General]\nName: 100% fun\nAuthor: https://example.test/profile // note\n[mania] // 7K\nKEYS: 7 // keys\n[Mania]\nKeys: 4\n[MANIA]\nKeys: 9\n', encoding='utf-8')
        skin = SkinLoader().load(str(self.root))
        self.assertEqual(skin.ini.get('General', 'Name'), '100% fun')
        self.assertEqual(skin.ini.get('General', 'Author'), 'https://example.test/profile')
        self.assertEqual(set(skin.mania_variants), {4, 7, 9})
        self.assertEqual(skin.mode_keys, 4)

    def test_default_key_priority_is_independent_of_section_order(self):
        for variants in ((4, 7), (7, 4), (9, 7), (9, 6)):
            with self.subTest(variants=variants):
                self.path.write_text(''.join(f'[Mania]\nKeys: {keys}\n' for keys in variants), encoding='utf-8')
                expected = 4 if 4 in variants else 7 if 7 in variants else variants[0]
                self.assertEqual(SkinLoader().load(str(self.root)).mode_keys, expected)

    def test_custom_assets_prefer_2x_regardless_of_directory_order(self):
        self.path.write_text('[General]\nName: custom\n', encoding='utf-8')
        for name in ('custom.png', 'custom@2x.PNG', 'HITCIRCLE.PNG', 'hitcircle@2x.png', 'my@2xasset.png'):
            (self.root / name).write_bytes(b'png')
        skin = SkinLoader().load(str(self.root))
        self.assertEqual(skin.assets['custom'].path.name, 'custom@2x.PNG')
        self.assertEqual(skin.assets['custom'].scale, 2)
        self.assertEqual(skin.assets['hitcircle'].scale, 2)
        self.assertIn('my@2xasset', skin.assets)

    def test_legacy_encoding_keeps_name(self):
        self.path.write_bytes('[General]\nName: 中文皮肤\n'.encode('gbk'))
        self.assertEqual(_read_ini_robust(self.path).get('General', 'Name'), '中文皮肤')

    def test_missing_ini_is_clear_error(self):
        with self.assertRaisesRegex(FileNotFoundError, 'skin.ini'):
            SkinLoader().load(str(self.root))

    def test_compact_numeric_comment_and_empty_value_match_editor(self):
        self.path.write_text('[Mania]\nKeys: 4//4K\nStageHint: // unused\n', encoding='utf-8')
        skin = SkinLoader().load(str(self.root))
        self.assertIn(4, skin.mania_variants)
        self.assertEqual(skin.mania_variants[4]['StageHint'], '')


if __name__ == '__main__':
    unittest.main()
