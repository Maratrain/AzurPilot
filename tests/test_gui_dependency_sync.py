import errno
import multiprocessing
import queue
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call, patch

import gui


async def _test_asgi_app(scope, receive, send):
    return None


def _run_test_uvicorn(ready_event):
    import uvicorn

    config = uvicorn.Config(
        _test_asgi_app,
        host="127.0.0.1",
        port=0,
        lifespan="off",
        log_level="critical",
    )
    gui._run_uvicorn_server(config, ready_event=ready_event)


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

    def test_sync_dependencies_times_out_when_service_does_not_respond(self):
        service = Mock()
        service.is_alive.return_value = True
        request_queue = Mock()

        self.assertFalse(
            gui._sync_dependencies(service, request_queue, queue.Queue(), timeout=0)
        )

        request_queue.put.assert_called_once_with("sync")

    def test_sync_dependencies_returns_false_when_request_queue_is_closed(self):
        service = Mock()
        service.is_alive.return_value = True
        request_queue = Mock()
        request_queue.put.side_effect = BrokenPipeError("queue closed")

        self.assertFalse(gui._sync_dependencies(service, request_queue, Mock()))

    def test_sync_dependencies_returns_false_when_response_queue_is_closed(self):
        service = Mock()
        service.is_alive.return_value = True
        request_queue = Mock()
        response_queue = Mock()
        response_queue.get.side_effect = EOFError("queue closed")

        self.assertFalse(gui._sync_dependencies(service, request_queue, response_queue))

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

    def test_sync_dependencies_redacts_command_and_error_logs(self):
        service = Mock()
        service.is_alive.return_value = True
        request_queue = Mock()
        response_queue = queue.Queue()
        response_queue.put(
            {
                "success": False,
                "command": [
                    "uv",
                    "sync",
                    "--default-index",
                    "https://user:password@example.test/simple?token=secret",
                ],
                "output": "",
                "error": "Authorization: Bearer private-token",
            }
        )

        with (
            patch("gui.log_command_output"),
            patch.object(gui.logger, "info") as info,
            patch.object(gui.logger, "critical") as critical,
        ):
            self.assertFalse(gui._sync_dependencies(service, request_queue, response_queue))

        info_text = "\n".join(call.args[0] for call in info.call_args_list)
        error_text = critical.call_args.args[0]
        self.assertNotIn("user:password", info_text)
        self.assertNotIn("secret", info_text)
        self.assertNotIn("private-token", error_text)
        self.assertIn("https://***@example.test/simple?token=***", info_text)
        self.assertIn("Authorization:***", error_text)

    def test_pending_sync_clears_marker_only_after_success(self):
        service = Mock()
        request_queue = Mock()
        response_queue = Mock()

        with (
            patch("gui.is_dependency_sync_pending", return_value=True),
            patch("gui._sync_dependencies", return_value=True) as sync,
            patch("gui.clear_dependency_sync_pending") as clear_pending,
        ):
            self.assertTrue(
                gui._complete_pending_dependency_sync(
                    service,
                    request_queue,
                    response_queue,
                )
            )

        sync.assert_called_once_with(service, request_queue, response_queue)
        clear_pending.assert_called_once_with()

    def test_pending_sync_keeps_marker_after_failure(self):
        with (
            patch("gui.is_dependency_sync_pending", return_value=True),
            patch("gui._sync_dependencies", return_value=False),
            patch("gui.clear_dependency_sync_pending") as clear_pending,
        ):
            self.assertFalse(
                gui._complete_pending_dependency_sync(Mock(), Mock(), Mock())
            )

        clear_pending.assert_not_called()


