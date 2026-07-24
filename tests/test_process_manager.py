import unittest
from unittest.mock import Mock, patch

from module.webui.process_manager import ProcessManager
from module.webui.setting import State


class TestProcessManagerRegistry(unittest.TestCase):
    def setUp(self):
        self.original_manager = State.manager
        self.original_registry = State.process_registry
        self.original_processes = ProcessManager._processes
        State.manager = Mock()
        State.manager.Queue.return_value = Mock()
        State.process_registry = {}
        ProcessManager._processes = {}

    def tearDown(self):
        State.manager = self.original_manager
        State.process_registry = self.original_registry
        ProcessManager._processes = self.original_processes

    def test_second_session_uses_registered_worker_pid(self):
        State.process_registry["alas"] = 12345
        manager = ProcessManager.get_manager("alas")

        with patch.object(ProcessManager, "_pid_exists", return_value=True):
            self.assertTrue(manager.alive)

    def test_stop_uses_registered_worker_pid_without_local_process(self):
        State.process_registry["alas"] = 12345
        manager = ProcessManager.get_manager("alas")

        with patch.object(ProcessManager, "_kill_process_tree") as kill:
            manager.stop()

        kill.assert_called_once_with(12345)
        self.assertNotIn("alas", State.process_registry)
