import asyncio
import subprocess
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pywebio.session as pywebio_session
from pywebio.output import put_text
from pywebio.session import local
from pywebio.session.threadbased import ThreadBasedSession
from rich.text import Text
from starlette.websockets import WebSocketState

from module.webui.app_home import HomeMixin
from module.webui.app_shell import AppShellMixin
from module.webui.app_task_config import TaskConfigMixin
from module.webui.fastapi import (
    SafeWebSocketConnection,
    WEBSOCKET_MAX_PENDING_MESSAGES,
)
from module.webui.utils import Task, TaskHandler
from module.webui.widgets import RichLog


class _RecordingWebSocket:
    def __init__(self) -> None:
        self.application_state = WebSocketState.CONNECTED
        self.messages = []
        self.close_count = 0
        self.concurrent_sends = 0
        self.max_concurrent_sends = 0

    async def send_json(self, message) -> None:
        self.concurrent_sends += 1
        self.max_concurrent_sends = max(
            self.max_concurrent_sends, self.concurrent_sends
        )
        await asyncio.sleep(0)
        self.messages.append(message)
        self.concurrent_sends -= 1

    async def close(self) -> None:
        self.close_count += 1
        self.application_state = WebSocketState.DISCONNECTED


class _BlockedWebSocket(_RecordingWebSocket):
    async def send_json(self, message) -> None:
        await asyncio.Event().wait()


class TestSafeWebSocketConnection(unittest.TestCase):
    def test_messages_share_one_ordered_sender_task(self):
        run_async_test(self._assert_messages_share_one_ordered_sender_task())

    async def _assert_messages_share_one_ordered_sender_task(self):
        websocket = _RecordingWebSocket()
        connection = SafeWebSocketConnection(
            websocket, asyncio.get_running_loop()
        )

        for index in range(100):
            connection.write_message({"index": index})

        sender_task = connection._sender_task
        self.assertIsNotNone(sender_task)
        self.assertFalse(sender_task.done())

        await sender_task
        self.assertEqual(
            list(range(100)),
            [message["index"] for message in websocket.messages],
        )
        self.assertEqual(1, websocket.max_concurrent_sends)

    def test_slow_client_backlog_is_bounded(self):
        run_async_test(self._assert_slow_client_backlog_is_bounded())

    async def _assert_slow_client_backlog_is_bounded(self):
        websocket = _BlockedWebSocket()
        connection = SafeWebSocketConnection(
            websocket, asyncio.get_running_loop()
        )

        for index in range(WEBSOCKET_MAX_PENDING_MESSAGES + 1):
            connection.write_message({"index": index})

        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertLessEqual(
            len(connection._pending_messages),
            WEBSOCKET_MAX_PENDING_MESSAGES,
        )
        self.assertEqual(1, websocket.close_count)
        self.assertTrue(connection.closed())
        self.assertEqual(0, len(connection._pending_messages))

        connection.write_message({"index": "after-close-1"})
        connection.write_message({"index": "after-close-2"})
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(0, len(connection._pending_messages))
        self.assertEqual(1, websocket.close_count)
        self.assertTrue(connection.closed())


class TestTaskHandlerScheduling(unittest.TestCase):
    def test_overdue_task_does_not_run_in_catch_up_burst(self):
        calls = 0
        first_call = threading.Event()

        def run_once():
            nonlocal calls
            calls += 1
            first_call.set()

        handler = TaskHandler()
        task = Task(
            iter_task(run_once),
            delay=0.2,
            next_run=time.time() - 60,
        )
        handler.add_task(task)
        handler.start()
        try:
            self.assertTrue(first_call.wait(timeout=1))
            time.sleep(0.05)
            self.assertEqual(1, calls)
        finally:
            handler.stop()

    def test_wake_task_interrupts_scheduler_wait(self):
        called = threading.Event()
        handler = TaskHandler()
        task = Task(
            iter_task(called.set),
            delay=60,
            next_run=time.time() + 60,
            name="deferred",
        )
        handler.add_task(task)
        handler.start()
        try:
            self.assertTrue(handler.wake_task("deferred"))
            self.assertTrue(called.wait(timeout=1))
        finally:
            handler.stop()

    def test_wake_during_execution_is_not_lost(self):
        first_call_started = threading.Event()
        release_first_call = threading.Event()
        second_call = threading.Event()
        calls = 0

        def run_task():
            nonlocal calls
            calls += 1
            if calls == 1:
                first_call_started.set()
                release_first_call.wait(timeout=1)
            else:
                second_call.set()

        handler = TaskHandler()
        handler.add_task(Task(iter_task(run_task), delay=60, name="running"))
        handler.start()
        try:
            self.assertTrue(first_call_started.wait(timeout=1))
            self.assertTrue(handler.wake_task("running"))
            release_first_call.set()
            self.assertTrue(second_call.wait(timeout=1))
        finally:
            handler.stop()


