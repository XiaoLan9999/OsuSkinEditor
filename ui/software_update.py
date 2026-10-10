"""Compact two-channel check with one-click verified download and restart."""
from dataclasses import dataclass
from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QProgressBar, QCheckBox, QToolButton, QWidget)
from core import i18n
from core.app_version import VERSION
from core.update_launch import installed_executable
from ui.widgets.wheel_guard import ClickWheelComboBox


@dataclass
class _ReleaseRow:
    title: QLabel
    status: QLabel
    button: QPushButton
    release: dict | None = None
    latest: dict | None = None
    checked: bool = False
    failed: bool = False


class SoftwareUpdateDialog(QDialog):
    update_requested = Signal(object)
    install_requested = Signal()
    cancel_install_requested = Signal()

    def __init__(self, service, settings, parent=None, *, can_install=None):
        super().__init__(parent)
        self.setModal(False)
        self.service, self.settings = service, settings
        self.can_install = installed_executable() is not None if can_install is None else can_install
        self.release = None
        self.preparing = False
        self.committed = False
        self._closed = False
        self._active_release = None
        self._auto_install = False
        self._install_emitted = False
        self._status_key = "updater.checking"
        self._source_kind = self._source_domain = None
        self.resize(460, 270)
        self.setMinimumWidth(420)
        self.setMaximumWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        self.heading = QLabel()
        self.heading.setObjectName("Title")
        self.heading.setWordWrap(True)
        self.check_button = QToolButton()
        self.check_button.clicked.connect(self.check)
        header.addWidget(self.heading)
        header.addStretch()
        header.addWidget(self.check_button)
        layout.addLayout(header)
        self.current = QLabel()
        self.current.setObjectName("Muted")
        layout.addWidget(self.current)
        self.rows = {}
        for channel in ("preview", "stable"):
            line = QHBoxLayout()
            title, status, button = QLabel(), QLabel(), QPushButton()
            title.setMinimumWidth(62)
            status.setTextFormat(Qt.PlainText)
            status.setWordWrap(True)
            status.setTextInteractionFlags(Qt.TextSelectableByMouse)
            button.setObjectName("PrimaryButton")
            button.setMinimumWidth(76)
            button.clicked.connect(lambda _checked=False, value=channel: self._request_update(value))
            line.addWidget(title)
            line.addWidget(status, 1)
            line.addWidget(button)
            layout.addLayout(line)
            self.rows[channel] = _ReleaseRow(title, status, button)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.current_source = QLabel()
        self.current_source.setObjectName("Muted")
        self.current_source.setTextFormat(Qt.PlainText)
        self.current_source.setWordWrap(True)
        layout.addWidget(self.current_source)
        footer = QHBoxLayout()
        self.advanced_button = QToolButton()
        self.advanced_button.setCheckable(True)
        self.advanced_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.advanced_button.setArrowType(Qt.RightArrow)
        self.advanced_button.toggled.connect(self._toggle_advanced)
        self.cancel_button = QPushButton()
        self.cancel_button.clicked.connect(self._cancel)
        footer.addWidget(self.advanced_button)
        footer.addStretch()
        footer.addWidget(self.cancel_button)
        layout.addLayout(footer)
        self.advanced_panel = QWidget()
        advanced = QVBoxLayout(self.advanced_panel)
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.setSpacing(8)
        source_row = QHBoxLayout()
        self.source_label = QLabel()
        self.source_mode = ClickWheelComboBox()
        for value in ("auto", "github", "ghfast", "ghproxy"):
            self.source_mode.addItem(value, value)
        saved_source = settings.value("updates/source_mode", "auto", str)
        saved_source = saved_source if self.source_mode.findData(saved_source) >= 0 else "auto"
        if service.set_source_mode(saved_source):
            settings.setValue("updates/source_mode", saved_source)
        self.source_mode.setCurrentIndex(max(0, self.source_mode.findData(service.source_mode)))
        self.source_mode.currentIndexChanged.connect(self._source_mode_changed)
        source_row.addWidget(self.source_label)
        source_row.addWidget(self.source_mode, 1)
        advanced.addLayout(source_row)
        self.auto_check = QCheckBox()
        self.auto_check.setChecked(settings.value("updates/auto_check", True, bool))
        self.auto_check.toggled.connect(lambda checked: settings.setValue("updates/auto_check", checked))
        advanced.addWidget(self.auto_check)
        layout.addWidget(self.advanced_panel)
        self.advanced_panel.hide()
        service.channels_ready.connect(self._checked)
        service.failed.connect(self._failed)
        service.download_progress.connect(self._progress)
        service.download_ready.connect(self._downloaded)
        service.state_changed.connect(self._state_changed)
        service.source_changed.connect(self._source_changed)
        self.retranslate()
        for kind in ("download", "updates", "announcements"):
            if service.last_source.get(kind):
                self._source_changed(kind, service.last_source[kind])
                break
        if service.last_update_results:
            self._checked(service.last_update_results)
        if service.ready_path:
            self._downloaded(service.ready_path, service.ready_release)
        self._controls()

    @staticmethod
    def _t(key, chinese, english):
        return i18n.t("updater."+key, chinese if i18n.lang() == "zh-CN" else english)

    @property
    def auto_install_pending(self):
        return self._auto_install and not self._closed and not self.committed

    def retranslate(self):
        self.setWindowTitle(i18n.t("updater.title"))
        self.heading.setText(i18n.t("updater.title"))
        self.current.setText(i18n.t("updater.current").format(version=VERSION))
        self.rows["preview"].title.setText("Beta")
        self.rows["stable"].title.setText(self._t("stable_short", "正式版", "Stable"))
        self.check_button.setText(self._t("retry_check", "重新检查", "Recheck"))
        self.cancel_button.setText(i18n.t("updater.cancel"))
        self.advanced_button.setText(self._t("connection_settings", "连接设置", "Connection settings"))
        self.source_label.setText(i18n.t("updater.connection"))
        for index, key in enumerate(("auto", "github", "ghfast", "ghproxy")):
            self.source_mode.setItemText(index, i18n.t("updater.connection_"+key))
        self.auto_check.setText(i18n.t("updater.auto_check"))
        self._render_source()
        for row in self.rows.values():
            self._render_row(row)
        if self._status_key:
            self._set_status(self._status_key)

    def _render_row(self, row):
        row.button.setText(self._t("update_now", "更新", "Update"))
        if row.failed:
            row.status.setText(self._t("check_failed_short", "检查失败", "Check failed"))
        elif not row.checked:
            row.status.setText(self._t("checking_short", "正在检查…", "Checking…"))
        elif row.release:
            row.status.setText(self._t("channel_available", "{version} · 有更新", "{version} · Available").format(version=row.release["version"]))
        elif row.latest:
            row.status.setText(self._t("channel_current", "{version} · 无更新", "{version} · No update").format(version=row.latest["version"]))
        else:
            row.status.setText(self._t("channel_unpublished", "暂无更新", "No update available"))

    def _set_status(self, key, text=None):
        self._status_key = key
        self.status.setText(text if text is not None else i18n.t(key) if key else "")
        self.status.setVisible(bool(self.status.text()))

    def _toggle_advanced(self, expanded):
        self.advanced_panel.setVisible(expanded)
        self.advanced_button.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.adjustSize()

    def _source_mode_changed(self):
        mode = self.source_mode.currentData()
        if not self.service.set_source_mode(mode):
            self.source_mode.blockSignals(True)
            self.source_mode.setCurrentIndex(max(0, self.source_mode.findData(self.service.source_mode)))
            self.source_mode.blockSignals(False)
            return
        self.settings.setValue("updates/source_mode", mode)
        self._source_kind = self._source_domain = None
        self._render_source()
        self.check()

    def _source_changed(self, kind, domain):
        if kind not in ("updates", "download", "announcements"):
            return
        if kind == "announcements" and self._source_kind in ("updates", "download"):
            return
        self._source_kind, self._source_domain = kind, domain
        self._render_source()

    def _render_source(self):
        if self._source_domain:
            kind = i18n.t("updater.route_"+self._source_kind)
            self.current_source.setText(i18n.t("updater.current_route").format(kind=kind, domain=self._source_domain))
        else:
            self.current_source.setText(i18n.t("updater.route_pending"))

    def check(self):
        if self.preparing or self.service.downloading or self.service.ready_path or self.committed:
            return
        self._closed = False
        self.release = None
        self._active_release = None
        self._auto_install = False
        for row in self.rows.values():
            row.release = row.latest = None
            row.checked = row.failed = False
            self._render_row(row)
        self._set_status("updater.checking")
        self.check_button.hide()
        self._controls()
        self.service.check_all_updates(force=True)

    def _checked(self, results):
        if self.preparing or self.service.downloading:
            return
        for channel, row in self.rows.items():
            result = results.get(channel, {})
            row.release = deepcopy(result.get("release"))
            row.latest = deepcopy(result.get("latest"))
            row.checked, row.failed = True, False
            self._render_row(row)
        self._set_status(None)
        self.check_button.show()
        self._controls()

    def _failed(self, category, message):
        if category not in ("updates", "download"):
            return
        self._auto_install = False
        if category == "updates":
            for row in self.rows.values():
                row.release = row.latest = None
                row.checked, row.failed = True, True
                self._render_row(row)
            self.check_button.show()
        self._set_status(None, i18n.t("updater.failed")+"\n"+message)
        self.progress.hide()
        self._controls()

    def _request_update(self, channel):
        row = self.rows[channel]
        if row.release and row.button.isEnabled():
            self.update_requested.emit(deepcopy(row.release))

    def begin_update(self, release):
        """Called only after the main window has resolved unsaved edits."""
        if self._closed or self.committed or self.preparing or self.service.downloading:
            return False
        self.release = deepcopy(release)
        self._active_release = deepcopy(release)
        self._auto_install = self.can_install
        self._install_emitted = False
        if self.service.ready_path and self.service.ready_release == release:
            self._downloaded(self.service.ready_path, release)
            return True
        if self.service.ready_path:
            self.service.discard_ready()
        self.progress.setValue(0)
        self.progress.show()
        self._set_status("updater.downloading")
        self._controls()
        started = self.service.download_release(release)
        if started is False:
            self._auto_install = False
            self.progress.hide()
            self._controls()
        return started is not False

    def _progress(self, received, total):
        if self._closed:
            return
        self.progress.show()
        self.progress.setValue(round(received/total*100) if total else 0)
        self.progress.setFormat(f'{received/1024/1024:.1f} / {total/1024/1024:.1f} MB')

    def _downloaded(self, path, release):
        self.release = deepcopy(release)
        if isinstance(release, dict) and release.get("channel") in self.rows:
            row = self.rows[release["channel"]]
            row.release, row.latest = deepcopy(release), deepcopy(release)
            row.checked, row.failed = True, False
            self._render_row(row)
        self.progress.hide()
        self._set_status("updater.ready" if self.can_install else "updater.source_mode")
        should_install = (self.auto_install_pending and not self._install_emitted
                          and release == self._active_release and self.can_install)
        if should_install:
            self._install_emitted = True
            self._set_status("updater.preparing")
            self._controls()
            self.install_requested.emit()
        else:
            if not self.can_install:
                self._auto_install = False
            self._controls()

    def _state_changed(self, state):
        self._controls()

    def _controls(self):
        busy = (self.preparing or self.service.downloading or self.service.state == "checking"
                or self.auto_install_pending or self.committed)
        ready = bool(self.service.ready_path)
        self.source_mode.setEnabled(not busy and not ready)
        self.auto_check.setEnabled(not busy)
        self.check_button.setEnabled(not busy and not ready)
        for row in self.rows.values():
            row.button.setEnabled(row.release is not None and not busy)
        self.cancel_button.setVisible(self.preparing or self.service.downloading or ready or self.auto_install_pending)
        self.cancel_button.setEnabled(not self.committed)

    def _cancel(self):
        self._auto_install = False
        if self.preparing:
            self.cancel_install_requested.emit()
        elif self.service.downloading:
            self.service.cancel_download()
        elif self.service.ready_path:
            self.service.discard_ready()
        self.progress.hide()
        self._set_status("updater.cancelled")
        self._controls()

    def set_preparing(self, preparing, error=None):
        self.preparing = preparing
        if not preparing:
            self._auto_install = False
        self._set_status("updater.preparing" if preparing else "updater.ready")
        if error:
            self._set_status(None, i18n.t("updater.install_failed")+"\n"+str(error))
        self._controls()

    def _closing(self):
        self._closed = True
        self._auto_install = False
        if self.committed:
            return
        if self.preparing:
            self.cancel_install_requested.emit()
        elif self.service.downloading:
            self.service.cancel_download()

    def closeEvent(self, event):
        self._closing()
        super().closeEvent(event)

    def reject(self):
        self._closing()
        super().reject()
