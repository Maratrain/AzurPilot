import threading
import unittest
from unittest.mock import Mock, patch

from module.webui.fake_pil_module import remove_fake_pil_module

remove_fake_pil_module()

from module.webui.app_developer_tools import prepare_webui_restart, request_webui_restart
from module.webui.setting import State


class TestDeveloperToolsRestart(unittest.TestCase):
    def setUp(self):
        self.original_restart_event = State.restart_event
        self.original_restart_requested = State._restart_requested
        State.restart_event = Mock()
        State._restart_requested = False

    def tearDown(self):
        State.restart_event = self.original_restart_event
        State._restart_requested = self.original_restart_requested

    def test_prepare_restart_saves_running_instance_names(self):
        instances = [Mock(config_name="alas"), Mock(config_name="farm")]

        with (
            patch(
                "module.webui.app_developer_tools.ProcessManager.running_instances",
                return_value=instances,
            ),
            patch("module.webui.app_developer_tools.atomic_write") as write_marker,
        ):
            self.assertTrue(prepare_webui_restart())

        write_marker.assert_called_once_with("./config/reloadalas", "alas\nfarm\n")

    def test_prepare_restart_cancels_when_marker_write_fails(self):
        with (
            patch(
                "module.webui.app_developer_tools.ProcessManager.running_instances",
                return_value=[],
            ),
            patch(
                "module.webui.app_developer_tools.atomic_write",
                side_effect=OSError("read-only"),
            ),
            patch("module.webui.app_developer_tools.logger.exception_context") as log_error,
        ):
            self.assertFalse(prepare_webui_restart())

        log_error.assert_called_once()

    def test_manual_restart_does_not_interrupt_active_update_transaction(self):
        entered = threading.Event()
        release = threading.Event()

        def hold_update_transaction():
            with State.restart_lock:
                entered.set()
                release.wait(timeout=2)

        holder = threading.Thread(target=hold_update_transaction)
        holder.start()
        self.assertTrue(entered.wait(timeout=2))
        try:
            with (
                patch(
                    "module.webui.app_developer_tools.prepare_webui_restart"
                ) as prepare_restart,
                patch("module.webui.app_developer_tools.clearup") as clearup,
            ):
                self.assertFalse(request_webui_restart())

            prepare_restart.assert_not_called()
            clearup.assert_not_called()
            State.restart_event.set.assert_not_called()
            self.assertFalse(State._restart_requested)
        finally:
            release.set()
            holder.join(timeout=2)

    def test_manual_restart_notifies_parent_after_cleanup(self):
        order = []

        with (
            patch(
                "module.webui.app_developer_tools.prepare_webui_restart",
                side_effect=lambda: order.append("prepare") or True,
            ),
            patch(
                "module.webui.app_developer_tools.clearup",
                side_effect=lambda: order.append("clearup") or True,
            ),
        ):
            State.restart_event.set.side_effect = lambda: order.append("reload")
            self.assertTrue(request_webui_restart())

        self.assertEqual(["prepare", "clearup", "reload"], order)
        self.assertTrue(State._restart_requested)
