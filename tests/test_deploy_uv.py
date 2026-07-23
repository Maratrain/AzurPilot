import queue
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from deploy import uv


class TestUvCommandOutput(unittest.TestCase):
    def test_run_captures_merged_output(self):
        completed = subprocess.CompletedProcess(["uv", "sync"], 0, "resolved\ninstalled\n")
        with patch("deploy.uv.subprocess.run", return_value=completed):
            output = uv._run(["uv", "sync"], Path("."), capture_output=True)

        self.assertEqual(output, "resolved\ninstalled\n")

    def test_run_preserves_error_output(self):
        completed = subprocess.CompletedProcess(["uv", "sync"], 2, "error: access denied\n")
        with patch("deploy.uv.subprocess.run", return_value=completed):
            with self.assertRaises(subprocess.CalledProcessError) as context:
                uv._run(["uv", "sync"], Path("."), capture_output=True)

        self.assertEqual(context.exception.returncode, 2)
        self.assertEqual(uv.command_output(context.exception), "error: access denied\n")

    def test_run_output_does_not_mix_stderr_into_python_path(self):
        completed = Mock(returncode=0, stdout="C:/Python/python.exe\n", stderr="warning\n")
        with patch("deploy.uv.subprocess.run", return_value=completed):
            output = uv._run_output(["uv", "python", "find"], Path("."))

        self.assertEqual(output, "C:/Python/python.exe")

    def test_dependency_service_reports_sync_result(self):
        requests = queue.Queue()
        responses = queue.Queue()
        requests.put("sync")
        requests.put("shutdown")
        result = uv.UvCommandResult(command=["uv", "sync"], output="audited 1 package\n")

        with patch("deploy.uv.sync_project_venv", return_value=result):
            uv.dependency_sync_service(requests, responses, root=Path("."))

        self.assertEqual(
            responses.get_nowait(),
            {
                "success": True,
                "command": ["uv", "sync"],
                "output": "audited 1 package\n",
                "error": "",
            },
        )

    def test_dependency_service_exits_when_parent_process_is_gone(self):
        requests = Mock()
        requests.get.side_effect = queue.Empty
        parent = Mock()
        parent.is_alive.return_value = False

        with patch("deploy.uv.multiprocessing.parent_process", return_value=parent):
            uv.dependency_sync_service(requests, queue.Queue(), root=Path("."))

        parent.is_alive.assert_called_once_with()
