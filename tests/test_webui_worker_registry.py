import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from module.webui import worker_registry
from module.webui.setting import State


def _wait_forever():
    while True:
        time.sleep(1)


def _get_gui():
    # spawn 子进程只验证登记锁，不应重复初始化 GUI 日志器。
    import gui

    return gui


class TestWorkerRegistry(unittest.TestCase):
    def test_registry_records_worker_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            registry_file = Path(directory) / "workers.json"
            with patch.object(worker_registry, "WORKER_REGISTRY_FILE", registry_file):
                with patch.object(worker_registry, "_process_created_at", return_value=10.5):
                    worker_registry.claim_owner(100)
                    worker_registry.register_worker(100, "alas", 200)

                self.assertEqual(
                    {"alas": {"created_at": 10.5, "pid": 200}},
                    worker_registry.get_workers(100),
                )
                self.assertEqual(100, worker_registry.get_owner())
                self.assertEqual(
                    {"created_at": 10.5, "pid": 100},
                    worker_registry.get_owner_record(),
                )
                self.assertEqual(
                    {
                        "owner_created_at": 10.5,
                        "owner_pid": 100,
                        "workers": {"alas": {"created_at": 10.5, "pid": 200}},
                    },
                    json.loads(registry_file.read_text(encoding="utf-8")),
                )

    def test_repeated_owner_claim_preserves_registered_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            registry_file = Path(directory) / "workers.json"
            with patch.object(worker_registry, "WORKER_REGISTRY_FILE", registry_file):
                with patch.object(worker_registry, "_process_created_at", return_value=10.5):
                    worker_registry.claim_owner(100)
                    worker_registry.register_worker(100, "alas", 200)
                    worker_registry.claim_owner(100)

                self.assertEqual(
                    {"alas": {"created_at": 10.5, "pid": 200}},
                    worker_registry.get_workers(100),
                )

    def test_active_owner_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            registry_file = Path(directory) / "workers.json"
            with patch.object(worker_registry, "WORKER_REGISTRY_FILE", registry_file):
                with patch.object(worker_registry, "_process_created_at", return_value=10.5):
                    worker_registry.claim_owner(100)
                    with patch.object(worker_registry, "process_matches", return_value=True):
                        with self.assertRaises(
                            worker_registry.WorkerRegistryOwnershipError
                        ):
                            worker_registry.claim_owner(200)

                self.assertEqual(100, worker_registry.get_owner())

    def test_stale_owner_with_workers_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            registry_file = Path(directory) / "workers.json"
            with patch.object(worker_registry, "WORKER_REGISTRY_FILE", registry_file):
                with patch.object(worker_registry, "_process_created_at", return_value=10.5):
                    worker_registry.claim_owner(100)
                    worker_registry.register_worker(100, "alas", 200)
                    with patch.object(worker_registry, "process_matches", return_value=None):
                        with self.assertRaises(
                            worker_registry.WorkerRegistryOwnershipError
                        ):
                            worker_registry.claim_owner(300)

                self.assertEqual(100, worker_registry.get_owner())
                self.assertEqual({"alas"}, worker_registry.get_workers(100).keys())

    def test_concurrent_owner_claim_has_exactly_one_winner(self):
        with tempfile.TemporaryDirectory() as directory:
            registry_file = Path(directory) / "workers.json"
            work_dir = Path(directory) / "claim"
            work_dir.mkdir()
            start_file = work_dir / "start"
            release_file = work_dir / "release"
            script = """
import os
import sys
import time
from pathlib import Path

sys.argv[0] = f"registryclaim{os.getpid()}.py"
from module.webui import worker_registry

work_dir = Path(os.environ["WORKER_REGISTRY_TEST_DIR"])
pid = os.getpid()
Path(os.environ["WORKER_REGISTRY_TEST_REGISTRY"]).parent.mkdir(parents=True, exist_ok=True)
worker_registry.WORKER_REGISTRY_FILE = Path(os.environ["WORKER_REGISTRY_TEST_REGISTRY"])
(work_dir / f"{pid}.ready").write_text("", encoding="utf-8")
while not (work_dir / "start").exists():
    time.sleep(0.01)
try:
    worker_registry.claim_owner(pid)
except worker_registry.WorkerRegistryOwnershipError:
    (work_dir / f"{pid}.result").write_text("conflict", encoding="utf-8")
except Exception as exc:
    (work_dir / f"{pid}.result").write_text(
        f"error:{type(exc).__name__}", encoding="utf-8"
    )
else:
    (work_dir / f"{pid}.result").write_text("claimed", encoding="utf-8")
    while not (work_dir / "release").exists():
        time.sleep(0.01)
"""
            environment = os.environ.copy()
            environment.update(
                {
                    "WORKER_REGISTRY_TEST_DIR": str(work_dir),
                    "WORKER_REGISTRY_TEST_REGISTRY": str(registry_file),
                }
            )
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", script],
                    cwd=Path.cwd(),
                    env=environment,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                for _ in range(2)
            ]
            try:
                deadline = time.monotonic() + 15
                while len(list(work_dir.glob("*.ready"))) < len(processes):
                    if time.monotonic() >= deadline:
                        self.fail("并发 owner 认领子进程未就绪")
                    time.sleep(0.05)
                start_file.touch()

                while len(list(work_dir.glob("*.result"))) < len(processes):
                    if time.monotonic() >= deadline:
                        self.fail("并发 owner 认领子进程未返回结果")
                    time.sleep(0.05)
                results = [
                    result_file.read_text(encoding="utf-8")
                    for result_file in work_dir.glob("*.result")
                ]
                self.assertCountEqual(["claimed", "conflict"], results)
                with patch.object(worker_registry, "WORKER_REGISTRY_FILE", registry_file):
                    self.assertIsNotNone(worker_registry.get_owner())
            finally:
                release_file.touch()
                for process in processes:
                    try:
                        process.communicate(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate(timeout=3)

    def test_dead_webui_reclaims_registered_worker_before_restart(self):
        webui = Mock(pid=12345)
        webui.is_alive.return_value = False
        record = {"pid": 23456, "created_at": 10.5}

        with (
            patch("gui.worker_registry.get_workers", return_value={"alas": record}),
            patch("gui.worker_registry.process_matches", side_effect=[True, None]),
            patch("gui._stop_registered_worker", return_value=True) as stop_worker,
            patch("gui.worker_registry.clear_owner") as clear_owner,
        ):
            self.assertTrue(_get_gui()._stop_webui_process_tree(webui))

        stop_worker.assert_called_once_with(23456, "alas", record)
        clear_owner.assert_called_once_with(12345)

    def test_pid_reuse_after_root_exit_clears_stale_registry(self):
        webui = Mock(pid=12345)
        webui.is_alive.return_value = False

        with (
            patch(
                "gui.worker_registry.get_workers",
                return_value={"alas": {"pid": 23456, "created_at": 10.5}},
            ),
            patch("gui.worker_registry.process_matches", return_value=False),
            patch("gui.worker_registry.clear_owner") as clear_owner,
        ):
            self.assertTrue(_get_gui()._stop_webui_process_tree(webui))

        clear_owner.assert_called_once_with(12345)

    def test_reused_owner_pid_reclaims_workers_instead_of_blocking_restart(self):
        record = {"pid": 12345, "created_at": 10.5}

        with (
            patch("gui.worker_registry.get_owner_record", return_value=record),
            patch("gui.worker_registry.process_matches", return_value=False),
            patch("gui._stop_registered_workers", return_value=True) as stop_workers,
        ):
            self.assertTrue(_get_gui()._recover_orphaned_workers())

        stop_workers.assert_called_once_with(12345, discard_reused=True)

    def test_reused_owner_and_worker_pids_clear_stale_registry(self):
        owner_record = {"pid": 12345, "created_at": 10.5}
        worker_record = {"pid": 23456, "created_at": 11.5}

        with (
            patch("gui.worker_registry.get_owner_record", return_value=owner_record),
            patch("gui.worker_registry.get_workers", return_value={"alas": worker_record}),
            patch("gui.worker_registry.process_matches", side_effect=[False, False]),
            patch("gui.worker_registry.clear_owner") as clear_owner,
            patch("gui._stop_registered_worker") as stop_worker,
        ):
            self.assertTrue(_get_gui()._recover_orphaned_workers())

        clear_owner.assert_called_once_with(12345)
        stop_worker.assert_not_called()

    def test_parent_terminates_registered_real_worker(self):
        context = multiprocessing.get_context("spawn")
        worker = context.Process(target=_wait_forever)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                registry_file = Path(directory) / "workers.json"
                with patch.object(worker_registry, "WORKER_REGISTRY_FILE", registry_file):
                    owner_pid = os.getpid()
                    worker_registry.claim_owner(owner_pid)
                    worker_registry.register_worker(owner_pid, "alas", worker.pid)
                    record = worker_registry.get_workers(owner_pid)["alas"]
                    self.assertTrue(worker_registry.process_matches(record))
                    self.assertTrue(_get_gui()._stop_registered_workers(owner_pid))
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
        finally:
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=3)

    def test_registered_worker_is_revalidated_before_taskkill(self):
        record = {"pid": 23456, "created_at": 10.5}

        with (
            patch("gui.os.name", "nt"),
            patch("gui.worker_registry.process_matches", return_value=False),
            patch("gui.subprocess.run") as taskkill,
        ):
            self.assertFalse(_get_gui()._stop_registered_worker(23456, "alas", record))

        taskkill.assert_not_called()


class TestStateWorkerOwnership(unittest.TestCase):
    def setUp(self):
        self.original_manager = State.manager
        self.original_registry = State.process_registry
        self.original_init = State._init

    def tearDown(self):
        State.manager = self.original_manager
        State.process_registry = self.original_registry
        State._init = self.original_init

    def test_init_shuts_down_manager_when_owner_claim_fails(self):
        manager = Mock()
        manager.dict.return_value = Mock()
        State.manager = None
        State.process_registry = None
        State._init = False

        with (
            patch("module.webui.setting.multiprocessing.Manager", return_value=manager),
            patch(
                "module.webui.worker_registry.claim_owner",
                side_effect=worker_registry.WorkerRegistryOwnershipError("owner exists"),
            ),
        ):
            with self.assertRaises(worker_registry.WorkerRegistryOwnershipError):
                State.init()

        manager.shutdown.assert_called_once_with()
        self.assertIsNone(State.manager)
        self.assertIsNone(State.process_registry)
        self.assertFalse(State._init)
