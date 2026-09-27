"""Offline, non-modal update history styled like the skin workspace."""
from html import escape
from PySide6.QtCore import Qt, QSize, QSignalBlocker, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
                              QListWidgetItem, QTextBrowser, QCheckBox, QPushButton, QTabBar)
from core import i18n
from core.update_announcements import (CURRENT_BUILD_ID, AUTO_SHOW_KEY, CHANGELOG_URL,
                                       load_announcements, localized, localized_items)


def version_label(entry):
    if entry.get("channel") == "preview" and entry["id"].startswith("preview-"):
        return i18n.t("announcements.preview_version", "开发预览 {version}").format(version=entry["id"][8:])
    return entry["version"]


class UpdateAnnouncementsDialog(QDialog):
    refresh_requested = Signal(bool)
    software_update_requested = Signal()

    def __init__(self, settings, entries=None, parent=None, current_id=CURRENT_BUILD_ID):
        super().__init__(parent)
        self.setModal(False)
        self.settings = settings
        self.entries = load_announcements() if entries is None else tuple(entries)
        self.local_entries = self.entries
        self.online_entries = None
        self._online_status = "announcements.online_idle"
        self.current_id = current_id
        self.viewed_ids = set()
        self.resize(860, 600)
        self.setMinimumSize(650, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        self.heading = QLabel()
        self.heading.setObjectName("Title")
        self.build_label = QLabel()
        self.build_label.setObjectName("Badge")
        heading_row = QHBoxLayout()
        heading_row.addWidget(self.heading)
        heading_row.addStretch()
        heading_row.addWidget(self.build_label)
        layout.addLayout(heading_row)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("Muted")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.subtitle)
        source_row = QHBoxLayout()
        self.source_tabs = QTabBar()
        self.source_tabs.addTab("")
        self.source_tabs.addTab("")
        self.source_tabs.currentChanged.connect(self._source_changed)
        self.refresh_button = QPushButton()
        self.refresh_button.clicked.connect(lambda: self.refresh_requested.emit(True))
        source_row.addWidget(self.source_tabs)
        source_row.addStretch()
        source_row.addWidget(self.refresh_button)
        layout.addLayout(source_row)
        self.online_status = QLabel()
        self.online_status.setObjectName("Muted")
        self.online_status.setWordWrap(True)
        self.online_status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.online_status)
        history = QHBoxLayout()
        history.setSpacing(18)
        self.version_list = QListWidget()
        self.version_list.setFixedWidth(178)
        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(False)
        self.body.setOpenLinks(False)
        self.body.setAccessibleName(i18n.t("announcements.contents", "公告内容"))
        self.body.document().setDefaultStyleSheet(
            "h2 {color:#e8f3ff;font-size:21px;margin-top:4px;}"
            "h3 {color:#86deef;font-size:15px;margin-top:20px;}"
            "p,li {font-size:14px;line-height:150%;}"
            "li {margin-bottom:10px;} .meta {color:#8da5bf;font-size:12px;}")
        history.addWidget(self.version_list)
        history.addWidget(self.body, 1)
        layout.addLayout(history, 1)
        self.show_on_start = QCheckBox()
        self.show_on_start.setChecked(settings.value(AUTO_SHOW_KEY, True, bool))
        self.show_on_start.toggled.connect(lambda value: settings.setValue(AUTO_SHOW_KEY, value))
        layout.addWidget(self.show_on_start)
        buttons = QHBoxLayout()
        self.project_button = QPushButton()
        self.project_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(CHANGELOG_URL)))
        self.close_button = QPushButton()
        self.close_button.setObjectName("PrimaryButton")
        self.close_button.clicked.connect(self.accept)
        self.close_button.setDefault(True)
        buttons.addWidget(self.project_button)
        self.update_button = QPushButton()
        self.update_button.clicked.connect(self.software_update_requested)
        buttons.addWidget(self.update_button)
        buttons.addStretch()
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.version_list.currentRowChanged.connect(self._show_entry)
        self.retranslate()

    def retranslate(self):
        selected = self.version_list.currentItem()
        identifier = selected.data(Qt.UserRole) if selected else self.current_id
        old_scroll = self.body.verticalScrollBar().value()
        self.setWindowTitle(i18n.t("announcements.title", "更新公告"))
        self.heading.setText(i18n.t("announcements.title", "更新公告"))
        self.subtitle.setText(i18n.t("announcements.subtitle", "随程序提供的版本记录，离线也能查看"))
        current = next((entry for entry in self.local_entries if entry["id"] == self.current_id), None)
        label = version_label(current) if current else self.current_id
        self.build_label.setText(i18n.t("announcements.current_build", "当前构建：{version}").format(version=label))
        self.show_on_start.setText(i18n.t("announcements.show_on_start", "启动时显示新公告"))
        self.project_button.setText(i18n.t("announcements.full_history", "项目更新记录"))
        self.project_button.setToolTip(CHANGELOG_URL)
        self.close_button.setText(i18n.t("announcements.close", "知道了"))
        self.source_tabs.setTabText(0, i18n.t("announcements.local"))
        self.source_tabs.setTabText(1, i18n.t("announcements.online"))
        self.refresh_button.setText(i18n.t("announcements.refresh"))
        self.refresh_button.setVisible(self.source_tabs.currentIndex() == 1)
        self.online_status.setVisible(self.source_tabs.currentIndex() == 1)
        self.online_status.setText(i18n.t(self._online_status))
        self.update_button.setText(i18n.t("updater.check"))
        self.version_list.setAccessibleName(i18n.t("announcements.history", "版本历史"))
        self.body.setAccessibleName(i18n.t("announcements.contents", "公告内容"))
        row = 0
        with QSignalBlocker(self.version_list):
            self.version_list.clear()
            for index, entry in enumerate(self.entries):
                current_tag = (" · "+i18n.t("announcements.current", "当前") if entry["id"] == self.current_id else "")
                item = QListWidgetItem(version_label(entry)+"\n"+entry["date"]+current_tag)
                item.setData(Qt.UserRole, entry["id"])
                item.setToolTip(localized(entry["title"], i18n.lang()))
                item.setSizeHint(QSize(165, 68))
                self.version_list.addItem(item)
                if entry["id"] == identifier:
                    row = index
            self.version_list.setCurrentRow(row if self.entries else -1)
        self.version_list.setEnabled(bool(self.entries))
        self._show_entry(row if self.entries else -1)
        self.body.verticalScrollBar().setValue(old_scroll)

    def _show_entry(self, row):
        if not 0 <= row < len(self.entries):
            self.body.setPlainText(i18n.t("announcements.empty", "公告暂不可用，可前往项目查看更新记录"))
            return
        entry = self.entries[row]
        language = i18n.lang()
        channel = i18n.t("announcements.preview" if entry["channel"] == "preview" else "announcements.stable")
        content = [f'<p class="meta">{escape(version_label(entry))} · {escape(entry["date"])} · {escape(channel)}</p>',
                   f'<h2>{escape(localized(entry["title"], language))}</h2>',
                   f'<p>{escape(localized(entry["summary"], language))}</p>']
        for section in entry["sections"]:
            content.append(f'<h3>{escape(localized(section["title"], language))}</h3><ul>')
            content.extend(f'<li>{escape(item)}</li>' for item in localized_items(section["items"], language))
            content.append('</ul>')
        self.body.setHtml(''.join(content))
        if self.isVisible():
            self.viewed_ids.add(entry["id"])

    def showEvent(self, event):
        super().showEvent(event)
        item = self.version_list.currentItem()
        if item is not None:
            self.viewed_ids.add(item.data(Qt.UserRole))

    def _source_changed(self, index):
        self.entries = self.local_entries if index == 0 else (self.online_entries or ())
        self.retranslate()
        if index == 1:
            self.refresh_requested.emit(False)

    def set_online_entries(self, entries, cached=False):
        self.online_entries = tuple(entries)
        self._online_status = "announcements.online_cached" if cached else "announcements.online_loaded"
        if self.source_tabs.currentIndex() == 1:
            self.entries = self.online_entries
        self.retranslate()

    def set_online_error(self, message):
        self.online_entries = None
        self._online_status = "announcements.online_failed"
        if self.source_tabs.currentIndex() == 1:
            self.entries = ()
            self.retranslate()
            self.body.setPlainText(i18n.t(self._online_status)+"\n"+message)

    def set_online_loading(self):
        self._online_status = "announcements.online_loading"
        self.online_status.setText(i18n.t(self._online_status))
