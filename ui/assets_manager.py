
# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
import sys, os

from PySide6.QtWidgets import (
    QDialog, QTabWidget, QWidget,
    QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QFileDialog, QMessageBox, QSlider, QSizePolicy
)
from PySide6.QtCore import Qt, QUrl, QSize
from PySide6.QtGui import QPixmap

# Qt Multimedia（用于音频预览）
try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    _HAS_MULTIMEDIA = True
except Exception:
    # 万一环境里没有 QtMultimedia，也不让对话框崩
    _HAS_MULTIMEDIA = False
    QMediaPlayer = object  # type: ignore
    QAudioOutput = object  # type: ignore

# 避免循环导入：在本文件内实现 resource_path（onefile / 源码运行均可用）
def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)
    return str(Path(base) / rel)

from core.assets_ops import (
    list_images, list_audio,
    replace_image, replace_audio,
    stem_conflicts, resolve_audio_conflicts,
    IMAGE_EXTS, AUDIO_EXTS_ALLOWED, AUDIO_EXTS_COMMON,
)


class AssetsManagerDialog(QDialog):
    def __init__(self, skin_root: Path, parent=None, start_tab: str="image"):
        super().__init__(parent)
        self.setWindowTitle("XiaoLan小蓝 皮肤文件小工具")
        self.resize(980, 640)
        self.skin_root = Path(skin_root) if skin_root else None

        # ---- Tabs ----
        self.tabs = QTabWidget(self)

        # ---------- 图片页 ----------
        self.img_tab = QWidget(self.tabs)
        self.tabs.addTab(self.img_tab, "图片")

        # 左侧表格
        self.img_table = QTableWidget(0, 3, self.img_tab)
        self.img_table.setHorizontalHeaderLabels(["文件名", "支持", "相对路径"])
        self.img_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.img_table.setAlternatingRowColors(True)

        # 右侧预览
        self.img_preview = QLabel("预览区：请选择一张图片", self.img_tab)
        self.img_preview.setAlignment(Qt.AlignCenter)
        self.img_preview.setMinimumSize(320, 280)
        self.img_preview.setStyleSheet("background:#111; color:#888; border-radius:8px;")
        self.img_preview_path = None  # 当前预览路径

        # 底部按钮
        self.btn_img_replace = QPushButton("替换…", self.img_tab)
        self.btn_img_refresh = QPushButton("刷新", self.img_tab)

        # 布局：左（表+按钮）/ 右（预览）
        img_left = QVBoxLayout()
        img_left.addWidget(self.img_table, 1)
        left_bar = QHBoxLayout()
        left_bar.addWidget(QLabel("仅接受：.png"))
        left_bar.addStretch(1)
        left_bar.addWidget(self.btn_img_replace)
        left_bar.addWidget(self.btn_img_refresh)
        img_left.addLayout(left_bar)

        img_right = QVBoxLayout()
        img_right.addWidget(self.img_preview, 1)

        img_area = QHBoxLayout(self.img_tab)
        img_area.addLayout(img_left, 2)
        img_area.addLayout(img_right, 3)

        # ---------- 音频页 ----------
        self.aud_tab = QWidget(self.tabs)
        self.tabs.addTab(self.aud_tab, "音频")

        # 左侧表格
        self.aud_table = QTableWidget(0, 3, self.aud_tab)
        self.aud_table.setHorizontalHeaderLabels(["文件名", "支持", "相对路径"])
        self.aud_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.aud_table.setAlternatingRowColors(True)

        # 右侧预览（播放器）
        self.aud_info = QLabel("预览区：请选择一个音频", self.aud_tab)
        self.aud_info.setAlignment(Qt.AlignCenter)
        self.aud_info.setMinimumSize(320, 120)
        self.aud_info.setStyleSheet("background:#111; color:#888; border-radius:8px; padding:8px;")

        self.btn_aud_play = QPushButton("► 播放", self.aud_tab)
        self.btn_aud_stop = QPushButton("■ 停止", self.aud_tab)
        self.btn_aud_conflicts = QPushButton("处理冲突…", self.aud_tab)
        self.btn_aud_replace = QPushButton("替换…", self.aud_tab)
        self.btn_aud_refresh = QPushButton("刷新", self.aud_tab)

        # 播放进度
        self.slider_pos = QSlider(Qt.Horizontal, self.aud_tab)
        self.slider_pos.setRange(0, 0)

        # 音量控制
        self.row_volume = QHBoxLayout()
        self.lbl_volume = QLabel("音量：25%", self.aud_tab)
        self.slider_vol = QSlider(Qt.Horizontal, self.aud_tab)
        self.slider_vol.setRange(0, 100)
        self.slider_vol.setValue(25)
        self.slider_vol.setSingleStep(1)
        self.slider_vol.setPageStep(5)
        self.slider_vol.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.row_volume.addWidget(self.lbl_volume)
        self.row_volume.addWidget(self.slider_vol)

        aud_left = QVBoxLayout()
        aud_left.addWidget(self.aud_table, 1)
        aud_left_bar = QHBoxLayout()
        aud_left_bar.addWidget(QLabel("接受：.wav / .ogg / .mp3（其他尝试转换）"))
        aud_left_bar.addStretch(1)
        aud_left_bar.addWidget(self.btn_aud_conflicts)
        aud_left_bar.addWidget(self.btn_aud_replace)
        aud_left_bar.addWidget(self.btn_aud_refresh)
        aud_left.addLayout(aud_left_bar)

        aud_right = QVBoxLayout()
        aud_right.addWidget(self.aud_info, 1)
        aud_ctrl = QHBoxLayout()
        aud_ctrl.addWidget(self.btn_aud_play)
        aud_ctrl.addWidget(self.btn_aud_stop)
        aud_right.addLayout(aud_ctrl)
        aud_right.addWidget(self.slider_pos)
        aud_right.addLayout(self.row_volume)

        aud_area = QHBoxLayout(self.aud_tab)
        aud_area.addLayout(aud_left, 2)
        aud_area.addLayout(aud_right, 3)

        # ---- 主体布局 ----
        root = QVBoxLayout(self)
        root.addWidget(self.tabs)

        # ---- 信号 ----
        self.btn_img_refresh.clicked.connect(self.refresh_images)
        self.btn_aud_refresh.clicked.connect(self.refresh_audio)
        self.btn_img_replace.clicked.connect(self._replace_image)
        self.btn_aud_replace.clicked.connect(self._replace_audio)
        self.btn_aud_conflicts.clicked.connect(self._resolve_conflicts)
        self.img_table.itemSelectionChanged.connect(self._on_img_selection_changed)
        self.aud_table.itemSelectionChanged.connect(self._on_audio_selection_changed)

        # 播放器初始化
        if _HAS_MULTIMEDIA:
            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_output)
            # 默认音量 50%
            self.audio_output.setVolume(0.5)
            # 绑定音量滑条
            self.slider_vol.valueChanged.connect(self._on_volume_changed)
            # 播放控制
            self.player.positionChanged.connect(self._on_player_position)
            self.player.durationChanged.connect(self._on_player_duration)
            self.btn_aud_play.clicked.connect(self._play_selected_audio)
            self.btn_aud_stop.clicked.connect(self.player.stop)
            self.slider_pos.sliderMoved.connect(self.player.setPosition)
        else:
            self.btn_aud_play.setEnabled(False)
            self.btn_aud_stop.setEnabled(False)
            self.slider_pos.setEnabled(False)
            self.slider_vol.setEnabled(False)
            self.lbl_volume.setText("未安装 QtMultimedia，无法预览音频。")
            self.aud_info.setText("未安装 QtMultimedia，无法预览音频。")

        # ---- 初始化数据 ----
        self.refresh_images()
        self.refresh_audio()
        if start_tab == "audio":
            self.tabs.setCurrentIndex(1)

    # ========== 工具函数 ==========
    def _ensure_root(self) -> Path | None:
        if not self.skin_root or not Path(self.skin_root).exists():
            QMessageBox.warning(self, "提示", "未检测到皮肤目录，请先打开一个皮肤。")
            return None
        return Path(self.skin_root)

    def _selected_path(self, table: QTableWidget) -> Path | None:
        root = self._ensure_root()
        if not root:
            return None
        r = table.currentRow()
        if r < 0:
            return None
        rel = table.item(r, 2).text()
        return (root / rel)

    # ========== 图片 ==========
    def refresh_images(self):
        root = self._ensure_root()
        if not root: return
        files = list_images(root)
        self.img_table.setRowCount(0)
        for p in files:
            support = "✓" if p.suffix.lower() in IMAGE_EXTS else "✗"
            row = self.img_table.rowCount()
            self.img_table.insertRow(row)
            self.img_table.setItem(row, 0, QTableWidgetItem(p.name))
            self.img_table.setItem(row, 1, QTableWidgetItem(support))
            self.img_table.setItem(row, 2, QTableWidgetItem(str(p.relative_to(root))))
        # 清空预览
        self._show_img_placeholder()

    def _show_img_placeholder(self, msg: str = "预览区：请选择一张图片"):
        self.img_preview.setPixmap(QPixmap())
        self.img_preview.setText(msg)
        self.img_preview_path = None

    def _update_img_preview_pixmap(self):
        """根据预览控件尺寸重新缩放已加载的图片。"""
        if not self.img_preview_path:
            return
        pm = QPixmap(self.img_preview_path)
        if pm.isNull():
            self._show_img_placeholder("无法预览该图片")
            return
        scaled = pm.scaled(self.img_preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.img_preview.setPixmap(scaled)
        self.img_preview.setText("")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # 在图片页签时，窗口尺寸变化重算预览
        if self.tabs.currentIndex() == 0:
            self._update_img_preview_pixmap()

    def _on_img_selection_changed(self):
        p = self._selected_path(self.img_table)
        if not p or not p.exists():
            self._show_img_placeholder()
            return
        self.img_preview_path = str(p)
        self._update_img_preview_pixmap()

    def _replace_image(self):
        root = self._ensure_root()
        if not root: return
        cur = self._selected_path(self.img_table)
        if not cur:
            QMessageBox.information(self, "替换图片", "请先在表格里选中一个目标文件。")
            return
        src, _ = QFileDialog.getOpenFileName(self, "选择图片", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not src: return
        try:
            replace_image(Path(src), cur.with_suffix(".png"), make_png=True)
            self._on_img_selection_changed()  # 立即刷新预览
            QMessageBox.information(self, "成功", f"已替换为 {cur.with_suffix('.png').name}")
        except Exception as e:
            QMessageBox.critical(self, "失败", str(e))
        self.refresh_images()

    # ========== 音频 ==========
    def refresh_audio(self):
        root = self._ensure_root()
        if not root: return
        files = list_audio(root)
        self.aud_table.setRowCount(0)
        for p in files:
            support = "✓" if p.suffix.lower() in AUDIO_EXTS_ALLOWED else "✗"
            row = self.aud_table.rowCount()
            self.aud_table.insertRow(row)
            self.aud_table.setItem(row, 0, QTableWidgetItem(p.name))
            self.aud_table.setItem(row, 1, QTableWidgetItem(support))
            self.aud_table.setItem(row, 2, QTableWidgetItem(str(p.relative_to(root))))
        self._show_aud_placeholder()

    def _show_aud_placeholder(self, msg: str = "预览区：请选择一个音频"):
        self.aud_info.setText(msg)
        if _HAS_MULTIMEDIA:
            try:
                self.player.stop()
                self.slider_pos.setRange(0, 0)
            except Exception:
                pass

    def _on_audio_selection_changed(self):
        p = self._selected_path(self.aud_table)
        if not p or not p.exists():
            self._show_aud_placeholder()
            return
        self.aud_info.setText(f"选中：{p.name}")
        # 不立即播，等用户点“播放”
        if _HAS_MULTIMEDIA:
            self.player.setSource(QUrl.fromLocalFile(str(p)))
            self.slider_pos.setRange(0, 0)

    def _play_selected_audio(self):
        if not _HAS_MULTIMEDIA:
            return
        p = self._selected_path(self.aud_table)
        if not p or not p.exists():
            self._show_aud_placeholder()
            return
        self.player.play()

    def _on_player_position(self, pos: int):
        if self.player.duration() > 0 and not self.slider_pos.isSliderDown():
            self.slider_pos.setValue(pos)

    def _on_player_duration(self, dur: int):
        self.slider_pos.setRange(0, dur)

    def _on_volume_changed(self, value: int):
        """音量滑条回调（0~100）"""
        if not _HAS_MULTIMEDIA:
            return
        vol = max(0, min(100, int(value))) / 100.0
        try:
            self.audio_output.setVolume(vol)  # Qt6: 0.0 ~ 1.0
        except Exception:
            pass
        self.lbl_volume.setText(f"音量：{int(value)}%")

    def _replace_audio(self):
        root = self._ensure_root()
        if not root: return
        cur = self._selected_path(self.aud_table)
        if not cur:
            QMessageBox.information(self, "替换音频", "请先在表格里选中一个目标文件。")
            return
        src, _ = QFileDialog.getOpenFileName(self, "选择音频", "", "Audio (*.wav *.ogg *.mp3 *.flac)")
        if not src: return
        prefer = ".wav"
        try:
            dst = replace_audio(Path(src), cur.with_suffix(prefer), prefer_ext=prefer)
            self._on_audio_selection_changed()  # 刷新预览/播放器
            QMessageBox.information(self, "成功", f"已替换为 {dst.name}")
        except Exception as e:
            QMessageBox.critical(self, "失败", str(e))
        self.refresh_audio()

    def _resolve_conflicts(self):
        root = self._ensure_root()
        if not root: return
        files = list_audio(root)
        dups = stem_conflicts(files)
        if not dups:
            QMessageBox.information(self, "冲突处理", "未发现同名不同后缀的音频。")
            return
        keep = {}
        for stem, paths in dups.items():
            # 简单策略：优先 .ogg -> .wav -> 其它
            preferred = None
            for ext in (".ogg", ".wav", ".mp3", ".flac"):
                hit = [p for p in paths if p.suffix.lower()==ext]
                if hit:
                    preferred = hit[0]; break
            keep[stem] = preferred or paths[0]
        bdir = resolve_audio_conflicts(root, keep)
        QMessageBox.information(self, "完成", f"已移动冲突文件到：{bdir}")