class TestGuiProcessStop(unittest.TestCase):
    def test_stop_process_escalates_to_kill_and_confirms_exit(self):
        process = Mock()
        process.pid = 12345
        process.is_alive.side_effect = [True, True, False]

        self.assertTrue(gui._stop_process(process, timeout=5))

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        process.join.assert_has_calls([call(timeout=5), call(timeout=3)])

    def test_stop_process_reports_failure_when_process_survives_kill(self):
        process = Mock()
        process.pid = 12345
        process.is_alive.side_effect = [True, True, True]

        self.assertFalse(gui._stop_process(process, timeout=5))

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()

    def test_posix_tree_stop_waits_for_enumerated_children(self):
        process = Mock(pid=12345)
        process.is_alive.side_effect = [True, False]
        child = Mock(pid=12346)
        parent = Mock()
        parent.children.return_value = [child]
        psutil = Mock(NoSuchProcess=ProcessLookupError)
        psutil.Process.return_value = parent
        psutil.wait_procs.return_value = ([child], [])

        with (
            patch("gui.os.name", "posix"),
            patch.dict(sys.modules, {"psutil": psutil}),
        ):
            self.assertTrue(gui._stop_process_tree(process, "WebUI"))

        child.kill.assert_called_once_with()
        process.kill.assert_called_once_with()
        psutil.wait_procs.assert_called_once_with([child], timeout=3)

    def test_posix_tree_stop_rejects_alive_enumerated_children(self):
        process = Mock(pid=12345)
        process.is_alive.side_effect = [True, False]
        child = Mock(pid=12346)
        parent = Mock()
        parent.children.return_value = [child]
        psutil = Mock(NoSuchProcess=ProcessLookupError)
        psutil.Process.return_value = parent
        psutil.wait_procs.return_value = ([], [child])

        with (
            patch("gui.os.name", "posix"),
            patch.dict(sys.modules, {"psutil": psutil}),
        ):
            self.assertFalse(gui._stop_process_tree(process, "WebUI"))

    def test_posix_tree_stop_accepts_root_exit_during_child_enumeration(self):
        process = Mock(pid=12345)
        process.is_alive.side_effect = [True, False]
        psutil = Mock(NoSuchProcess=ProcessLookupError)
        psutil.Process.side_effect = ProcessLookupError

        with (
            patch("gui.os.name", "posix"),
            patch.dict(sys.modules, {"psutil": psutil}),
        ):
            self.assertTrue(gui._stop_process_tree(process, "WebUI"))

        psutil.wait_procs.assert_not_called()


