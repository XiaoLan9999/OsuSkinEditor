from pathlib import Path
import io
import json
import os
import stat
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import warnings
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED, ZIP_STORED, BadZipFile

from core.osk_io import OskCancelled, export_osk, import_osk, inspect_osk


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
        for name in ('../outside.txt', '..\\outside.txt', '/outside.txt', 'C:/outside.txt', 'notes/file:stream', 'notes/NUL.png', 'notes/COM¹.png', 'notes/trailing. ', 'notes/file?.png', 'notes/control\x01.png', '//host/share/file.png', 'notes//file.png'):
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
        (self.root / 'skin.ini.2026-10-10.bak').write_bytes(b'backup')
        (self.root / '.osk-editor.json').write_bytes(b'editor origin')
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
        with patch('core.osk_io.ZipFile.open', side_effect=OSError('write error')):
            with self.assertRaises(OSError):
                export_osk(source, self.archive)
        self.assertEqual(self.archive.read_bytes(), b'previous archive')
        self.assertEqual(list(self.root.glob('.osk-export-*.tmp')), [])

    def write_archive(self, files, *, compression=ZIP_DEFLATED):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with ZipFile(self.archive, 'w', compression=compression) as archive:
                for name, data in files:
                    archive.writestr(name, data)

    def assert_import_rejected(self, files, exception=ValueError):
        self.write_archive(files)
        destination = self.root / 'destination'
        with self.assertRaises(exception):
            import_osk(self.archive, destination)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob('.osk-import-*')), [])

    def test_single_folder_wrapper_flattens_and_preserves_subdirectories(self):
        self.write_archive([
            ('中文皮肤/', b''), ('中文皮肤\\skin.ini', b'[General]\nName: test'),
            ('中文皮肤\\音效\\soft-hitnormal.wav', b'sound'),
        ])
        destination = self.root / 'destination'
        result = import_osk(self.archive, destination)
        self.assertEqual(result.root_prefix, '中文皮肤')
        self.assertEqual(result.file_count, 2)
        self.assertTrue(result.has_skin_ini)
        self.assertEqual((destination / '音效' / 'soft-hitnormal.wav').read_bytes(), b'sound')
        self.assertFalse((destination / '中文皮肤').exists())

    def test_root_skin_takes_precedence_over_nested_copy(self):
        self.write_archive([('skin.ini', b'root'), ('other/skin.ini', b'other')])
        result = import_osk(self.archive, self.root / 'destination')
        self.assertEqual(result.root_prefix, '')
        self.assertEqual((self.root / 'destination' / 'skin.ini').read_bytes(), b'root')
        self.assertTrue((self.root / 'destination' / 'other' / 'skin.ini').exists())

    def test_no_ini_legacy_asset_skin_is_recognised(self):
        self.write_archive([('hitcircle.png', b'image'), ('custom/readme.txt', b'hello')])
        inspection = inspect_osk(self.archive)
        self.assertFalse(inspection.has_skin_ini)
        self.assertEqual(inspection.file_count, 2)
        self.assertFalse((self.root / 'destination').exists())
        import_osk(self.archive, self.root / 'destination')
        self.assertEqual((self.root / 'destination' / 'hitcircle.png').read_bytes(), b'image')

    def test_lazer_json_only_skin_preserves_all_bytes(self):
        metadata = json.dumps({'Name': '小蓝', 'Creator': '作者',
                               'InstantiationInfo': 'osu.Game.Skinning.Argon.ArgonSkin, osu.Game'}, ensure_ascii=False).encode()
        layout = b'{"Version": 1, "DrawableInfo": {"osu": [{"Type": "native widget"}]}}'
        self.write_archive([('skininfo.json', metadata), ('MainHUDComponents.json', layout),
                            ('Playfield.json', b'[]'), ('SongSelect.json', b'[]')])
        before = self.archive.read_bytes()
        result = import_osk(self.archive, self.root / 'destination')
        self.assertFalse(result.has_skin_ini)
        self.assertTrue(result.has_lazer_metadata)
        self.assertTrue(result.has_lazer_layouts)
        self.assertEqual(result.display_name, '小蓝')
        self.assertEqual(result.creator, '作者')
        self.assertIn('Argon', result.lazer_instantiation)
        self.assertEqual((self.root / 'destination' / 'skininfo.json').read_bytes(), metadata)
        self.assertEqual((self.root / 'destination' / 'MainHUDComponents.json').read_bytes(), layout)
        self.assertEqual(self.archive.read_bytes(), before)

    def test_invalid_manifest_with_real_assets_is_retained_without_execution(self):
        self.write_archive([('skininfo.json', b'not json'), ('mania-note1.png', b'image')])
        result = import_osk(self.archive, self.root / 'destination')
        self.assertTrue(result.has_lazer_metadata)
        self.assertIsNone(result.display_name)
        self.assertEqual((self.root / 'destination' / 'skininfo.json').read_bytes(), b'not json')

    def test_arbitrary_zip_or_empty_metadata_is_rejected(self):
        for files in ([], [('readme.txt', b'document')], [('skininfo.json', b'{}')],
                      [('skininfo.json', b'not json')], [('photo.png', b'image')]):
            with self.subTest(files=files):
                self.assert_import_rejected(files)

    def test_metadata_parsing_is_bounded(self):
        self.write_archive([('skininfo.json', b'{"Name":"long"}'), ('cursor.png', b'image')])
        with patch('core.osk_io.MAX_METADATA_BYTES', 8):
            result = import_osk(self.archive, self.root / 'destination')
        self.assertIsNone(result.display_name)
        self.assertEqual((self.root / 'destination' / 'skininfo.json').read_bytes(), b'{"Name":"long"}')

    def test_case_aliases_and_file_directory_collisions_are_rejected(self):
        for files in (
            [('skin.ini', b'ini'), ('SKIN.INI', b'ini')],
            [('skin.ini', b'ini'), ('skin.ini', b'ini')],
            [('skin.ini', b'ini'), ('Notes/a.png', b'a'), ('notes/b.png', b'b')],
            [('skin.ini', b'ini'), ('folder', b'a'), ('folder/a.png', b'b')],
            [('skin.ini', b'ini'), ('folder/a.png', b'b'), ('folder', b'a')],
            [('skin.ini', b'ini'), ('folder/', b''), ('folder/', b'')],
        ):
            with self.subTest(files=files):
                self.assert_import_rejected(files)

    def test_old_lazer_zero_byte_parent_placeholder_round_trip(self):
        # Same structure as ppy/osu #27540, with our own small INI/content.
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                folder = 'Fun Folder Full of Fun Features'
                files = [(folder, b''), (folder + '/Fun Feature.txt', b'feature'),
                         ('Skin Content.txt', b'content'), ('skin.ini', b'[General]\nName: own fixture\n')]
                self.write_archive(list(reversed(files)) if reverse else files)
                original = self.archive.read_bytes()
                destination = self.root / ('destination-' + str(reverse))
                result = import_osk(self.archive, destination)
                self.assertEqual(result.repaired_directory_entries, 1)
                self.assertEqual(result.file_count, 3)
                self.assertEqual((destination / folder / 'Fun Feature.txt').read_bytes(), b'feature')
                self.assertEqual(self.archive.read_bytes(), original)
                out = self.root / ('re-export-' + str(reverse) + '.osk')
                export_osk(destination, out)
                with ZipFile(out) as archive:
                    self.assertNotIn(folder, archive.namelist())
                    self.assertEqual(archive.read(folder + '/Fun Feature.txt'), b'feature')
                    self.assertIsNone(archive.testzip())

    def test_nonempty_parent_file_is_not_repaired(self):
        self.assert_import_rejected([
            ('skin.ini', b'ini'), ('folder', b'content'), ('folder/asset.png', b'image'),
        ])

    def test_zero_byte_parent_case_alias_is_not_repaired(self):
        self.assert_import_rejected([
            ('skin.ini', b'ini'), ('Folder', b''), ('folder/asset.png', b'image'),
        ])

    def test_duplicate_zero_byte_parent_placeholders_still_rejected(self):
        for duplicate in ('folder', 'folder/'):
            with self.subTest(duplicate=duplicate):
                self.assert_import_rejected([
                    ('skin.ini', b'ini'), ('folder', b''), (duplicate, b''),
                    ('folder/asset.png', b'image'),
                ])

    def test_isolated_zero_byte_file_is_preserved(self):
        self.write_archive([('skin.ini', b'ini'), ('empty-file', b'')])
        destination = self.root / 'destination'
        result = import_osk(self.archive, destination)
        self.assertEqual(result.repaired_directory_entries, 0)
        self.assertEqual(result.file_count, 2)
        self.assertTrue((destination / 'empty-file').is_file())
        self.assertEqual((destination / 'empty-file').read_bytes(), b'')
        out = self.root / 'output.osk'
        export_osk(destination, out)
        with ZipFile(out) as archive:
            self.assertIn('empty-file', archive.namelist())
            self.assertEqual(archive.read('empty-file'), b'')

    def test_repaired_placeholder_can_be_single_wrapper(self):
        self.write_archive([('wrapper', b''), ('wrapper/skin.ini', b'ini'),
                            ('wrapper/mania-note1.png', b'image')])
        result = import_osk(self.archive, self.root / 'destination')
        self.assertEqual(result.root_prefix, 'wrapper')
        self.assertEqual(result.repaired_directory_entries, 1)
        self.assertEqual((self.root / 'destination' / 'skin.ini').read_bytes(), b'ini')

    def test_repaired_windows_separator_placeholder_is_compatible(self):
        self.write_archive([('skin.ini', b'ini'), ('图片\\长条', b''),
                            ('图片\\长条\\mania-note1.png', b'image')])
        result = import_osk(self.archive, self.root / 'destination')
        self.assertEqual(result.repaired_directory_entries, 1)
        self.assertEqual((self.root / 'destination' / '图片' / '长条' / 'mania-note1.png').read_bytes(), b'image')

    def test_repaired_directory_placeholder_crc_is_still_verified(self):
        self.write_archive([('folder', b''), ('folder/asset.png', b'image'), ('skin.ini', b'ini')])
        data = bytearray(self.archive.read_bytes())
        # Corrupt the first zero-byte record's local and central CRC.
        struct.pack_into('<I', data, 14, 1)
        central = data.index(b'PK\x01\x02')
        struct.pack_into('<I', data, central + 16, 1)
        self.archive.write_bytes(data)
        with self.assertRaises(BadZipFile):
            import_osk(self.archive, self.root / 'destination')
        self.assertFalse((self.root / 'destination').exists())
        self.assertEqual(list(self.root.glob('.osk-import-*')), [])

    def test_repaired_wrapper_crc_is_verified_before_wrapper_is_removed(self):
        self.write_archive([('wrapper', b''), ('wrapper/skin.ini', b'ini')])
        data = bytearray(self.archive.read_bytes())
        struct.pack_into('<I', data, 14, 1)
        central = data.index(b'PK\x01\x02')
        struct.pack_into('<I', data, central + 16, 1)
        self.archive.write_bytes(data)
        with self.assertRaises(BadZipFile):
            import_osk(self.archive, self.root / 'destination')
        self.assertFalse((self.root / 'destination').exists())

    def test_unix_symlink_and_windows_reparse_entries_are_rejected(self):
        for attrs in ((stat.S_IFLNK | 0o777) << 16, 0x400):
            info = ZipInfo('link.png')
            info.external_attr = attrs
            self.assert_import_rejected([('skin.ini', b'ini'), (info, b'outside')])

    def test_declared_limits_reject_before_creating_destination(self):
        for constant, limit, files in (
            ('MAX_ENTRIES', 1, [('skin.ini', b'ini'), ('cursor.png', b'image')]),
            ('MAX_FILE_BYTES', 2, [('skin.ini', b'ini')]),
            ('MAX_TOTAL_BYTES', 5, [('skin.ini', b'ini'), ('cursor.png', b'image')]),
            ('MAX_ARCHIVE_BYTES', 2, [('skin.ini', b'ini')]),
        ):
            with self.subTest(constant=constant):
                with patch('core.osk_io.' + constant, limit):
                    self.assert_import_rejected(files)

    def test_zip_directory_limit_checked_before_zipinfo_allocation(self):
        self.write_archive([('skin.ini', b'ini')])
        with patch('core.osk_io.MAX_DIRECTORY_BYTES', 20):
            with patch('core.osk_io.ZipFile') as parser:
                with self.assertRaises(ValueError):
                    import_osk(self.archive, self.root / 'destination')
                parser.assert_not_called()

    def test_forged_low_file_count_is_rejected_before_zipinfo_allocation(self):
        self.write_archive([('skin.ini', b'ini'), ('cursor.png', b'image'), ('notes/readme.txt', b'hi')])
        data = bytearray(self.archive.read_bytes())
        end = data.rindex(b'PK\x05\x06')
        struct.pack_into('<HH', data, end + 8, 1, 1)
        self.archive.write_bytes(data)
        with patch('core.osk_io.MAX_ENTRIES', 2):
            with patch('core.osk_io.ZipFile') as parser:
                with self.assertRaises(ValueError):
                    import_osk(self.archive, self.root / 'destination')
                parser.assert_not_called()
        self.assertFalse((self.root / 'destination').exists())

    def test_zip64_small_archive_is_supported(self):
        with ZipFile(self.archive, 'w', compression=ZIP_DEFLATED) as archive:
            with archive.open('skin.ini', 'w', force_zip64=True) as target:
                target.write(b'ini')
        import_osk(self.archive, self.root / 'destination')
        self.assertEqual((self.root / 'destination' / 'skin.ini').read_bytes(), b'ini')

    def test_zip64_directory_footer_is_supported(self):
        with patch('zipfile.ZIP64_LIMIT', 1):
            with ZipFile(self.archive, 'w') as archive:
                archive.writestr('skin.ini', b'ini')
        self.assertIn(b'PK\x06\x06', self.archive.read_bytes())
        import_osk(self.archive, self.root / 'destination')
        self.assertEqual((self.root / 'destination' / 'skin.ini').read_bytes(), b'ini')

    def test_zip_directory_inconsistency_rejects_before_destination_write(self):
        self.write_archive([('skin.ini', b'ini')])
        data = bytearray(self.archive.read_bytes())
        end = data.rindex(b'PK\x05\x06')
        struct.pack_into('<HH', data, end + 8, 2, 2)
        self.archive.write_bytes(data)
        with self.assertRaises(BadZipFile):
            import_osk(self.archive, self.root / 'destination')
        self.assertFalse((self.root / 'destination').exists())

    def test_actual_stream_limit_is_enforced_and_staging_removed(self):
        self.write_archive([('skin.ini', b'ini'), ('cursor.png', b'x')])
        original = ZipFile.open
        def enlarged(archive, entry, *args, **kwargs):
            if getattr(entry, 'filename', entry) == 'cursor.png':
                return io.BytesIO(b'larger than declared')
            return original(archive, entry, *args, **kwargs)
        with patch('core.osk_io.ZipFile.open', enlarged):
            with self.assertRaises(ValueError):
                import_osk(self.archive, self.root / 'destination')
        self.assertFalse((self.root / 'destination').exists())
        self.assertEqual(list(self.root.glob('.osk-import-*')), [])

    def test_corrupt_crc_preserves_empty_destination_and_source_archive(self):
        self.write_archive([('skin.ini', b'[General]')], compression=ZIP_STORED)
        data = bytearray(self.archive.read_bytes())
        filename_length, extra_length = struct.unpack_from('<HH', data, 26)
        data[30 + filename_length + extra_length] ^= 1
        self.archive.write_bytes(data)
        destination = self.root / 'destination'
        destination.mkdir()
        with self.assertRaises(BadZipFile):
            import_osk(self.archive, destination)
        self.assertTrue(destination.is_dir())
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual(self.archive.read_bytes(), data)
        self.assertEqual(list(self.root.glob('.osk-import-*')), [])

    def test_cancelled_import_preserves_empty_destination_and_source(self):
        self.write_archive([('skin.ini', b'ini'), ('cursor.png', b'x' * 200000)])
        before = self.archive.read_bytes()
        destination = self.root / 'destination'
        destination.mkdir()
        progress = []
        def report(done, total):
            progress.append((done, total))
        with self.assertRaises(OskCancelled):
            import_osk(self.archive, destination, progress=report,
                       cancelled=lambda: any(done >= 65536 for done, _ in progress))
        self.assertTrue(destination.is_dir())
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual(self.archive.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.osk-import-*')), [])
        self.assertTrue(all(0 <= done <= total for done, total in progress))

    def test_cancel_during_scan_does_not_create_destination(self):
        self.write_archive([('skin.ini', b'ini')])
        with self.assertRaises(OskCancelled):
            import_osk(self.archive, self.root / 'destination', cancelled=lambda: True)
        self.assertFalse((self.root / 'destination').exists())

    def test_import_progress_ends_at_exact_total(self):
        self.write_archive([('skin.ini', b'ini'), ('cursor.png', b'x' * 200000)])
        progress = []
        result = import_osk(self.archive, self.root / 'destination',
                            progress=lambda done, total: progress.append((done, total)))
        self.assertEqual(progress[0], (0, result.total_size))
        self.assertEqual(progress[-1], (result.total_size, result.total_size))
        self.assertEqual([done for done, _ in progress], sorted(done for done, _ in progress))

    def test_empty_destination_commit_failure_restores_old_directory(self):
        self.write_archive([('skin.ini', b'ini')])
        destination = self.root / 'destination'
        destination.mkdir()
        original = os.rename
        def fail_commit(source, target):
            if Path(source).name.startswith('.osk-import-') and Path(target) == destination:
                if not Path(source).name.endswith('-empty'):
                    raise OSError('commit failed')
            return original(source, target)
        with patch('core.osk_io.os.rename', side_effect=fail_commit):
            with self.assertRaises(OSError):
                import_osk(self.archive, destination)
        self.assertTrue(destination.is_dir())
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual(list(self.root.glob('.osk-import-*')), [])

    def test_new_destination_commit_failure_leaves_no_partial_skin(self):
        self.write_archive([('skin.ini', b'ini')])
        with patch('core.osk_io.os.rename', side_effect=OSError('commit failed')):
            with self.assertRaises(OSError):
                import_osk(self.archive, self.root / 'destination')
        self.assertFalse((self.root / 'destination').exists())
        self.assertEqual(list(self.root.glob('.osk-import-*')), [])

    def test_concurrent_write_to_empty_destination_is_preserved(self):
        self.write_archive([('skin.ini', b'ini')])
        destination = self.root / 'destination'
        destination.mkdir()
        original = os.rename
        def concurrent_write(source, target):
            if Path(source) == destination:
                (destination / 'user.txt').write_bytes(b'user data')
            return original(source, target)
        with patch('core.osk_io.os.rename', side_effect=concurrent_write):
            with self.assertRaises(FileExistsError):
                import_osk(self.archive, destination)
        self.assertEqual((destination / 'user.txt').read_bytes(), b'user data')
        self.assertFalse((destination / 'skin.ini').exists())
        self.assertEqual(list(self.root.glob('.osk-import-*')), [])

    def test_nul_character_is_rejected_before_path_truncation(self):
        self.write_archive([('skin.ini', b'ini'), ('cursor0.png', b'image')])
        data = self.archive.read_bytes().replace(b'cursor0.png', b'cursor\x00.png')
        self.archive.write_bytes(data)
        with self.assertRaises(ValueError):
            import_osk(self.archive, self.root / 'destination')
        self.assertFalse((self.root / 'destination').exists())

    def test_encrypted_flag_is_rejected_without_prompting(self):
        self.write_archive([('skin.ini', b'ini')])
        data = bytearray(self.archive.read_bytes())
        struct.pack_into('<H', data, 6, struct.unpack_from('<H', data, 6)[0] | 1)
        central = data.index(b'PK\x01\x02')
        struct.pack_into('<H', data, central + 8, struct.unpack_from('<H', data, central + 8)[0] | 1)
        self.archive.write_bytes(data)
        with self.assertRaises(ValueError):
            import_osk(self.archive, self.root / 'destination')
        self.assertFalse((self.root / 'destination').exists())

    def test_valid_explicit_directory_entry_can_follow_implicit_parent(self):
        self.write_archive([('skin.ini', b'ini'), ('notes/a.png', b'a'), ('notes/', b'')])
        import_osk(self.archive, self.root / 'destination')
        self.assertEqual((self.root / 'destination' / 'notes' / 'a.png').read_bytes(), b'a')

    def test_export_atomic_replace_failure_retains_previous_osk(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'skin.ini').write_bytes(b'ini')
        self.archive.write_bytes(b'old output')
        with patch('core.osk_io.os.replace', side_effect=OSError('locked archive')):
            with self.assertRaises(OSError):
                export_osk(source, self.archive)
        self.assertEqual(self.archive.read_bytes(), b'old output')
        self.assertEqual(list(self.root.glob('.osk-export-*.tmp')), [])

    def test_export_rechecks_an_earlier_asset_after_later_asset_is_read(self):
        source = self.root / 'source'
        source.mkdir()
        early = source / 'cursor.png'
        early.write_bytes(b'first')
        (source / 'skin.ini').write_bytes(b'last')
        self.archive.write_bytes(b'old output')
        def mutate_earlier(done, total):
            if done == total:
                early.write_bytes(b'changed later')
        with self.assertRaises(ValueError):
            export_osk(source, self.archive, progress=mutate_earlier)
        self.assertEqual(self.archive.read_bytes(), b'old output')

    def make_directory_link(self, target, link):
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                self.skipTest('Symlinks unavailable')
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                    capture_output=True)
            if result.returncode:
                self.skipTest('Junctions unavailable')
        self.addCleanup(lambda: os.rmdir(link) if os.path.lexists(link) else None)

    def test_import_rejects_real_junction_destination_or_parent(self):
        self.write_archive([('skin.ini', b'ini')])
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.root / 'link'
        self.make_directory_link(outside, link)
        for destination in (link, link / 'new-skin'):
            with self.subTest(destination=destination):
                with self.assertRaises(ValueError):
                    import_osk(self.archive, destination)
        self.assertEqual(list(outside.iterdir()), [])

    def write_legacy_archive(self, name, encoding, data=b'legacy'):
        class EncodedInfo(ZipInfo):
            def _encodeFilenameFlags(self):
                return self.filename.encode(encoding), self.flag_bits & ~0x800
        with ZipFile(self.archive, 'w') as archive:
            archive.writestr('skin.ini', b'ini')
            archive.writestr(EncodedInfo(name), data)

    def test_official_stable_shift_jis_filenames(self):
        self.write_legacy_archive('日本語.png', 'cp932')
        result = import_osk(self.archive, self.root / 'destination')
        self.assertEqual(result.filename_encoding, 'cp932')
        self.assertEqual((self.root / 'destination' / '日本語.png').read_bytes(), b'legacy')

    def test_unflagged_utf8_chinese_filenames(self):
        self.write_legacy_archive('中文.png', 'utf-8')
        result = import_osk(self.archive, self.root / 'destination')
        self.assertEqual(result.filename_encoding, 'utf-8')
        self.assertEqual((self.root / 'destination' / '中文.png').read_bytes(), b'legacy')

    def test_explicit_gb18030_chinese_filenames(self):
        self.write_legacy_archive('中文.png', 'gb18030')
        result = import_osk(self.archive, self.root / 'destination', filename_encoding='gb18030')
        self.assertEqual(result.filename_encoding, 'gb18030')
        self.assertEqual((self.root / 'destination' / '中文.png').read_bytes(), b'legacy')

    def test_export_unicode_utf8_paths_and_untouched_lazer_json(self):
        source = self.root / 'source'
        source.mkdir()
        metadata = b'{"Name":"test","InstantiationInfo":"native"}'
        (source / 'skininfo.json').write_bytes(metadata)
        (source / '中文.png').write_bytes(b'image')
        progress = []
        export_osk(source, self.archive, progress=lambda done, total: progress.append((done, total)))
        with ZipFile(self.archive) as archive:
            self.assertEqual(archive.read('skininfo.json'), metadata)
            self.assertEqual(archive.read('中文.png'), b'image')
            self.assertTrue(archive.getinfo('中文.png').flag_bits & 0x800)
        self.assertEqual(progress[0][0], 0)
        self.assertEqual(progress[-1][0], progress[-1][1])

    def test_cancelled_export_retains_old_output_and_cleans_temporary(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'skin.ini').write_bytes(b'x' * 200000)
        self.archive.write_bytes(b'old output')
        done_bytes = []
        with self.assertRaises(OskCancelled):
            export_osk(source, self.archive, progress=lambda done, total: done_bytes.append(done),
                       cancelled=lambda: any(done >= 65536 for done in done_bytes))
        self.assertEqual(self.archive.read_bytes(), b'old output')
        self.assertEqual(list(self.root.glob('.osk-export-*.tmp')), [])

    def test_cancel_at_complete_progress_does_not_commit_export(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'skin.ini').write_bytes(b'ini')
        self.archive.write_bytes(b'old output')
        finished = []
        with self.assertRaises(OskCancelled):
            export_osk(source, self.archive, progress=lambda done, total: finished.append(done == total),
                       cancelled=lambda: any(finished))
        self.assertEqual(self.archive.read_bytes(), b'old output')

    def test_external_source_mutation_aborts_export_and_preserves_old_output(self):
        source = self.root / 'source'
        source.mkdir()
        asset = source / 'skin.ini'
        asset.write_bytes(b'old')
        self.archive.write_bytes(b'old output')
        def mutate(done, total):
            if done:
                asset.write_bytes(b'new content')
        with self.assertRaises(ValueError):
            export_osk(source, self.archive, progress=mutate)
        self.assertEqual(self.archive.read_bytes(), b'old output')
        self.assertEqual(asset.read_bytes(), b'new content')

    def test_external_new_asset_aborts_export_and_preserves_old_output(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'skin.ini').write_bytes(b'ini')
        self.archive.write_bytes(b'old output')
        def add_asset(done, total):
            if done:
                (source / 'cursor.png').write_bytes(b'new asset')
        with self.assertRaises(ValueError):
            export_osk(source, self.archive, progress=add_asset)
        self.assertEqual(self.archive.read_bytes(), b'old output')

    def test_export_skips_linked_subdirectory_and_preserves_external_content(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'skin.ini').write_bytes(b'ini')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'private.png').write_bytes(b'private')
        self.make_directory_link(outside, source / 'link')
        export_osk(source, self.archive)
        with ZipFile(self.archive) as archive:
            self.assertEqual(archive.namelist(), ['skin.ini'])
        self.assertEqual((outside / 'private.png').read_bytes(), b'private')

    def test_export_rejects_real_junction_output_parent(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'skin.ini').write_bytes(b'ini')
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.root / 'link'
        self.make_directory_link(outside, link)
        with self.assertRaises(ValueError):
            export_osk(source, link / 'output.osk')
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
