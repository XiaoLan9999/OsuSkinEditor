from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from core import image_compat as compat
from core.image_compat import (
    ImageRepairCancelled, ImageRepairError, repair_skin_images, scan_skin_images,
)


class ImageCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "skin"
        self.root.mkdir()

    def image(self, name="Mania/LN_Body.png", *, fmt="TIFF", mode="RGBA",
              size=(138, 400), color=None):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if color is None:
            color = (42, 133, 190, 109) if mode == "RGBA" else 42
        with Image.new(mode, size, color) as image:
            image.save(path, format=fmt)
        return path

    def rgba(self, path):
        with Image.open(path) as image:
            return image.convert("RGBA").tobytes()

    def mismatch(self, **kwargs):
        path = self.image(**kwargs)
        scan = scan_skin_images(self.root)
        self.assertEqual(scan.scanned_count, 1)
        self.assertEqual(len(scan.issues), 1)
        self.assertTrue(scan.issues[0].repairable)
        return path, scan.issues[0]

    def test_tiff_disguised_as_png_is_detected_read_only(self):
        path = self.image()
        before = path.read_bytes()
        before_stat = path.stat()
        progress = []
        result = scan_skin_images(self.root, progress=lambda *value: progress.append(value))
        issue, = result.issues
        self.assertEqual((issue.relative_path, issue.declared_format, issue.actual_format),
                         ("Mania/LN_Body.png", "PNG", "TIFF"))
        self.assertEqual((issue.width, issue.height, issue.status, issue.repairable),
                         (138, 400, "mismatch", True))
        self.assertEqual(issue.sha256, hashlib.sha256(before).hexdigest())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(path.stat().st_mtime_ns, before_stat.st_mtime_ns)
        self.assertEqual(progress, [(1, 1, "Mania/LN_Body.png")])
        self.assertFalse((self.root / "__conflicts_backup").exists())

    def test_repair_preserves_rgba_and_references_and_keeps_verified_original(self):
        path, issue = self.mismatch()
        before, pixels = path.read_bytes(), self.rgba(path)
        ini = self.root / "skin.ini"
        ini.write_bytes(b"[Mania]\r\nNoteImage0L = Mania/LN_Body\r\n")
        before_ini = ini.read_bytes()
        progress = []
        result = repair_skin_images(self.root, [issue], progress=lambda *value: progress.append(value))
        self.assertEqual(result.repaired, ("Mania/LN_Body.png",))
        self.assertEqual(result.skipped, ())
        self.assertEqual((result.backup_dir / issue.relative_path).read_bytes(), before)
        self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(self.rgba(path), pixels)
        self.assertEqual(ini.read_bytes(), before_ini)
        self.assertEqual(scan_skin_images(self.root).issues, ())
        self.assertEqual(progress, [(1, 1, "Mania/LN_Body.png")])
        self.assertEqual(list(path.parent.glob(".osu-image-repair-*")), [])

    def test_transparent_rounded_ln_at_40000_height_preserves_every_pixel(self):
        path = self.root / "long.png"
        with Image.new("RGBA", (138, 40_000), (0, 0, 0, 0)) as image:
            ImageDraw.Draw(image).rounded_rectangle((4, 0, 133, 39_999), radius=64,
                                                   fill=(111, 112, 113, 160))
            image.save(path, format="TIFF")
        before = self.rgba(path)
        issue, = scan_skin_images(self.root).issues
        self.assertTrue(issue.repairable)
        repair_skin_images(self.root, [issue])
        self.assertEqual(self.rgba(path), before)
        with Image.open(path) as image:
            self.assertEqual(image.size, (138, 40_000))
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 0, 0))
            self.assertEqual(image.getpixel((69, 0)), (111, 112, 113, 160))

    def test_valid_png_is_untouched_and_no_backup_is_created(self):
        path = self.image(fmt="PNG")
        before = path.read_bytes()
        scan = scan_skin_images(self.root)
        self.assertEqual(scan.issues, ())
        result = repair_skin_images(self.root, scan.issues)
        self.assertEqual(path.read_bytes(), before)
        self.assertIsNone(result.backup_dir)

    def test_common_extensions_are_inspected_by_content(self):
        self.image("normal.png", fmt="PNG")
        self.image("normal.jpg", fmt="JPEG", mode="RGB", color=(12, 34, 56))
        self.image("normal.bmp", fmt="BMP")
        self.image("normal.gif", fmt="GIF")
        self.image("normal.webp", fmt="WEBP")
        self.image("actual.tiff", fmt="TIFF")
        self.image("disguised.jpeg", fmt="PNG")
        result = scan_skin_images(self.root)
        self.assertEqual(result.scanned_count, 7)
        self.assertEqual({issue.relative_path for issue in result.issues},
                         {"actual.tiff", "disguised.jpeg"})
        self.assertTrue(all(not issue.repairable for issue in result.issues))
        self.assertEqual(next(i for i in result.issues if i.relative_path == "actual.tiff").status,
                         "unsupported")

    def test_single_frame_palette_transparency_is_lossless(self):
        path = self.root / "indexed.png"
        with Image.new("P", (8, 4)) as image:
            image.putpalette([12, 34, 56, 99, 88, 77] + [0] * (768 - 6))
            image.putpixel((3, 2), 1)
            image.save(path, format="GIF", transparency=0)
        before = self.rgba(path)
        issue, = scan_skin_images(self.root).issues
        self.assertTrue(issue.repairable)
        repair_skin_images(self.root, [issue])
        self.assertEqual(self.rgba(path), before)

    def test_multiframe_tiff_is_reported_and_never_flattened(self):
        path = self.root / "multi.png"
        with Image.new("RGBA", (8, 8), "red") as first, Image.new("RGBA", (8, 8), "blue") as second:
            first.save(path, format="TIFF", save_all=True, append_images=[second])
        before = path.read_bytes()
        issue, = scan_skin_images(self.root).issues
        self.assertFalse(issue.repairable)
        self.assertIn("多帧", issue.message)
        result = repair_skin_images(self.root, [issue])
        self.assertEqual(result.skipped, ("multi.png",))
        self.assertEqual(path.read_bytes(), before)
        self.assertIsNone(result.backup_dir)

    def test_animated_gif_is_not_flattened(self):
        path = self.root / "animation.png"
        with Image.new("RGB", (8, 8), "red") as first, Image.new("RGB", (8, 8), "blue") as second:
            first.save(path, format="GIF", save_all=True, append_images=[second])
        issue, = scan_skin_images(self.root).issues
        self.assertFalse(issue.repairable)
        self.assertIn("多帧", issue.message)

    def test_16_bit_tiff_is_not_truncated(self):
        path = self.image("high.png", mode="I;16", color=4096)
        before = path.read_bytes()
        issue, = scan_skin_images(self.root).issues
        self.assertFalse(issue.repairable)
        self.assertIn("位深", issue.message)
        repair_skin_images(self.root, [issue])
        self.assertEqual(path.read_bytes(), before)

    def test_16_bit_png_is_reported_without_changing_it(self):
        path = self.image("high.png", fmt="PNG", mode="I;16", color=4096)
        before = path.read_bytes()
        issue, = scan_skin_images(self.root).issues
        self.assertFalse(issue.repairable)
        self.assertEqual(issue.status, "unsupported")
        self.assertEqual(path.read_bytes(), before)

    def test_unsupported_color_mode_is_not_converted(self):
        self.image("cmyk.png", mode="CMYK", color=(11, 22, 33, 44))
        issue, = scan_skin_images(self.root).issues
        self.assertFalse(issue.repairable)
        self.assertIn("颜色模式", issue.message)

    def test_corrupt_header_and_truncated_image_are_unreadable(self):
        (self.root / "broken.png").write_bytes(b"not an image")
        stream = io.BytesIO()
        with Image.new("RGB", (30, 30), "red") as image:
            image.save(stream, format="PNG")
        (self.root / "truncated.png").write_bytes(stream.getvalue()[:40])
        scan = scan_skin_images(self.root)
        self.assertEqual(scan.scanned_count, 2)
        self.assertTrue(all(issue.status == "unreadable" and not issue.repairable for issue in scan.issues))

    def test_oversize_file_and_pixel_count_are_bounded(self):
        self.image("big.png", size=(50, 50))
        with patch.object(compat, "MAX_FILE_BYTES", 2):
            issue, = scan_skin_images(self.root).issues
            self.assertIn("文件过大", issue.message)
        with patch.object(compat, "MAX_IMAGE_PIXELS", 10):
            issue, = scan_skin_images(self.root).issues
            self.assertFalse(issue.repairable)
            self.assertIn("尺寸过大", issue.message)

    def test_changed_stat_refuses_repair(self):
        path, issue = self.mismatch()
        before = path.read_bytes()
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))
        with self.assertRaisesRegex(ImageRepairError, "发生变化") as caught:
            repair_skin_images(self.root, [issue])
        self.assertEqual(caught.exception.partial_result.repaired, ())
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.root / "__conflicts_backup").exists())

    def test_changed_hash_refuses_repair_even_with_current_stat(self):
        path, issue = self.mismatch()
        self.image(color=(1, 2, 3, 4))
        issue = replace(issue, _identity=compat._identity(path.stat()))
        before = path.read_bytes()
        with self.assertRaisesRegex(ImageRepairError, "内容.*发生变化"):
            repair_skin_images(self.root, [issue])
        self.assertEqual(path.read_bytes(), before)

    def test_change_during_encoding_refuses_replacement(self):
        path, issue = self.mismatch()
        original_stage = compat._stage_png

        def stage_and_change(*args):
            staged = original_stage(*args)
            path.write_bytes(b"new externally changed source")
            return staged

        with patch.object(compat, "_stage_png", side_effect=stage_and_change):
            with self.assertRaises(ImageRepairError) as caught:
                repair_skin_images(self.root, [issue])
        self.assertEqual(path.read_bytes(), b"new externally changed source")
        self.assertEqual(caught.exception.partial_result.repaired, ())
        self.assertEqual(list(path.parent.glob(".osu-image-repair-*")), [])

    def test_internal_and_backup_directories_are_excluded(self):
        for directory in ("__conflicts_backup", ".skin_ini_history", ".git", "__pycache__"):
            self.image(directory + "/hidden.png")
        self.image("visible.png")
        scan = scan_skin_images(self.root)
        self.assertEqual(scan.scanned_count, 1)
        self.assertEqual(scan.issues[0].relative_path, "visible.png")

    def test_scan_skips_symlink_files_and_directories(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        source = self.image("source.png")
        (outside / "other.png").write_bytes(source.read_bytes())
        try:
            (self.root / "linked.png").symlink_to(source)
            (self.root / "linked-directory").symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        scan = scan_skin_images(self.root)
        self.assertEqual(scan.scanned_count, 1)
        self.assertEqual(scan.issues[0].relative_path, "source.png")

    def test_repair_refuses_a_replaced_symlink(self):
        path, issue = self.mismatch()
        outside = Path(self.temp.name) / "outside.png"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        before = outside.read_bytes()
        with self.assertRaises(ImageRepairError):
            repair_skin_images(self.root, [issue])
        self.assertEqual(outside.read_bytes(), before)

    def test_reparse_attribute_is_treated_as_a_link(self):
        class FakeStat:
            st_mode = stat_mode = 0o040755
            st_file_attributes = compat._REPARSE_POINT
        self.assertTrue(compat._linked(FakeStat()))

    def test_path_traversal_and_backup_references_are_refused(self):
        path, issue = self.mismatch()
        before = path.read_bytes()
        for value in ("../outside.png", "/absolute.png", "C:/outside.png", "Mania/../LN_Body.png",
                      "__conflicts_backup/original.png", ".git/file.png"):
            with self.subTest(value=value), self.assertRaises(ImageRepairError):
                repair_skin_images(self.root, [replace(issue, relative_path=value)])
        self.assertEqual(path.read_bytes(), before)

    def test_backup_write_failure_leaves_source_and_cleans_stage(self):
        path, issue = self.mismatch()
        before = path.read_bytes()
        with patch.object(compat, "_write_verified_backup", side_effect=OSError("no space")):
            with self.assertRaises(ImageRepairError) as caught:
                repair_skin_images(self.root, [issue])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(caught.exception.partial_result.repaired, ())
        self.assertIsNotNone(caught.exception.partial_result.backup_dir)
        self.assertEqual(list(path.parent.glob(".osu-image-repair-*")), [])

    def test_atomic_replace_failure_keeps_verified_backup_and_original(self):
        path, issue = self.mismatch()
        before = path.read_bytes()
        with patch.object(compat.os, "replace", side_effect=PermissionError("file in use")):
            with self.assertRaises(ImageRepairError) as caught:
                repair_skin_images(self.root, [issue])
        result = caught.exception.partial_result
        self.assertEqual(result.repaired, ())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((result.backup_dir / issue.relative_path).read_bytes(), before)
        self.assertEqual(list(path.parent.glob(".osu-image-repair-*")), [])

    def test_backup_bytes_are_reverified_before_replacement(self):
        path, issue = self.mismatch()
        before = path.read_bytes()
        original_read = Path.read_bytes

        def corrupted_backup_read(candidate):
            if "__conflicts_backup" in candidate.parts:
                return b"damaged backup bytes"
            return original_read(candidate)

        with patch.object(Path, "read_bytes", corrupted_backup_read):
            with self.assertRaisesRegex(ImageRepairError, "备份校验失败") as caught:
                repair_skin_images(self.root, [issue])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(caught.exception.partial_result.repaired, ())

    def test_encoded_png_is_reverified_before_backup_or_replacement(self):
        path, issue = self.mismatch()
        before = path.read_bytes()
        original_save = Image.Image.save

        def incorrect_save(image, stream, *args, **kwargs):
            if kwargs.get("format") == "PNG":
                with Image.new("RGBA", image.size, (0, 0, 0, 0)) as wrong:
                    return original_save(wrong, stream, *args, **kwargs)
            return original_save(image, stream, *args, **kwargs)

        with patch.object(Image.Image, "save", incorrect_save):
            with self.assertRaisesRegex(ImageRepairError, "像素校验失败") as caught:
                repair_skin_images(self.root, [issue])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(caught.exception.partial_result.repaired, ())
        self.assertIsNone(caught.exception.partial_result.backup_dir)
        self.assertEqual(list(path.parent.glob(".osu-image-repair-*")), [])

    def test_second_file_encoding_error_preserves_partial_result(self):
        self.image("a.png")
        second = self.image("b.png")
        before = second.read_bytes()
        issues = scan_skin_images(self.root).issues
        original_stage = compat._stage_png
        calls = 0

        def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("encoder unavailable")
            return original_stage(*args)

        with patch.object(compat, "_stage_png", side_effect=fail_second):
            with self.assertRaises(ImageRepairError) as caught:
                repair_skin_images(self.root, issues)
        self.assertEqual(caught.exception.partial_result.repaired, ("a.png",))
        self.assertEqual(second.read_bytes(), before)

    def test_cancel_scan_does_not_change_files(self):
        path = self.image()
        before = path.read_bytes()
        with self.assertRaises(ImageRepairCancelled) as caught:
            scan_skin_images(self.root, cancelled=lambda: True)
        self.assertIsNone(caught.exception.partial_result)
        self.assertEqual(path.read_bytes(), before)

    def test_cancel_before_repair_has_empty_partial_result(self):
        path, issue = self.mismatch()
        before = path.read_bytes()
        with self.assertRaises(ImageRepairCancelled) as caught:
            repair_skin_images(self.root, [issue], cancelled=lambda: True)
        self.assertEqual(caught.exception.partial_result.repaired, ())
        self.assertIsNone(caught.exception.partial_result.backup_dir)
        self.assertEqual(path.read_bytes(), before)

    def test_cancellation_after_one_repair_reports_completed_items(self):
        first = self.image("a.png")
        second = self.image("b.png")
        originals = {p.name: p.read_bytes() for p in (first, second)}
        issues = scan_skin_images(self.root).issues
        cancel = False

        def progress(done, total, relative):
            nonlocal cancel
            self.assertEqual((done, total, relative), (1, 2, "a.png"))
            cancel = True

        with self.assertRaises(ImageRepairCancelled) as caught:
            repair_skin_images(self.root, issues, cancelled=lambda: cancel, progress=progress)
        result = caught.exception.partial_result
        self.assertEqual(result.repaired, ("a.png",))
        self.assertEqual(first.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual((result.backup_dir / "a.png").read_bytes(), originals["a.png"])
        self.assertEqual(second.read_bytes(), originals["b.png"])

    def test_cancellation_after_backup_keeps_original(self):
        path, issue = self.mismatch()
        before = path.read_bytes()
        cancel = False
        original_backup = compat._write_verified_backup

        def backup_and_cancel(*args):
            nonlocal cancel
            original_backup(*args)
            cancel = True

        with patch.object(compat, "_write_verified_backup", side_effect=backup_and_cancel):
            with self.assertRaises(ImageRepairCancelled) as caught:
                repair_skin_images(self.root, [issue], cancelled=lambda: cancel)
        result = caught.exception.partial_result
        self.assertEqual(result.repaired, ())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((result.backup_dir / issue.relative_path).read_bytes(), before)
        self.assertEqual(list(path.parent.glob(".osu-image-repair-*")), [])

    def test_failure_after_one_repair_is_not_reported_as_whole_batch_atomic(self):
        self.image("a.png")
        second = self.image("b.png")
        before = second.read_bytes()
        issues = scan_skin_images(self.root).issues
        original_replace = compat.os.replace
        calls = 0

        def fail_second(src, dst):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PermissionError("blocked")
            original_replace(src, dst)

        with patch.object(compat.os, "replace", side_effect=fail_second):
            with self.assertRaises(ImageRepairError) as caught:
                repair_skin_images(self.root, issues)
        result = caught.exception.partial_result
        self.assertEqual(result.repaired, ("a.png",))
        self.assertEqual(second.read_bytes(), before)
        self.assertEqual((result.backup_dir / "b.png").read_bytes(), before)

    def test_independent_repairs_have_unique_backup_directories(self):
        _, issue = self.mismatch()
        first = repair_skin_images(self.root, [issue])
        _, issue = self.mismatch()
        second = repair_skin_images(self.root, [issue, issue])
        self.assertNotEqual(first.backup_dir, second.backup_dir)
        self.assertEqual(second.repaired, ("Mania/LN_Body.png",))


if __name__ == "__main__":
    unittest.main()
