"""Inspect skin images without blocking Qt and repair selected compatible files."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue
import threading

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView,
                              QLabel, QProgressBar, QPushButton, QTableWidget,
                              QTableWidgetItem, QVBoxLayout)

from core import i18n
from core.image_compat import (ImageRepairCancelled, repair_skin_images,
                               scan_skin_images)


def _run_operation(operation, root, issues, cancellation, messages):
    """The worker owns no QObject, bound Qt callback or dialog reference."""
    progress = lambda done, total, path: messages.put((done, total, str(path)))
    if operation == "scan":
        return scan_skin_images(root, cancelled=cancellation.is_set, progress=progress)
    return repair_skin_images(root, issues, cancelled=cancellation.is_set, progress=progress)


class ImageCompatibilityDialog(QDialog):
    assets_changed = Signal()

    def __init__(self, skin_root: Path, parent=None):
        super().__init__(parent)
        self.skin_root = Path(skin_root)
        self.setModal(True)
        self.resize(900, 490)
        self.setMinimumSize(620, 360)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="skin-images")
        self._cancellation = threading.Event()
        self._messages = Queue()
        self._future = None
        self._operation = None
        self._issues = ()
        self._backup_dir = None
        self._close_pending = False
        self._executor_closed = False
        # Even a parent destroyed without closeEvent only cancels plain Python work
        # No worker completion callback is ever scheduled against a deleted QObject
        cancellation, executor = self._cancellation, self._executor
        self.destroyed.connect(lambda *_: (cancellation.set(),
                                           executor.shutdown(wait=False, cancel_futures=True)))
        layout = QVBoxLayout(self)
        self.explanation = QLabel()
        self.explanation.setWordWrap(True)
        self.explanation.setTextFormat(Qt.PlainText)
        layout.addWidget(self.explanation)
        self.safety = QLabel()
        self.safety.setWordWrap(True)
        self.safety.setObjectName("Muted")
        self.safety.setTextFormat(Qt.PlainText)
        layout.addWidget(self.safety)
        self.table = QTableWidget(0, 5)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)
        backup_row = QHBoxLayout()
        self.backup_label = QLabel()
        self.backup_label.setTextFormat(Qt.PlainText)
        self.backup_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.backup_label.setWordWrap(True)
        self.open_backup_button = QPushButton()
        self.open_backup_button.clicked.connect(self._open_backup)
        backup_row.addWidget(self.backup_label, 1)
        backup_row.addWidget(self.open_backup_button)
        layout.addLayout(backup_row)
        buttons = QHBoxLayout()
        self.rescan_button = QPushButton()
        self.rescan_button.clicked.connect(self.start_scan)
        self.select_button = QPushButton()
        self.select_button.clicked.connect(self._select_repairable)
        self.repair_button = QPushButton()
        self.repair_button.setObjectName("PrimaryButton")
        self.repair_button.clicked.connect(self.repair_selected)
        self.cancel_button = QPushButton()
        self.cancel_button.clicked.connect(self.cancel)
        self.close_button = QPushButton()
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.rescan_button)
        buttons.addWidget(self.select_button)
        buttons.addStretch()
        buttons.addWidget(self.repair_button)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(40)
        self._poll_timer.timeout.connect(self._poll)
        self._start_timer = QTimer(self)
        self._start_timer.setSingleShot(True)
        self._start_timer.timeout.connect(self.start_scan)
        self.retranslate()
        self._controls()
        self._start_timer.start(0)

    @staticmethod
    def _t(key, chinese, english):
        return i18n.t("image_compat." + key, chinese if i18n.lang() == "zh-CN" else english)

    @property
    def busy(self):
        return self._future is not None

    def retranslate(self):
        self.setWindowTitle(self._t("title", "图片兼容性检查与修复", "Image compatibility check and repair"))
        self.explanation.setText(self._t("description", "检查图片的真实格式，修复因格式与 PNG 后缀不符而在 osu!lazer 中消失的素材", "Check actual image formats and repair assets hidden in osu!lazer because their content is not PNG"))
        self.safety.setText(self._t("safety", "扫描不会修改文件；修复前自动备份到皮肤内的 __conflicts_backup，按原文件名保存无损 PNG，不改皮肤配置；损坏、多帧或高位深图片需要手动处理", "Scanning does not change files; repairs back up originals under __conflicts_backup and save lossless PNG with the same filename and skin settings; damaged, multiframe or high bit depth images need manual attention"))
        self.table.setHorizontalHeaderLabels(["", self._t("path", "素材路径", "Asset path"),
                                             self._t("format", "真实格式 / 后缀", "Actual format / extension"),
                                             self._t("dimensions", "尺寸", "Dimensions"),
                                             self._t("recommendation", "处理建议", "Recommendation")])
        self.rescan_button.setText(self._t("rescan", "重新扫描", "Rescan"))
        self.select_button.setText(self._t("select_all", "全选可修复", "Select repairable"))
        self.cancel_button.setText(self._t("cancel", "取消", "Cancel"))
        self.close_button.setText(self._t("close", "关闭", "Close"))
        self.open_backup_button.setText(self._t("open_backup", "打开备份", "Open backup"))
        self._selection_changed()
        self._render_backup()

    def _selected(self):
        return tuple(issue for row, issue in enumerate(self._issues)
                     if issue.repairable and self.table.item(row, 0).checkState() == Qt.Checked
                     and self.table.item(row, 0).flags() & Qt.ItemIsUserCheckable)

    def _selection_changed(self, *_args):
        count = len(self._selected())
        self.repair_button.setText(self._t("repair_selected", "修复所选 ({count})", "Repair selected ({count})").format(count=count))
        self.repair_button.setEnabled(not self.busy and bool(count) and not self._close_pending)

    def _controls(self):
        active = self.busy
        self.table.setEnabled(not active)
        self.rescan_button.setEnabled(not active and not self._close_pending)
        self.select_button.setEnabled(not active and any(issue.repairable for issue in self._issues))
        self.cancel_button.setVisible(active)
        self.cancel_button.setEnabled(active and not self._cancellation.is_set())
        self.progress.setVisible(active)
        self._selection_changed()
        self._render_backup()

    def _render_backup(self):
        visible = self._backup_dir is not None
        self.backup_label.setVisible(visible)
        self.open_backup_button.setVisible(visible)
        if visible:
            self.backup_label.setText(self._t("backup_path", "原文件备份：{path}", "Original files backed up to: {path}").format(path=self._backup_dir))

    def _open_backup(self):
        if self._backup_dir is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._backup_dir)))

    def _select_repairable(self):
        if self.busy:
            return
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(Qt.Checked)

    def _submit(self, operation, issues=()):
        if self.busy or self._close_pending or self._executor_closed:
            return False
        self._cancellation.clear()
        self._operation = operation
        self._messages = Queue()
        self.progress.setRange(0, 0)
        self.status.setText(self._t("scanning", "正在检查图片…", "Checking images…") if operation == "scan" else
                            self._t("repairing", "正在备份并修复所选图片…", "Backing up and repairing selected images…"))
        self._future = self._executor.submit(_run_operation, operation, self.skin_root,
                                             tuple(issues), self._cancellation, self._messages)
        self._controls()
        self._poll_timer.start()
        return True

    def start_scan(self):
        self._start_timer.stop()
        return self._submit("scan")

    def repair_selected(self):
        selected = self._selected()
        return self._submit("repair", selected) if selected else False

    def cancel(self):
        if self.busy:
            self._cancellation.set()
            self.status.setText(self._t("cancelling", "正在安全停止，已完成的修复与备份会保留…", "Stopping safely; completed repairs and backups will be kept…"))
            self._controls()

    def _poll(self):
        latest = None
        while True:
            try:
                latest = self._messages.get_nowait()
            except Empty:
                break
        if latest is not None:
            done, total, path = latest
            self.progress.setRange(0, max(1, total))
            self.progress.setValue(done)
            if not self._cancellation.is_set():
                self.status.setText(self._t("progress", "已处理 {done} / {total}  ·  {path}", "Processed {done} / {total}  ·  {path}").format(done=done, total=total, path=path))
        if self._future is None or not self._future.done():
            return
        future, operation = self._future, self._operation
        self._future = self._operation = None
        self._poll_timer.stop()
        result = None
        try:
            result = future.result()
        except ImageRepairCancelled as error:
            result = getattr(error, "partial_result", None)
            if operation == "repair":
                self._apply_repair_result(result)
                self.status.setText(self._t("repair_cancelled", "已取消：修复 {repaired} 张，跳过 {skipped} 张，已完成的修复与备份已保留", "Cancelled: {repaired} repaired, {skipped} skipped; completed repairs and backups were kept").format(repaired=len(result.repaired) if result else 0, skipped=len(result.skipped) if result else 0))
            else:
                self.status.setText(self._t("scan_cancelled", "扫描已取消，可重新扫描以获取完整结果", "Scan cancelled; rescan to get complete results"))
        except Exception as error:
            if operation == "repair":
                result = getattr(error, "partial_result", None)
                self._apply_repair_result(result)
                self.status.setText(self._t("repair_failed", "修复未全部完成：修复 {repaired} 张，跳过 {skipped} 张；{error}", "Repair incomplete: {repaired} repaired, {skipped} skipped; {error}").format(repaired=len(result.repaired) if result else 0, skipped=len(result.skipped) if result else 0, error=str(error)))
            else:
                self.status.setText(self._t("failed", "处理未全部完成：{error}", "Could not complete the operation: {error}").format(error=str(error)))
        else:
            if operation == "scan":
                self._show_scan(result)
            else:
                self._apply_repair_result(result)
                self.status.setText(self._t("repaired", "修复完成：{repaired} 张；跳过 {skipped} 张", "Repair complete: {repaired} repaired; {skipped} skipped").format(repaired=len(result.repaired), skipped=len(result.skipped)))
        if operation == "repair" and result is not None and result.repaired:
            self.assets_changed.emit()
        self._controls()
        if self._close_pending:
            self._finish_close()

    def _show_scan(self, result):
        self._issues = tuple(result.issues)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._issues))
        for row, issue in enumerate(self._issues):
            checked = QTableWidgetItem()
            checked.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if issue.repairable:
                checked.setFlags(checked.flags() | Qt.ItemIsUserCheckable)
                checked.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, checked)
            actual = issue.actual_format or self._t("unknown", "无法识别", "Unknown")
            suffix = Path(issue.relative_path).suffix or str(issue.declared_format)
            dimensions = f"{issue.width} × {issue.height}" if issue.width and issue.height else "—"
            if issue.repairable:
                advice = self._t("convert_png", "无损转换为 PNG", "Convert to lossless PNG")
            elif issue.status == "unreadable":
                advice = self._t("unreadable", "图片损坏或无法读取，需替换素材", "Unreadable or damaged; replace the image")
            elif issue.status == "unsupported":
                advice = self._t("unsupported", "不受支持的格式，需另存并更新引用", "Unsupported format; export a supported image and update references")
            else:
                advice = self._t("manual", "需手动处理（多帧或高位深等）", "Manual attention required (multiple frames or high bit depth)")
            for column, value in enumerate((str(issue.relative_path), f"{actual} / {suffix}", dimensions, advice), 1):
                item = QTableWidgetItem(value)
                item.setToolTip(str(issue.message))
                self.table.setItem(row, column, item)
        self.table.blockSignals(False)
        count = sum(issue.repairable for issue in self._issues)
        if not self._issues:
            self.status.setText(self._t("healthy", "已检查 {count} 张图片，未发现格式兼容性问题", "Checked {count} images; no format compatibility issues found").format(count=result.scanned_count))
        else:
            self.status.setText(self._t("scan_result", "已检查 {scanned} 张图片，发现 {issues} 项问题，其中 {repairable} 项可安全修复", "Checked {scanned} images; found {issues} issues, {repairable} safe to repair").format(scanned=result.scanned_count, issues=len(self._issues), repairable=count))

    def _apply_repair_result(self, result):
        if result is None:
            return
        if result.backup_dir is not None:
            self._backup_dir = Path(result.backup_dir)
        repaired = {str(path).replace("\\", "/") for path in result.repaired}
        self.table.blockSignals(True)
        for row, issue in enumerate(self._issues):
            if str(issue.relative_path).replace("\\", "/") not in repaired:
                continue
            item = self.table.item(row, 0)
            item.setCheckState(Qt.Unchecked)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.item(row, 2).setText("PNG / " + Path(issue.relative_path).suffix)
            self.table.item(row, 4).setText(self._t("fixed", "已修复并备份", "Repaired and backed up"))
        self.table.blockSignals(False)

    def _shutdown(self):
        if not self._executor_closed:
            self._executor_closed = True
            self._executor.shutdown(wait=False, cancel_futures=True)
        self._poll_timer.stop()
        self._start_timer.stop()

    def _finish_close(self):
        self._shutdown()
        super().reject()

    def reject(self):
        if self.busy:
            self._close_pending = True
            self.cancel()
        else:
            self._finish_close()

    def closeEvent(self, event):
        if self.busy:
            self._close_pending = True
            self.cancel()
            event.ignore()
        else:
            self._shutdown()
            super().closeEvent(event)
