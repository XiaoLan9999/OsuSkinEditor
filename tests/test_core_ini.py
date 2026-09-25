from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.skin_ini import SkinIni, decode_ini, parse_list_csv


class SkinIniTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'skin.ini'

    def write(self, text, encoding='utf-8'):
        original = text.encode(encoding)
        self.path.write_bytes(original)
        return original

    def test_backup_retains_original_bytes_across_multiple_saves(self):
        original = self.write('[Mania]\r\nKeys: 4\r\nHitPosition: 400\r\n', 'utf-8-sig')
        ini = SkinIni.read(self.path)
        ini.mania_set_values(4, {'HitPosition': 410})
        ini.save()
        self.assertEqual(self.path.with_suffix('.ini.bak').read_bytes(), original)
        self.assertEqual(self.path.read_bytes(), original.replace(b'400', b'410'))
        ini.mania_set_values(4, {'HitPosition': 420})
        ini.save()
        self.assertEqual(self.path.with_suffix('.ini.bak').read_bytes(), original)

    def test_legacy_encoding_newlines_and_comments_survive_edit(self):
        text = '// 中文皮肤\r\n[Mania] // layout\r\nkeys = 4 // columns\r\n  hitposition = 400  // position\r\nUnknown: keep me\r\n'
        for encoding in ('utf-8-sig', 'gb18030', 'utf-16', 'utf-16-le', 'utf-16-be'):
            with self.subTest(encoding=encoding):
                self.write(text, encoding)
                ini = SkinIni.read(self.path)
                self.assertEqual(ini.available_mania_keys(), [4])
                ini.mania_set_values(4, {'HitPosition': 425})
                ini.save(create_backup=False)
                self.assertEqual(self.path.read_bytes(), text.replace('400', '425').encode(encoding))

    def test_big_endian_bom_is_preserved(self):
        original = b'\xfe\xff' + '[Mania]\nKeys: 4\nHitPosition: 400'.encode('utf-16-be')
        self.path.write_bytes(original)
        ini = SkinIni.read(self.path)
        ini.mania_set_values(4, {'HitPosition': 420})
        ini.save(create_backup=False)
        self.assertEqual(self.path.read_bytes(), original.replace('400'.encode('utf-16-be'), '420'.encode('utf-16-be')))

    def test_repeated_sections_edit_only_selected_variant_and_keep_unknown_lines(self):
        text = '[General]\nName: 100% fun\n\n[Mania]\nkeys: 4 // first\nHitPosition: 400\n\n[Mania]\nKeys: 7\nHitPosition: 470\n\n[Mania]\nkeys: 4 // final\nHitPosition: 410 // keep this\nUnexpected decoration\n'
        self.write(text)
        ini = SkinIni.read(self.path)
        ini.mania_set_values(4, {'hitposition': 420, 'Keys': 9, 'ColumnWidth': [50, 50, 50, 50]})
        ini.save(create_backup=False)
        result = self.path.read_text(encoding='utf-8')
        self.assertIn('HitPosition: 400', result)
        self.assertIn('HitPosition: 470', result)
        self.assertIn('HitPosition: 420 // keep this', result)
        self.assertIn('Unexpected decoration', result)
        self.assertEqual(result.count('keys: 4'), 2)
        self.assertNotIn('Keys: 9', result)
        self.assertEqual(SkinIni.read(self.path).mania_get(4)['ColumnWidth'], '50,50,50,50')

    def test_new_variant_does_not_duplicate_keys(self):
        self.write('[General]\nName: demo\n')
        ini = SkinIni.read(self.path)
        ini.mania_set_values(7, {'Keys': 7, 'KeysUnderNotes': True})
        self.assertEqual(ini.available_mania_keys(), [7])
        self.assertEqual(ini.lines.count('Keys: 7'), 1)
        self.assertEqual(ini.mania_get(7)['KeysUnderNotes'], '1')

    def test_no_op_save_retains_mixed_newlines_exactly(self):
        original = self.write('[Mania]\r\nKeys: 4\nHitPosition: 400')
        ini = SkinIni.read(self.path)
        ini.mania_set_values(4, {'HitPosition': 400})
        ini.save(create_backup=False)
        self.assertEqual(self.path.read_bytes(), original)

    def test_external_edits_are_not_overwritten(self):
        self.write('[Mania]\nKeys: 4\n')
        ini = SkinIni.read(self.path)
        ini.mania_set_values(4, {'HitPosition': 400})
        external = b'[General]\nName: edited elsewhere\n'
        self.path.write_bytes(external)
        with self.assertRaisesRegex(OSError, 'changed on disk'):
            ini.save()
        self.assertEqual(self.path.read_bytes(), external)

    def test_failed_replace_preserves_original_and_removes_temporary_file(self):
        original = self.write('[Mania]\nKeys: 4\nHitPosition: 400\n')
        ini = SkinIni.read(self.path)
        ini.mania_set_values(4, {'HitPosition': 420})
        with patch('core.skin_ini.os.replace', side_effect=OSError('locked')):
            with self.assertRaises(OSError):
                ini.save()
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.path.parent.glob('.skin-*.tmp')), [])

    def test_gbk_is_not_accidentally_decoded_as_utf16(self):
        raw = '[General]\nName: 中文皮肤\n'.encode('gbk')
        raw += b' ' if len(raw) % 2 else b''
        self.assertIn('中文皮肤', decode_ini(raw)[0])

    def test_csv_inline_comment(self):
        self.assertEqual(parse_list_csv('40, 50, 60 // widths'), [40, 50, 60])

    def test_empty_and_compact_commented_values_remain_editable(self):
        self.write('[Mania]\nKeys: 4//4K\nStageHint: // unused\n')
        ini = SkinIni.read(self.path)
        self.assertEqual(ini.mania_get(4)['StageHint'], '')
        ini.mania_set_values(4, {'StageHint': 'custom-hint'})
        ini.save(create_backup=False)
        self.assertEqual(SkinIni.read(self.path).mania_get(4)['StageHint'], 'custom-hint')
        self.assertIn('// unused', self.path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