class TestGuiDualStackSockets(unittest.TestCase):
    def test_dual_stack_port_zero_reuses_ipv4_port_for_ipv6(self):
        ipv4_socket = Mock()
        ipv4_socket.getsockname.return_value = ("0.0.0.0", 23456)
        ipv6_socket = Mock()

        with patch("gui.socket.socket", side_effect=[ipv4_socket, ipv6_socket]):
            sockets = gui._create_dual_stack_sockets(0)

        self.assertEqual([ipv4_socket, ipv6_socket], sockets)
        ipv4_socket.bind.assert_called_once_with(("0.0.0.0", 0))
        ipv6_socket.bind.assert_called_once_with(("::", 23456))

    def test_default_wildcard_falls_back_to_ipv4_when_ipv6_is_unavailable(self):
        ipv4_socket = Mock()
        ipv4_socket.getsockname.return_value = ("0.0.0.0", 23456)

        with patch(
            "gui.socket.socket",
            side_effect=[ipv4_socket, OSError(errno.EAFNOSUPPORT, "IPv6 is unavailable")],
        ):
            sockets = gui._create_dual_stack_sockets(
                23456,
                allow_ipv6_fallback=True,
            )

        self.assertEqual([ipv4_socket], sockets)
        ipv4_socket.close.assert_not_called()

    def test_default_wildcard_falls_back_when_ipv6_socket_option_is_unsupported(self):
        ipv4_socket = Mock()
        ipv4_socket.getsockname.return_value = ("0.0.0.0", 23456)
        ipv6_socket = Mock()
        ipv6_socket.setsockopt.side_effect = OSError(
            errno.ENOPROTOOPT,
            "IPv6 socket option is unavailable",
        )

        with patch("gui.socket.socket", side_effect=[ipv4_socket, ipv6_socket]):
            sockets = gui._create_dual_stack_sockets(
                23456,
                allow_ipv6_fallback=True,
            )

        self.assertEqual([ipv4_socket], sockets)
        ipv6_socket.close.assert_called_once_with()

    def test_explicit_ipv6_host_does_not_silently_fall_back_to_ipv4(self):
        ipv4_socket = Mock()
        ipv4_socket.getsockname.return_value = ("0.0.0.0", 23456)

        with (
            patch(
                "gui.socket.socket",
                side_effect=[ipv4_socket, OSError(errno.EAFNOSUPPORT, "IPv6 is unavailable")],
            ),
            self.assertRaisesRegex(OSError, "IPv6 is unavailable"),
        ):
            gui._create_dual_stack_sockets(23456)

        ipv4_socket.close.assert_called_once_with()

    def test_default_wildcard_does_not_hide_ipv6_port_conflict(self):
        ipv4_socket = Mock()
        ipv4_socket.getsockname.return_value = ("0.0.0.0", 23456)

        with (
            patch(
                "gui.socket.socket",
                side_effect=[ipv4_socket, OSError(errno.EADDRINUSE, "address in use")],
            ),
            self.assertRaisesRegex(OSError, "address in use"),
        ):
            gui._create_dual_stack_sockets(
                23456,
                allow_ipv6_fallback=True,
            )

        ipv4_socket.close.assert_called_once_with()

    def test_default_wildcard_routes_through_dual_stack_listener(self):
        deployment = SimpleNamespace(
            WebuiHost="127.0.0.1",
            WebuiPort=25548,
            WebuiSSLKey=None,
            WebuiSSLCert=None,
        )
        uvicorn_config = Mock(backlog=2048)
        listeners = [Mock(), Mock()]

        with (
            patch.object(gui.State, "deploy_config", deployment),
            patch.object(sys, "argv", ["gui.py", "--host", "0.0.0.0", "--port", "23456"]),
            patch("gui.sys.platform", "linux"),
            patch("uvicorn.Config", return_value=uvicorn_config) as config_factory,
            patch("gui._create_dual_stack_sockets", return_value=listeners) as create_sockets,
            patch("gui._run_uvicorn_server") as run_server,
        ):
            gui.func(None)

        self.assertEqual("0.0.0.0", config_factory.call_args.kwargs["host"])
        create_sockets.assert_called_once_with(
            23456,
            backlog=2048,
            allow_ipv6_fallback=True,
        )
        run_server.assert_called_once_with(
            uvicorn_config,
            ready_event=None,
            sockets=listeners,
        )

    def test_explicit_ipv6_routes_through_dual_stack_without_fallback(self):
        deployment = SimpleNamespace(
            WebuiHost="127.0.0.1",
            WebuiPort=25548,
            WebuiSSLKey=None,
            WebuiSSLCert=None,
        )
        uvicorn_config = Mock(backlog=2048)
        listeners = [Mock(), Mock()]

        with (
            patch.object(gui.State, "deploy_config", deployment),
            patch.object(sys, "argv", ["gui.py", "--host", "::", "--port", "23456"]),
            patch("gui.sys.platform", "linux"),
            patch("uvicorn.Config", return_value=uvicorn_config) as config_factory,
            patch("gui._create_dual_stack_sockets", return_value=listeners) as create_sockets,
            patch("gui._run_uvicorn_server") as run_server,
        ):
            gui.func(None)

        self.assertEqual("::", config_factory.call_args.kwargs["host"])
        create_sockets.assert_called_once_with(
            23456,
            backlog=2048,
            allow_ipv6_fallback=False,
        )
        run_server.assert_called_once_with(
            uvicorn_config,
            ready_event=None,
            sockets=listeners,
        )

    def test_loopback_host_uses_standard_uvicorn_listener(self):
        deployment = SimpleNamespace(
            WebuiHost="0.0.0.0",
            WebuiPort=25548,
            WebuiSSLKey=None,
            WebuiSSLCert=None,
        )
        uvicorn_config = Mock(backlog=2048)

        with (
            patch.object(gui.State, "deploy_config", deployment),
            patch.object(sys, "argv", ["gui.py", "--host", "127.0.0.1", "--port", "23456"]),
            patch("gui.sys.platform", "linux"),
            patch("uvicorn.Config", return_value=uvicorn_config) as config_factory,
            patch("gui._create_dual_stack_sockets") as create_sockets,
            patch("gui._run_uvicorn_server") as run_server,
        ):
            gui.func(None)

        self.assertEqual("127.0.0.1", config_factory.call_args.kwargs["host"])
        create_sockets.assert_not_called()
        run_server.assert_called_once_with(uvicorn_config, ready_event=None)


