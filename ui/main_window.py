# -*- coding: utf-8 -*-
import os
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QFileDialog, QSplitter, QListWidget, QWidget, QVBoxLayout, QTabWidget,
    QMessageBox, QMenu, QDockWidget, QPushButton, QHBoxLayout, QGridLayout, QLabel, QSpinBox, QCheckBox, QDialog, QDialogButtonBox,
    QFrame, QLineEdit, QComboBox, QStackedWidget, QListWidgetItem, QStyle, QSizePolicy, QToolButton
)
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QIcon, QImageReader, QPixmap, QKeySequence
from PySide6.QtCore import Qt, QSettings, QByteArray, QTimer, QUrl, QSize
from core.app_links import get_links
from core import i18n

from core.skin_loader import SkinLoader
from ui.assets_manager import AssetsManagerDialog
from pathlib import Path
from ui.preview.std_preview import StdPreview
from ui.preview.mania_preview import ManiaPreview
from ui.mania_ini_dock import ManiaIniDock
from ui.mania_design_dock import ManiaDesignDock
from ui.mania_preview_controls import ManiaPreviewControls
from ui.widgets.asset_inspector import AssetInspector
from ui.widgets.identity import AvatarBadge, TechPanel, WelcomeCanvas
from ui.icons import workspace_icon
from core import i18n

RECENT_LIMIT = 12


