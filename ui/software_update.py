"""Explicit download and restart controls, separate from announcement reading."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar, QCheckBox
from core import i18n
from core.app_version import VERSION, CHANNEL
from core.update_launch import installed_executable
from ui.widgets.wheel_guard import ClickWheelComboBox


class SoftwareUpdateDialog(QDialog):
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
        self.resize(620, 490)
        self.setMinimumWidth(510)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        self.heading = QLabel()
        self.heading.setObjectName("Title")
        layout.addWidget(self.heading)
        self.current = QLabel()
        self.current.setObjectName("Muted")
        layout.addWidget(self.current)
        row = QHBoxLayout()
        self.channel_label = QLabel()
        self.channel = ClickWheelComboBox()
        for value in ("stable", "preview"):
            self.channel.addItem(value, value)
        saved = settings.value("updates/channel", CHANNEL, str)
        self.channel.setCurrentIndex(max(0, self.channel.findData(saved)))
        self.channel.currentIndexChanged.connect(self._channel_changed)
        self.check_button = QPushButton()
        self.check_button.clicked.connect(self.check)
        row.addWidget(self.channel_label)
        row.addWidget(self.channel)
        row.addStretch()
        row.addWidget(self.check_button)
        layout.addLayout(row)
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
        source_row.addWidget(self.source_mode)
        source_row.addStretch()
        layout.addLayout(source_row)
        self.current_source = QLabel()
        self.current_source.setObjectName("Muted")
        self.current_source.setTextFormat(Qt.PlainText)
        self.current_source.setWordWrap(True)
        self._source_kind = None
        self._source_domain = None
        layout.addWidget(self.current_source)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.release_info = QLabel()
        self.release_info.setWordWrap(True)
        self.release_info.setTextFormat(Qt.PlainText)
        layout.addWidget(self.release_info)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.auto_check = QCheckBox()
        self.auto_check.setChecked(settings.value("updates/auto_check", True, bool))
        self.auto_check.toggled.connect(lambda checked: settings.setValue("updates/auto_check", checked))
        layout.addWidget(self.auto_check)
        layout.addStretch()
        self.help_text = QLabel()
        self.help_text.setObjectName("Muted")
        self.help_text.setWordWrap(True)
        layout.addWidget(self.help_text)
        buttons = QHBoxLayout()
        self.cancel_button = QPushButton()
        self.cancel_button.clicked.connect(self._cancel)
        self.download_button = QPushButton()
        self.download_button.clicked.connect(self._download)
        self.install_button = QPushButton()
        self.install_button.setObjectName("PrimaryButton")
        self.install_button.clicked.connect(self.install_requested)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        buttons.addWidget(self.download_button)
        buttons.addWidget(self.install_button)
        layout.addLayout(buttons)
        service.update_ready.connect(self._checked)
        service.failed.connect(self._failed)
        service.download_progress.connect(self._progress)
        service.download_ready.connect(self._downloaded)
        service.state_changed.connect(self._state_changed)
        service.source_changed.connect(self._source_changed)
        self._status_key = "updater.idle"
        self.retranslate()
        for kind in ("download", "updates", "announcements"):
            if service.last_source.get(kind):
                self._source_changed(kind, service.last_source[kind])
                break
        if service.ready_path:
            self._downloaded(service.ready_path, service.ready_release)
        self._controls()

    def retranslate(self):
        self.setWindowTitle(i18n.t("updater.title"))
        self.heading.setText(i18n.t("updater.title"))
        self.current.setText(i18n.t("updater.current").format(version=VERSION))
        self.channel_label.setText(i18n.t("updater.channel"))
        for index, key in enumerate(("stable", "preview")):
            self.channel.setItemText(index, i18n.t("updater."+key))
        self.source_label.setText(i18n.t("updater.connection"))
        for index, key in enumerate(("auto", "github", "ghfast", "ghproxy")):
            self.source_mode.setItemText(index, i18n.t("updater.connection_"+key))
        self._render_source()
        self.check_button.setText(i18n.t("updater.check"))
        self.download_button.setText(i18n.t("updater.download"))
        self.install_button.setText(i18n.t("updater.install"))
        self.cancel_button.setText(i18n.t("updater.cancel"))
        self.auto_check.setText(i18n.t("updater.auto_check"))
        self.help_text.setText(i18n.t("updater.help" if self.can_install else "updater.source_mode"))
        if self._status_key:
            self.status.setText(i18n.t(self._status_key))

    def _channel_changed(self):
        self.settings.setValue("updates/channel", self.channel.currentData())
        self.release = None
        self.release_info.clear()
        self.check()

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
        self.release = None
        self.release_info.clear()
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
        if self.preparing or self.service.downloading or self.service.ready_path:
            return
        self.release = None
        self.release_info.clear()
        self._controls()
        self._status_key = "updater.checking"
        self.status.setText(i18n.t(self._status_key))
        self.service.check_updates(self.channel.currentData(), force=True)

    def _checked(self, release):
        if self.service.ready_path:
            return
        self.release = release
        self._status_key = "updater.available" if release else "updater.up_to_date"
        self.status.setText(i18n.t(self._status_key))
        self.release_info.setText((f'{release["version"]} · {release["size"]/1024/1024:.1f} MB') if release else "")
        self._controls()

    def _failed(self, category, message):
        if category not in ("updates", "download"):
            return
        self._status_key = None
        self.status.setText(i18n.t("updater.failed")+"\n"+message)
        self._controls()

    def _download(self):
        if self.release:
            self._status_key = "updater.downloading"
            self.status.setText(i18n.t(self._status_key))
            self.service.download_release(self.release)

    def _progress(self, received, total):
        self.progress.show()
        self.progress.setValue(round(received/total*100) if total else 0)
        self.progress.setFormat(f'{received/1024/1024:.1f} / {total/1024/1024:.1f} MB')

    def _downloaded(self, path, release):
        self.release = release
        self._status_key = "updater.ready"
        self.status.setText(i18n.t(self._status_key))
        self.release_info.setText(f'{release["version"]} · {release["size"]/1024/1024:.1f} MB')
        self._controls()

    def _state_changed(self, state):
        self._controls()

    def _controls(self):
        busy = self.preparing or self.service.downloading or self.service.state == "checking"
        ready = bool(self.service.ready_path)
        self.channel.setEnabled(not busy and not ready)
        self.source_mode.setEnabled(not busy and not ready)
        self.check_button.setEnabled(not busy and not ready)
        self.download_button.setEnabled(self.release is not None and not ready and not busy)
        self.install_button.setEnabled(ready and self.can_install and not busy)
        self.cancel_button.setEnabled(self.preparing or self.service.downloading or ready)

    def _cancel(self):
        if self.preparing:
            self.cancel_install_requested.emit()
        elif self.service.downloading:
            self.service.cancel_download()
        elif self.service.ready_path:
            self.service.discard_ready()
            self.release = None
            self.release_info.clear()
        self._status_key = "updater.cancelled"
        self.status.setText(i18n.t(self._status_key))
        self._controls()

    def set_preparing(self, preparing, error=None):
        self.preparing = preparing
        self._status_key = "updater.preparing" if preparing else "updater.ready"
        self.status.setText(i18n.t(self._status_key))
        if error:
            self._status_key = None
            self.status.setText(i18n.t("updater.install_failed")+"\n"+str(error))
        self._controls()

    def closeEvent(self, event):
        if self.preparing and not self.committed:
            self.cancel_install_requested.emit()
        super().closeEvent(event)

    def reject(self):
        if self.preparing and not self.committed:
            self.cancel_install_requested.emit()
        super().reject()
