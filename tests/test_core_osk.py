from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED

from core.osk_io import export_osk, import_osk


class OskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = self.root / 'skin.osk'

    def test_round_trip_preserves_nested_file_bytes(self):
        source = self.root / 'source'
        (source / 'sounds').mkdir(parents=True)
        (source / 'skin.ini').write_bytes(b'[General]\nName: test\n')
        (source / 'sounds' / 'hit.wav').write_bytes(bytes(range(256)))
        export_osk(source, self.archive)
        destination = self.root / 'destination'
        import_osk(self.archive, destination)
        self.assertEqual((destination / 'skin.ini').read_bytes(), (source / 'skin.ini').read_bytes())
        self.assertEqual((destination / 'sounds' / 'hit.wav').read_bytes(), bytes(range(256)))
        with ZipFile(self.archive) as archive:
            self.assertTrue(all(entry.compress_type == ZIP_DEFLATED for entry in archive.infolist()))

    def test_paths_are_validated_before_any_destination_write(self):
        for name in ('../outside.txt', '..\\outside.txt', '/outside.txt', 'C:/outside.txt', 'notes/file:stream', 'notes/NUL.png', 'notes/trailing. '):
            with self.subTest(name=name):
                with ZipFile(self.archive, 'w') as archive:
                    archive.writestr('skin.ini', '[General]')
                    archive.writestr(name, 'unexpected')
                destination = self.root / 'destination'
                with self.assertRaises(ValueError):
                    import_osk(self.archive, destination)
                self.assertFalse(destination.exists())
                self.assertFalse((self.root / 'outside.txt').exists())

    def test_import_never_overwrites_existing_file(self):
        destination = self.root / 'destination'
        destination.mkdir()
        (destination / 'skin.ini').write_bytes(b'original')
        with ZipFile(self.archive, 'w') as archive:
            archive.writestr('new.png', b'new')
            archive.writestr('skin.ini', b'replacement')
        with self.assertRaises(FileExistsError):
            import_osk(self.archive, destination)
        self.assertEqual((destination / 'skin.ini').read_bytes(), b'original')
        self.assertFalse((destination / 'new.png').exists())

    def test_export_inside_skin_excludes_itself_and_editor_backups(self):
        (self.root / 'skin.ini').write_bytes(b'[General]')
        (self.root / 'skin.ini.bak').write_bytes(b'backup')
        (self.root / '.skin_ini_history').mkdir()
        (self.root / '.skin_ini_history' / 'snapshot.bak').write_bytes(b'backup')
        (self.root / '__conflicts_backup').mkdir()
        (self.root / '__conflicts_backup' / 'cursor.png').write_bytes(b'backup')
        self.archive.write_bytes(b'old output')
        export_osk(self.root, self.archive)
        with ZipFile(self.archive) as archive:
            self.assertEqual(archive.namelist(), ['skin.ini'])

    def test_failed_export_does_not_destroy_previous_archive(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'skin.ini').write_bytes(b'[General]')
        self.archive.write_bytes(b'previous archive')
        with patch('core.osk_io.ZipFile.write', side_effect=OSError('read error')):
            with self.assertRaises(OSError):
                export_osk(source, self.archive)
        self.assertEqual(self.archive.read_bytes(), b'previous archive')
        self.assertEqual(list(self.root.glob('.osk-export-*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
