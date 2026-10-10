"""Persistent OSK workspace and explicit export actions for the main window."""
from pathlib import Path
import json
import os
import re
import tempfile

from PySide6.QtCore import Qt, QStandardPaths, QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog

from core import i18n
from ui.skin_archive_controller import ArchiveController


class SkinArchiveWorkflow:
    def _init_archive_workflow(self):
        self._archive_controller = ArchiveController(self)
        self._archive_progress = None
        self._archive_kind = None
        self._close_after_archive = False
        self._archive_export_origin = None
        self._archive_export_keys = None
        self._archive_sources = {}
        try:
            saved = self.settings.value("paths/osk_sources", "{}", str)
            value = json.loads(saved) if len(saved) <= 64*1024 else {}
            if isinstance(value, dict):
                self._archive_sources = {k: v for k, v in list(value.items())[-30:]
                                         if isinstance(k, str) and isinstance(v, str)}
        except (ValueError, TypeError, RecursionError):
            pass
        controller = self._archive_controller
        controller.busy_changed.connect(self._archive_busy_changed)
        controller.progress.connect(self._archive_progress_changed)
        controller.imported.connect(self._archive_imported)
        controller.exported.connect(self._archive_exported)
        controller.failed.connect(self._archive_failed)
        controller.cancelled.connect(self._archive_cancelled)

    def _archive_workspace_parent(self):
        override = getattr(self, "_archive_workspace_override", None)
        if override is not None:
            return Path(override)
        location = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
        base = Path(location) if location and Path(location).is_absolute() else Path(
            os.environ.get("LOCALAPPDATA") or Path.home()) / "OsuSkinEditor"
        return base / "imported-skins"

    def _archive_origin(self, root=None):
        root = root or (self.skin.root if self.skin else None)
        if root is None:
            return None
        root = Path(root).resolve()
        origin = self._archive_sources.get(str(root))
        if origin:
            return origin
        # Permanent workspaces outlive the recent-source cache. Their private
        # metadata lives beside the skin, never inside an exported OSK.
        metadata = root.parent / ".osk-origins" / (root.name+".json")
        try:
            if metadata.is_symlink() or metadata.parent.is_symlink() or getattr(metadata.parent, "is_junction", lambda: False)():
                return None
            with metadata.open("rb") as stream:
                data = stream.read(64*1024+1)
            if len(data) > 64*1024:
                return None
            value = json.loads(data.decode("utf-8"))
            if (isinstance(value, dict) and set(value) == {"schema_version", "root", "source"}
                    and type(value["schema_version"]) is int and value["schema_version"] == 1
                    and value["root"] == str(root) and isinstance(value["source"], str)
                    and Path(value["source"]).is_absolute()):
                return value["source"]
        except (OSError, ValueError, TypeError, RecursionError):
            pass
        return None

    def _remember_archive_origin(self, root, source):
        root = Path(root).resolve()
        key = str(root)
        source = str(Path(source).resolve())
        self._archive_sources.pop(key, None)
        self._archive_sources[key] = source
        self._archive_sources = dict(list(self._archive_sources.items())[-30:])
        self.settings.setValue("paths/osk_sources", json.dumps(self._archive_sources, ensure_ascii=False))
        temporary = None
        try:
            folder = root.parent / ".osk-origins"
            if folder.is_symlink() or getattr(folder, "is_junction", lambda: False)():
                raise OSError("Linked origin metadata folder")
            folder.mkdir(exist_ok=True)
            metadata = folder / (root.name+".json")
            if metadata.is_symlink():
                raise OSError("Linked origin metadata file")
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder,
                    prefix=".osk-origin-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"schema_version": 1, "root": key, "source": source}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, metadata)
            temporary = None
        except OSError:
            # Retain the in-profile cache without discarding a valid workspace.
            # Do not silently evict its protection when sidecar persistence fails.
            self._archive_sources[key] = source
            self.settings.setValue("paths/osk_sources", json.dumps(self._archive_sources, ensure_ascii=False))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _sync_archive_actions(self):
        busy = self._archive_controller.busy or self._preparing_update
        self.act_import_osk.setEnabled(not busy)
        self.act_export_osk.setEnabled(self.skin is not None and not busy)
        self.btn_import_osk.setEnabled(not busy)
        self.btn_welcome_osk.setEnabled(not busy)
        self.btn_export_osk.setEnabled(self.skin is not None and not busy)
        notes = []
        if self.skin:
            origin = self._archive_origin()
            if origin:
                notes.append(i18n.t("osk.source_note").format(name=Path(origin).name))
            try:
                names = {p.name.casefold() for p in self.skin.root.iterdir() if p.is_file()}
            except OSError:
                names = set()
            if names.intersection({"skininfo.json", "mainhudcomponents.json", "playfield.json", "songselect.json"}):
                notes.append(i18n.t("osk.native_notice"))
        self.archive_note.setText("\n".join(notes))
        self.archive_note.setVisible(bool(notes))

    def on_import_osk(self):
        if self._archive_controller.busy or self._preparing_update:
            return False
        start = self.settings.value("paths/last_osk", self._start_dir_for_dialog(), str)
        path, _ = QFileDialog.getOpenFileName(self, i18n.t("osk.import_title"), start, i18n.t("osk.filter"))
        return self.import_osk_file(path) if path else False

    def import_osk_file(self, path):
        if self._closing or self._archive_controller.busy or self._preparing_update:
            return False
        self._archive_kind = "import"
        self.settings.setValue("paths/last_osk", str(path))
        # Validate/extract first. Corrupt archives must leave pending edits alone.
        return self._archive_controller.import_file(path, self._archive_workspace_parent())

    def _archive_imported(self, root, inspection, source):
        self._remember_archive_origin(root, source)
        if self._close_after_archive:
            return
        if self.load_skin(root):
            text = i18n.t("osk.imported_status").format(path=root)
            repaired = getattr(inspection, "repaired_directory_entries", 0)
            if repaired:
                text += " · " + i18n.t("osk.repaired_status").format(count=repaired)
        else:
            text = i18n.t("osk.kept_workspace", "副本已解包，当前皮肤未切换：{path}").format(path=root)
        self.statusBar().showMessage(text, 12000)

    def _choose_export_edit_mode(self):
        if not self.mania_ini_dock._dirty and not self.mania_design_dock._dirty:
            return "saved"
        box = QMessageBox(self)
        box.setWindowTitle(i18n.t("osk.unsaved_title"))
        box.setText(i18n.t("osk.unsaved_message"))
        apply = box.addButton(i18n.t("osk.apply_and_export"), QMessageBox.AcceptRole)
        saved = box.addButton(i18n.t("osk.export_saved"), QMessageBox.ActionRole)
        box.addButton(i18n.t("osk.cancel"), QMessageBox.RejectRole)
        box.exec()
        return "apply" if box.clickedButton() is apply else "saved" if box.clickedButton() is saved else None

    def on_export_osk(self):
        if not self.skin or self._closing or self._archive_controller.busy or self._preparing_update:
            return False
        origin = self._archive_origin()
        directory = self.settings.value("paths/last_osk_export", "", str)
        if not directory:
            directory = str(Path(origin).parent) if origin else QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", self.skin_title.text()).strip(" .") or "Skin"
        path, _ = QFileDialog.getSaveFileName(self, i18n.t("osk.export_title"),
                                             str(Path(directory) / (name+"-edited.osk")), i18n.t("osk.filter"))
        if not path:
            return False
        output = Path(path).expanduser()
        if output.suffix.casefold() != ".osk":
            output = Path(str(output)+".osk")
        if origin and output.resolve() == Path(origin).resolve():
            QMessageBox.warning(self, i18n.t("osk.export_title"), i18n.t("osk.same_source"))
            return False
        mode = self._choose_export_edit_mode()
        if mode is None:
            return False
        if mode == "apply" and self.mania_ini_dock._dirty and not self.mania_ini_dock._on_save_clicked():
            return False
        design = self.mania_design_dock.options() if mode == "apply" and self.mania_design_dock._dirty else None
        self._archive_export_origin = origin
        self._archive_export_keys = self.mania_preview.keys
        self._archive_kind = "export"
        self.settings.setValue("paths/last_osk_export", str(output.parent))
        return self._archive_controller.export_folder(self.skin.root, output, design=design,
                keys=self.mania_preview.keys, working_parent=self._archive_workspace_parent())

    def _archive_exported(self, output):
        if self._close_after_archive:
            return
        exported_root = self._archive_controller.last_export_root
        if exported_root:
            if self._archive_export_origin:
                self._remember_archive_origin(exported_root, self._archive_export_origin)
            if self.load_skin(exported_root, check_dirty=False):
                self._request_mania_keys(self._archive_export_keys)
        self.statusBar().showMessage(i18n.t("osk.exported_status").format(path=output), 12000)

    def _archive_busy_changed(self, busy):
        if busy:
            label = i18n.t("osk.importing" if self._archive_kind == "import" else "osk.exporting")
            dialog = QProgressDialog(label, i18n.t("osk.cancel"), 0, 100, self)
            dialog.setWindowModality(Qt.WindowModal)
            dialog.setMinimumDuration(0)
            dialog.setAutoClose(False)
            dialog.setAutoReset(False)
            dialog.canceled.connect(self._archive_controller.cancel)
            self._archive_progress = dialog
            dialog.show()
        elif self._archive_progress is not None:
            dialog, self._archive_progress = self._archive_progress, None
            dialog.close()
            dialog.deleteLater()
        enabled = not busy and not self._preparing_update
        for widget in (self.centralWidget(), self.menuBar(), self.mania_ini_dock, self.mania_design_dock, self.debug_dock):
            widget.setEnabled(enabled)
        if self._preview_window:
            self._preview_window.setEnabled(enabled)
        self._sync_archive_actions()
        if not busy and self._close_after_archive:
            QTimer.singleShot(0, self.close)

    def _archive_progress_changed(self, kind, done, total):
        if self._archive_progress is not None:
            self._archive_progress.setValue(min(100, int(done*100/total)) if total else 0)

    def _archive_failed(self, kind, message):
        if not self._close_after_archive:
            QMessageBox.critical(self, i18n.t("osk.failure"), message)

    def _archive_cancelled(self, kind):
        self.statusBar().showMessage(i18n.t("osk.cancelled"), 8000)