class TestDependencySyncServiceStop(unittest.TestCase):
    def test_windows_tree_stop_targets_sync_service_pid(self):
        process = Mock()
        process.pid = 12345
        process.is_alive.side_effect = [True, False]
        completed = Mock(returncode=0)

        with (
            patch("gui.os.name", "nt"),
            patch("gui.subprocess.run", return_value=completed) as taskkill,
        ):
            self.assertTrue(gui._stop_dependency_sync_service_tree(process))

        self.assertEqual(
            ["taskkill", "/PID", "12345", "/T", "/F"],
            taskkill.call_args.args[0],
        )
        process.join.assert_called_once_with(timeout=3)

    def test_windows_tree_stop_accepts_natural_exit_after_taskkill_failure(self):
        process = Mock()
        process.pid = 12345
        process.is_alive.side_effect = [True, False, False]
        completed = Mock(returncode=1)

        with (
            patch("gui.os.name", "nt"),
            patch("gui.subprocess.run", return_value=completed),
        ):
            self.assertTrue(gui._stop_process_tree(process, "WebUI"))

        process.kill.assert_not_called()
        process.join.assert_called_once_with(timeout=3)

    def test_webui_tree_stop_does_not_clear_workers_when_root_survives(self):
        process = Mock(pid=12345)

        with (
            patch("gui._stop_process_tree", return_value=False),
            patch("gui._stop_registered_workers") as stop_workers,
        ):
            self.assertFalse(gui._stop_webui_process_tree(process))

        stop_workers.assert_not_called()


class TestGuiReadyHandshake(unittest.TestCase):
    def test_wait_for_ready_returns_false_when_child_exits_early(self):
        process = Mock()
        process.is_alive.return_value = False
        ready_event = Mock()
        ready_event.wait.return_value = False

        self.assertFalse(gui._wait_for_webui_ready(process, ready_event, timeout=1))

    def test_wait_for_ready_returns_true_after_server_listens(self):
        process = Mock()
        process.is_alive.return_value = True
        ready_event = Mock()
        ready_event.wait.return_value = True

        self.assertTrue(gui._wait_for_webui_ready(process, ready_event, timeout=1))

    def test_spawned_uvicorn_can_stop_and_restart_after_ready(self):
        context = multiprocessing.get_context("spawn")
        first_ready_event = context.Event()
        first = context.Process(target=_run_test_uvicorn, args=(first_ready_event,))
        first.start()
        try:
            self.assertTrue(gui._wait_for_webui_ready(first, first_ready_event, timeout=10))
        finally:
            self.assertTrue(gui._stop_process(first))

        second_ready_event = context.Event()
        second = context.Process(target=_run_test_uvicorn, args=(second_ready_event,))
        second.start()
        try:
            self.assertTrue(gui._wait_for_webui_ready(second, second_ready_event, timeout=10))
            self.assertNotEqual(first.pid, second.pid)
        finally:
            self.assertTrue(gui._stop_process(second))


