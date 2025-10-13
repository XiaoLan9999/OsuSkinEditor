# -*- coding: utf-8 -*-
# build: mania-ini-dock v6 — keep current K + dirty guard + snapshots + RGBA colors + history ops
from pathlib import Path
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QDockWidget, QLabel, QSpinBox, QLineEdit, QPushButton, QFormLayout,
    QHBoxLayout, QVBoxLayout, QMessageBox, QComboBox, QCheckBox, QListWidget,
    QFileDialog, QColorDialog
)
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence
from PySide6.QtCore import Qt, QUrl, Signal

from core.skin_ini import SkinIni, parse_list_csv
from core import i18n

# ---------------- color helpers ----------------
def _parse_rgba_text(s: str, default_alpha: int = 255):
    """Accept formats: 'R,G,B' | 'R,G,B,A' | '#RRGGBB' | '#AARRGGBB'.
    Return tuple (R,G,B,A) with ints 0~255. On failure, use 0,0,0,default_alpha.
    """
    try:
        s = (s or "").strip()
        if not s:
            return (0, 0, 0, default_alpha)
        if s.startswith("#"):
            h = s.lstrip("#")
            if len(h) == 6:  # RRGGBB
                r = int(h[0:2], 16); g = int(h[2:4], 16); b = int(h[4:6], 16); a = default_alpha
            elif len(h) == 8:  # AARRGGBB
                a = int(h[0:2], 16); r = int(h[2:4], 16); g = int(h[4:6], 16); b = int(h[6:8], 16)
            else:
                return (0, 0, 0, default_alpha)
            return (r, g, b, a)
        # R,G,B or R,G,B,A
        parts = [p.strip() for p in s.replace(" ", "").split(",") if p.strip() != ""]
        if len(parts) == 3:
            r, g, b = map(int, parts); a = default_alpha
            return (r, g, b, a)
        if len(parts) == 4:
            r, g, b, a = map(int, parts)
            return (r, g, b, a)
    except Exception:
        pass
    return (0, 0, 0, default_alpha)

def _rgba_text(rgba):
    r, g, b, a = rgba
    return f"{int(r)},{int(g)},{int(b)},{int(a)}"

def _qcolor_from_text(s: str):
    r, g, b, a = _parse_rgba_text(s)
    return QColor(r, g, b, a)

# ---------------- util helpers ----------------
def _bool_from_any(v):
    if isinstance(v, bool): return v
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "on")

def _to_int(v, default=None):
    try: return int(v)
    except Exception: return default

