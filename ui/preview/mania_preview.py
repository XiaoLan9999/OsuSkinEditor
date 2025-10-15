# -*- coding: utf-8 -*-
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QFont
from PySide6.QtCore import Qt, QRectF

from pathlib import Path

# Optional: use SkinIni to read mania layout if available
try:
    from core.skin_ini import SkinIni, parse_list_csv
except Exception:
    SkinIni = None
    parse_list_csv = lambda s: []

class ManiaPreview(QWidget):
    """Simple mania lanes preview that respects current keys (K).

    API expected by MainWindow:
      - set_skin(skin)
      - set_keys(k)
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(800, 520)
        self.skin = None
        self.skin_ini = None
        self.keys = 7
        self.layout = {
            "ColumnStart": None,
            "ColumnRight": None,
            "ColumnWidth": [],
            "ColumnSpacing": [],
            "ColumnLineWidth": [],
            "HitPosition": None,
            "WidthForNoteHeightScale": None,
        }

    # ------- public API -------
    def set_skin(self, skin):
        self.skin = skin
        self._load_skin_ini()
        self._load_layout_for_keys(self.keys)
        self.update()

    def set_keys(self, k: int):
        """Called by MainWindow when ManiaIniDock emits keys_changed."""
        try:
            self.keys = max(1, int(k))
        except Exception:
            self.keys = 7
        self._load_layout_for_keys(self.keys)
        self.update()

    # ------- internals -------
    def _skin_root(self):
        try:
            return Path(self.skin.root) if self.skin else None
        except Exception:
            return None

    def _load_skin_ini(self):
        root = self._skin_root()
        self.skin_ini = None
        if root and SkinIni:
            ini = root / "skin.ini"
            if ini.exists():
                try:
                    self.skin_ini = SkinIni.read(ini)
                except Exception:
                    self.skin_ini = None

    def _load_layout_for_keys(self, k: int):
        """Read layout from skin.ini if present; otherwise fallback to equal lanes."""
        lay = {**self.layout}
        if self.skin_ini and hasattr(self.skin_ini, "mania_get"):
            try:
                d = self.skin_ini.mania_get(int(k))
                lay["ColumnStart"] = self._int_or_none(d.get("ColumnStart"))
                lay["ColumnRight"] = self._int_or_none(d.get("ColumnRight"))
                lay["HitPosition"] = self._int_or_none(d.get("HitPosition"))
                lay["WidthForNoteHeightScale"] = self._int_or_none(d.get("WidthForNoteHeightScale"))
                lay["ColumnWidth"] = self._list_of_ints(d.get("ColumnWidth"), fallback_len=int(k), fill=40)
                lay["ColumnSpacing"] = self._list_of_ints(d.get("ColumnSpacing"), fallback_len=max(int(k)-1,0), fill=6)
                lay["ColumnLineWidth"] = self._list_of_ints(d.get("ColumnLineWidth"), fallback_len=int(k)+1, fill=1)
            except Exception:
                # Fallback
                lay["ColumnWidth"] = [40]*int(k)
                lay["ColumnSpacing"] = [6]*(max(int(k)-1,0))
                lay["ColumnLineWidth"] = [1]*(int(k)+1)
                lay["ColumnStart"] = 100; lay["ColumnRight"] = 700
                lay["HitPosition"] = 420
        else:
            lay["ColumnWidth"] = [40]*int(k)
            lay["ColumnSpacing"] = [6]*(max(int(k)-1,0))
            lay["ColumnLineWidth"] = [1]*(int(k)+1)
            lay["ColumnStart"] = 100; lay["ColumnRight"] = 700
            lay["HitPosition"] = 420

        self.layout = lay

    def _int_or_none(self, v):
        try:
            return int(v)
        except Exception:
            return None

    def _list_of_ints(self, v, fallback_len=0, fill=0):
        if v is None: return [fill]*fallback_len
        if isinstance(v, list):
            try: return [int(x) for x in v]
            except Exception: return [fill]*fallback_len
        try:
            s = str(v).strip()
            parts = [p.strip() for p in s.split(",") if p.strip()!=""]
            return [int(p) for p in parts]
        except Exception:
            return [fill]*fallback_len

    # ------- painting -------
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        # bg
        p.fillRect(self.rect(), Qt.black)

        # logical playfield
        margin = 20
        field = self.rect().adjusted(margin, margin, -margin, -margin)

        # compute x positions
        k = max(1, int(self.keys))
        widths = (self.layout.get("ColumnWidth") or [40]*k)[:k]
        while len(widths) < k: widths.append(widths[-1] if widths else 40)
        spacing = (self.layout.get("ColumnSpacing") or [6]*(k-1))[:max(k-1,0)]
        while len(spacing) < max(k-1,0): spacing.append(spacing[-1] if spacing else 6)

        total_w = sum(widths) + sum(spacing)
        # determine left x
        left_x = self.layout.get("ColumnStart")
        right_x = self.layout.get("ColumnRight")
        if left_x is None and right_x is None:
            left = field.left() + (field.width()-total_w)/2.0
        elif left_x is not None and right_x is not None:
            # scale into field proportionally
            left = field.left() + float(left_x) / max(1.0, (right_x - left_x)) * (field.width()-total_w)
        elif left_x is not None:
            left = field.left() + left_x
        else:
            left = field.right() - total_w if right_x is not None else field.left() + (field.width()-total_w)/2.0

        # vertical separators
        x = left
        pen_line = QPen(QColor(120,120,120), 1)
        p.setPen(pen_line)
        p.setBrush(Qt.NoBrush)
        for i in range(k+1):
            p.drawLine(int(round(x)), field.top(), int(round(x)), field.bottom())
            if i < k:
                x += widths[i]
                if i < k-1:
                    x += spacing[i]

        # hit position
        hp = self.layout.get("HitPosition") or int(field.center().y())
        pen_hp = QPen(QColor(200,200,200), 2, Qt.SolidLine)
        p.setPen(pen_hp)
        p.drawLine(field.left(), hp, field.right(), hp)

        # draw some sample notes (five rows)
        note_r = min(max(min(widths)/2 - 4, 6), 28)
        brush_note = QBrush(QColor(160,160,160))
        pen_note = QPen(QColor(230,230,230), 2)
        p.setBrush(brush_note); p.setPen(pen_note)

        # recompute lane centers for drawing
        centers = []
        x = left
        for i in range(k):
            w = widths[i]
            centers.append(x + w/2.0)
            x += w
            if i < k-1: x += spacing[i]

        rows = 5
        row_gap = (field.height()-40) / (rows+1)
        for r in range(rows):
            cy = field.top() + 20 + (r+1)*row_gap
            for cx in centers:
                rect = QRectF(cx - note_r, cy - note_r, note_r*2, note_r*2)
                p.drawEllipse(rect)

        p.end()
