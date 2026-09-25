"""Live mania design controls with explicit export to a new skin folder."""
from pathlib import Path
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QDialog, QDialogButtonBox, QDockWidget,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core import i18n
from core.mania_designer import ManiaDesign, export_skin
from ui.widgets.wheel_guard import ClickWheelSpinBox


def _next_copy_name(parent: Path, source_name: str, keys: int) -> str:
    base = f"{source_name} - Designer {keys}K"
    candidate = base
    index = 2
    while (parent / candidate).exists():
        candidate = f"{base} ({index})"
        index += 1
    return candidate


class _ExportCopyDialog(QDialog):
    """Review a new folder destination without creating or replacing anything."""

    def __init__(self, source: Path, keys: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(i18n.t("designer.export_title", "保存皮肤副本"))
        self.setMinimumWidth(450)
        self.destination: Path | None = None
        layout = QVBoxLayout(self)
        note = QLabel(i18n.t("designer.export_note", "将整个皮肤复制到新文件夹，再写入本次设计"), self)
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        layout.addLayout(form)
        directory_row = QHBoxLayout()
        self.directory_edit = QLineEdit(str(source.parent), self)
        self.directory_edit.setReadOnly(True)
        browse = QPushButton(i18n.t("designer.browse", "浏览…"), self)
        browse.clicked.connect(self._browse)
        directory_row.addWidget(self.directory_edit, 1)
        directory_row.addWidget(browse)
        form.addRow(i18n.t("designer.parent_folder", "保存到"), directory_row)
        self.name_edit = QLineEdit(_next_copy_name(source.parent, source.name, keys), self)
        form.addRow(i18n.t("designer.copy_name", "副本名称"), self.name_edit)
        self.path_label = QLabel(self)
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path_label.setObjectName("Muted")
        layout.addWidget(self.path_label)
        self.error_label = QLabel(self)
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(self)
        self.save_button = buttons.addButton(i18n.t("designer.export", "保存皮肤副本"), QDialogButtonBox.AcceptRole)
        self.save_button.setObjectName("PrimaryButton")
        buttons.addButton(i18n.t("designer.cancel", "取消"), QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.name_edit.textChanged.connect(self._update_destination)
        self.directory_edit.textChanged.connect(self._update_destination)
        self._update_destination()

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(
            self, i18n.t("designer.parent_folder", "保存到"), self.directory_edit.text()
        )
        if folder:
            self.directory_edit.setText(folder)

    def _update_destination(self, *_):
        name = self.name_edit.text().strip()
        directory = Path(self.directory_edit.text())
        reserved = re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name, re.IGNORECASE)
        invalid = (
            not name or name in (".", "..") or name.endswith((".", " "))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or reserved
        )
        error = ""
        self.destination = None
        if invalid:
            error = i18n.t("designer.invalid_name", "请输入有效的新文件夹名称")
        elif not directory.is_dir():
            error = i18n.t("designer.invalid_parent", "请选择已有的保存文件夹")
        elif (directory / name).exists():
            error = i18n.t("designer.destination_exists", "该文件夹已存在，请换一个副本名称")
        else:
            self.destination = directory / name
        self.path_label.setText(str(directory / name) if name else str(directory))
        self.error_label.setText(error)
        self.save_button.setEnabled(self.destination is not None)


class ManiaDesignDock(QDockWidget):
    """Keep design edits virtual until the user exports a separate skin copy."""

    design_changed = Signal(object)
    exported = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ManiaDesignDock")
        self.setMinimumWidth(315)
        self._skin = None
        self._skin_root: Path | None = None
        self._keys = 4
        self._colour = "#000000"
        self._loading = False
        self._dirty = False
        self._saved_design = ManiaDesign()
        self._last_export: Path | None = None
        self.before_export = None
        self._preview_error = ""

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)
        self.scope_label = QLabel(content)
        self.scope_label.setObjectName("Badge")
        self.scope_label.setWordWrap(True)
        layout.addWidget(self.scope_label)
        self.note_label = QLabel(content)
        self.note_label.setObjectName("Muted")
        self.note_label.setWordWrap(True)
        layout.addWidget(self.note_label)

        self.controls = QWidget(content)
        controls_layout = QVBoxLayout(self.controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(14)
        self.mask_group = QGroupBox(self.controls)
        mask_layout = QFormLayout(self.mask_group)
        mask_layout.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.top_height = self._spin(0, 350, "DesignerTopMaskHeight")
        self.top_fade = self._spin(0, 120, "DesignerTopMaskFade")
        self.height_label = QLabel(self.mask_group)
        self.fade_label = QLabel(self.mask_group)
        self.colour_label = QLabel(self.mask_group)
        self.colour_button = QPushButton(self.mask_group)
        self.colour_button.setObjectName("DesignerMaskColour")
        self.colour_button.clicked.connect(self._pick_colour)
        mask_layout.addRow(self.height_label, self.top_height)
        mask_layout.addRow(self.fade_label, self.top_fade)
        mask_layout.addRow(self.colour_label, self.colour_button)
        controls_layout.addWidget(self.mask_group)

        self.bottom_group = QGroupBox(self.controls)
        bottom_layout = QVBoxLayout(self.bottom_group)
        row = QFormLayout()
        row.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.raise_label = QLabel(self.bottom_group)
        self.receptor_raise = self._spin(0, 180, "DesignerReceptorRaise")
        row.addRow(self.raise_label, self.receptor_raise)
        bottom_layout.addLayout(row)
        self.link_hit_position = QCheckBox(self.bottom_group)
        self.link_hit_position.setObjectName("DesignerLinkHitPosition")
        self.link_hit_position.setChecked(True)
        bottom_layout.addWidget(self.link_hit_position)
        self.bottom_hint = QLabel(self.bottom_group)
        self.bottom_hint.setWordWrap(True)
        self.bottom_hint.setObjectName("Muted")
        bottom_layout.addWidget(self.bottom_hint)
        controls_layout.addWidget(self.bottom_group)
        layout.addWidget(self.controls)

        self.error_label = QLabel(content)
        self.error_label.setWordWrap(True)
        self.error_label.setObjectName("DesignError")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.status_label = QLabel(content)
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status_label.setObjectName("Muted")
        layout.addWidget(self.status_label)
        self.reset_button = QPushButton(content)
        self.reset_button.setObjectName("DesignerReset")
        self.reset_button.clicked.connect(self.reset_preview)
        layout.addWidget(self.reset_button)
        self.export_button = QPushButton(content)
        self.export_button.setObjectName("PrimaryButton")
        self.export_button.clicked.connect(self._export_copy)
        layout.addWidget(self.export_button)
        layout.addStretch(1)
        scroll.setWidget(content)
        self.setWidget(scroll)
        for spin in (self.top_height, self.top_fade, self.receptor_raise):
            spin.valueChanged.connect(self._changed)
        self.link_hit_position.toggled.connect(self._changed)
        self.retranslate()
        self._update_enabled()

    def _spin(self, minimum: int, maximum: int, name: str) -> ClickWheelSpinBox:
        spin = ClickWheelSpinBox(self)
        spin.setObjectName(name)
        spin.setRange(minimum, maximum)
        spin.setMinimumWidth(90)
        spin.setMaximumWidth(125)
        return spin

    def options(self) -> ManiaDesign:
        return ManiaDesign(
            top_mask_height=self.top_height.value(),
            top_mask_fade=self.top_fade.value(),
            receptor_raise=self.receptor_raise.value(),
            link_hit_position=self.link_hit_position.isChecked(),
            mask_colour=self._colour,
        )

    def set_skin(self, skin, keys: int | None = None, *, force: bool = False) -> bool:
        root = Path(skin.root).resolve() if skin is not None else None
        next_keys = int(keys if keys is not None else getattr(skin, "mode_keys", 4))
        if not 1 <= next_keys <= 18:
            raise ValueError("Mania keys must be between 1 and 18")
        changed = root != self._skin_root or next_keys != self._keys
        if changed and not force and not self.confirm_discard_if_dirty():
            return False
        self._skin = skin
        self._skin_root = root
        self._keys = next_keys
        if changed or force:
            self._saved_design = ManiaDesign()
            self._last_export = None
            self._apply_options(ManiaDesign())
        else:
            # A refreshed Skin object may contain different loaded assets, so the
            # preview must reapply the current virtual design without losing it
            self.design_changed.emit(self.options())
        self._update_enabled()
        self._update_status()
        return True

    def set_keys(self, keys: int) -> bool:
        return self.set_skin(self._skin, keys)

    def confirm_discard_if_dirty(self) -> bool:
        if not self._dirty:
            return True
        choice = QMessageBox.question(
            self,
            i18n.t("designer.unsaved_title", "设计尚未保存"),
            i18n.t("designer.unsaved_message", "当前设计还未保存到皮肤副本\n保存副本后继续，或放弃这次设计？"),
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if choice == QMessageBox.Save:
            return self._export_copy()
        if choice == QMessageBox.Discard:
            self._dirty = False
            return True
        return False

    def reset_preview(self):
        self._saved_design = ManiaDesign()
        self._last_export = None
        self._apply_options(ManiaDesign())

    def _apply_options(self, design: ManiaDesign):
        self._loading = True
        try:
            self.top_height.setValue(int(design.top_mask_height))
            self.top_fade.setValue(int(design.top_mask_fade))
            self.receptor_raise.setValue(int(design.receptor_raise))
            self.link_hit_position.setChecked(design.link_hit_position)
            self._colour = design.mask_colour
        finally:
            self._loading = False
        self._changed()

    def _changed(self, *_):
        if self._loading:
            return
        design = self.options()
        self._dirty = self._skin_root is not None and design != self._saved_design
        self._update_colour()
        self._update_status()
        self.design_changed.emit(design)

    def _pick_colour(self):
        colour = QColorDialog.getColor(
            QColor(self._colour), self,
            i18n.t("designer.mask_colour", "遮罩颜色"),
        )
        if colour.isValid():
            self._colour = colour.name(QColor.HexRgb)
            self._changed()

    def _update_colour(self):
        swatch = QPixmap(16, 16)
        swatch.fill(QColor(self._colour))
        self.colour_button.setIcon(QIcon(swatch))
        self.colour_button.setText(self._colour.upper())

    def _update_enabled(self):
        loaded = self._skin_root is not None
        self.controls.setEnabled(loaded)
        self.reset_button.setEnabled(loaded)
        self.export_button.setEnabled(loaded and not self._preview_error)

    def set_preview_error(self, message):
        self._preview_error = str(message)
        self.error_label.setText(self._preview_error)
        self.error_label.setVisible(bool(self._preview_error))
        self._update_enabled()

    def _update_status(self):
        if self._skin_root is None:
            self.scope_label.setText(i18n.t("designer.no_skin", "请先打开皮肤"))
            self.status_label.setText("")
            return
        self.scope_label.setText(i18n.t("designer.scope", "{keys}K · {name}").format(keys=self._keys, name=self._skin_root.name))
        if self._dirty:
            self.status_label.setText(i18n.t("designer.unsaved_status", "设计已更新，尚未保存到皮肤副本"))
        elif self._last_export is not None:
            self.status_label.setText(i18n.t("designer.saved_status", "副本已保存到\n{path}").format(path=self._last_export))
        else:
            self.status_label.setText(i18n.t("designer.original_status", "当前显示原始皮肤"))

    def _choose_export_destination(self) -> Path | None:
        if self._skin_root is None:
            return None
        dialog = _ExportCopyDialog(self._skin_root, self._keys, self)
        return dialog.destination if dialog.exec() == QDialog.Accepted else None

    def _export_copy(self, *_args) -> bool:
        if self._skin_root is None:
            return False
        destination = self._choose_export_destination()
        if destination is None:
            return False
        if self.before_export is not None and self.before_export() is False:
            return False
        design = self.options()
        self.export_button.setEnabled(False)
        try:
            result = export_skin(self._skin_root, self._keys, destination, design)
        except Exception as error:
            QMessageBox.warning(
                self, i18n.t("designer.export_failed", "无法保存皮肤副本"),
                str(error),
            )
            return False
        finally:
            self._update_enabled()
        self._last_export = Path(result.root)
        self._saved_design = design
        self._dirty = False
        self._update_status()
        self.exported.emit(str(self._last_export))
        return True

    def retranslate(self):
        self.setWindowTitle(i18n.t("designer.title", "Mania 视觉设计"))
        self.note_label.setText(i18n.t("designer.note", "实时预览上隐与底部高度，保存时生成皮肤副本\n尺寸以 480 高度为基准"))
        self.mask_group.setTitle(i18n.t("designer.mask_group", "上隐遮罩"))
        self.height_label.setText(i18n.t("designer.mask_height", "遮挡高度"))
        self.fade_label.setText(i18n.t("designer.mask_fade", "渐变高度"))
        self.colour_label.setText(i18n.t("designer.mask_colour", "遮罩颜色"))
        self.bottom_group.setTitle(i18n.t("designer.bottom_group", "球槽与判定位置"))
        self.raise_label.setText(i18n.t("designer.bottom_height", "底部加高"))
        self.link_hit_position.setText(i18n.t("designer.link_hit", "联动实际判定位置"))
        self.bottom_hint.setText(i18n.t("designer.bottom_hint", "加高会向上移动按键图像\n勾选联动时同步移动实际判定位置"))
        self.reset_button.setText(i18n.t("designer.reset", "重置本次设计"))
        self.export_button.setText(i18n.t("designer.export", "保存皮肤副本"))
        self.top_height.setToolTip(i18n.t("designer.mask_height_tip", "从轨道顶端开始完全遮挡的高度"))
        self.top_fade.setToolTip(i18n.t("designer.mask_fade_tip", "完整遮挡下方逐渐透明的过渡高度"))
        self.receptor_raise.setToolTip(i18n.t("designer.bottom_height_tip", "相对原图增加底部留白并抬高按键图像"))
        self.link_hit_position.setToolTip(i18n.t("designer.link_hit_tip", "取消联动时只移动图像，实际判定位置保持原值"))
        self._update_colour()
        self._update_status()