class ManiaIniDock(QDockWidget):
    """Mania INI editor with full settings, snapshots, hotkeys, K persistence, and dirty guard.
       This build writes Colour* as 'R,G,B,A' (RGBA numbers) to ensure your reader can parse it.
    """
    keys_changed = Signal(int)  # emitted when keys (K) changes

    def __init__(self, parent=None):
        super().__init__("Mania INI", parent)
        self.setObjectName("ManiaIniDock")
        self._skin_root: Path | None = None
        self._skin_ini: SkinIni | None = None

        # track current K and dirty state
        self._current_view_k: int | None = None
        self._dirty: bool = False
        self._blocking_key_change: bool = False  # prevent recursion when programmatically selecting

        cw = QWidget(self); self.setWidget(cw)
        root_layout = QVBoxLayout(cw)
        form = QFormLayout()
        root_layout.addLayout(form)

        # ----- Keys row (select existing / new) -----
        row_keys = QHBoxLayout()
        self.cmb_keys = QComboBox(cw)     # existing keys from skin.ini
        self.spn_keys = QSpinBox(cw); self.spn_keys.setRange(1, 18)  # new key count
        self.lbl_keys = QLabel(); self.lbl_or_create = QLabel()
        row_keys.addWidget(self.cmb_keys)
        row_keys.addWidget(self.lbl_or_create)
        row_keys.addWidget(self.spn_keys)
        form.addRow(self.lbl_keys, row_keys)

        # ----- Basic numeric/bool controls -----
        def spn(minv, maxv, step=1, width=90):
            s = QSpinBox(cw); s.setRange(minv, maxv); s.setSingleStep(step); s.setMaximumWidth(width); return s

        self.chk_keys_under = QCheckBox(cw)
        self.spn_hit_pos = spn(-4096, 4096)
        self.spn_barline_h = spn(0, 64)
        self.spn_score_pos = spn(-4096, 4096)
        self.spn_combo_pos = spn(-4096, 4096)
        self.spn_col_start = spn(-4096, 4096)
        self.spn_light_pos = spn(-4096, 4096)
        self.spn_light_fps = spn(1, 240)
        self.chk_judge_line = QCheckBox(cw)
        self.le_stage_hint = QLineEdit(cw)
        self.le_warning_arrow = QLineEdit(cw)
        self.chk_upside_down = QCheckBox(cw)
        self.le_colour_hold = QLineEdit(cw)
        self.le_colour_barline = QLineEdit(cw)
        self.spn_light_nw = spn(0, 256)
        self.spn_light_lw = spn(0, 256)

        # stage/warning browse
        def add_browse(le: QLineEdit):
            btn = QPushButton(i18n.t("mania.browse", "浏览…"), cw)
            def choose():
                start = str(self._skin_root) if self._skin_root else ""
                path, _ = QFileDialog.getOpenFileName(self, i18n.t("mania.pick_file", "选择图像"), start, "Images (*.png *.jpg *.jpeg *.bmp *.gif);;All files (*.*)")
                if path:
                    try:
                        p = Path(path)
                        if self._skin_root and self._skin_root in p.parents:
                            le.setText(str(p.relative_to(self._skin_root).as_posix()))
                        else:
                            le.setText(p.name)
                    except Exception:
                        le.setText(Path(path).name)
            btn.clicked.connect(choose)
            return btn

        row_stage = QHBoxLayout(); row_stage.addWidget(self.le_stage_hint); row_stage.addWidget(add_browse(self.le_stage_hint))
        row_warn  = QHBoxLayout(); row_warn.addWidget(self.le_warning_arrow); row_warn.addWidget(add_browse(self.le_warning_arrow))

        # colors with pickers (store as 'R,G,B,A')
        def add_color_row(le: QLineEdit):
            btn = QPushButton(i18n.t("mania.pick", "取色…"), cw)
            def pick():
                # keep previous alpha if any, else default 255
                _, _, _, prev_a = _parse_rgba_text(le.text(), default_alpha=255)
                c = QColorDialog.getColor(_qcolor_from_text(le.text()), self, i18n.t("mania.pick", "取色…"))
                if c.isValid():
                    rgba = (c.red(), c.green(), c.blue(), prev_a if prev_a is not None else 255)
                    le.setText(_rgba_text(rgba))
            btn.clicked.connect(pick)
            row = QHBoxLayout(); row.addWidget(le); row.addWidget(btn)
            return row

        # ----- Column arrays & helpers -----
        self.le_col_width = QLineEdit(cw)
        self.le_col_spacing = QLineEdit(cw)
        self.le_col_line = QLineEdit(cw)
        # helpers: set-all for widths and line widths
        self.spn_all_width = spn(0, 512); self.btn_apply_all_width = QPushButton(i18n.t("mania.apply_all_widths", "列宽一键填充"), cw)
        self.spn_all_line  = spn(0, 128); self.btn_apply_all_line  = QPushButton(i18n.t("mania.apply_all_lines", "间隙线宽一键填充"), cw)

        row_w = QHBoxLayout(); row_w.addWidget(self.le_col_width); row_w.addWidget(self.spn_all_width); row_w.addWidget(self.btn_apply_all_width)
        row_l = QHBoxLayout(); row_l.addWidget(self.le_col_line);  row_l.addWidget(self.spn_all_line);  row_l.addWidget(self.btn_apply_all_line)

        # ----- Build form -----
        form.addRow(QLabel(i18n.t("mania.col_width", "ColumnWidth（轨道宽度）")), row_w)
        form.addRow(QLabel(i18n.t("mania.col_spacing", "ColumnSpacing（轨道间距）")), self.le_col_spacing)
        form.addRow(QLabel(i18n.t("mania.line_width", "ColumnLineWidth（轨道间隙线宽）")), row_l)

        form.addRow(QLabel(i18n.t("mania.keys_under", "KeysUnderNotes（按键画在音符下）")), self.chk_keys_under)
        form.addRow(QLabel(i18n.t("mania.hit_pos", "HitPosition（打击线Y）")), self.spn_hit_pos)
        form.addRow(QLabel(i18n.t("mania.barline_h", "BarlineHeight（小节线粗细）")), self.spn_barline_h)
        form.addRow(QLabel(i18n.t("mania.score_pos", "ScorePosition（判定值Y）")), self.spn_score_pos)
        form.addRow(QLabel(i18n.t("mania.combo_pos", "ComboPosition（连击数Y）")), self.spn_combo_pos)
        form.addRow(QLabel(i18n.t("mania.col_start", "ColumnStart（左侧X）")), self.spn_col_start)
        form.addRow(QLabel(i18n.t("mania.light_pos", "LightPosition（灯效Y）")), self.spn_light_pos)
        form.addRow(QLabel(i18n.t("mania.light_fps", "LightFramePerSecond（灯效FPS）")), self.spn_light_fps)
        form.addRow(QLabel(i18n.t("mania.judge_line", "JudgementLine（额外判定细线）")), self.chk_judge_line)
        form.addRow(QLabel(i18n.t("mania.stage_hint", "StageHint（判定线图）")), row_stage)
        form.addRow(QLabel(i18n.t("mania.warning_arrow", "WarningArrow（下落箭头）")), row_warn)
        form.addRow(QLabel(i18n.t("mania.upside_down", "UpsideDown（上下颠倒）")), self.chk_upside_down)
        form.addRow(QLabel(i18n.t("mania.colour_hold", "ColourHold（长条连击颜色）")), add_color_row(self.le_colour_hold))
        form.addRow(QLabel(i18n.t("mania.colour_barline", "ColourBarline（小节线颜色）")), add_color_row(self.le_colour_barline))
        form.addRow(QLabel(i18n.t("mania.light_nw", "LightingNWidth（单击灯效宽度）")), self.spn_light_nw)
        form.addRow(QLabel(i18n.t("mania.light_lw", "LightingLWidth（长条灯效宽度）")), self.spn_light_lw)

        # ----- Buttons (I/O) -----
        io_row = QHBoxLayout()
        self.btn_reload = QPushButton(i18n.t("mania.btn_reload", "从 skin.ini 读取"), cw)
        self.btn_save   = QPushButton(i18n.t("mania.btn_save",   "保存到 skin.ini"), cw)
        self.btn_restore= QPushButton(i18n.t("mania.btn_restore","恢复 .bak 备份"), cw)
        io_row.addWidget(self.btn_reload); io_row.addWidget(self.btn_save); io_row.addWidget(self.btn_restore)
        root_layout.addLayout(io_row)

        # shortcuts
        self.btn_save.setShortcut(QKeySequence("Ctrl+S"))
        self.btn_reload.setShortcut(QKeySequence("F5"))

        # ----- Snapshot section -----
        snap_row = QHBoxLayout()
        self.btn_save_and_snap = QPushButton(i18n.t("mania.btn_save_and_snap", "保存并新建快照 (Ctrl+Shift+S)"), cw)
        self.btn_quick_snap   = QPushButton(i18n.t("mania.btn_quick_snap", "仅新建快照（不写入 ini）"), cw)
        snap_row.addWidget(self.btn_save_and_snap); snap_row.addWidget(self.btn_quick_snap)
        root_layout.addLayout(snap_row)
        self.btn_save_and_snap.setShortcut(QKeySequence("Ctrl+Shift+S"))

        self.lbl_modified = QLabel(i18n.t("mania.last_modified", "最后修改时间：—"), cw)
        root_layout.addWidget(self.lbl_modified)
        self.history = QListWidget(cw); self.history.setMaximumHeight(140)
        root_layout.addWidget(self.history)

        hist_btns = QHBoxLayout()
        self.btn_hist_refresh   = QPushButton(i18n.t("mania.hist_refresh", "刷新列表"), cw)
        self.btn_hist_delete    = QPushButton(i18n.t("mania.hist_delete", "删除所选快照"), cw)
        self.btn_hist_restore   = QPushButton(i18n.t("mania.hist_restore", "从所选快照恢复"), cw)
        self.btn_hist_overwrite = QPushButton(i18n.t("mania.hist_overwrite", "用当前 ini 覆盖所选快照"), cw)
        self.btn_hist_open      = QPushButton(i18n.t("mania.hist_open", "打开快照文件夹"), cw)
        for b in (self.btn_hist_refresh, self.btn_hist_delete, self.btn_hist_restore, self.btn_hist_overwrite, self.btn_hist_open):
            hist_btns.addWidget(b)
        root_layout.addLayout(hist_btns)

        # ----- signals -----
        self.btn_reload.clicked.connect(self._on_reload_clicked)
        self.btn_save.clicked.connect(self._on_save_clicked)
        self.btn_restore.clicked.connect(self._on_restore_clicked)

        self.btn_save_and_snap.clicked.connect(self._on_save_and_snapshot)
        self.btn_quick_snap.clicked.connect(self._on_snapshot_clicked)

        self.cmb_keys.currentIndexChanged.connect(self._on_select_existing_keys)
        self.spn_keys.valueChanged.connect(self._on_change_new_keys)

        self.btn_hist_refresh.clicked.connect(self._refresh_history_list)
        self.btn_hist_delete.clicked.connect(self._on_hist_delete)
        self.btn_hist_restore.clicked.connect(self._on_hist_restore)
        self.btn_hist_overwrite.clicked.connect(self._on_hist_overwrite)
        self.btn_hist_open.clicked.connect(self._on_hist_open)

        self.btn_apply_all_width.clicked.connect(self._on_apply_all_width)
        self.btn_apply_all_line.clicked.connect(self._on_apply_all_line)

        # connect dirty markers
        self._connect_dirty_signals()

        self._set_fields_enabled(False)
        self.retranslate()

    # ---------- i18n ----------
    def retranslate(self):
        self.setWindowTitle(i18n.t("mania.dock_title", "Mania INI"))
        self.lbl_keys.setText(i18n.t("mania.keys", "键位（已有 / 新建）"))
        self.lbl_or_create.setText(i18n.t("mania.or_create", "→ 或新建："))

    # ---------- dirty tracking ----------
    def _connect_dirty_signals(self):
        # spinboxes (except spn_keys)
        for s in [self.spn_hit_pos, self.spn_barline_h, self.spn_score_pos, self.spn_combo_pos,
                  self.spn_col_start, self.spn_light_pos, self.spn_light_fps,
                  self.spn_light_nw, self.spn_light_lw]:
            s.valueChanged.connect(self._mark_dirty)
        # checkboxes
        for c in [self.chk_keys_under, self.chk_judge_line, self.chk_upside_down]:
            c.toggled.connect(self._mark_dirty)
        # lineedits
        for le in [self.le_stage_hint, self.le_warning_arrow, self.le_colour_hold, self.le_colour_barline,
                   self.le_col_width, self.le_col_spacing, self.le_col_line]:
            le.textChanged.connect(self._mark_dirty)
        # apply buttons implicitly change text -> also mark dirty
        self.btn_apply_all_width.clicked.connect(self._mark_dirty)
        self.btn_apply_all_line.clicked.connect(self._mark_dirty)

    def _mark_dirty(self, *args):
        self._dirty = True

    def _clear_dirty(self):
        self._dirty = False

    def _confirm_discard_if_dirty(self) -> bool:
        if not self._dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle(i18n.t("mania.unsaved_title", "未保存"))
        box.setText(i18n.t("mania.unsaved_text", "未保存，现在切换 Keys 将会丢失设置进度。"))
        btn_save = box.addButton(i18n.t("mania.unsaved_save", "保存"), QMessageBox.AcceptRole)
        btn_discard = box.addButton(i18n.t("mania.unsaved_discard", "放弃更改"), QMessageBox.DestructiveRole)
        btn_cancel = box.addButton(i18n.t("mania.unsaved_cancel", "取消"), QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_save:
            self._on_save_clicked()
            return True
        elif clicked is btn_discard:
            self._clear_dirty()
            return True
        else:
            return False

    def _reselect_current_k_in_widgets(self):
        if self._current_view_k is None:
            return
        self._blocking_key_change = True
        try:
            # set combobox index
            target_idx = -1
            for i in range(self.cmb_keys.count()):
                if int(self.cmb_keys.itemData(i)) == int(self._current_view_k):
                    target_idx = i; break
            self.cmb_keys.setCurrentIndex(target_idx)
            # set spin value
            self.spn_keys.setValue(int(self._current_view_k))
        finally:
            self._blocking_key_change = False

    # ---------- public ----------
    def set_skin_root(self, root: Path | str | None):
        if isinstance(root, str): root = Path(root)
        self._skin_root = root; self._skin_ini = None
        ini_path = self._resolve_ini_path()
        if not ini_path: self._set_fields_enabled(False); return
        try:
            self._skin_ini = SkinIni.read(ini_path)
        except Exception as e:
            QMessageBox.warning(self, "skin.ini", f"Failed to read skin.ini: {e}")
            self._set_fields_enabled(False); return
        self._refresh_existing_keys()
        self._set_fields_enabled(True)
        self._load_values_for_current_keys()
        self._refresh_modified_time(); self._refresh_history_list()

    # ---------- internals ----------
    def _resolve_ini_path(self) -> Path | None:
        if not self._skin_root: return None
        p = Path(self._skin_root) / "skin.ini"
        return p if p.exists() else None

    def _history_dir(self) -> Path | None:
        if not self._skin_root: return None
        d = Path(self._skin_root) / ".skin_ini_history"
        d.mkdir(exist_ok=True)
        return d

    def _refresh_existing_keys(self):
        prev = self._current_view_k
        self.cmb_keys.blockSignals(True); self.cmb_keys.clear()
        keys = self._skin_ini.available_mania_keys() if self._skin_ini else []
        for k in keys: self.cmb_keys.addItem(f"{k}K", k)
        # try to keep previous selection
        if prev and prev in keys:
            idx = keys.index(prev)
        else:
            idx = 0 if keys else -1
            prev = keys[0] if keys else (self.spn_keys.value() or 4)
        self.cmb_keys.blockSignals(False)

        self._blocking_key_change = True
        try:
            self.cmb_keys.setCurrentIndex(idx)
            self.spn_keys.setValue(int(prev))
        finally:
            self._blocking_key_change = False
        self._current_view_k = int(prev)

    def _current_selected_keys(self) -> int:
        idx = self.cmb_keys.currentIndex()
        if idx >= 0: return int(self.cmb_keys.itemData(idx))
        return int(self.spn_keys.value())

    def _set_fields_enabled(self, on: bool):
        for w in [self.cmb_keys, self.spn_keys, self.le_col_width, self.le_col_spacing, self.le_col_line,
                  self.chk_keys_under, self.spn_hit_pos, self.spn_barline_h, self.spn_score_pos, self.spn_combo_pos,
                  self.spn_col_start, self.spn_light_pos, self.spn_light_fps, self.chk_judge_line, self.le_stage_hint,
                  self.le_warning_arrow, self.chk_upside_down, self.le_colour_hold, self.le_colour_barline,
                  self.spn_light_nw, self.spn_light_lw, self.btn_reload, self.btn_save, self.btn_restore,
                  self.spn_all_width, self.btn_apply_all_width, self.spn_all_line, self.btn_apply_all_line,
                  self.history, self.btn_hist_restore, self.btn_hist_overwrite, self.btn_hist_open,
                  self.btn_save_and_snap, self.btn_quick_snap, self.btn_hist_refresh, self.btn_hist_delete]:
            w.setEnabled(on)

    def _load_values_for_current_keys(self):
        if not self._skin_ini: return
        k = self._current_selected_keys(); d = self._skin_ini.mania_get(k)

        # lists
        self.le_col_width.setText(d.get("ColumnWidth", ""))
        self.le_col_spacing.setText(d.get("ColumnSpacing", ""))
        self.le_col_line.setText(d.get("ColumnLineWidth", ""))

        # bools / ints / strings
        self.chk_keys_under.setChecked(_bool_from_any(d.get("KeysUnderNotes", False)))
        self.spn_hit_pos.setValue(_to_int(d.get("HitPosition"), 420) or 420)
        self.spn_barline_h.setValue(_to_int(d.get("BarlineHeight"), 1) or 1)
        self.spn_score_pos.setValue(_to_int(d.get("ScorePosition"), 0) or 0)
        self.spn_combo_pos.setValue(_to_int(d.get("ComboPosition"), 0) or 0)
        self.spn_col_start.setValue(_to_int(d.get("ColumnStart"), 0) or 0)
        self.spn_light_pos.setValue(_to_int(d.get("LightPosition"), 0) or 0)
        self.spn_light_fps.setValue(_to_int(d.get("LightFramePerSecond"), 60) or 60)
        self.chk_judge_line.setChecked(_bool_from_any(d.get("JudgementLine", False)))
        self.le_stage_hint.setText(d.get("StageHint", ""))
        self.le_warning_arrow.setText(d.get("WarningArrow", ""))
        self.chk_upside_down.setChecked(_bool_from_any(d.get("UpsideDown", False)))
        # Colors: normalize to RGBA text
        ch = d.get("ColourHold", "")
        cb = d.get("ColourBarline", "")
        self.le_colour_hold.setText(_rgba_text(_parse_rgba_text(ch, 255)))
        self.le_colour_barline.setText(_rgba_text(_parse_rgba_text(cb, 255)))
        self.spn_light_nw.setValue(_to_int(d.get("LightingNWidth"), 0) or 0)
        self.spn_light_lw.setValue(_to_int(d.get("LightingLWidth"), 0) or 0)

        # record current K and clear dirty
        self._current_view_k = int(k)
        self._clear_dirty()

        # notify preview about current K
        try:
            self.keys_changed.emit(int(k))
        except Exception:
            pass

    def _collect_updates(self, k: int) -> dict:
        upd = {}
        # lists
        if self.le_col_width.text().strip():
            upd["ColumnWidth"] = parse_list_csv(self.le_col_width.text())
        if self.le_col_spacing.text().strip():
            upd["ColumnSpacing"] = parse_list_csv(self.le_col_spacing.text())
        if self.le_col_line.text().strip():
            upd["ColumnLineWidth"] = parse_list_csv(self.le_col_line.text())

        # bools / ints / strings
        upd["KeysUnderNotes"] = 1 if self.chk_keys_under.isChecked() else 0
        upd["HitPosition"] = int(self.spn_hit_pos.value())
        upd["BarlineHeight"] = int(self.spn_barline_h.value())
        upd["ScorePosition"] = int(self.spn_score_pos.value())
        upd["ComboPosition"] = int(self.spn_combo_pos.value())
        upd["ColumnStart"] = int(self.spn_col_start.value())
        upd["LightPosition"] = int(self.spn_light_pos.value())
        upd["LightFramePerSecond"] = int(self.spn_light_fps.value())
        upd["JudgementLine"] = 1 if self.chk_judge_line.isChecked() else 0
        upd["StageHint"] = self.le_stage_hint.text().strip()
        upd["WarningArrow"] = self.le_warning_arrow.text().strip()
        upd["UpsideDown"] = 1 if self.chk_upside_down.isChecked() else 0
        # Colors: always write 'R,G,B,A' strings
        upd["ColourHold"] = _rgba_text(_parse_rgba_text(self.le_colour_hold.text(), 255))
        upd["ColourBarline"] = _rgba_text(_parse_rgba_text(self.le_colour_barline.text(), 255))
        upd["LightingNWidth"] = int(self.spn_light_nw.value())
        upd["LightingLWidth"] = int(self.spn_light_lw.value())
        return upd

    # ----- history helpers -----
    def _refresh_modified_time(self):
        ini = self._resolve_ini_path()
        if not ini: self.lbl_modified.setText(i18n.t("mania.last_modified", "最后修改时间：—")); return
        try:
            ts = datetime.fromtimestamp(ini.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            self.lbl_modified.setText(i18n.t("mania.last_modified", "最后修改时间：{ts}").format(ts=ts))
        except Exception:
            self.lbl_modified.setText(i18n.t("mania.last_modified", "最后修改时间：—"))

    def _refresh_history_list(self):
        d = self._history_dir()
        self.history.clear()
        if not d: return
        items = sorted(d.glob("skin.ini.*.bak"), reverse=True)
        for p in items:
            self.history.addItem(p.name)

    def _archive_snapshot(self):
        ini = self._resolve_ini_path()
        d = self._history_dir()
        if not ini or not d: return
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = d / f"skin.ini.{ts}.bak"
        try:
            dst.write_text(ini.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass

    # ----- slots -----
    def _on_apply_all_width(self):
        k = self._current_selected_keys()
        v = int(self.spn_all_width.value())
        self.le_col_width.setText(",".join(str(v) for _ in range(max(1,int(k)))))

    def _on_apply_all_line(self):
        k = self._current_selected_keys()
        v = int(self.spn_all_line.value())
        self.le_col_line.setText(",".join(str(v) for _ in range(int(k)+1)))

    def _on_reload_clicked(self):
        if not self._skin_ini: return
        try:
            self._skin_ini = SkinIni.read(self._resolve_ini_path())
            # keep current K
            curk = self._current_view_k
            self._refresh_existing_keys()
            if curk is not None:
                self._current_view_k = curk
                self._reselect_current_k_in_widgets()
            self._load_values_for_current_keys()
            self._refresh_modified_time(); self._refresh_history_list()
            QMessageBox.information(self, "Mania INI", "Reloaded from disk.")
        except Exception as e:
            QMessageBox.warning(self, "Mania INI", f"Reload failed: {e}")

    def _on_save_and_snapshot(self):
        self._on_save_clicked()

    def _on_snapshot_clicked(self):
        self._archive_snapshot()
        self._refresh_history_list()
        QMessageBox.information(self, "Snapshot", "已创建快照（未写入 ini）。")

    def _on_save_clicked(self):
        if not self._skin_ini: return
        # always save for the K that is currently being viewed
        curk = int(self._current_view_k if self._current_view_k is not None else self._current_selected_keys())
        updates = self._collect_updates(curk)

        try:
            self._skin_ini.mania_set_values(curk, updates)
            self._skin_ini.save(create_backup=True)
            self._archive_snapshot()
        except Exception as e:
            QMessageBox.critical(self, "Mania INI", f"Save failed: {e}"); return

        # stay on current K after save
        self._clear_dirty()
        self._refresh_modified_time(); self._refresh_history_list()
        self._refresh_existing_keys()            # may repopulate list, e.g. new K added
        self._current_view_k = curk
        self._reselect_current_k_in_widgets()
        # reload fields from the saved ini to reflect normalized formats
        self._load_values_for_current_keys()

        # notify preview
        try:
            self.keys_changed.emit(int(curk))
        except Exception:
            pass

        QMessageBox.information(self, "Mania INI", f"Saved for {curk}K，并已创建快照。")

    def _on_restore_clicked(self):
        ini = self._resolve_ini_path()
        if not ini: return
        bak = ini.with_suffix(ini.suffix + ".bak")
        if not bak.exists():
            QMessageBox.information(self, "Restore", "No backup (.bak) found yet."); return
        try:
            ini.write_text(bak.read_text(encoding="utf-8"), encoding="utf-8")
            self._skin_ini = SkinIni.read(ini)
            # keep current K
            curk = self._current_view_k
            self._refresh_existing_keys()
            if curk is not None:
                self._current_view_k = curk
                self._reselect_current_k_in_widgets()
            self._load_values_for_current_keys()
            self._refresh_modified_time(); self._refresh_history_list()
            QMessageBox.information(self, "Restore", "Restored from .bak.")
        except Exception as e:
            QMessageBox.warning(self, "Restore", f"Restore failed: {e}")

    def _on_hist_delete(self):
        d = self._history_dir()
        if not d: return
        item = self.history.currentItem()
        if not item:
            QMessageBox.information(self, "Snapshot", i18n.t("mania.pick_snapshot", "请先选择要删除的快照。"))
            return
        target = d / item.text()
        if not target.exists():
            QMessageBox.warning(self, "Snapshot", i18n.t("mania.not_found", "未找到该快照文件。"))
            return
        r = QMessageBox.question(self, "Snapshot",
                                 i18n.t("mania.confirm_delete", f"确定要删除快照：\n{target.name} ？"),
                                 QMessageBox.Yes | QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        try:
            target.unlink()
            self._refresh_history_list()
            QMessageBox.information(self, "Snapshot", i18n.t("mania.deleted", "已删除。"))
        except Exception as e:
            QMessageBox.warning(self, "Snapshot", i18n.t("mania.delete_fail", f"删除失败：{e}"))

    def _on_select_existing_keys(self, idx: int):
        if self._blocking_key_change: return
        # target k from combo
        if idx < 0: return
        target_k = int(self.cmb_keys.itemData(idx))
        if target_k == self._current_view_k:
            return
        if not self._confirm_discard_if_dirty():
            # revert selection
            self._reselect_current_k_in_widgets()
            return
        # proceed switch
        self._blocking_key_change = True
        try:
            self.spn_keys.setValue(target_k)
        finally:
            self._blocking_key_change = False
        self._load_values_for_current_keys()

    def _on_change_new_keys(self, val: int):
        if self._blocking_key_change: return
        target_k = int(val)
        if target_k == self._current_view_k:
            return
        if not self._confirm_discard_if_dirty():
            # revert spinner
            self._blocking_key_change = True
            try:
                if self._current_view_k is not None:
                    self.spn_keys.setValue(int(self._current_view_k))
            finally:
                self._blocking_key_change = False
            return
        # align combo to this K if exists
        keys = self._skin_ini.available_mania_keys() if self._skin_ini else []
        self._blocking_key_change = True
        try:
            if target_k in keys:
                self.cmb_keys.setCurrentIndex(keys.index(target_k))
            else:
                self.cmb_keys.setCurrentIndex(-1)  # new K
        finally:
            self._blocking_key_change = False
        self._load_values_for_current_keys()

    def _on_hist_restore(self):
        d = self._history_dir()
        if not d: return
        item = self.history.currentItem()
        if not item: return
        path = d / item.text()
        ini = self._resolve_ini_path()
        if not ini or not path.exists(): return
        try:
            ini.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            self._skin_ini = SkinIni.read(ini)
            # keep current K
            curk = self._current_view_k
            self._refresh_existing_keys()
            if curk is not None:
                self._current_view_k = curk
                self._reselect_current_k_in_widgets()
            self._load_values_for_current_keys()
            self._refresh_modified_time()
            QMessageBox.information(self, "Restore", f"Restored: {path.name}")
        except Exception as e:
            QMessageBox.warning(self, "Restore", f"Failed: {e}")

    def _on_hist_overwrite(self):
        d = self._history_dir()
        if not d: return
        item = self.history.currentItem()
        if not item:
            QMessageBox.information(self, "Overwrite", "请先在快照列表选择一项。")
            return
        target = d / item.text()
        ini = self._resolve_ini_path()
        if not ini or not target.exists():
            QMessageBox.warning(self, "Overwrite", "未找到 skin.ini 或选中快照。"); return
        r = QMessageBox.question(self, "Overwrite", f"确定要用当前 skin.ini 覆盖：\n{target.name} ？", QMessageBox.Yes | QMessageBox.No)
        if r != QMessageBox.Yes: return
        try:
            target.write_text(ini.read_text(encoding="utf-8"), encoding="utf-8")
            QMessageBox.information(self, "Overwrite", "已覆盖选中的快照。")
        except Exception as e:
            QMessageBox.warning(self, "Overwrite", f"失败：{e}")

    def _on_hist_open(self):
        d = self._history_dir()
        if not d: return
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))
        except Exception:
            pass
