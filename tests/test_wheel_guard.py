import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSpinBox

from ui.mania_ini_dock import ManiaIniDock
from ui.widgets.wheel_guard import ClickWheelSpinBox


class ManiaWheelGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "skin.ini").write_text(
            "[Mania]\nKeys: 4\nHitPosition: 402\n"
            "[Mania]\nKeys: 7\nHitPosition: 410\n", encoding="utf-8"
        )
        self.dock = ManiaIniDock()
        self.dock.set_skin_root(self.root)
        self.dock.resize(380, 500)
        self.dock.show()
        self.dock.activateWindow()
        self.app.processEvents()
        self.scroll = self.dock.widget()

    def tearDown(self):
        self.dock.close()
        self.dock.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.temp.cleanup()

    def wheel(self, widget, delta=-120):
        position = widget.rect().center()
        event = QWheelEvent(
            QPointF(position), QPointF(widget.mapToGlobal(position)),
            QPoint(), QPoint(0, delta), Qt.NoButton, Qt.NoModifier,
            Qt.NoScrollPhase, False,
        )
        QApplication.sendEvent(widget, event)
        self.app.processEvents()

    def click_text(self, spin):
        self.scroll.ensureWidgetVisible(spin)
        self.app.processEvents()
        QTest.mouseClick(spin.lineEdit(), Qt.LeftButton)
        self.app.processEvents()

    def test_auto_focus_does_not_arm_keys_selector_or_new_keys(self):
        changes = []
        self.dock.keys_changed.connect(changes.append)
        for field in (self.dock.cmb_keys, self.dock.spn_keys):
            with self.subTest(field=type(field).__name__):
                self.scroll.verticalScrollBar().setValue(0)
                field.setFocus(Qt.ActiveWindowFocusReason)
                self.app.processEvents()
                self.wheel(field)
                self.assertEqual(self.dock._current_view_k, 4)
                self.assertEqual(self.dock.spn_keys.value(), 4)
                self.assertEqual(self.dock.cmb_keys.currentData(), 4)
                self.assertGreater(self.scroll.verticalScrollBar().value(), 0)
        self.assertEqual(changes, [])
        self.assertFalse(self.dock._dirty)

    def test_wheel_over_numeric_text_scrolls_without_dirtying(self):
        spin = self.dock.spn_hit_pos
        self.scroll.ensureWidgetVisible(spin)
        spin.setFocus(Qt.TabFocusReason)
        self.app.processEvents()
        before_scroll = self.scroll.verticalScrollBar().value()
        self.wheel(spin.lineEdit())
        self.assertEqual(spin.value(), 402)
        self.assertFalse(self.dock._dirty)
        self.assertGreater(self.scroll.verticalScrollBar().value(), before_scroll)

    def test_clicking_numeric_text_allows_wheel_edits_and_marks_dirty(self):
        spin = self.dock.spn_hit_pos
        self.click_text(spin)
        before_scroll = self.scroll.verticalScrollBar().value()
        self.wheel(spin.lineEdit(), 120)
        self.assertEqual(spin.value(), 403)
        self.assertTrue(self.dock._dirty)
        self.assertEqual(self.scroll.verticalScrollBar().value(), before_scroll)

    def test_clicking_another_field_disarms_previous_field(self):
        first, second = self.dock.spn_hit_pos, self.dock.spn_barline_h
        self.click_text(first)
        self.wheel(first, 120)
        self.click_text(second)
        self.dock._clear_dirty()
        before_scroll = self.scroll.verticalScrollBar().value()
        self.wheel(first)
        self.assertEqual(first.value(), 403)
        self.assertFalse(self.dock._dirty)
        self.assertGreater(self.scroll.verticalScrollBar().value(), before_scroll)
        # Returning focus without another click must not restore the permission.
        first.setFocus(Qt.TabFocusReason)
        self.wheel(first, 120)
        self.assertEqual(first.value(), 403)

    def test_tab_focus_keeps_keyboard_editing_without_arming_wheel(self):
        spin = self.dock.spn_hit_pos
        self.click_text(spin)
        QTest.keyClick(spin, Qt.Key_Tab)
        self.assertFalse(spin.hasFocus())
        QTest.keyClick(QApplication.focusWidget(), Qt.Key_Backtab)
        self.assertTrue(spin.hasFocus())
        QTest.keyClick(spin, Qt.Key_Up)
        self.assertEqual(spin.value(), 403)
        self.dock._clear_dirty()
        self.wheel(spin)
        self.assertEqual(spin.value(), 403)
        self.assertFalse(self.dock._dirty)

    def test_explicit_click_allows_keys_combo_wheel_selection(self):
        combo = self.dock.cmb_keys
        # Offscreen Windows deactivates the whole window when closing a native
        # popup. Test the click/wheel contract without that platform artefact.
        with patch.object(combo, "showPopup"):
            QTest.mouseClick(combo, Qt.LeftButton)
        self.app.processEvents()
        self.wheel(combo)
        self.assertEqual(self.dock._current_view_k, 7)
        self.assertEqual(self.dock.spn_hit_pos.value(), 410)

    def test_every_mania_numeric_field_has_click_guard(self):
        fields = self.dock.findChildren(QSpinBox)
        self.assertGreater(len(fields), 10)
        self.assertTrue(all(isinstance(field, ClickWheelSpinBox) for field in fields))

    def test_new_skin_prefers_4k_over_first_section(self):
        target = self.root / "other"
        target.mkdir()
        (target / "skin.ini").write_text(
            "[Mania]\nKeys: 1\nHitPosition: 300\n"
            "[Mania]\nKeys: 4\nHitPosition: 450\n", encoding="utf-8"
        )
        self.dock.set_skin_root(target)
        self.assertEqual(self.dock._current_view_k, 4)
        self.assertEqual(self.dock.spn_hit_pos.value(), 450)

    def test_loading_new_skin_honours_preview_preferred_keys(self):
        target = self.root / "other"
        target.mkdir()
        (target / "skin.ini").write_text(
            "[Mania]\nKeys: 4\nHitPosition: 402\n"
            "[Mania]\nKeys: 7\nHitPosition: 450\n", encoding="utf-8"
        )
        self.dock.set_skin_root(target, preferred_keys=7)
        self.assertEqual(self.dock._current_view_k, 7)
        self.assertEqual(self.dock.spn_hit_pos.value(), 450)

    def test_same_skin_reload_retains_selected_keys(self):
        self.dock.cmb_keys.setCurrentIndex(self.dock.cmb_keys.findData(7))
        self.dock.set_skin_root(self.root, preferred_keys=4)
        self.assertEqual(self.dock._current_view_k, 7)
        self.assertEqual(self.dock.spn_hit_pos.value(), 410)

    def test_new_skin_does_not_inherit_previous_skin_key_selection(self):
        self.dock.cmb_keys.setCurrentIndex(self.dock.cmb_keys.findData(7))
        target = self.root / "other"
        target.mkdir()
        (target / "skin.ini").write_text((self.root / "skin.ini").read_text(encoding="utf-8"), encoding="utf-8")
        self.dock.set_skin_root(target)
        self.assertEqual(self.dock._current_view_k, 4)

    def test_7k_fallback_precedes_first_section_when_4k_absent(self):
        target = self.root / "other"
        target.mkdir()
        (target / "skin.ini").write_text(
            "[Mania]\nKeys: 1\n[Mania]\nKeys: 7\n", encoding="utf-8"
        )
        self.dock.set_skin_root(target)
        self.assertEqual(self.dock._current_view_k, 7)


if __name__ == "__main__":
    unittest.main()
