# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog, QHeaderView, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSizePolicy, QSlider, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QPixmap

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    _HAS_MULTIMEDIA = True
except ImportError:
    _HAS_MULTIMEDIA = False

from core.assets_ops import (
    AUDIO_EXTS_ALLOWED, IMAGE_EXTS, list_audio, list_images, replace_audio,
    replace_image, resolve_audio_conflicts, stem_conflicts,
)


class AssetsManagerDialog(QDialog):
    assets_changed = Signal()

    def __init__(self, skin_root: Path, parent=None, start_tab: str = "image"):
        super().__init__(parent)
        self.setWindowTitle("素材管理 · osu! Skin Editor")
        self.resize(1080, 700)
        self.setMinimumSize(840, 540)
        self.skin_root = Path(skin_root).resolve() if skin_root else None
        self.img_preview_path = None
        self._original_pixmap = QPixmap()
        self.player = None
        self.audio_output = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 18)
        root.setSpacing(16)
        heading = QLabel("素材管理")
        heading.setObjectName("Title")
        root.addWidget(heading)
        description = QLabel("浏览、试听与替换皮肤素材；替换和冲突整理会自动保留原文件备份")
        description.setObjectName("Muted")
        description.setWordWrap(True)
        root.addWidget(description)

        self.tabs = QTabWidget(self)
        root.addWidget(self.tabs, 1)
        self.img_tab = QWidget()
        self.aud_tab = QWidget()
        self.tabs.addTab(self.img_tab, "图片")
        self.tabs.addTab(self.aud_tab, "音频")

        self.img_table = self._make_table()
        self.img_search = self._make_search("搜索图片名称或路径…", self.img_table)
        self.btn_img_replace = QPushButton("替换图片…")
        self.btn_img_replace.setObjectName("PrimaryButton")
        self.btn_img_replace.setEnabled(False)
        self.btn_img_refresh = QPushButton("刷新")
        self.img_preview = QLabel("选择左侧图片以预览")
        self.img_preview.setObjectName("PreviewPanel")
        self.img_preview.setAlignment(Qt.AlignCenter)
        self.img_preview.setMinimumSize(300, 240)
        self.img_preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.img_details = QLabel("支持 PNG；其他图片会在替换时转换")
        self.img_details.setObjectName("Muted")
        self.img_details.setWordWrap(True)
        image_area = QHBoxLayout(self.img_tab)
        image_area.setContentsMargins(0, 16, 0, 0)
        image_area.setSpacing(20)
        image_left = QVBoxLayout()
        image_left.addWidget(self.img_search)
        image_left.addWidget(self.img_table, 1)
        image_buttons = QHBoxLayout()
        image_buttons.addWidget(self.btn_img_refresh)
        image_buttons.addStretch()
        image_buttons.addWidget(self.btn_img_replace)
        image_left.addLayout(image_buttons)
        image_right = QVBoxLayout()
        image_right.addWidget(self.img_preview, 1)
        image_right.addWidget(self.img_details)
        image_area.addLayout(image_left, 3)
        image_area.addLayout(image_right, 2)

        self.aud_table = self._make_table()
        self.aud_search = self._make_search("搜索音频名称或路径…", self.aud_table)
        self.aud_info = QLabel("选择左侧音频以试听")
        self.aud_info.setObjectName("PreviewPanel")
        self.aud_info.setAlignment(Qt.AlignCenter)
        self.aud_info.setWordWrap(True)
        self.aud_info.setMinimumSize(300, 160)
        self.btn_aud_play = QPushButton("▶ 播放")
        self.btn_aud_play.setObjectName("PrimaryButton")
        self.btn_aud_stop = QPushButton("停止")
        self.btn_aud_play.setEnabled(False)
        self.btn_aud_stop.setEnabled(False)
        self.btn_aud_conflicts = QPushButton("整理冲突")
        self.btn_aud_conflicts.setToolTip("同一目录中同名音频优先保留 OGG，其次 WAV、MP3；其他文件移入备份")
        self.btn_aud_replace = QPushButton("替换音频…")
        self.btn_aud_replace.setEnabled(False)
        self.btn_aud_refresh = QPushButton("刷新")
        self.slider_pos = QSlider(Qt.Horizontal)
        self.slider_pos.setRange(0, 0)
        self.slider_pos.setAccessibleName("播放进度")
        self.lbl_time = QLabel("0:00 / 0:00")
        self.lbl_time.setObjectName("Muted")
        self.lbl_volume = QLabel("音量：25%")
        self.slider_vol = QSlider(Qt.Horizontal)
        self.slider_vol.setRange(0, 100)
        self.slider_vol.setValue(25)
        self.slider_vol.setPageStep(5)
        self.slider_vol.setAccessibleName("试听音量")
        self.slider_vol.valueChanged.connect(self._on_volume_changed)
        audio_area = QHBoxLayout(self.aud_tab)
        audio_area.setContentsMargins(0, 16, 0, 0)
        audio_area.setSpacing(20)
        audio_left = QVBoxLayout()
        audio_left.addWidget(self.aud_search)
        audio_left.addWidget(self.aud_table, 1)
        audio_buttons = QHBoxLayout()
        audio_buttons.addWidget(self.btn_aud_refresh)
        audio_buttons.addWidget(self.btn_aud_conflicts)
        audio_buttons.addStretch()
        audio_buttons.addWidget(self.btn_aud_replace)
        audio_left.addLayout(audio_buttons)
        audio_right = QVBoxLayout()
        audio_right.addWidget(self.aud_info, 1)
        audio_right.addWidget(self.slider_pos)
        audio_right.addWidget(self.lbl_time)
        audio_controls = QHBoxLayout()
        audio_controls.addWidget(self.btn_aud_play)
        audio_controls.addWidget(self.btn_aud_stop)
        audio_right.addLayout(audio_controls)
        volume_row = QHBoxLayout()
        volume_row.addWidget(self.lbl_volume)
        volume_row.addWidget(self.slider_vol)
        audio_right.addLayout(volume_row)
        audio_area.addLayout(audio_left, 3)
        audio_area.addLayout(audio_right, 2)
        footer = QHBoxLayout()
        self.backup_hint = QLabel("原文件备份：皮肤目录 / __conflicts_backup")
        self.backup_hint.setObjectName("Muted")
        footer.addWidget(self.backup_hint)
        footer.addStretch()
        close_button = QPushButton("完成")
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        root.addLayout(footer)

        self.btn_img_refresh.clicked.connect(self.refresh_images)
        self.btn_aud_refresh.clicked.connect(self.refresh_audio)
        self.btn_img_replace.clicked.connect(self._replace_image)
        self.btn_aud_replace.clicked.connect(self._replace_audio)
        self.btn_aud_conflicts.clicked.connect(self._resolve_conflicts)
        self.img_table.itemSelectionChanged.connect(self._on_img_selection_changed)
        self.aud_table.itemSelectionChanged.connect(self._on_audio_selection_changed)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        if _HAS_MULTIMEDIA:
            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_output)
            self.audio_output.setVolume(self.slider_vol.value() / 100)
            self.player.positionChanged.connect(self._on_player_position)
            self.player.durationChanged.connect(self._on_player_duration)
            self.player.errorOccurred.connect(self._on_player_error)
            self.btn_aud_play.clicked.connect(self._play_selected_audio)
            self.btn_aud_stop.clicked.connect(self.player.stop)
            self.slider_pos.sliderMoved.connect(self.player.setPosition)
        else:
            self.slider_pos.setEnabled(False)
            self.slider_vol.setEnabled(False)
            self.lbl_volume.setText("音频预览不可用：缺少 QtMultimedia")
        self.refresh_images()
        self.refresh_audio()
        if start_tab == "audio":
            self.tabs.setCurrentIndex(1)

    @staticmethod
    def _make_table():
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["文件", "格式", "相对路径"])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(38)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        return table

    def _make_search(self, placeholder, table):
        search = QLineEdit()
        search.setPlaceholderText(placeholder)
        search.setClearButtonEnabled(True)
        search.textChanged.connect(lambda text: self._filter_table(table, text))
        return search

    @staticmethod
    def _filter_table(table, text):
        needle = text.strip().casefold()
        for row in range(table.rowCount()):
            matches = any(needle in table.item(row, col).text().casefold() for col in (0, 2))
            table.setRowHidden(row, not matches)
        if table.currentRow() >= 0 and table.isRowHidden(table.currentRow()):
            table.clearSelection()

    def _ensure_root(self) -> Path | None:
        if not self.skin_root or not self.skin_root.is_dir():
            return None
        return self.skin_root

    def _selected_path(self, table: QTableWidget) -> Path | None:
        root = self._ensure_root()
        if root is None or not table.selectionModel().hasSelection():
            return None
        item = table.item(table.currentRow(), 0)
        if item is None:
            return None
        # The display text is never used as a writable filesystem path.
        path = Path(item.data(Qt.UserRole))
        try:
            path.resolve().relative_to(root)
        except ValueError:
            return None
        return path

    def _populate_table(self, table, files, supported, select=None):
        root = self._ensure_root()
        table.blockSignals(True)
        try:
            table.setRowCount(0)
            for path in files:
                row = table.rowCount()
                table.insertRow(row)
                name = QTableWidgetItem(path.name)
                name.setData(Qt.UserRole, str(path))
                name.setToolTip(str(path.relative_to(root)))
                table.setItem(row, 0, name)
                format_item = QTableWidgetItem(path.suffix[1:].upper())
                format_item.setToolTip("osu! 支持" if path.suffix.lower() in supported else "替换时需要转换")
                table.setItem(row, 1, format_item)
                table.setItem(row, 2, QTableWidgetItem(str(path.relative_to(root))))
            table.clearSelection()
        finally:
            table.blockSignals(False)
        if select:
            for row, path in enumerate(files):
                if path == select:
                    table.selectRow(row)
                    table.scrollToItem(table.item(row, 0))
                    break

    def refresh_images(self, select=None):
        if isinstance(select, bool):
            select = None
        select = select or self._selected_path(self.img_table)
        root = self._ensure_root()
        files = list_images(root) if root else []
        self._show_img_placeholder()
        self._populate_table(self.img_table, files, IMAGE_EXTS, select)
        self._filter_table(self.img_table, self.img_search.text())
        self.tabs.setTabText(0, f"图片  {len(files)}")
        self.btn_img_replace.setEnabled(self._selected_path(self.img_table) is not None)

    def _show_img_placeholder(self, msg="选择左侧图片以预览"):
        self.img_preview.clear()
        self.img_preview.setText(msg)
        self.img_preview_path = None
        self._original_pixmap = QPixmap()
        self.img_details.setText("支持 PNG；其他图片会在替换时转换")
        self.btn_img_replace.setEnabled(False)

    def _update_img_preview_pixmap(self):
        if self._original_pixmap.isNull():
            return
        # QLabel.setText() clears its pixmap, even when the text is empty.
        self.img_preview.setPixmap(self._original_pixmap.scaled(
            self.img_preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_img_preview_pixmap()

    def _on_tab_changed(self, index):
        if index == 0:
            if self.player:
                self.player.stop()
            self._update_img_preview_pixmap()

    def _on_img_selection_changed(self):
        path = self._selected_path(self.img_table)
        if not path or not path.is_file():
            self._show_img_placeholder()
            return
        self.img_preview_path = str(path)
        self._original_pixmap = QPixmap()
        try:
            # Bypass Qt's filename cache after an in-place replacement.
            self._original_pixmap.loadFromData(path.read_bytes())
        except OSError:
            self._show_img_placeholder("图片已被移动或无法读取")
            return
        self.btn_img_replace.setEnabled(True)
        if self._original_pixmap.isNull():
            self._show_img_placeholder("无法预览该图片")
            self.btn_img_replace.setEnabled(True)
            return
        self.img_details.setText(f"{path.name}\n{self._original_pixmap.width()} × {self._original_pixmap.height()} px")
        self._update_img_preview_pixmap()

    def _replace_image(self):
        current = self._selected_path(self.img_table)
        if current is None:
            return
        source, _ = QFileDialog.getOpenFileName(self, "选择替换图片", "", "图片 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not source:
            return
        try:
            final = replace_image(Path(source), current, skin_root=self.skin_root)
        except Exception as exc:
            QMessageBox.critical(self, "图片替换失败", str(exc))
            return
        self.assets_changed.emit()
        self.refresh_images(select=final)
        self.backup_hint.setText("图片已替换；原文件已保存在 __conflicts_backup")

    def refresh_audio(self, select=None):
        if isinstance(select, bool):
            select = None
        select = select or self._selected_path(self.aud_table)
        root = self._ensure_root()
        files = list_audio(root) if root else []
        self._show_aud_placeholder()
        self._populate_table(self.aud_table, files, AUDIO_EXTS_ALLOWED, select)
        self._filter_table(self.aud_table, self.aud_search.text())
        self.tabs.setTabText(1, f"音频  {len(files)}")
        self.btn_aud_conflicts.setEnabled(bool(stem_conflicts(files)))

    def _release_audio(self):
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl())
        self.slider_pos.setRange(0, 0)
        self.lbl_time.setText("0:00 / 0:00")

    def _show_aud_placeholder(self, msg="选择左侧音频以试听"):
        self._release_audio()
        self.aud_info.setText(msg)
        self.btn_aud_replace.setEnabled(False)
        self.btn_aud_play.setEnabled(False)
        self.btn_aud_stop.setEnabled(False)

    def _on_audio_selection_changed(self):
        path = self._selected_path(self.aud_table)
        self._release_audio()
        if not path or not path.is_file():
            self._show_aud_placeholder()
            return
        self.aud_info.setText(f"♪\n\n{path.name}\n\n{path.suffix[1:].upper()} · 点击播放试听")
        self.btn_aud_replace.setEnabled(True)
        self.btn_aud_play.setEnabled(self.player is not None)
        self.btn_aud_stop.setEnabled(self.player is not None)
        if self.player:
            self.player.setSource(QUrl.fromLocalFile(str(path)))

    def _play_selected_audio(self):
        if self.player and self._selected_path(self.aud_table):
            self.player.play()

    @staticmethod
    def _time_string(milliseconds):
        seconds = max(0, milliseconds // 1000)
        return f"{seconds // 60}:{seconds % 60:02d}"

    def _on_player_position(self, pos):
        if self.player and not self.slider_pos.isSliderDown():
            self.slider_pos.setValue(pos)
        if self.player:
            self.lbl_time.setText(f"{self._time_string(pos)} / {self._time_string(self.player.duration())}")

    def _on_player_duration(self, duration):
        self.slider_pos.setRange(0, duration)
        self._on_player_position(self.player.position())

    def _on_player_error(self, error, message):
        if self.player and error != QMediaPlayer.NoError and not self.player.source().isEmpty():
            self.aud_info.setText(f"无法播放此音频\n\n{message}")

    def _on_volume_changed(self, value):
        value = max(0, min(100, int(value)))
        if self.audio_output:
            self.audio_output.setVolume(value / 100)
        self.lbl_volume.setText(f"音量：{value}%")

    def _replace_audio(self):
        current = self._selected_path(self.aud_table)
        if current is None:
            return
        source, _ = QFileDialog.getOpenFileName(self, "选择替换音频", "", "音频 (*.wav *.ogg *.mp3 *.flac)")
        if not source:
            return
        self._release_audio()
        source = Path(source)
        preferred = source.suffix.lower() if source.suffix.lower() in AUDIO_EXTS_ALLOWED else ".wav"
        try:
            final = replace_audio(source, current, prefer_ext=preferred, skin_root=self.skin_root)
        except Exception as exc:
            self._on_audio_selection_changed()
            QMessageBox.critical(self, "音频替换失败", str(exc))
            return
        self.assets_changed.emit()
        self.refresh_audio(select=final)
        self.backup_hint.setText("音频已替换；原文件和同名冲突文件已保存在 __conflicts_backup")

    def _resolve_conflicts(self):
        root = self._ensure_root()
        if root is None:
            return
        duplicates = stem_conflicts(list_audio(root))
        if not duplicates:
            return
        priority = {extension: index for index, extension in enumerate((".ogg", ".wav", ".mp3", ".flac"))}
        keep = {key: min(paths, key=lambda path: priority[path.suffix.lower()])
                for key, paths in duplicates.items()}
        self._release_audio()
        try:
            resolve_audio_conflicts(root, keep)
        except Exception as exc:
            self.refresh_audio()
            QMessageBox.critical(self, "冲突整理失败", str(exc))
            return
        self.assets_changed.emit()
        self.refresh_audio()
        self.backup_hint.setText(f"已整理 {len(duplicates)} 组冲突；原文件已保存在 __conflicts_backup")

    def done(self, result):
        self._release_audio()
        super().done(result)