class TestRichLogRendering(unittest.TestCase):
    def test_batch_render_preserves_all_lines(self):
        log = RichLog("log")
        html = log.render_many((Text("第一行"), Text("第二行")))

        self.assertIn("第一行", html)
        self.assertIn("第二行", html)

    def test_batch_render_empty_iterable_returns_empty_string(self):
        log = RichLog("log")

        self.assertEqual("", log.render_many([]))


class TestInitialRendering(unittest.TestCase):
    def test_shell_is_sent_before_localstorage_roundtrip(self):
        events = []

        class StopAfterLocalStorage(Exception):
            pass

        def read_localstorage(keys):
            events.append(("localstorage", keys))
            raise StopAfterLocalStorage

        gui = SimpleNamespace(
            theme="default",
            is_mobile=False,
            mount_shell=lambda: events.append("shell"),
        )
        with (
            patch("module.webui.app_home.set_env"),
            patch("module.webui.app_home.load_webui_styles"),
            patch("module.webui.app_home.is_oobe_needed", return_value=False),
            patch(
                "module.webui.app_home.get_localstorage_values",
                side_effect=read_localstorage,
            ),
            self.assertRaises(StopAfterLocalStorage),
        ):
            HomeMixin.run(gui)

        self.assertEqual(
            ["shell", ("localstorage", ("clarity_notice_shown", "aside"))],
            events,
        )

    def test_aside_date_does_not_trigger_network_time_refresh(self):
        gui = SimpleNamespace(
            af_flag=False,
            refresh_aside_labels=lambda: None,
            refresh_aside_instances=lambda force=False: None,
        )
        with (
            patch("module.webui.app_shell.put_scope"),
            patch(
                "module.webui.app_shell.current_time",
                side_effect=AssertionError("首屏不应读取网络时间"),
            ),
        ):
            AppShellMixin.set_aside.__wrapped__(gui)


class TestTaskConfigRendering(unittest.TestCase):
    def test_subconfig_fields_are_sent_as_one_nested_output(self):
        commands = []
        closed = threading.Event()
        previous_session_classes = pywebio_session._active_session_cls.copy()

        def collect_commands(session):
            batch = session.get_task_commands()
            commands.extend(batch if isinstance(batch, list) else [batch])

        def render_subconfig():
            gui = object.__new__(TaskConfigMixin)
            local.gui = gui
            gui.alas_name = "test"
            gui.alas_config = SimpleNamespace(
                read_file=lambda _: {
                    "Alas": {"Emulator": {"PackageName": "cn"}},
                    "Synthetic": {"Group": {"First": "a", "Second": "b"}},
                }
            )
            gui.ALAS_ARGS = {
                "Synthetic": {
                    "Group": {
                        "First": {"type": "input", "value": "a"},
                        "Second": {"type": "input", "value": "b"},
                    }
                }
            }
            gui.init_menu = lambda name: None
            gui.set_title = lambda text: None
            gui._bind_config_watcher = lambda path: None
            gui._build_navigator = lambda group: put_text(group[0])
            gui.alas_set_group("Synthetic")

        pywebio_session._active_session_cls[:] = [ThreadBasedSession]
        try:
            with patch(
                "module.webui.app_task_config.t",
                side_effect=lambda key, *args, **kwargs: (
                    "" if key.endswith(".help") else key
                ),
            ):
                ThreadBasedSession(
                    render_subconfig,
                    session_info=SimpleNamespace(),
                    on_task_command=collect_commands,
                    on_session_close=closed.set,
                )
                self.assertTrue(closed.wait(timeout=2))
        finally:
            pywebio_session._active_session_cls[:] = previous_session_classes

        output_commands = [
            command for command in commands if command.get("command") == "output"
        ]
        self.assertEqual(1, len(output_commands))
        self.assertEqual("scope", output_commands[0]["spec"]["type"])
        self.assertEqual(
            "pywebio-scope-_groups",
            output_commands[0]["spec"]["dom_id"],
        )


class TestWebUIImports(unittest.TestCase):
    def test_entry_does_not_eagerly_import_image_stack(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import sys; import module.webui.app; "
                    "print(int('numpy' in sys.modules), "
                    "int('cv2' in sys.modules), "
                    "int('module.statistics.azurstats' in sys.modules))"
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual("0 0 0", result.stdout.strip().splitlines()[-1])


def iter_task(func):
    """创建符合 Task 协议的简单生成器。"""
    yield
    while True:
        func()
        yield


def run_async_test(coroutine):
    """复用当前事件循环，避免替换 WebUI 已配置的默认循环。"""
    created = False
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        created = True
    try:
        loop.run_until_complete(coroutine)
    finally:
        if created:
            loop.close()


if __name__ == "__main__":
    unittest.main()
