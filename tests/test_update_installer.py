"""Real-file transactions with injected process lifetimes and failure points"""
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import threading
from unittest.mock import patch

from core.update_installer import (
    InstallerHooks, UpdateError, WindowsParentWaiter, WindowsTargetMutex, capture_process_identity,
    clean_restart_environment, load_job, run_update,
)


class FakeParents:
    def __init__(self, hooks):
        self.hooks = hooks
        self.closed = False

    def all_exited(self):
        return self.hooks.parent_exit_at is not None and self.hooks.clock >= self.hooks.parent_exit_at

    def close(self):
        self.closed = True


class FakeChild:
    pid = 50001
    creation_time = 99

    def __init__(self, alive=True, stoppable=True):
        self.alive = alive
        self.stoppable = stoppable
        self.terminated = False
        self.detached = False
        self.closed = False

    def is_running(self):
        return self.alive

    def contains_pid(self, pid, creation_time=None):
        return pid in (50001, 50002) and creation_time in (None, 99) and self.alive

    def terminate_and_wait(self, timeout):
        self.terminated = True
        if self.stoppable:
            self.alive = False
        return not self.alive

    def detach(self):
        self.detached = True

    def close(self):
        self.closed = True


class FakeHooks(InstallerHooks):
    def __init__(self, fixture):
        self.fixture = fixture
        self.clock = 0
        self.parent_exit_at = 0
        self.parents = FakeParents(self)
        self.launches = []
        self.children = []
        self.mode = "success"
        self.replace_failures = 0
        self.replace_attempts = 0
        self.deny_copy = False
        self.corrupt_candidate = False
        self.on_sleep = None
        self.on_wait_open = None
        self.stop_fails = False
        self.target_mutex = type("FakeMutex", (), {"closed": False,
                                  "close": lambda mutex: setattr(mutex, "closed", True)})()

    def monotonic(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += seconds
        if self.on_sleep:
            self.on_sleep(self)

    def open_parents(self, pids, creation_times):
        self.parent_arguments = (pids, creation_times)
        if self.on_wait_open:
            self.on_wait_open()
        return self.parents

    def acquire_target(self, path):
        return self.target_mutex

    def copy_file(self, source, target):
        if self.deny_copy:
            raise PermissionError("Target directory is not writable")
        super().copy_file(source, target)
        if self.corrupt_candidate and ".update-" in target.name:
            target.write_bytes(b"corrupt copy")

    def replace_file(self, source, target):
        self.replace_attempts += 1
        if self.replace_failures:
            self.replace_failures -= 1
            raise PermissionError("Executable temporarily locked")
        super().replace_file(source, target)

    def launch(self, executable, arguments, environment):
        self.launches.append((Path(executable), list(arguments), dict(environment)))
        if arguments and self.mode == "launch-error":
            raise OSError("Could not start the new application")
        child = FakeChild(alive=self.mode != "crash" or not arguments, stoppable=not self.stop_fails)
        self.children.append(child)
        if arguments and self.mode in ("success", "child-ready", "wrong-nonce", "foreign-pid", "wrong-identity"):
            data = {"schema_version": 1, "status": "ready", "nonce": self.fixture.data["nonce"],
                    "pid": 50002 if self.mode == "child-ready" else child.pid, "creation_time": 99}
            if self.mode == "wrong-nonce":
                data["nonce"] = "z"*32
            if self.mode == "foreign-pid":
                data["pid"] = 50009
            if self.mode == "wrong-identity":
                data["creation_time"] = 199
            self.fixture.ready.write_text(json.dumps(data), encoding="utf-8")
        return child


class UpdateInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.install = self.root / "installed"
        self.stage = self.root / "staging"
        self.install.mkdir()
        self.stage.mkdir()
        self.target = self.install / "Skin Editor.exe"
        self.target.write_bytes(b"old executable bytes")
        self.old = self.target.read_bytes()
        self.payload = self.stage / "payload.exe"
        self.payload.write_bytes(b"new executable bytes")
        self.new = self.payload.read_bytes()
        self.ready = self.stage / "ready.json"
        self.result_path = self.stage / "result.json"
        self.prepared = self.stage / "helper-ready.json"
        self.cancel = self.stage / "cancel.json"
        self.job_path = self.stage / "job.json"
        self.data = {"schema_version": 1, "target_exe": str(self.target),
                     "payload_exe": str(self.payload), "size": len(self.new),
                     "expected_sha256": hashlib.sha256(self.new).hexdigest(),
                     "parent_pids": [42420001, 42420002],
                     "parent_creation_times": {"42420001": 123, "42420002": 456},
                     "nonce": "a"*32, "ready_file": str(self.ready),
                     "result_file": str(self.result_path), "prepared_file": str(self.prepared),
                     "cancel_file": str(self.cancel), "wait_timeout_seconds": 1,
                     "ready_timeout_seconds": 1}
        self.write_job()
        self.hooks = FakeHooks(self)
        self.user_file = self.install / "skin.ini"
        self.user_file.write_text("do not modify", encoding="utf-8")

    def tearDown(self):
        self.assertEqual(self.user_file.read_text(encoding="utf-8"), "do not modify")
        self.temp.cleanup()

    def write_job(self):
        self.job_path.write_text(json.dumps(self.data), encoding="utf-8")

    def run_update(self):
        return run_update(self.job_path, hooks=self.hooks)

    def assert_old_intact(self):
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertEqual(self.payload.read_bytes(), self.new)

    def test_success_waits_before_replacement_preserves_payload_and_keeps_backup(self):
        self.hooks.parent_exit_at = .3
        observed = []

        def while_waiting(hooks):
            observed.append((self.prepared.exists(), self.target.read_bytes()))

        self.hooks.on_sleep = while_waiting
        result = self.run_update()
        self.assertEqual(result["status"], "success")
        self.assertTrue(observed)
        self.assertTrue(all(prepared and content == self.old for prepared, content in observed))
        self.assertEqual(self.target.read_bytes(), self.new)
        self.assertEqual(self.payload.read_bytes(), self.new)
        self.assertEqual(Path(result["backup_exe"]).read_bytes(), self.old)
        self.assertEqual(Path(result["backup_exe"]).parent, self.install)
        self.assertEqual(json.loads(self.result_path.read_text())["status"], "success")
        self.assertTrue(self.hooks.parents.closed)
        self.assertTrue(self.hooks.target_mutex.closed)
        self.assertTrue(self.hooks.children[0].detached)
        self.assertFalse(self.hooks.children[0].terminated)
        self.assertEqual(self.hooks.launches[0][2]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(list(self.install.glob("*.new")), [])

    def test_handshake_from_own_pyinstaller_child_is_accepted(self):
        self.hooks.mode = "child-ready"
        self.assertEqual(self.run_update()["status"], "success")

    def test_payload_hash_mismatch_fails_before_prepared_or_parent_exit(self):
        self.data["expected_sha256"] = "0"*64
        self.write_job()
        result = self.run_update()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.prepared.exists())
        self.assertFalse(self.hooks.launches)
        self.assert_old_intact()

    def test_payload_size_mismatch_fails_before_prepared(self):
        self.data["size"] += 1
        self.write_job()
        self.assertEqual(self.run_update()["status"], "failed")
        self.assertFalse(self.prepared.exists())
        self.assert_old_intact()

    def test_unwritable_install_directory_keeps_current_program_and_download(self):
        self.hooks.deny_copy = True
        result = self.run_update()
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.prepared.exists())
        self.assertFalse(self.hooks.launches)
        self.assert_old_intact()

    def test_target_volume_copy_is_verified_before_main_program_may_exit(self):
        self.hooks.corrupt_candidate = True
        self.assertEqual(self.run_update()["status"], "failed")
        self.assertFalse(self.prepared.exists())
        self.assertEqual(list(self.install.glob("*.new")), [])
        self.assert_old_intact()

    def test_parent_wait_is_bounded_and_never_terminates_existing_processes(self):
        self.hooks.parent_exit_at = None
        result = self.run_update()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["phase"], "waiting")
        self.assertLess(self.hooks.clock, 1.3)
        self.assertFalse(self.hooks.launches)
        self.assert_old_intact()

    def test_cancel_during_parent_wait_never_replaces_or_restarts_main(self):
        self.hooks.parent_exit_at = None

        def cancel(hooks):
            self.cancel.write_text(json.dumps({"schema_version": 1, "nonce": self.data["nonce"],
                                               "status": "cancel"}), encoding="utf-8")

        self.hooks.on_sleep = cancel
        result = self.run_update()
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(self.hooks.launches)
        self.assertEqual(list(self.install.glob("*.new")), [])
        self.assert_old_intact()

    def test_target_changed_while_waiting_is_not_overwritten(self):
        self.hooks.parent_exit_at = .1
        self.hooks.on_sleep = lambda hooks: self.target.write_bytes(b"changed by another installer")
        result = self.run_update()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.target.read_bytes(), b"changed by another installer")
        self.assertFalse(self.hooks.launches)

    def test_transient_file_lock_retries_then_succeeds(self):
        self.hooks.replace_failures = 2
        self.assertEqual(self.run_update()["status"], "success")
        self.assertEqual(self.hooks.replace_attempts, 3)

    def test_persistent_file_lock_stops_and_restarts_intact_old_program(self):
        self.hooks.replace_failures = 100
        result = self.run_update()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["phase"], "replacing")
        self.assertLessEqual(self.hooks.clock, 4.3)
        self.assertTrue(result["old_version_restarted"])
        self.assertEqual(self.hooks.launches[0][1], [])
        self.assert_old_intact()

    def test_launch_failure_rolls_back_and_starts_original_path_without_new_arguments(self):
        self.hooks.mode = "launch-error"
        result = self.run_update()
        self.assertTrue(result["rolled_back"])
        self.assertTrue(result["old_version_restarted"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.hooks.launches[-1][0], self.target)
        self.assertEqual(self.hooks.launches[-1][1], [])
        self.assert_old_intact()

    def test_new_application_crash_rolls_back_without_killing_any_other_process(self):
        self.hooks.mode = "crash"
        result = self.run_update()
        self.assertTrue(result["rolled_back"])
        self.assertFalse(self.hooks.children[0].terminated)
        self.assertTrue(result["old_version_restarted"])
        self.assert_old_intact()

    def test_ready_timeout_stops_only_our_new_process_tree_then_rolls_back(self):
        self.hooks.mode = "never-ready"
        result = self.run_update()
        self.assertEqual(result["status"], "failed")
        self.assertTrue(self.hooks.children[0].terminated)
        self.assertTrue(result["rolled_back"])
        self.assertTrue(result["old_version_restarted"])
        self.assert_old_intact()

    def test_bad_nonce_foreign_pid_or_reused_pid_cannot_confirm_success(self):
        for mode in ("wrong-nonce", "foreign-pid", "wrong-identity"):
            with self.subTest(mode=mode):
                # Each attempt is an independent staging job
                self.hooks.mode = mode
                result = self.run_update()
                self.assertNotEqual(result["status"], "success")
                self.assertTrue(result["rolled_back"])
                self.assertTrue(self.hooks.children[-2].terminated)
                self.assert_old_intact()
                for path in (self.ready, self.result_path, self.prepared, self.stage / ".update-helper.lock"):
                    path.unlink(missing_ok=True)

    def test_uncertain_child_shutdown_preserves_new_target_and_backup_for_recovery(self):
        self.hooks.mode = "never-ready"
        self.hooks.stop_fails = True
        result = self.run_update()
        self.assertEqual(result["status"], "rollback_failed")
        self.assertFalse(result["rolled_back"])
        self.assertEqual(self.target.read_bytes(), self.new)
        self.assertEqual(Path(result["backup_exe"]).read_bytes(), self.old)
        self.assertEqual(len(self.hooks.launches), 1)

    def test_completed_job_is_idempotent_and_does_not_overwrite_its_result(self):
        initial = self.run_update()
        stored = self.result_path.read_bytes()
        count = len(self.hooks.launches)
        self.assertEqual(self.run_update(), initial)
        self.assertEqual(self.result_path.read_bytes(), stored)
        self.assertEqual(len(self.hooks.launches), count)

    def test_busy_staging_does_not_write_a_competing_result(self):
        (self.stage / ".update-helper.lock").write_text("another helper", encoding="ascii")
        self.assertEqual(self.run_update()["status"], "busy")
        self.assertFalse(self.result_path.exists())
        self.assert_old_intact()

    def test_unrelated_existing_result_is_preserved(self):
        self.result_path.write_text('{"nonce":"unrelated","status":"success"}', encoding="utf-8")
        original = self.result_path.read_bytes()
        self.assertEqual(self.run_update()["status"], "failed")
        self.assertEqual(self.result_path.read_bytes(), original)
        self.assert_old_intact()

    def test_rejects_outside_outputs_aliases_missing_identities_and_unbounded_timeouts(self):
        changes = ({"ready_file": str(self.root / "outside.json")},
                   {"result_file": str(self.payload)}, {"payload_exe": str(self.target)},
                   {"target_exe": str(self.user_file)}, {"parent_creation_times": {}},
                   {"parent_pids": [os.getpid()]}, {"wait_timeout_seconds": 900},
                   {"ready_timeout_seconds": float("nan")}, {"size": True})
        original = dict(self.data)
        for change in changes:
            with self.subTest(change=change):
                self.data = {**original, **change}
                self.write_job()
                with self.assertRaises((UpdateError, OSError)):
                    load_job(self.job_path)
        self.assert_old_intact()

    def test_hardlinked_payload_cannot_alias_installed_executable(self):
        self.payload.unlink()
        os.link(self.target, self.payload)
        with self.assertRaises(UpdateError):
            load_job(self.job_path)

    def test_reparse_point_in_any_ancestor_is_rejected_without_following_it(self):
        original = Path.lstat

        def reparse(path):
            info = original(path)
            if path == self.stage:
                class ReparseInfo:
                    st_mode = info.st_mode
                    st_file_attributes = 0x400
                return ReparseInfo()
            return info

        with patch.object(Path, "lstat", new=reparse), self.assertRaises(UpdateError):
            load_job(self.job_path)


class RestartEnvironmentTests(unittest.TestCase):
    def test_pyinstaller_parent_state_is_removed_without_mutating_environment(self):
        original = {"_PYI_ARCHIVE_FILE": "old.exe", "_PYI_PARENT_PROCESS_LEVEL": "1",
                    "_PYI_APPLICATION_HOME_DIR": "old-temp", "_MEIPASS2": "legacy-temp",
                    "PYINSTALLER_RESET_ENVIRONMENT": "0", "PATH": "kept", "USER_VALUE": "kept"}
        result = clean_restart_environment(original)
        self.assertEqual(result, {"PATH": "kept", "USER_VALUE": "kept", "PYINSTALLER_RESET_ENVIRONMENT": "1"})
        self.assertEqual(original["_PYI_PARENT_PROCESS_LEVEL"], "1")

    @unittest.skipUnless(os.name == "nt", "Windows process identity")
    def test_native_creation_identity_and_reused_pid_wait_do_not_touch_process(self):
        identity = capture_process_identity(os.getpid())
        self.assertEqual(identity["pid"], os.getpid())
        self.assertGreater(identity["creation_time"], 0)
        waiter = WindowsParentWaiter([os.getpid()], {os.getpid(): identity["creation_time"]})
        try:
            self.assertFalse(waiter.all_exited())
        finally:
            waiter.close()
        reused = WindowsParentWaiter([os.getpid()], {os.getpid(): identity["creation_time"]+1})
        try:
            self.assertTrue(reused.all_exited())
        finally:
            reused.close()

    @unittest.skipUnless(os.name == "nt", "Windows named mutex")
    def test_target_mutex_blocks_another_updater_and_releases_for_retry(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "app.exe"
            target.write_bytes(b"fixture")
            first = WindowsTargetMutex(target)
            result = []

            def contender():
                try:
                    lock = WindowsTargetMutex(target)
                except UpdateError:
                    result.append("blocked")
                else:
                    lock.close()
                    result.append("acquired")

            try:
                thread = threading.Thread(target=contender)
                thread.start()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(result, ["blocked"])
            finally:
                first.close()
            contender()
            self.assertEqual(result, ["blocked", "acquired"])


if __name__ == "__main__":
    unittest.main()
