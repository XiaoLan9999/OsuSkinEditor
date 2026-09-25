import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import Qt, QUrl, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QAbstractItemView

from ui.assets_manager import AssetsManagerDialog


class AssetsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.image_path = self.root / "cursor.png"
        Image.new("RGBA", (48, 32), "pink").save(self.image_path)
        with wave.open(str(self.root / "hit.wav"), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes(b"\x00\x00" * 800)
        self.dialog = AssetsManagerDialog(self.root)

    def tearDown(self):
        self.dialog.reject()
        self.dialog.deleteLater()
        # These tests have no app.exec() loop to dispatch deferred destruction.
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.dialog = None
        self.temp.cleanup()

    def test_image_selection_keeps_pixmap_and_table_cannot_edit_paths(self):
        self.dialog.img_table.selectRow(0)
        self.assertFalse(self.dialog.img_preview.pixmap().isNull())
        self.assertEqual(self.dialog.img_table.editTriggers(), QAbstractItemView.NoEditTriggers)
        self.dialog.img_table.item(0, 2).setText("../../outside.png")
        self.assertEqual(self.dialog._selected_path(self.dialog.img_table), self.image_path)

    def test_filtering_selected_image_clears_selection_and_preview(self):
        self.dialog.img_table.selectRow(0)
        self.dialog.img_search.setText("no such image")
        self.assertIsNone(self.dialog._selected_path(self.dialog.img_table))
        self.assertTrue(self.dialog.img_preview.pixmap().isNull())
        self.assertFalse(self.dialog.btn_img_replace.isEnabled())

    def test_initial_volume_matches_slider_and_close_releases_audio(self):
        if self.dialog.player is None:
            self.skipTest("QtMultimedia unavailable")
        self.assertAlmostEqual(self.dialog.audio_output.volume(), 0.25)
        self.dialog.aud_table.selectRow(0)
        self.assertFalse(self.dialog.player.source().isEmpty())
        self.dialog.accept()
        self.assertEqual(self.dialog.player.source(), QUrl())

    def test_replace_refreshes_preview_and_emits_change(self):
        self.dialog.img_table.selectRow(0)
        replacement = self.root / "new-image.png"
        Image.new("RGBA", (14, 22), "blue").save(replacement)
        changes = []
        self.dialog.assets_changed.connect(lambda: changes.append(True))
        with patch("ui.assets_manager.QFileDialog.getOpenFileName", return_value=(str(replacement), "")):
            self.dialog._replace_image()
        self.assertEqual(changes, [True])
        self.assertEqual(self.dialog._original_pixmap.width(), 14)
        self.assertEqual(self.dialog._original_pixmap.height(), 22)
        self.assertEqual(self.dialog._selected_path(self.dialog.img_table), self.image_path)


if __name__ == "__main__":
    unittest.main()
