import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from core.assets_ops import (
    BACKUP_FOLDER, backup_dir, list_audio, list_images, replace_audio,
    replace_image, resolve_audio_conflicts, stem_conflicts,
)
from core.image_ops import outline


class AssetOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "skin"
        self.root.mkdir()
        self.source = Path(self.temp.name) / "source"
        self.source.mkdir()

    def write(self, relative, content=b"audio"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_case_insensitive_discovery_skips_backups(self):
        image = self.write("CURSOR.PNG")
        audio = self.write("sub/HIT.OGG")
        self.write(f"{BACKUP_FOLDER}/old/HIT.wav")
        self.write(f"{BACKUP_FOLDER}/old/CURSOR.png")
        self.assertEqual(list_images(self.root), [image])
        self.assertEqual(list_audio(self.root), [audio])

    def test_conflicts_only_compare_files_in_same_directory(self):
        paths = [self.write("HIT.WAV"), self.write("hit.ogg"), self.write("other/hit.wav")]
        groups = list(stem_conflicts(paths).values())
        self.assertEqual(groups, [paths[:2]])

    def test_conflict_cleanup_preserves_nested_assets_and_previous_backups(self):
        keep = self.write("hit.ogg", b"keep")
        self.write("hit.wav", b"remove")
        sibling = self.write("other/hit.wav", b"other")
        previous = self.write(f"{BACKUP_FOLDER}/old/hit.wav", b"previous")
        directory = resolve_audio_conflicts(self.root, {"hit": keep})
        self.assertEqual((directory / "hit.wav").read_bytes(), b"remove")
        self.assertFalse((self.root / "hit.wav").exists())
        self.assertEqual(keep.read_bytes(), b"keep")
        self.assertEqual(sibling.read_bytes(), b"other")
        self.assertEqual(previous.read_bytes(), b"previous")

    def test_invalid_conflict_choice_changes_nothing(self):
        old = self.write("hit.wav", b"old")
        with self.assertRaises(ValueError):
            resolve_audio_conflicts(self.root, {"hit": self.root / "missing.ogg"})
        self.assertEqual(old.read_bytes(), b"old")
        self.assertFalse((self.root / BACKUP_FOLDER).exists())

    def test_backups_are_unique_even_within_same_second(self):
        self.assertNotEqual(backup_dir(self.root), backup_dir(self.root))

    def test_image_conversion_preserves_both_replaced_files(self):
        old_jpg = self.root / "cursor.jpg"
        old_png = self.root / "cursor.png"
        Image.new("RGB", (3, 3), "red").save(old_jpg)
        Image.new("RGBA", (4, 4), "blue").save(old_png)
        jpg_bytes, png_bytes = old_jpg.read_bytes(), old_png.read_bytes()
        source = self.source / "replacement.webp"
        Image.new("RGBA", (12, 10), "green").save(source)
        result = replace_image(source, old_jpg, skin_root=self.root)
        with Image.open(result) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (12, 10))
        self.assertFalse(old_jpg.exists())
        self.assertEqual(next((self.root / BACKUP_FOLDER).rglob("cursor.jpg")).read_bytes(), jpg_bytes)
        self.assertEqual(next((self.root / BACKUP_FOLDER).rglob("cursor.png")).read_bytes(), png_bytes)

    def test_broken_image_leaves_original_and_no_temporary_files(self):
        old = self.root / "cursor.png"
        Image.new("RGBA", (2, 2), "red").save(old)
        before = old.read_bytes()
        source = self.source / "broken.png"
        source.write_bytes(b"not an image")
        with self.assertRaises(Exception):
            replace_image(source, old, skin_root=self.root)
        self.assertEqual(old.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".osu-asset-*")), [])

    def test_audio_matching_encoding_preserves_variants_and_source(self):
        old = self.write("sub/hit.wav", b"original wav")
        self.write("sub/hit.ogg", b"original ogg")
        source = self.source / "new.ogg"
        source.write_bytes(b"replacement ogg")
        with patch("core.assets_ops.subprocess.run") as convert:
            result = replace_audio(source, old, prefer_ext=".ogg", skin_root=self.root)
        convert.assert_not_called()
        self.assertEqual(result.name, "hit.ogg")
        self.assertEqual(result.read_bytes(), b"replacement ogg")
        self.assertFalse(old.exists())
        backup_files = {path.name: path.read_bytes() for path in (self.root / BACKUP_FOLDER).rglob("*") if path.is_file()}
        self.assertEqual(backup_files, {"hit.wav": b"original wav", "hit.ogg": b"original ogg"})
        self.assertEqual(source.read_bytes(), b"replacement ogg")

    def test_different_audio_encoding_requires_actual_conversion(self):
        old = self.write("hit.wav", b"old wav")
        source = self.source / "new.ogg"
        source.write_bytes(b"ogg bytes")

        def convert(command, **kwargs):
            self.assertEqual(command[0], "ffmpeg")
            self.assertEqual(Path(command[-1]).suffix, ".wav")
            Path(command[-1]).write_bytes(b"RIFF converted wav")
            return subprocess.CompletedProcess(command, 0)

        with patch("core.assets_ops.subprocess.run", side_effect=convert) as conversion:
            result = replace_audio(source, old, skin_root=self.root)
        conversion.assert_called_once()
        self.assertEqual(result.read_bytes(), b"RIFF converted wav")

    def test_audio_can_use_existing_variant_as_replacement(self):
        old = self.write("hit.wav", b"old wav")
        source = self.write("hit.ogg", b"chosen ogg")
        result = replace_audio(source, old, prefer_ext=".ogg", skin_root=self.root)
        self.assertEqual(result.read_bytes(), b"chosen ogg")
        self.assertFalse(old.exists())
        self.assertEqual(next((self.root / BACKUP_FOLDER).rglob("hit.wav")).read_bytes(), b"old wav")

    def test_missing_converter_preserves_original_audio(self):
        old = self.write("hit.wav", b"old wav")
        source = self.source / "new.flac"
        source.write_bytes(b"flac")
        with patch("core.assets_ops.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(RuntimeError, "ffmpeg"):
                replace_audio(source, old, skin_root=self.root)
        self.assertEqual(old.read_bytes(), b"old wav")
        self.assertEqual(list(self.root.glob(".osu-asset-*")), [])

    def test_replacement_rejects_path_outside_skin(self):
        source = self.source / "new.wav"
        source.write_bytes(b"source")
        with self.assertRaises(ValueError):
            replace_audio(source, self.source / "other.wav", skin_root=self.root)
        self.assertEqual(source.read_bytes(), b"source")

    def test_outline_accepts_rgb_and_respects_outline_opacity(self):
        result = outline(Image.new("RGB", (4, 4), "red"))
        self.assertEqual(result.mode, "RGBA")
        image = Image.new("RGBA", (5, 5))
        image.putpixel((2, 2), (255, 255, 255, 255))
        outlined = outline(image, 1, (255, 0, 0, 64))
        self.assertEqual(outlined.getpixel((1, 2)), (255, 0, 0, 64))
        self.assertEqual(outlined.getpixel((2, 2)), (255, 255, 255, 255))


if __name__ == "__main__":
    unittest.main()
