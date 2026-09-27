"""Keep staging and helper handshakes asynchronous while the old app stays open."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from PySide6.QtCore import QObject, QTimer, Signal
from core.update_launch import prepare_install_job, launch_helper, read_record, write_record


class UpdateInstallController(QObject):
    ready_to_exit = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None, *, prepare=prepare_install_job, launch=launch_helper):
        super().__init__(parent)
        self.prepare = prepare
        self.launch = launch
        self.prepared = None
        self.process = None
        self.future = None
        self.executor = None
        self.cancel_event = threading.Event()
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.state = "idle"
        self._closed = False
        self._timeout_reported = False

    def start(self, payload, release):
        if self.state not in ("idle", "failed", "cancelled") or self._closed:
            return False
        self.prepared = None
        self.process = None
        self.cancel_event = threading.Event()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="UpdatePreparation")
        self.future = self.executor.submit(self.prepare, payload, release, self.cancel_event)
        self.started = time.monotonic()
        self._timeout_reported = False
        self.state = "preparing"
        self.timer.start()
        return True

    def cancel(self):
        self.cancel_event.set()
        if self.prepared:
            job = self.prepared["job"]
            try:
                write_record(job["cancel_file"], dict(schema_version=1, nonce=job["nonce"], status="cancel"))
            except OSError:
                # Keep the old process alive until the helper exits if the
                # cancellation cannot be persisted (for example, a full disk).
                return self.process is None or self.process.poll() is not None
        return True

    def commit(self):
        self.state = "committed"
        self.timer.stop()

    def _fail(self, message):
        self.timer.stop()
        self.state = "failed"
        self.failed.emit(str(message))

    def poll(self):
        if self._closed:
            return
        if self.state == "preparing" and self.future.done():
            self.executor.shutdown(wait=False)
            try:
                self.prepared = self.future.result()
                if not self.cancel_event.is_set():
                    self.process = self.launch(self.prepared)
                    self.state = "waiting"
            except Exception as error:
                if not self.cancel_event.is_set():
                    self._fail(error)
                    return
        if self.cancel_event.is_set():
            if self.future is not None and not self.future.done():
                return
            if self.prepared:
                if not self.cancel():
                    return
            self.timer.stop()
            self.state = "cancelled"
            self.cancelled.emit()
            return
        if self.state == "waiting":
            job = self.prepared["job"]
            result = read_record(job["result_file"])
            if result and result.get("nonce") == job["nonce"]:
                self._fail(result.get("message", "The update helper stopped before applying the update"))
                return
            if self.process.poll() is not None:
                self._fail("The update helper could not start")
                return
            ready = read_record(job["prepared_file"])
            if ready and ready.get("nonce") == job["nonce"] and ready.get("status") == "waiting":
                self.timer.stop()
                self.state = "ready"
                self.ready_to_exit.emit(self.prepared)
                return
        if time.monotonic()-self.started > 120:
            if self.cancel():
                self._fail("Timed out preparing the update; the existing application was kept")
            else:
                self.state = "waiting_cancel"
                if not self._timeout_reported:
                    self._timeout_reported = True
                    self.failed.emit("Waiting for the update helper to stop; the existing application is still running")

    def close(self):
        if self.state != "committed" and not self.cancel():
            self.state = "waiting_cancel"
            self.timer.start()
            return False
        self.timer.stop()
        self._closed = True
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)
        return True
