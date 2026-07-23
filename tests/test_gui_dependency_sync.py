import queue
import unittest
from unittest.mock import Mock, patch

import gui


class TestGuiDependencySync(unittest.TestCase):
    def test_sync_dependencies_logs_result_and_returns_success(self):
        service = Mock()
        service.is_alive.return_value = True
        request_queue = Mock()
        response_queue = queue.Queue()
        response_queue.put(
            {
                "success": True,
                "command": ["uv", "sync"],
                "output": "Installed 1 package\n",
                "error": "",
            }
        )

        with patch("gui.log_command_output") as log_output:
            self.assertTrue(gui._sync_dependencies(service, request_queue, response_queue))

        request_queue.put.assert_called_once_with("sync")
        log_output.assert_called_once_with(gui.logger, "Installed 1 package\n")

    def test_sync_dependencies_does_not_restart_after_failure(self):
        service = Mock()
        service.is_alive.return_value = True
        request_queue = Mock()
        response_queue = queue.Queue()
        response_queue.put(
            {
                "success": False,
                "command": ["uv", "sync"],
                "output": "error: access denied\n",
                "error": "Command returned exit status 2",
            }
        )

        with patch("gui.log_command_output"):
            self.assertFalse(gui._sync_dependencies(service, request_queue, response_queue))

        request_queue.put.assert_called_once_with("sync")