class MainWindow(QMainWindow):
    def _open_assets_manager(self, tab: str = "image"):
        if not self.skin:
            self.on_open_generic()
        if not self.skin:
            return
        had_design = self.mania_design_dock._dirty
        if not self._confirm_design_navigation():
            return
        if had_design:
            self.mania_design_dock.reset_preview()
        was_dirty = self.mania_ini_dock._dirty
        if not self.mania_ini_dock._confirm_discard_if_dirty():
            return
        if was_dirty and not self.load_skin(str(self.skin.root), check_dirty=False):
            return
        try:
            dlg = AssetsManagerDialog(self.skin.root, self, start_tab=tab)
            changed = []
            dlg.assets_changed.connect(lambda: changed.append(True))
            dlg.exec()
            if changed:
                self.load_skin(str(self.skin.root), check_dirty=False)
            dlg.deleteLater()
        except Exception as e:
            QMessageBox.critical(self, "打开失败", f"打开皮肤文件小工具失败：{e}")

    def __init__(self):
        super().__init__()
        self.setAnimated(False)
        i18n.load_language()

        self.skin = None
        self.loader = SkinLoader()
        self.settings = QSettings()
        self.osu_root = self._load_osu_root()

        self._build_workspace()

        # ---------- Menus & Actions ----------
        menubar = self.menuBar()
        self.file_menu = menubar.addMenu("")
        self.settings_menu = menubar.addMenu("")
        self.debug_menu = menubar.addMenu("")      # Debug 顶栏
        self.mania_menu = menubar.addMenu("")
        self.assets_menu = menubar.addMenu("")  # 资产管理      # MANIA SETTINGS 顶栏

        self.author_menu = menubar.addMenu("")      # 作者 顶栏
        self.recent_menu = QMenu(self)

        self.act_open = QAction(self)
        self.act_open_osu = QAction(self)
        self.act_open_last = QAction(self)
        self.act_reload = QAction(self)
        self.act_set_osu = QAction(self)
        self.act_quit = QAction(self)

        # 作者信息动作
        self.act_about_author = QAction(self)
        self.act_assets_images = QAction(self)
        self.act_assets_audio = QAction(self)

        # 作者链接动作
        self.act_link_github = QAction(self)
        self.act_link_steam = QAction(self)
        self.act_link_bilibili = QAction(self)
        self.act_link_blog = QAction(self)

        self.act_open.triggered.connect(self.on_open_generic)
        self.act_open_osu.triggered.connect(self.on_open_osu_skins)
        self.act_open_last.triggered.connect(self.on_open_last_skin)
        self.act_reload.triggered.connect(self.reload_skin)
        self.act_set_osu.triggered.connect(self.on_set_osu_root)
        self.act_quit.triggered.connect(self.close)
        self.act_open.setShortcut(QKeySequence.Open)
        self.act_reload.setShortcut("F5")
        self.act_quit.setShortcut(QKeySequence.Quit)
        self.btn_open.clicked.connect(self.on_open_generic)
        self.btn_welcome_open.clicked.connect(self.on_open_generic)
        self.btn_welcome_osu.clicked.connect(self.on_open_osu_skins)
        self.btn_images.clicked.connect(lambda: self._open_assets_manager("image"))
        self.btn_audio.clicked.connect(lambda: self._open_assets_manager("audio"))
        self.btn_reload.clicked.connect(self.reload_skin)
        self.btn_folder.clicked.connect(self._reveal_skin)

        # 连接作者链接动作（点击后在浏览器打开）
        self.act_link_github.triggered.connect(lambda: QDesktopServices.openUrl(QUrl('https://github.com/XiaoLan9999/OsuSkinEditor')))
        self.act_link_steam.triggered.connect(lambda: QDesktopServices.openUrl(QUrl('https://steamcommunity.com/profiles/76561198969998874/')))
        self.act_link_bilibili.triggered.connect(lambda: QDesktopServices.openUrl(QUrl('https://space.bilibili.com/325569826')))
        self.act_link_blog.triggered.connect(lambda: QDesktopServices.openUrl(QUrl('https://blog.xiaolan9999.net/')))
        self.act_about_author.triggered.connect(self._show_author_info_dialog)
        self.act_assets_images.triggered.connect(lambda: self._open_assets_manager('image'))
        self.act_assets_audio.triggered.connect(lambda: self._open_assets_manager('audio'))


        # Keep language selection discoverable even when the current language
        # is unfamiliar: this label deliberately stays bilingual.
        self.lang_menu = QMenu(self)
        self.lang_group = QActionGroup(self)
        self.lang_group.setExclusive(True)
        self.act_lang_zh = QAction("中文", self)
        self.act_lang_en = QAction("English", self)
        for action, code in ((self.act_lang_zh, "zh-CN"), (self.act_lang_en, "en-US")):
            action.setCheckable(True)
            action.setData(code)
            self.lang_group.addAction(action)
            self.lang_menu.addAction(action)
            action.triggered.connect(lambda checked=False, language=code: self.on_change_language(language))
        self.language_button = QToolButton(menubar)
        self.language_button.setObjectName("LanguageButton")
        self.language_button.setText("语言 / Language")
        self.language_button.setAccessibleName("语言 / Language")
        self.language_button.setToolTip("语言 / Language")
        self.language_button.setFocusPolicy(Qt.StrongFocus)
        self.language_button.setPopupMode(QToolButton.InstantPopup)
        self.language_button.setMenu(self.lang_menu)
        menubar.setCornerWidget(self.language_button, Qt.TopRightCorner)

        # centering menu
        self.center_menu = QMenu(self)
        self.center_group = QActionGroup(self); self.center_group.setExclusive(True)
        self.act_center_image = QAction(self); self.act_center_image.setCheckable(True)
        self.act_center_alpha = QAction(self); self.act_center_alpha.setCheckable(True)
        self.center_group.addAction(self.act_center_image); self.center_group.addAction(self.act_center_alpha)
        self.author_menu.addAction(self.act_about_author)
        self.assets_menu.addAction(self.act_assets_images)
        self.assets_menu.addAction(self.act_assets_audio)

        self.author_menu.addSeparator()
        self.author_menu.addAction(self.act_link_github)
        self.author_menu.addAction(self.act_link_steam)
        self.author_menu.addAction(self.act_link_bilibili)
        self.author_menu.addAction(self.act_link_blog)
        self.center_menu.addAction(self.act_center_image); self.center_menu.addAction(self.act_center_alpha)
        cmode = self.settings.value("ui/approach_center_mode","image",str); self.std_preview.set_approach_center_mode(cmode)
        (self.act_center_alpha if cmode=="alpha" else self.act_center_image).setChecked(True)
        self.act_center_image.triggered.connect(lambda: self.on_set_center_mode("image"))
        self.act_center_alpha.triggered.connect(lambda: self.on_set_center_mode("alpha"))

        # build menus（去掉 Settings 里的 “STD OFFSETS” 条目）
        self.file_menu.addAction(self.act_open); self.file_menu.addAction(self.act_open_osu); self.file_menu.addAction(self.act_open_last); self.file_menu.addMenu(self.recent_menu)
        self.file_menu.addAction(self.act_reload); self.file_menu.addSeparator(); self.file_menu.addAction(self.act_set_osu); self.file_menu.addSeparator(); self.file_menu.addAction(self.act_quit)
        self.settings_menu.addMenu(self.center_menu)
        # 不再添加 self.act_offsets 到 Settings（它现在在 Debug 面板里）

        # ---------- Debug Dock ----------
        self._init_debug_dock()
        # Debug 菜单里的“显示调试面板”开关（默认关闭）
        self.act_debug_show = QAction(self); self.act_debug_show.setCheckable(True)
        self.debug_menu.addAction(self.act_debug_show)
        self.act_debug_show.triggered.connect(lambda c: self.debug_dock.setVisible(bool(c)))
        self.debug_dock.visibilityChanged.connect(self.act_debug_show.setChecked)
        self.debug_dock.hide()
        self.act_debug_show.setChecked(False)

        # ---------- Mania INI Dock（与 Debug 完全分离） ----------
        self.mania_ini_dock = ManiaIniDock(self)
        self.addDockWidget(Qt.RightDockWidgetArea, self.mania_ini_dock)
        self.mania_ini_dock.hide()
        try:
            self.mania_ini_dock.keys_changed.connect(self._apply_mania_keys)
        except Exception:
            pass

        # MANIA SETTINGS 菜单里的开关（默认关闭）
        self.act_mania_show = QAction(self); self.act_mania_show.setCheckable(True)
        self.mania_menu.addAction(self.act_mania_show)
        self.act_mania_show.triggered.connect(lambda checked: self.mania_ini_dock.setVisible(bool(checked)))
        self.mania_ini_dock.visibilityChanged.connect(self.act_mania_show.setChecked)
        self.act_mania_show.setChecked(False)
        self.btn_mania.clicked.connect(lambda: self.mania_ini_dock.setVisible(not self.mania_ini_dock.isVisible()))

        self.mania_design_dock = ManiaDesignDock(self)
        self.addDockWidget(Qt.RightDockWidgetArea, self.mania_design_dock)
        self.tabifyDockWidget(self.mania_ini_dock, self.mania_design_dock)
        self.mania_design_dock.hide()
        self.mania_design_dock.design_changed.connect(self._apply_mania_design)
        self.mania_design_dock.exported.connect(self._design_exported)
        self.mania_design_dock.before_export = self._prepare_design_export
        self.act_design_show = QAction(self)
        self.act_design_show.triggered.connect(self._show_designer)
        self.mania_menu.addAction(self.act_design_show)
        self.btn_design.clicked.connect(self._show_designer)
        self.mania_playback.speed_changed.connect(self._change_mania_speed)
        self.mania_playback.tempo_changed.connect(self._change_mania_tempo)
        self.mania_playback.guide_changed.connect(self.mania_preview.set_show_hit_guide)
        self.mania_playback.restart_requested.connect(self.mania_preview.restart_demo)
        self.mania_playback.keys_requested.connect(self._request_mania_keys)
        self.mania_playback.speed.setValue(self.settings.value("preview/mania_speed", 20, int))
        self.mania_playback.tempo.setValue(self.settings.value("preview/mania_tempo", 120, int))
        self.mania_preview.set_scroll_speed(self.mania_playback.speed.value())
        self.mania_preview.set_demo_bpm(self.mania_playback.tempo.value())

        self._refresh_recent_menu()
        self.retranslate()
        self.statusBar().showMessage(i18n.t("status.ready", "Ready"))
        self._restore_or_default_geometry()
        self._sync_skin_ui()
        self.setAcceptDrops(True)

        # 强制启动时隐藏两个面板（即使恢复了上次布局）
        QTimer.singleShot(0, self._force_hide_debug_and_mania)

    def _build_workspace(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(20, 16, 20, 6)
        layout.setSpacing(16)
        header = TechPanel("Header")
        header_row = QHBoxLayout(header); header_row.setContentsMargins(18, 16, 18, 16)
        self.brand_avatar = AvatarBadge(72)
        header_row.addWidget(self.brand_avatar)
        header_row.setSpacing(16)
        titles = QVBoxLayout(); titles.setSpacing(3)
        eyebrow = QLabel("XIAOLAN  /  SKIN EDITOR"); eyebrow.setObjectName("Eyebrow")
        self.skin_title = QLabel(); self.skin_title.setObjectName("Title")
        self.skin_title.setTextFormat(Qt.PlainText)
        self.skin_title.setMinimumWidth(0); self.skin_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.skin_path = QLabel(); self.skin_path.setObjectName("SkinPath")
        self.skin_path.setTextFormat(Qt.PlainText)
        self.skin_path.setMinimumWidth(0); self.skin_path.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        titles.addWidget(eyebrow); titles.addWidget(self.skin_title); titles.addWidget(self.skin_path)
        header_row.addLayout(titles, 1)
        self.btn_folder = QPushButton(); self.btn_reload = QPushButton(); self.btn_open = QPushButton()
        self.btn_open.setObjectName("PrimaryButton")
        for button, icon in ((self.btn_folder, "folder"), (self.btn_reload, "reload"), (self.btn_open, "open")):
            button.setIcon(workspace_icon(icon)); header_row.addWidget(button)
        layout.addWidget(header)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(12)
        sidebar = TechPanel("Sidebar"); sidebar.setMinimumWidth(240)
        sidebar_layout = QVBoxLayout(sidebar); sidebar_layout.setContentsMargins(14, 16, 14, 14); sidebar_layout.setSpacing(12)
        list_heading = QHBoxLayout()
        self.library_label = QLabel(); self.library_label.setObjectName("SectionTitle")
        self.asset_count = QLabel("0"); self.asset_count.setObjectName("Counter")
        list_heading.addWidget(self.library_label); list_heading.addStretch(); list_heading.addWidget(self.asset_count)
        sidebar_layout.addLayout(list_heading)
        self.asset_search = QLineEdit(); self.asset_search.setClearButtonEnabled(True)
        self.asset_search.textChanged.connect(self._filter_assets)
        sidebar_layout.addWidget(self.asset_search)
        self.asset_filter = QComboBox()
        for key in ("all", "std", "mania", "other"): self.asset_filter.addItem(key, key)
        self.asset_filter.currentIndexChanged.connect(self._filter_assets)
        sidebar_layout.addWidget(self.asset_filter)
        self.asset_list = QListWidget(); self.asset_list.setIconSize(QSize(36, 36)); self.asset_list.setSpacing(1)
        self.asset_list.currentItemChanged.connect(self._inspect_asset)
        sidebar_layout.addWidget(self.asset_list, 1)
        self.no_results = QLabel(); self.no_results.setObjectName("Muted"); self.no_results.setWordWrap(True)
        sidebar_layout.addWidget(self.no_results)
        self.asset_inspector = AssetInspector(); sidebar_layout.addWidget(self.asset_inspector)
        self.asset_details = QLabel(); self.asset_details.setObjectName("Muted"); self.asset_details.setWordWrap(True); self.asset_details.setTextFormat(Qt.PlainText)
        sidebar_layout.addWidget(self.asset_details)
        tools = QHBoxLayout(); self.btn_images = QPushButton(); self.btn_audio = QPushButton()
        tools.addWidget(self.btn_images); tools.addWidget(self.btn_audio); sidebar_layout.addLayout(tools)
        self.splitter.addWidget(sidebar)
        preview_panel = TechPanel("PreviewPanel")
        preview_layout = QVBoxLayout(preview_panel); preview_layout.setContentsMargins(18, 16, 18, 14); preview_layout.setSpacing(12)
        preview_heading = QHBoxLayout()
        self.preview_label = QLabel(); self.preview_label.setObjectName("SectionTitle")
        self.preview_badge = QLabel(); self.preview_badge.setObjectName("Badge")
        preview_heading.addWidget(self.preview_label); preview_heading.addWidget(self.preview_badge); preview_heading.addStretch()
        self.btn_pause = QPushButton(); self.btn_pause.setCheckable(True); self.btn_pause.toggled.connect(self._set_paused)
        self.preview_zoom = QComboBox()
        for percent in (50, 75, 100, 125, 150, 200, 300):
            self.preview_zoom.addItem(f"{percent}%", percent / 100.0)
        self.preview_zoom.setCurrentIndex(2)
        self.preview_zoom.currentIndexChanged.connect(lambda: self.std_preview.set_preview_scale(self.preview_zoom.currentData()))
        preview_heading.addWidget(self.preview_zoom)
        self.btn_mania = QPushButton()
        self.btn_design = QPushButton()
        self.btn_design.setObjectName("PrimaryButton")
        preview_heading.addWidget(self.btn_pause); preview_heading.addWidget(self.btn_mania); preview_heading.addWidget(self.btn_design)
        preview_layout.addLayout(preview_heading)
        self.mania_playback = ManiaPreviewControls()
        preview_layout.addWidget(self.mania_playback)
        self.mania_playback.hide()
        self.preview_stack = QStackedWidget()
        welcome = WelcomeCanvas(); welcome_layout = QVBoxLayout(welcome); welcome_layout.setAlignment(Qt.AlignCenter); welcome_layout.setSpacing(16)
        welcome.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Ignored)
        self.welcome_avatar = AvatarBadge(144)
        welcome_layout.addWidget(self.welcome_avatar, 0, Qt.AlignHCenter)
        welcome_kicker = QLabel("XIAOLAN  /  SKIN WORKSPACE")
        welcome_kicker.setObjectName("SectionCode"); welcome_kicker.setAlignment(Qt.AlignCenter)
        welcome_layout.addWidget(welcome_kicker)
        self.welcome_title = QLabel(); self.welcome_title.setObjectName("WelcomeTitle"); self.welcome_title.setAlignment(Qt.AlignCenter)
        self.welcome_description = QLabel(); self.welcome_description.setObjectName("WelcomeDescription"); self.welcome_description.setAlignment(Qt.AlignCenter); self.welcome_description.setWordWrap(True)
        welcome_layout.addWidget(self.welcome_title); welcome_layout.addWidget(self.welcome_description)
        welcome_actions = QHBoxLayout(); welcome_actions.addStretch()
        self.btn_welcome_open = QPushButton(); self.btn_welcome_open.setObjectName("PrimaryButton")
        self.btn_welcome_osu = QPushButton()
        welcome_actions.addWidget(self.btn_welcome_open); welcome_actions.addWidget(self.btn_welcome_osu); welcome_actions.addStretch()
        welcome_layout.addLayout(welcome_actions)
        self.welcome_hint = QLabel(); self.welcome_hint.setObjectName("WelcomeHint"); self.welcome_hint.setAlignment(Qt.AlignCenter); self.welcome_hint.setWordWrap(True)
        welcome_layout.addWidget(self.welcome_hint)
        self.preview_stack.addWidget(welcome)
        self.tabs = QTabWidget()
        self.std_preview = StdPreview(); self.mania_preview = ManiaPreview()
        self.std_preview.setMinimumSize(380, 270); self.mania_preview.setMinimumSize(380, 270)
        self.tabs.addTab(self.std_preview, "osu!standard"); self.tabs.addTab(self.mania_preview, "osu!mania")
        self.tabs.currentChanged.connect(self._preview_mode_changed)
        self.preview_stack.addWidget(self.tabs)
        preview_layout.addWidget(self.preview_stack, 1)
        self.preview_note = QLabel(); self.preview_note.setObjectName("Muted"); self.preview_note.setWordWrap(True)
        preview_layout.addWidget(self.preview_note)
        self.splitter.addWidget(preview_panel); self.splitter.setStretchFactor(0, 0); self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([292, 920])
        layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)

    def _preview_mode_changed(self, index):
        self.preview_zoom.setVisible(index == 0)
        self.mania_playback.setVisible(index == 1 and self.skin is not None)
        self.btn_mania.setVisible(index == 1)
        self.btn_design.setVisible(index == 1)

    def _change_mania_speed(self, speed):
        self.mania_preview.set_scroll_speed(speed)
        self.settings.setValue("preview/mania_speed", speed)

    def _change_mania_tempo(self, tempo):
        self.mania_preview.set_demo_bpm(tempo)
        self.settings.setValue("preview/mania_tempo", tempo)

    def _show_designer(self):
        if self.skin is None:
            return
        self.tabs.setCurrentIndex(1)
        self.mania_design_dock.show()
        self.mania_design_dock.raise_()

    def _confirm_design_navigation(self):
        self._guarding_design = True
        try:
            return self.mania_design_dock.confirm_discard_if_dirty()
        finally:
            self._guarding_design = False

    def _apply_mania_design(self, design):
        try:
            self.mania_preview.set_design_options(design)
        except (ValueError, OSError) as error:
            self.mania_design_dock.set_preview_error(str(error))
        else:
            self.mania_design_dock.set_preview_error("")

    def _prepare_design_export(self):
        if not self.mania_ini_dock._confirm_discard_if_dirty():
            return False
        # Refresh disk values without clearing the virtual design being exported.
        current_keys = self.mania_design_dock._keys
        self._syncing_mania_keys = True
        try:
            self.mania_ini_dock._current_view_k = current_keys
            self.mania_ini_dock.set_skin_root(self.skin.root, preferred_keys=current_keys)
            self.mania_preview.set_keys(current_keys)
            self._apply_mania_design(self.mania_design_dock.options())
        finally:
            self._syncing_mania_keys = False
        return True

    def _design_exported(self, directory):
        if getattr(self, "_guarding_design", False):
            self.statusBar().showMessage(i18n.t("status.loaded", "Loaded: {path}").format(path=directory), 7000)
            return
        selected_keys = self.mania_design_dock._keys
        self.load_skin(directory, check_dirty=False)
        self._request_mania_keys(selected_keys)
        self.tabs.setCurrentIndex(1)

    def _refresh_playback_keys(self):
        available = self.mania_ini_dock._skin_ini.available_mania_keys() if self.mania_ini_dock._skin_ini else []
        self.mania_playback.set_keys(available, self.mania_preview.keys)

    def _request_mania_keys(self, keys):
        index = self.mania_ini_dock.cmb_keys.findData(keys)
        if index >= 0:
            self.mania_ini_dock.cmb_keys.setCurrentIndex(index)
        else:
            self._apply_mania_keys(keys)
        self._refresh_playback_keys()

    def _set_paused(self, paused):
        self.std_preview.set_playing(not paused)
        self.mania_preview.set_playing(not paused)
        self.btn_pause.setText(i18n.t("workspace.resume" if paused else "workspace.pause"))

    def _sync_skin_ui(self):
        loaded = self.skin is not None
        self.preview_stack.setCurrentIndex(1 if loaded else 0)
        for control in (self.btn_folder, self.btn_reload, self.btn_images, self.btn_audio, self.btn_pause,
                        self.btn_mania, self.btn_design, self.mania_playback, self.act_design_show,
                        self.preview_zoom, self.asset_search, self.asset_filter, self.act_reload,
                        self.act_assets_images, self.act_assets_audio, self.act_mania_show, self.act_debug_show):
            control.setEnabled(loaded)
        self.act_open_last.setEnabled(bool(self.settings.value("paths/last_skin", "", str)))
        if loaded:
            general = next((s for s in self.skin.ini.sections() if s.casefold() == "general"), None)
            info = {k.lower(): v for k, v in self.skin.ini.items(general)} if general else {}
            self.skin_title.setText(info.get("name") or self.skin.root.name)
            self.skin_path.setText(str(self.skin.root)); self.skin_path.setToolTip(str(self.skin.root))
        else:
            self.skin_title.setText(i18n.t("workspace.title"))
            self.skin_path.setText(i18n.t("workspace.subtitle"))
        self._filter_assets()
        self._preview_mode_changed(self.tabs.currentIndex())

    def _filter_assets(self, *args):
        needle = self.asset_search.text().strip().casefold()
        category = self.asset_filter.currentData()
        visible = 0
        for n in range(self.asset_list.count()):
            item = self.asset_list.item(n)
            name = item.data(Qt.UserRole).casefold()
            group = "mania" if name.startswith("mania-") else ("std" if name.startswith(("hit", "cursor", "slider", "approach", "default-", "score-", "reversearrow", "followpoint", "spinner")) else "other")
            show = (category == "all" or group == category) and needle in item.text().casefold()
            item.setHidden(not show); visible += int(show)
        self.asset_count.setText(f"{visible} / {self.asset_list.count()}")
        self.no_results.setText(i18n.t("workspace.no_results" if self.skin else "workspace.library_empty"))
        self.no_results.setVisible(visible == 0)
        selected = self.asset_list.currentItem()
        if selected and selected.isHidden():
            self.asset_list.setCurrentRow(-1)

    def _inspect_asset(self, current=None, previous=None):
        asset = self.skin.assets.get(current.data(Qt.UserRole)) if current and self.skin else None
        self.asset_inspector.set_asset(asset.path if asset else None)
        if not asset:
            self.asset_details.setText(""); return
        size = self.asset_inspector.image_size
        self.asset_details.setText(f"{asset.path.name}\n{size.width()} × {size.height()} px  ·  {'@2x' if asset.scale == 2 else '1x'}")

    def _reveal_skin(self):
        if self.skin: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.skin.root)))

    @staticmethod
    def _dropped_skin(mime):
        urls = mime.urls() if mime.hasUrls() else []
        if len(urls) != 1 or not urls[0].isLocalFile(): return None
        path = Path(urls[0].toLocalFile())
        if path.is_file() and path.name.lower() == "skin.ini": path = path.parent
        return path if path.is_dir() and (path / "skin.ini").is_file() else None

    def dragEnterEvent(self, event):
        if self._dropped_skin(event.mimeData()): event.acceptProposedAction()

    def dropEvent(self, event):
        path = self._dropped_skin(event.mimeData())
        if path and self.load_skin(str(path)): event.acceptProposedAction()

    def _force_hide_debug_and_mania(self):
        try:
            self.debug_dock.hide(); self.act_debug_show.setChecked(False)
        except Exception: pass
        try:
            self.mania_ini_dock.hide(); self.act_mania_show.setChecked(False)
        except Exception: pass
        self.mania_design_dock.hide()

    def _init_debug_dock(self):
        dock = QDockWidget("Debug", self); dock.setObjectName("DebugDock")
        w = QWidget(dock); dock.setWidget(w); self.addDockWidget(Qt.RightDockWidgetArea, dock)

        grid = QGridLayout(w)
        r = 0
        self.chk_show_centers = QCheckBox("Show centers", w); grid.addWidget(self.chk_show_centers, r, 0, 1, 2); r += 1
        grid.addWidget(QLabel("Sample digit:"), r, 0); self.sp_digit = QSpinBox(w); self.sp_digit.setRange(0,9); self.sp_digit.setValue(6); grid.addWidget(self.sp_digit, r, 1); r += 1
        self.chk_link_num = QCheckBox("Link number with circle", w); self.chk_link_num.setChecked(True); grid.addWidget(self.chk_link_num, r, 0, 1, 2); r += 1

        def add_row(title):
            nonlocal r
            grid.addWidget(QLabel(title), r, 0, 1, 2); r += 1
            sbx = QSpinBox(w); sby = QSpinBox(w)
            sbx.setRange(-128,128); sby.setRange(-128,128)
            grid.addWidget(QLabel("X:"), r, 0); grid.addWidget(sbx, r, 1); r += 1
            grid.addWidget(QLabel("Y:"), r, 0); grid.addWidget(sby, r, 1); r += 1
            return sbx, sby

        self.sb_hit_x, self.sb_hit_y = add_row("HitCircle (circle) offset")
        self.sb_ovl_x, self.sb_ovl_y = add_row("Overlay offset (relative to circle)")
        self.sb_num_x, self.sb_num_y = add_row("Number offset")
        self.sb_app_x, self.sb_app_y = add_row("Approach offset")

        # buttons
        btn_row = QHBoxLayout()
        self.btn_reset = QPushButton("Reset")
        self.btn_save = QPushButton("Save")
        btn_row.addWidget(self.btn_reset); btn_row.addWidget(self.btn_save)
        grid.addLayout(btn_row, r, 0, 1, 2); r += 1

        def live_apply():
            d = {
                "hit_dx": self.sb_hit_x.value(), "hit_dy": self.sb_hit_y.value(),
                "ovl_dx": self.sb_ovl_x.value(), "ovl_dy": self.sb_ovl_y.value(),
                "num_dx": self.sb_num_x.value(), "num_dy": self.sb_num_y.value(),
                "approach_dx": self.sb_app_x.value(), "approach_dy": self.sb_app_y.value(),
                "link_num": 1 if self.chk_link_num.isChecked() else 0,
            }
            self.std_preview.set_user_offsets(d)
            self.std_preview.set_debug_config({
                "show_centers": self.chk_show_centers.isChecked(),
                "sample_digit": self.sp_digit.value()
            })

        for sb in (self.sb_hit_x, self.sb_hit_y, self.sb_ovl_x, self.sb_ovl_y, self.sb_num_x, self.sb_num_y, self.sb_app_x, self.sb_app_y):
            sb.valueChanged.connect(live_apply)
        self.chk_link_num.toggled.connect(live_apply)
        self.chk_show_centers.toggled.connect(live_apply)
        self.sp_digit.valueChanged.connect(live_apply)

        def do_reset():
            for sb in (self.sb_hit_x, self.sb_hit_y, self.sb_ovl_x, self.sb_ovl_y, self.sb_num_x, self.sb_num_y, self.sb_app_x, self.sb_app_y):
                sb.setValue(0)
            self.chk_link_num.setChecked(True)
            live_apply()

        def do_save():
            if not self.skin: return
            sid = self._skin_id()
            data = {
                "hit_dx": self.sb_hit_x.value(), "hit_dy": self.sb_hit_y.value(),
                "ovl_dx": self.sb_ovl_x.value(), "ovl_dy": self.sb_ovl_y.value(),
                "num_dx": self.sb_num_x.value(), "num_dy": self.sb_num_y.value(),
                "approach_dx": self.sb_app_x.value(), "approach_dy": self.sb_app_y.value(),
                "link_num": 1 if self.chk_link_num.isChecked() else 0,
            }
            self.settings.setValue(f"std_offsets/{sid}", data)
            self.statusBar().showMessage("Saved offsets for this skin", 2000)

        self.btn_reset.clicked.connect(do_reset)
        self.btn_save.clicked.connect(do_save)

        self.debug_dock = dock
        self._refresh_debug_panel_from_settings()

    def _refresh_debug_panel_from_settings(self):
        if not hasattr(self, "sb_hit_x"): return
        sid = self._skin_id()
        val = self.settings.value(f"std_offsets/{sid}", {})
        d = {}
        try:
            d = {k:int(v) for k,v in dict(val).items()}
        except Exception:
            if isinstance(val, dict):
                for k,v in val.items():
                    try: d[k] = int(v)
                    except Exception: pass
        if "hit_dx" not in d and "base_dx" in d: d["hit_dx"] = d.get("base_dx", 0)
        if "hit_dy" not in d and "base_dy" in d: d["hit_dy"] = d.get("base_dy", 0)
        d.setdefault("hit_dx", 0); d.setdefault("hit_dy", 0)
        d.setdefault("ovl_dx", 0); d.setdefault("ovl_dy", 0)
        d.setdefault("num_dx", 0); d.setdefault("num_dy", 0)
        d.setdefault("approach_dx", 0); d.setdefault("approach_dy", 0)
        d.setdefault("link_num", 1)

        self.sb_hit_x.setValue(d["hit_dx"]); self.sb_hit_y.setValue(d["hit_dy"])
        self.sb_ovl_x.setValue(d["ovl_dx"]); self.sb_ovl_y.setValue(d["ovl_dy"])
        self.sb_num_x.setValue(d["num_dx"]); self.sb_num_y.setValue(d["num_dy"])
        self.sb_app_x.setValue(d["approach_dx"]); self.sb_app_y.setValue(d["approach_dy"])
        self.chk_link_num.setChecked(bool(d["link_num"]))

        self.std_preview.set_user_offsets(d)

    # ---------- Window geometry ----------
    def _restore_or_default_geometry(self):
        g = self.settings.value("ui/geometry")
        s = self.settings.value("ui/state")
        if isinstance(g, QByteArray) and not g.isEmpty():
            self.restoreGeometry(g)
            if isinstance(s, QByteArray) and not s.isEmpty():
                self.restoreState(s)
        else:
            self.resize(1280, 800)

    def closeEvent(self, event):
        if not self.mania_ini_dock._confirm_discard_if_dirty():
            event.ignore()
            return
        if not self._confirm_design_navigation():
            event.ignore()
            return
        # 仍保存窗口布局，但界面启动后会强制隐藏两个调试类面板
        self.settings.setValue("ui/geometry", self.saveGeometry())
        self.settings.setValue("ui/state", self.saveState())
        super().closeEvent(event)

    
    def _apply_mania_keys(self, k: int):
        if getattr(self, "_syncing_mania_keys", False):
            return
        previous = self.mania_preview.keys
        self._syncing_mania_keys = True
        try:
            if hasattr(self, "mania_design_dock") and not getattr(self, "_loading_skin", False):
                self._guarding_design = True
                try:
                    accepted = self.mania_design_dock.set_keys(int(k))
                finally:
                    self._guarding_design = False
                if not accepted:
                    self.mania_ini_dock._current_view_k = previous
                    self.mania_ini_dock._reselect_current_k_in_widgets()
                    self.mania_ini_dock._load_values_for_current_keys()
                    self._refresh_playback_keys()
                    return
            self.mania_preview.set_keys(int(k))
            if hasattr(self, "mania_design_dock") and not getattr(self, "_loading_skin", False):
                self._apply_mania_design(self.mania_design_dock.options())
            if self.mania_ini_dock._current_view_k != int(k):
                self.mania_ini_dock._current_view_k = int(k)
                self.mania_ini_dock._reselect_current_k_in_widgets()
                self.mania_ini_dock._load_values_for_current_keys()
            self._refresh_playback_keys()
        finally:
            self._syncing_mania_keys = False
    # ---------- i18n ----------

    def _show_author_info_dialog(self):
            # ---------- xiaolan ----------
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox
        from PySide6.QtCore import Qt
        dlg = QDialog(self)
        dlg.setWindowTitle(i18n.t("about.author_title", "作者信息"))
        lay = QVBoxLayout(dlg)
        html = (
            "<b>作者：小蓝（XiaoLanツ / XiaoLan9999）</b><br>"
            "谢谢你使用本工具，欢迎反馈与交流！<br>"
            "后续还会进行更新<br>"
            "（Std的问题太多了暂时搞不完）<br>"
            "<b>有建议请直接告诉我！！<b>"
        )
        lab = QLabel(html, dlg)
        lab.setTextFormat(Qt.RichText)
        lab.setOpenExternalLinks(True)
        lab.setWordWrap(True)
        lay.addWidget(lab)
        btns = QDialogButtonBox(QDialogButtonBox.Ok, parent=dlg)
        btns.accepted.connect(dlg.accept)
        lay.addWidget(btns)
        dlg.exec()

    def retranslate(self):
        self.setWindowTitle(i18n.t("app.title", "Osu XiaoLan Skin Editor "))
        self.file_menu.setTitle(i18n.t("menu.file", "File"))
        self.settings_menu.setTitle(i18n.t("menu.settings", "Settings"))
        self.debug_menu.setTitle(i18n.t("menu.std_settings", "Std 设置(仍在开发中)"))
        self.mania_menu.setTitle(i18n.t("menu.mania_settings", "Mania 设置"))
        self.recent_menu.setTitle(i18n.t("menu.recent_skins", "Recent skins"))
        self.lang_menu.setTitle("语言 / Language")
        self.center_menu.setTitle(i18n.t("menu.centering", "Centering"))

        self.author_menu.setTitle(i18n.t("menu.author", "作者"))
        self.act_about_author.setText(i18n.t("action.about_author", "作者信息…"))
        self.assets_menu.setTitle(i18n.t('menu.assets', '皮肤文件小工具'))
        self.act_assets_images.setText(i18n.t('action.assets_images', '图片管理…'))
        self.act_assets_audio.setText(i18n.t('action.assets_audio', '音频管理…'))

        self.act_link_github.setText(i18n.t("links.github", "Github项目地址"))
        self.act_link_steam.setText(i18n.t("links.steam", "Steam个人主页"))
        self.act_link_bilibili.setText(i18n.t("links.bilibili", "B站个人主页"))
        self.act_link_blog.setText(i18n.t("links.blog", "个人博客"))

        self.act_open.setText(i18n.t("action.open_skin_folder", "Open Skin Folder…"))
        self.act_open_osu.setText(i18n.t("action.open_osu_skins", "Open osu! Skins…"))
        self.act_open_last.setText(i18n.t("action.open_last_skin", "Open Last Skin"))
        self.act_reload.setText(i18n.t("action.reload", "Reload"))
        self.act_set_osu.setText(i18n.t("action.set_osu_folder", "Set osu! Folder…"))
        self.act_quit.setText(i18n.t("action.exit", "Exit"))
        for action in self.lang_group.actions():
            action.setChecked(action.data() == i18n.lang())
        self.act_center_image.setText(i18n.t("action.center_image", "Approach center: Image"))
        self.act_center_alpha.setText(i18n.t("action.center_alpha", "Approach center: Alpha"))

        # actions under menus
        self.act_debug_show.setText(i18n.t("action.debug_show", "显示 STD 调试面板"))
        self.act_mania_show.setText(i18n.t("action.mania_ini", "Mania INI 调试面板"))

        # tabs
        self.tabs.setTabText(0, i18n.t("tab.std", "STD"))
        self.tabs.setTabText(1, i18n.t("tab.mania", "MANIA"))
        for widget, key in (
            (self.btn_open, "open"), (self.btn_welcome_open, "open"), (self.btn_folder, "folder"),
            (self.btn_reload, "reload"), (self.btn_images, "images"), (self.btn_audio, "audio"),
            (self.btn_mania, "mania_settings"), (self.library_label, "library"),
            (self.preview_label, "preview"), (self.preview_badge, "demo"),
            (self.welcome_title, "welcome_title"), (self.welcome_description, "welcome_description"),
            (self.welcome_hint, "welcome_hint"), (self.btn_welcome_osu, "osu_skins"),
            (self.preview_note, "preview_note"),
        ):
            widget.setText(i18n.t("workspace." + key))
        self.asset_search.setPlaceholderText(i18n.t("workspace.search"))
        self.btn_design.setText(i18n.t("workspace.design", "皮肤设计"))
        self.act_design_show.setText(i18n.t("workspace.design", "皮肤设计"))
        self.mania_playback.retranslate()
        self.mania_design_dock.retranslate()
        self.preview_zoom.setToolTip(i18n.t("workspace.zoom", "Standard preview zoom"))
        for index, key in enumerate(("all", "std", "mania", "other")):
            self.asset_filter.setItemText(index, i18n.t("workspace.filter_" + key))
        self.asset_inspector.placeholder = i18n.t("workspace.select_asset")
        self._set_paused(self.btn_pause.isChecked())
        self._inspect_asset(self.asset_list.currentItem())
        self._sync_skin_ui()

        # propagate to Mania dock
        try:
            self.mania_ini_dock.retranslate()
        except Exception:
            pass

    def on_change_language(self, code: str):
        i18n.load_language(code)
        self.retranslate()

    def on_set_center_mode(self, mode: str):
        self.settings.setValue("ui/approach_center_mode", mode)
        self.std_preview.set_approach_center_mode(mode)
        self.statusBar().showMessage("Center mode: " + mode, 2000)

    # ---------- Helpers ----------
    def _skin_id(self) -> str:
        if not self.skin: return ""
        try: return str(Path(self.skin.root).resolve())
        except Exception: return str(self.skin.root)

    def _load_osu_root(self) -> Path:
        val = self.settings.value("paths/osu_root","",str)
        if val and Path(val).exists(): return Path(val)
        cands=[]; user=os.environ.get("USERPROFILE") or ""
        if user: cands+=[Path(user)/"AppData"/"Local"/"osu!"]
        for d in "CDEFGHIJKLMNOPQRSTUVWXYZ": cands+=[Path(f"{d}:/osu!")]
        for p in cands:
            try:
                if (p/"Skins").exists(): return p
            except Exception: pass
        return Path("")

    def _start_dir_for_dialog(self) -> str:
        last=self.settings.value("paths/last_skin","",str)
        if last and Path(last).exists(): return last
        if self.osu_root and (self.osu_root/"Skins").exists(): return str(self.osu_root/"Skins")
        return os.path.expanduser("~")

    def _remember_last_skin(self, directory:str):
        self.settings.setValue("paths/last_skin", directory)
        rec=list(self.settings.value("recent/skins",[],list))
        if directory in rec: rec.remove(directory)
        rec.insert(0,directory); rec=rec[:RECENT_LIMIT]; self.settings.setValue("recent/skins", rec); self._refresh_recent_menu()

    def _remember_osu_root(self, path:str):
        self.osu_root=Path(path); self.settings.setValue("paths/osu_root", str(path))

    def _refresh_recent_menu(self):
        self.recent_menu.clear(); rec=list(self.settings.value("recent/skins",[],list))
        for p in rec:
            act=self.recent_menu.addAction(p); act.triggered.connect(lambda checked=False, pp=p: self.load_skin(pp))

    def on_open_generic(self):
        start=self._start_dir_for_dialog(); d=QFileDialog.getExistingDirectory(self, i18n.t("dialog.select_skin", "Select skin folder"), start)
        if d: self.load_skin(d)

    def on_open_osu_skins(self):
        if not (self.osu_root and (self.osu_root/"Skins").exists()):
            self.on_set_osu_root()
            if not (self.osu_root and (self.osu_root/"Skins").exists()): return
        start=str(self.osu_root/"Skins"); d=QFileDialog.getExistingDirectory(self, i18n.t("dialog.select_osu_skin", "Select an osu! skin"), start)
        if d: self.load_skin(d)

    def on_open_last_skin(self):
        last=self.settings.value("paths/last_skin","",str)
        if last and Path(last).exists(): self.load_skin(last)

    def on_set_osu_root(self):
        start=str(self.osu_root) if self.osu_root else os.path.expanduser("~")
        d=QFileDialog.getExistingDirectory(self, i18n.t("dialog.select_osu_folder", "Select osu! folder"), start)
        if d:
            if not (Path(d)/"Skins").exists():
                QMessageBox.warning(self, i18n.t("dialog.not_osu_title", "Not osu!"), i18n.t("dialog.not_osu_msg", "This folder does not contain a 'Skins' subfolder")); return
            self._remember_osu_root(d); self.statusBar().showMessage(i18n.t("status.osu_set", "osu! folder set: {path}").format(path=d), 5000)

    def load_skin(self, directory: str, check_dirty=True):
        # A failed folder selection must preserve the current workspace.
        try: candidate = self.loader.load(directory)
        except Exception as e:
            QMessageBox.critical(self, i18n.t("workspace.load_error"), str(e)); return False
        if check_dirty and not self.mania_ini_dock._confirm_discard_if_dirty():
            return False
        if check_dirty and not self._confirm_design_navigation():
            return False
        # Confirmation may have saved the same INI; load its current contents.
        try: candidate = self.loader.load(directory)
        except Exception as e:
            QMessageBox.critical(self, i18n.t("workspace.load_error"), str(e)); return False
        selected = self.asset_list.currentItem()
        selected_name = selected.data(Qt.UserRole) if selected else None
        self.skin = candidate
        self.asset_list.clear()
        for name in sorted(self.skin.assets, key=str.casefold):
            asset = self.skin.assets[name]
            item = QListWidgetItem(asset.path.name)
            item.setData(Qt.UserRole, name); item.setToolTip(str(asset.path))
            reader = QImageReader(str(asset.path)); size = reader.size()
            if size.isValid(): reader.setScaledSize(size.scaled(48, 48, Qt.KeepAspectRatio))
            item.setIcon(QIcon(QPixmap.fromImage(reader.read())))
            item.setSizeHint(QSize(180, 48))
            self.asset_list.addItem(item)
            if name == selected_name: self.asset_list.setCurrentItem(item)
        self._loading_skin = True
        self.std_preview.set_skin(self.skin); self.mania_preview.set_skin(self.skin)
        # 让 Mania INI dock 知道当前皮肤根目录
        try:
            root_path = getattr(self.skin, "root", None)
            if root_path:
                self.mania_ini_dock.set_skin_root(root_path, preferred_keys=self.skin.mode_keys)
        except Exception:
            pass
        self._loading_skin = False
        self.mania_design_dock.set_skin(self.skin, self.mania_preview.keys, force=True)
        self._refresh_playback_keys()
        self.statusBar().showMessage(i18n.t("status.loaded", "Loaded: {path}").format(path=directory), 5000)
        self._remember_last_skin(directory)
        self._refresh_debug_panel_from_settings()
        self._sync_skin_ui()
        return True

    def reload_skin(self):
        if not self.skin: return
        self.load_skin(str(self.skin.root))


    def _toggle_mania_ini(self, on: bool):
        # Lazily create dock
        if self.mania_ini_dock is None:
            try:
                from ui.mania_ini_dock import ManiaIniDock
            except Exception:
                return
            self.mania_ini_dock = ManiaIniDock(self)
            self.addDockWidget(Qt.RightDockWidgetArea, self.mania_ini_dock)
            # Do not show by default
            self.mania_ini_dock.hide()
            # Connect preview lane update
            if hasattr(self, 'mania_preview') and hasattr(self.mania_preview, 'set_keys'):
                self.mania_ini_dock.keys_changed.connect(self.mania_preview.set_keys)
        self.mania_ini_dock.setVisible(on)
        self.act_mania_show.setChecked(on)