class TestWebUISupervisor(unittest.TestCase):
    @staticmethod
    def _service():
        return Mock(), Mock(), Mock()

    def test_supervisor_retries_startup_only_up_to_limit(self):
        processes = [Mock(pid=100 + index) for index in range(3)]

        with (
            patch("gui._recover_orphaned_workers", return_value=True),
            patch("gui.is_dependency_sync_pending", return_value=False),
            patch("gui._start_dependency_sync_service", return_value=self._service()),
            patch("gui._stop_dependency_sync_service", return_value=True),
            patch("gui.Process", side_effect=processes) as process_factory,
            patch("gui._wait_for_webui_ready", return_value=False) as wait_ready,
            patch("gui._stop_webui_process_tree", return_value=True),
            patch("gui.time.sleep") as sleep,
            patch("gui.logger.error_context"),
        ):
            gui.run_webui_supervisor()

        self.assertEqual(3, process_factory.call_count)
        self.assertEqual(3, wait_ready.call_count)
        sleep.assert_has_calls([call(1), call(2)])
        for process in processes:
            process.start.assert_called_once_with()

    def test_supervisor_syncs_pending_environment_before_creating_webui(self):
        with (
            patch("gui._recover_orphaned_workers", return_value=True),
            patch("gui.is_dependency_sync_pending", return_value=True),
            patch("gui._start_dependency_sync_service", return_value=self._service()),
            patch("gui._complete_pending_dependency_sync", return_value=False) as sync,
            patch("gui._stop_dependency_sync_service", return_value=True),
            patch("gui.Process") as process_factory,
            patch("gui.logger.error_context"),
        ):
            gui.run_webui_supervisor()

        sync.assert_called_once_with(ANY, ANY, ANY, force=True)
        process_factory.assert_not_called()

    def test_supervisor_does_not_replace_child_that_failed_to_stop(self):
        restart_event = Mock()
        restart_event.wait.return_value = True
        dependency_sync_event = Mock()
        dependency_sync_event.is_set.return_value = False
        process = Mock(pid=12345)

        with (
            patch("gui._recover_orphaned_workers", return_value=True),
            patch("gui.is_dependency_sync_pending", return_value=False),
            patch("gui._start_dependency_sync_service", return_value=self._service()),
            patch("gui._stop_dependency_sync_service", return_value=True),
            patch("gui.Event", side_effect=[restart_event, dependency_sync_event, Mock()]),
            patch("gui.Process", return_value=process) as process_factory,
            patch("gui._wait_for_webui_ready", return_value=True),
            patch("gui._stop_webui_process_tree", side_effect=[False, True, True]),
            patch("gui.logger.error_context"),
        ):
            gui.run_webui_supervisor()

        process_factory.assert_called_once_with(
            target=gui.func,
            args=(restart_event, dependency_sync_event, ANY),
            name="gui",
        )

    def test_supervisor_retries_ready_child_that_exits_unexpectedly(self):
        processes = [Mock(pid=100 + index) for index in range(3)]
        for process in processes:
            process.is_alive.return_value = False
        events = []
        for _ in processes:
            restart_event = Mock()
            restart_event.wait.return_value = False
            events.extend([restart_event, Mock(), Mock()])

        with (
            patch("gui._recover_orphaned_workers", return_value=True),
            patch("gui.is_dependency_sync_pending", return_value=False),
            patch("gui._start_dependency_sync_service", return_value=self._service()),
            patch("gui._stop_dependency_sync_service", return_value=True),
            patch("gui.Event", side_effect=events),
            patch("gui.Process", side_effect=processes) as process_factory,
            patch("gui._wait_for_webui_ready", return_value=True),
            patch("gui._stop_webui_process_tree", return_value=True),
            patch("gui.time.sleep") as sleep,
            patch("gui.logger.error_context") as error_context,
        ):
            gui.run_webui_supervisor()

        self.assertEqual(3, process_factory.call_count)
        sleep.assert_has_calls([call(1), call(2)])
        error_context.assert_called_once()

    def test_supervisor_does_not_replace_child_when_unexpected_exit_cleanup_fails(self):
        process = Mock(pid=100)
        process.is_alive.return_value = False
        restart_event = Mock()
        restart_event.wait.return_value = False

        with (
            patch("gui._recover_orphaned_workers", return_value=True),
            patch("gui.is_dependency_sync_pending", return_value=False),
            patch("gui._start_dependency_sync_service", return_value=self._service()),
            patch("gui._stop_dependency_sync_service", return_value=True),
            patch("gui.Event", side_effect=[restart_event, Mock(), Mock()]),
            patch("gui.Process", return_value=process) as process_factory,
            patch("gui._wait_for_webui_ready", return_value=True),
            patch("gui._stop_webui_process_tree", side_effect=[False, True]),
            patch("gui.time.sleep"),
            patch("gui.logger.error_context") as error_context,
        ):
            gui.run_webui_supervisor()

        process_factory.assert_called_once_with(
            target=gui.func,
            args=(restart_event, ANY, ANY),
            name="gui",
        )
        error_context.assert_called_once()

    def test_supervisor_does_not_restart_after_unexpected_exit_with_pending_sync(self):
        process = Mock(pid=100)
        process.is_alive.return_value = False
        restart_event = Mock()
        restart_event.wait.return_value = False

        with (
            patch("gui._recover_orphaned_workers", return_value=True),
            patch("gui.is_dependency_sync_pending", side_effect=[False, True]),
            patch("gui._start_dependency_sync_service", return_value=self._service()),
            patch("gui._complete_pending_dependency_sync", return_value=False) as sync,
            patch("gui._stop_dependency_sync_service", return_value=True),
            patch("gui.Event", side_effect=[restart_event, Mock(), Mock()]),
            patch("gui.Process", return_value=process) as process_factory,
            patch("gui._wait_for_webui_ready", return_value=True),
            patch("gui._stop_webui_process_tree", return_value=True),
            patch("gui.time.sleep"),
            patch("gui.logger.error_context") as error_context,
        ):
            gui.run_webui_supervisor()

        process_factory.assert_called_once_with(
            target=gui.func,
            args=(restart_event, ANY, ANY),
            name="gui",
        )
        sync.assert_called_once_with(ANY, ANY, ANY, force=True)
        error_context.assert_called_once()

    def test_supervisor_refuses_new_child_when_previous_owner_is_alive(self):
        with (
            patch(
                "gui.worker_registry.get_owner_record",
                return_value={"pid": 12345, "created_at": 10.5},
            ),
            patch("gui.worker_registry.process_matches", return_value=True),
            patch("gui._start_dependency_sync_service") as start_service,
        ):
            gui.run_webui_supervisor()

        start_service.assert_not_called()

    def test_supervisor_starts_without_sync_service_when_no_pending_marker(self):
        processes = [Mock(pid=100 + index) for index in range(3)]

        with (
            patch("gui._recover_orphaned_workers", return_value=True),
            patch("gui.is_dependency_sync_pending", return_value=False),
            patch(
                "gui._start_dependency_sync_service",
                side_effect=OSError("process denied"),
            ) as start_service,
            patch("gui._stop_webui_process_tree", return_value=True),
            patch("gui._stop_dependency_sync_service", return_value=True),
            patch("gui.Process", side_effect=processes) as process_factory,
            patch("gui._wait_for_webui_ready", return_value=False),
            patch("gui.logger.exception_context"),
            patch("gui.logger.error_context") as error_context,
            patch("gui.time.sleep") as sleep,
        ):
            gui.run_webui_supervisor()

        start_service.assert_not_called()
        sleep.assert_has_calls([call(1), call(2)])
        self.assertEqual(gui.WEBUI_START_RETRY_LIMIT, process_factory.call_count)
        error_context.assert_called_once()

    def test_supervisor_does_not_restart_webui_when_replacement_sync_service_cannot_start(self):
        restart_event = Mock()
        restart_event.wait.return_value = True
        dependency_sync_event = Mock()
        dependency_sync_event.is_set.return_value = True
        process = Mock(pid=12345)

        with (
            patch("gui._recover_orphaned_workers", return_value=True),
            patch("gui.is_dependency_sync_pending", return_value=False),
            patch(
                "gui._start_dependency_sync_service",
                side_effect=[OSError("process denied")] * gui.DEPENDENCY_SYNC_START_RETRY_LIMIT,
            ) as start_service,
            patch("gui._stop_dependency_sync_service", return_value=True),
            patch("gui.Event", side_effect=[restart_event, dependency_sync_event, Mock()]),
            patch("gui.Process", return_value=process) as process_factory,
            patch("gui._wait_for_webui_ready", return_value=True),
            patch("gui._stop_webui_process_tree", return_value=True),
            patch("gui.logger.exception_context"),
            patch("gui.logger.error_context") as error_context,
            patch("gui.time.sleep"),
        ):
            gui.run_webui_supervisor()

        self.assertEqual(1, process_factory.call_count)
        self.assertEqual(gui.DEPENDENCY_SYNC_START_RETRY_LIMIT, start_service.call_count)
        error_context.assert_called_once()
