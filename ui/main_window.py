# -*- coding: utf-8 -*-
import os
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QFileDialog, QSplitter, QListWidget, QWidget, QVBoxLayout, QTabWidget,
    QMessageBox, QMenu, QDockWidget, QPushButton, QHBoxLayout, QGridLayout, QLabel, QSpinBox, QCheckBox, QDialog, QDialogButtonBox
)
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices
from PySide6.QtCore import Qt, QSettings, QByteArray, QTimer, QUrl
from core.app_links import get_links
from core import i18n

from core.skin_loader import SkinLoader
from ui.preview.std_preview import StdPreview
from ui.preview.mania_preview import ManiaPreview
from ui.mania_ini_dock import ManiaIniDock
from core import i18n

RECENT_LIMIT = 12


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        i18n.load_language()

        self.skin = None
        self.loader = SkinLoader()
        self.settings = QSettings()
        self.osu_root = self._load_osu_root()

        # ---------- Central UI ----------
        splitter = QSplitter(Qt.Horizontal, self)
        self.asset_list = QListWidget()
        self.asset_list.setMinimumWidth(280)

        right = QWidget()
        right_layout = QVBoxLayout(right)

        self.tabs = QTabWidget()
        self.std_preview = StdPreview()
        self.mania_preview = ManiaPreview()
        self.std_preview.setMinimumSize(800, 520)
        self.mania_preview.setMinimumSize(800, 520)
        self.tabs.addTab(self.std_preview, "")
        self.tabs.addTab(self.mania_preview, "")
        right_layout.addWidget(self.tabs)

        splitter.addWidget(self.asset_list)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 960])
        self.setCentralWidget(splitter)

        # ---------- Menus & Actions ----------
        menubar = self.menuBar()
        self.file_menu = menubar.addMenu("")
        self.settings_menu = menubar.addMenu("")
        self.debug_menu = menubar.addMenu("")      # Debug 顶栏
        self.mania_menu = menubar.addMenu("")      # MANIA SETTINGS 顶栏

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

        # 连接作者链接动作（点击后在浏览器打开）
        self.act_link_github.triggered.connect(lambda: QDesktopServices.openUrl(QUrl('https://github.com/XiaoLan9999/OsuSkinEditor')))
        self.act_link_steam.triggered.connect(lambda: QDesktopServices.openUrl(QUrl('https://steamcommunity.com/profiles/76561198969998874/')))
        self.act_link_bilibili.triggered.connect(lambda: QDesktopServices.openUrl(QUrl('https://space.bilibili.com/325569826')))
        self.act_link_blog.triggered.connect(lambda: QDesktopServices.openUrl(QUrl('https://blog.xiaolan9999.net/')))
        self.act_about_author.triggered.connect(self._show_author_info_dialog)


        # language menu
        self.lang_menu = QMenu(self)
        self.act_lang_en = QAction(self); self.act_lang_en.triggered.connect(lambda: self.on_change_language("en-US"))
        self.act_lang_zh = QAction(self); self.act_lang_zh.triggered.connect(lambda: self.on_change_language("zh-CN"))
        self.lang_menu.addAction(self.act_lang_en); self.lang_menu.addAction(self.act_lang_zh)

        # centering menu
        self.center_menu = QMenu(self)
        self.center_group = QActionGroup(self); self.center_group.setExclusive(True)
        self.act_center_image = QAction(self); self.act_center_image.setCheckable(True)
        self.act_center_alpha = QAction(self); self.act_center_alpha.setCheckable(True)
        self.center_group.addAction(self.act_center_image); self.center_group.addAction(self.act_center_alpha)
        self.author_menu.addAction(self.act_about_author)

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
        self.settings_menu.addMenu(self.lang_menu); self.settings_menu.addMenu(self.center_menu)
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

        self._refresh_recent_menu()
        self.retranslate()
        self.statusBar().showMessage(i18n.t("status.ready", "Ready"))
        self._restore_or_default_geometry()

        # 强制启动时隐藏两个面板（即使恢复了上次布局）
        QTimer.singleShot(0, self._force_hide_debug_and_mania)

    def _force_hide_debug_and_mania(self):
        try:
            self.debug_dock.hide(); self.act_debug_show.setChecked(False)
        except Exception: pass
        try:
            self.mania_ini_dock.hide(); self.act_mania_show.setChecked(False)
        except Exception: pass

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
            self.statusBar().showMessage("Saved offsets for this skin.", 2000)

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
        # 仍保存窗口布局，但界面启动后会强制隐藏两个调试类面板
        self.settings.setValue("ui/geometry", self.saveGeometry())
        self.settings.setValue("ui/state", self.saveState())
        super().closeEvent(event)

    
    def _apply_mania_keys(self, k: int):
        # Try several common method names on ManiaPreview to set lane count.
        for name in ("set_keys", "setKeyCount", "set_keys_count", "set_lane_count", "setLanes"):
            if hasattr(self.mania_preview, name):
                try:
                    getattr(self.mania_preview, name)(int(k))
                    return
                except Exception:
                    pass
        # Fallback: set attribute and update
        try:
            setattr(self.mania_preview, "keys", int(k))
        except Exception:
            pass
        try:
            self.mania_preview.update()
        except Exception:
            pass
    # ---------- i18n ----------

    def _show_author_info_dialog(self):
            # ---------- xiaolan ----------
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox
        from PySide6.QtCore import Qt
        dlg = QDialog(self)
        dlg.setWindowTitle(i18n.t("about.author_title", "作者信息"))
        lay = QVBoxLayout(dlg)
        html = (
            "当前版本 v1.0<br>"
            "<b>作者：小蓝（XiaoLanツ / XiaoLan9999）</b><br>"
            "谢谢你使用本工具，欢迎反馈与交流！<br>"
            "后续还会进行更新<br>"
            "（Std的问题太多了暂时搞不完）<br>"
            "有建议请直接告诉我！！<br>"
            "<b>喜欢本工具就在Bilibili上关注我吧！</b><br>"
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
        self.lang_menu.setTitle(i18n.t("menu.language", "Language"))
        self.center_menu.setTitle(i18n.t("menu.centering", "Centering"))

        self.author_menu.setTitle(i18n.t("menu.author", "作者"))
        self.act_about_author.setText(i18n.t("action.about_author", "作者信息…"))

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
        self.act_lang_en.setText(i18n.t("action.lang_en", "English"))
        self.act_lang_zh.setText(i18n.t("action.lang_zh", "简体中文"))

        # actions under menus
        self.act_debug_show.setText(i18n.t("action.debug_show", "显示 STD 调试面板"))
        self.act_mania_show.setText(i18n.t("action.mania_ini", "Mania INI Editor"))

        # tabs
        self.tabs.setTabText(0, i18n.t("tab.std", "STD"))
        self.tabs.setTabText(1, i18n.t("tab.mania", "MANIA"))

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
                QMessageBox.warning(self, i18n.t("dialog.not_osu_title", "Not osu!"), i18n.t("dialog.not_osu_msg", "This folder does not contain a 'Skins' subfolder.")); return
            self._remember_osu_root(d); self.statusBar().showMessage(i18n.t("status.osu_set", "osu! folder set: {path}").format(path=d), 5000)

    def load_skin(self, directory:str):
        try: self.skin=self.loader.load(directory)
        except Exception as e:
            QMessageBox.critical(self,"Load Error",f"Failed to load skin: {e}"); return
        self.asset_list.clear()
        for name in sorted(self.skin.assets.keys()):
            a=self.skin.assets[name]; self.asset_list.addItem(f"{name}  ({a.scale}x) - {a.path.name}")
        self.std_preview.set_skin(self.skin); self.mania_preview.set_skin(self.skin)
        # 让 Mania INI dock 知道当前皮肤根目录
        try:
            root_path = getattr(self.skin, "root", None)
            if root_path:
                self.mania_ini_dock.set_skin_root(root_path)
        except Exception:
            pass
        self.statusBar().showMessage(i18n.t("status.loaded", "Loaded: {path}").format(path=directory), 5000)
        self._remember_last_skin(directory)
        self._refresh_debug_panel_from_settings()

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
        self.act_mania_ini.setChecked(on)
