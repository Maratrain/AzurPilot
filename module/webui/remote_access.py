"""
远程访问服务。

默认使用 localshare 的 P2P bootstrap：优先 WebRTC 直连/TURN 中继，失败后回到
localshare 现有 SSH 反向隧道。`RemoteAccessMode=ssh` 时保持旧行为。
"""

import asyncio
import base64
import fnmatch
import ipaddress
import json
import os
import shlex
import threading
import time
from dataclasses import dataclass
from subprocess import PIPE, Popen
from typing import TYPE_CHECKING, List, Optional, Tuple
from urllib.parse import urlsplit

from module.config.utils import random_id
from module.logger import logger
from module.webui.setting import State

if TYPE_CHECKING:
    from module.webui.utils import TaskHandler


HTTP_BODY_CHUNK = 12 * 1024
P2P_SETUP_TIMEOUT = 60


class ParseError(Exception):
    pass


class RemoteDependencyError(Exception):
    pass


class RemoteSignalError(Exception):
    pass


@dataclass
class RemoteAccessInfo:
    address: Optional[str] = None
    fallback_address: Optional[str] = None
    peer_id: Optional[str] = None
    signal_url: Optional[str] = None
    ice_servers: Optional[list] = None
    connection_state: str = "stopped"
    error: str = ""


def am_i_the_only_thread() -> bool:
    """判断当前线程是否是进程中唯一的非守护线程。"""
    alive_none_daemonic_thread_cnt = sum(
        1
        for t in threading.enumerate()
        if (t.is_alive() and not t.daemon) or t is threading.current_thread()
    )
    return alive_none_daemonic_thread_cnt == 1


def _parse_host_port(value: Optional[str]) -> Tuple[str, int]:
    try:
        server, port = str(value or "").rsplit(":", 1)
        return server, int(port)
    except (TypeError, ValueError) as e:
        raise ParseError(f"Failed to parse SSH server [{value}]") from e


def _parse_host_port_default(value: Optional[str], default_port: int) -> Tuple[str, int]:
    text = str(value or "").strip()
    if ":" not in text:
        return text, default_port
    return _parse_host_port(text)


def _csv_or_json_list(value, default=None) -> List[str]:
    if value in (None, ""):
        return list(default or [])
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in text.split(",") if item.strip()]


def _default_redirect_hosts(primary_host: str) -> List[str]:
    host = (primary_host or "").strip().lower()
    if not host:
        return []
    parts = host.split(".")
    hosts = [host]
    if len(parts) >= 2:
        base = ".".join(parts[-2:])
        hosts.append(f"*.{base}")
    if len(parts) >= 3:
        base = ".".join(parts[-3:])
        hosts.append(f"*.{base}")
    return list(dict.fromkeys(hosts))


def _is_private_redirect_host(host: str) -> bool:
    value = (host or "").strip("[]").lower()
    if value in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any((
        ip.is_private,
        ip.is_loopback,
        ip.is_link_local,
        ip.is_multicast,
        ip.is_reserved,
        ip.is_unspecified,
    ))


def _local_host() -> str:
    if State.deploy_config.WebuiHost == "0.0.0.0":
        return "127.0.0.1"
    if State.deploy_config.WebuiHost == "::":
        return "[::1]"
    return State.deploy_config.WebuiHost


def _remote_mode() -> str:
    mode = getattr(State.deploy_config, "RemoteAccessMode", "auto")
    mode = str(mode or "auto").strip().lower()
    if mode not in ("ssh", "webrtc", "auto"):
        logger.warning(f"[WebUI] 未知远程访问模式 [{mode}]，回退到 auto")
        return "auto"
    return mode


def _json_list(value, default=None) -> list:
    if value in (None, ""):
        return list(default or [])
    if isinstance(value, list):
        return value
    text = str(value).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else list(default or [])
    except json.JSONDecodeError:
        return [item.strip() for item in text.split(",") if item.strip()]


def _configured_ice_servers(remote_servers=None) -> List[dict]:
    if remote_servers:
        return remote_servers

    servers = []
    stun = _json_list(
        getattr(State.deploy_config, "StunServers", None),
        ["stun:stun.l.google.com:19302"],
    )
    if stun:
        servers.append({"urls": stun})

    turn = _json_list(getattr(State.deploy_config, "TurnServers", None))
    if turn:
        servers.append({"urls": turn})
    return servers


def _signal_url_from_ssh_server() -> str:
    configured = getattr(State.deploy_config, "SignalingServer", None)
    if configured:
        return configured

    server, _port = _parse_host_port(State.deploy_config.SSHServer)
    scheme = "wss"
    return f"{scheme}://{server}/signal"


def _format_signal_error(error: Exception, signal_url: str) -> str:
    status = getattr(error, "status", None)
    message = getattr(error, "message", "") or str(error)
    request_info = getattr(error, "request_info", None)
    url = getattr(request_info, "real_url", None) or signal_url
    if status:
        return f"P2P 信令连接失败（HTTP {status}: {message}），已继续使用 SSH 远程访问：{url}"
    return f"P2P 信令连接失败（{message}），已继续使用 SSH 远程访问：{url}"


class RemoteAccessProvider:
    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def is_alive(self) -> bool:
        raise NotImplementedError

    def get_state(self) -> int:
        raise NotImplementedError

    def get_entry_point(self) -> Optional[str]:
        raise NotImplementedError

    def get_connection_state(self) -> str:
        return "stopped"

    def get_error(self) -> str:
        return ""


class SSHRemoteAccessProvider(RemoteAccessProvider):
    def __init__(self) -> None:
        self.process: Optional[Popen] = None
        self.thread: Optional[threading.Thread] = None
        self.notfound = False
        self.info = RemoteAccessInfo()

    def _max_redirects(self) -> int:
        try:
            return max(0, int(getattr(State.deploy_config, "MaxRedirects", 2) or 0))
        except (TypeError, ValueError):
            logger.warning("Invalid MaxRedirects, fallback to 2")
            return 2

    def _redirect_hosts(self, primary_host: str) -> List[str]:
        configured = _csv_or_json_list(getattr(State.deploy_config, "AllowedRedirectHosts", None))
        return configured or _default_redirect_hosts(primary_host)

    def _validate_redirect_target(self, ssh_server: str, primary_host: str) -> Tuple[str, int]:
        host, port = _parse_host_port_default(ssh_server, 1022)
        host = host.strip().lower()
        if not host:
            raise ParseError("Redirect ssh_server host is empty")
        if _is_private_redirect_host(host):
            raise ParseError(f"Refuse redirect to private host [{host}]")
        allowed_hosts = [item.lower() for item in self._redirect_hosts(primary_host)]
        if allowed_hosts and not any(fnmatch.fnmatch(host, pattern) for pattern in allowed_hosts):
            raise ParseError(f"Refuse redirect to untrusted host [{host}], allowed hosts: {allowed_hosts}")
        return host, port

    def _terminate_process(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.kill()
            try:
                self.process.wait(timeout=3)
            except Exception:
                pass

    def _start_ssh_process(
        self,
        local_host: str,
        local_port: int,
        server: str,
        server_port: int,
        remote_port: str,
    ) -> Optional[Popen]:
        bin_path = State.deploy_config.SSHExecutable
        known_hosts = os.devnull
        cmd = (
            f"{bin_path} -oStrictHostKeyChecking=no "
            f"-oUserKnownHostsFile={known_hosts} "
            f"-oGlobalKnownHostsFile={known_hosts} "
            f"-oLogLevel=ERROR "
            f"-R {remote_port}:{local_host}:{local_port} "
            f"-p {server_port} {server} -- --output json"
        )
        args = shlex.split(cmd)
        logger.debug(f"remote access service command: {cmd}")

        if self.process is not None and self.process.poll() is None:
            logger.warning(f"Kill previous ssh process [{self.process.pid}]")
            self.process.kill()
        try:
            self.process = Popen(args, stdout=PIPE, stderr=PIPE)
        except FileNotFoundError:
            logger.critical(
                f"无法找到SSH可执行文件{bin_path}，请安装OpenSSH或在deploy.yaml中指定SSHExecutable"
            )
            self.notfound = True
            self.info.error = "ssh_not_found"
            return None

        logger.info(f"remote access process pid: {self.process.pid}")
        return self.process

    def _run(
        self,
        local_host="127.0.0.1",
        local_port=25548,
        server="app.pywebio.online",
        server_port=1022,
        remote_port="/",
        setup_timeout=60,
    ) -> None:
        primary_user, primary_host = server.rsplit("@", 1) if "@" in server else ("", server)
        current_server = server
        current_port = server_port
        success = False
        redirects = 0

        while True:
            process = self._start_ssh_process(
                local_host=local_host,
                local_port=local_port,
                server=current_server,
                server_port=current_port,
                remote_port=remote_port,
            )
            if process is None:
                return

            def timeout_killer(wait_sec, target_process):
                time.sleep(wait_sec)
                if not success and target_process.poll() is None:
                    logger.info("Connection timeout, kill ssh process")
                    target_process.kill()

            threading.Thread(
                target=timeout_killer,
                args=(setup_timeout, process),
                daemon=True,
            ).start()

            stdout = process.stdout.readline().decode("utf8")
            logger.debug(f"ssh server stdout: {stdout}")
            try:
                connection_info = json.loads(stdout)
            except json.JSONDecodeError:
                self.info.error = "invalid_provider_response"
                if process.poll() is None:
                    process.kill()
                break

            status = connection_info.get("status", "fail")
            if status == "redirect":
                redirects += 1
                if redirects > self._max_redirects():
                    self.info.error = "too_many_redirects"
                    logger.error("Too many SSH redirect responses")
                    self._terminate_process()
                    break
                ssh_server = connection_info.get("ssh_server")
                try:
                    redirect_host, redirect_port = self._validate_redirect_target(ssh_server, primary_host)
                except ParseError as e:
                    self.info.error = str(e)
                    logger.error(str(e))
                    self._terminate_process()
                    break
                redirect_user = connection_info.get("ssh_user") or primary_user or State.deploy_config.SSHUser
                current_server = f"{redirect_user}@{redirect_host}" if redirect_user else redirect_host
                current_port = redirect_port
                logger.info(f"Remote access redirected to {redirect_host}:{redirect_port}")
                self._terminate_process()
                continue

            success = True
            if status != "success":
                message = connection_info.get("message", "")
                self.info.error = message or status or "remote_access_failed"
                logger.info(
                    "Failed to establish remote access, this is the error message "
                    f"from service provider: {message}"
                )
                new_username = connection_info.get("change_username", None)
                if new_username:
                    logger.info(f"Server requested to change username, change it to: {new_username}")
                    State.deploy_config.SSHUser = new_username
                break

            self.info.address = connection_info.get("address")
            self.info.fallback_address = connection_info.get("fallback_address") or self.info.address
            self.info.peer_id = connection_info.get("peer_id")
            self.info.signal_url = connection_info.get("signal_url")
            self.info.ice_servers = connection_info.get("ice_servers")
            self.info.connection_state = "ssh_forward"
            self.info.error = ""
            logger.debug(f"Remote access url: {self.info.address}")
            break

        while not am_i_the_only_thread() and self.process and self.process.poll() is None:
            time.sleep(1)

        if self.process and self.process.poll() is None:
            logger.info("App process exit, killing ssh process")
            self.process.kill()
        elif self.process:
            stderr = self.process.stderr.read().decode("utf8")
            if stderr:
                logger.error(f"PyWebIO application remote access service error: {stderr}")
                self.info.error = stderr.strip()
            else:
                logger.info("PyWebIO application remote access service exit.")
        self.info.connection_state = "stopped"
        self.info.address = None

    def start(self) -> None:
        server, server_port = _parse_host_port(State.deploy_config.SSHServer)
        if State.deploy_config.SSHUser is None:
            logger.info("SSHUser is not set, generate a random one")
            State.deploy_config.SSHUser = random_id(24)

        target = f"{State.deploy_config.SSHUser}@{server}"
        self.thread = threading.Thread(
            target=self._thread_main,
            kwargs={
                "server": target,
                "server_port": server_port,
                "local_host": _local_host(),
                "local_port": State.deploy_config.WebuiPort,
            },
            daemon=False,
        )
        self.thread.start()

    def _thread_main(self, **kwargs) -> None:
        logger.info("Start SSH remote access service")
        try:
            self._run(**kwargs)
        except KeyboardInterrupt:
            pass
        except Exception as e:
            self.info.error = str(e)
            logger.exception(e)
        finally:
            if self.process and self.process.poll() is None:
                logger.info("Exception occurred, killing ssh process")
                self.process.kill()
        logger.info("Exit SSH remote access service thread")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.kill()

    def is_alive(self) -> bool:
        return (
            self.thread is not None
            and self.thread.is_alive()
            and self.process is not None
            and self.process.poll() is None
        )

    def get_state(self) -> int:
        if self.is_alive():
            return 1 if self.info.address else 2
        if self.notfound:
            return 3
        return 0

    def get_entry_point(self) -> Optional[str]:
        return self.info.address if self.is_alive() else None

    def get_connection_state(self) -> str:
        if self.is_alive():
            return self.info.connection_state if self.info.address else "starting"
        return "ssh_not_found" if self.notfound else "stopped"

    def get_error(self) -> str:
        return self.info.error


class WebRTCTunnel:
    def __init__(self, local_host: str, local_port: int, channel, peer_id: Optional[str] = None) -> None:
        self.local_host = local_host.strip("[]")
        self.local_port = int(local_port)
        self.channel = channel
        self.peer_id = str(peer_id or "").strip("/").lower()
        self.ws_sessions = {}
        self.sse_tasks = {}
        self.ws_incoming_chunks = {}

    @property
    def base_http_url(self) -> str:
        host = self.local_host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"http://{host}:{self.local_port}"

    @property
    def base_ws_url(self) -> str:
        host = self.local_host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"ws://{host}:{self.local_port}"

    def send_json(self, payload: dict) -> None:
        self.channel.send(json.dumps(payload, ensure_ascii=False))

    def _normalize_proxy_path(self, value) -> str:
        text = str(value or "/")
        if "://" in text:
            parsed = urlsplit(text)
            text = parsed.path or "/"
            if parsed.query:
                text = f"{text}?{parsed.query}"
        if not text.startswith("/"):
            text = f"/{text}"

        if self.peer_id:
            prefix = f"/p2p/{self.peer_id}"
            lower_text = text.lower()
            if lower_text == prefix or lower_text.startswith(f"{prefix}?") or lower_text.startswith(f"{prefix}/"):
                suffix = text[len(prefix):]
                if suffix.startswith("/"):
                    text = suffix or "/"
                elif suffix.startswith("?"):
                    text = f"/{suffix}"
                else:
                    text = "/"
        return text

    @staticmethod
    def _apply_browser_headers(headers: dict, payload: dict) -> dict:
        headers = dict(headers or {})
        user_agent = payload.get("user_agent")
        accept_language = payload.get("accept_language")
        if user_agent:
            headers["User-Agent"] = user_agent
        if accept_language:
            headers.setdefault("Accept-Language", accept_language)
        return headers

    async def handle(self, payload: dict) -> None:
        msg_type = payload.get("type")
        if msg_type == "http.request":
            await self._http_request(payload)
        elif msg_type == "ws.open":
            await self._ws_open(payload)
        elif msg_type == "ws.send":
            await self._ws_send(payload)
        elif msg_type == "ws.send.start":
            self._ws_send_start(payload)
        elif msg_type == "ws.send.chunk":
            self._ws_send_chunk(payload)
        elif msg_type == "ws.send.end":
            await self._ws_send_end(payload)
        elif msg_type == "ws.close":
            await self._ws_close(payload)
        elif msg_type == "sse.open":
            await self._sse_open(payload)
        elif msg_type == "sse.close":
            await self._sse_close(payload)

    async def _http_request(self, payload: dict) -> None:
        req_id = payload.get("id")
        try:
            import aiohttp

            method = payload.get("method") or "GET"
            path = self._normalize_proxy_path(payload.get("path"))
            headers = self._apply_browser_headers(payload.get("headers"), payload)
            body = payload.get("body") or ""
            data = base64.b64decode(body) if body else None
            url = f"{self.base_http_url}{path}"
            async with aiohttp.ClientSession(auto_decompress=False) as session:
                async with session.request(method, url, headers=headers, data=data) as resp:
                    self.send_json({
                        "type": "http.response.start",
                        "id": req_id,
                        "status": resp.status,
                        "status_text": resp.reason,
                        "headers": dict(resp.headers),
                    })
                    async for chunk in resp.content.iter_chunked(HTTP_BODY_CHUNK):
                        self.send_json({
                            "type": "http.response.chunk",
                            "id": req_id,
                            "data": base64.b64encode(chunk).decode("ascii"),
                        })
                    self.send_json({"type": "http.response.end", "id": req_id})
        except Exception as e:
            logger.warning(f"P2P HTTP proxy failed: {e}")
            self.send_json({"type": "http.response.error", "id": req_id, "message": str(e)})

    async def _ws_open(self, payload: dict) -> None:
        ws_id = payload.get("id")
        session = None
        try:
            import aiohttp

            path = self._normalize_proxy_path(payload.get("path"))
            url = f"{self.base_ws_url}{path}"
            session = aiohttp.ClientSession()
            ws = await session.ws_connect(
                url,
                heartbeat=30,
                headers=self._apply_browser_headers({}, payload),
            )
            self.ws_sessions[ws_id] = (session, ws)
            self.send_json({"type": "ws.opened", "id": ws_id})
            asyncio.create_task(self._ws_reader(ws_id, session, ws))
        except Exception as e:
            if session is not None:
                await session.close()
            logger.warning(f"P2P WebSocket open failed: {e}")
            self.send_json({"type": "ws.error", "id": ws_id, "message": str(e)})

    async def _ws_reader(self, ws_id, session, ws) -> None:
        try:
            import aiohttp

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self.send_json({"type": "ws.message", "id": ws_id, "binary": False, "data": msg.data})
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await self._ws_send_chunked_message(ws_id, True, msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        finally:
            self.ws_sessions.pop(ws_id, None)
            await session.close()
            self.send_json({"type": "ws.closed", "id": ws_id, "code": 1000, "reason": ""})

    async def _ws_send(self, payload: dict) -> None:
        ws_id = payload.get("id")
        item = self.ws_sessions.get(ws_id)
        if not item:
            return
        _session, ws = item
        if payload.get("binary"):
            await ws.send_bytes(base64.b64decode(payload.get("data") or ""))
        else:
            await ws.send_str(payload.get("data") or "")

    def _ws_send_start(self, payload: dict) -> None:
        message_id = payload.get("message_id")
        if not message_id:
            return
        self.ws_incoming_chunks[message_id] = {
            "id": payload.get("id"),
            "binary": bool(payload.get("binary")),
            "chunks": [b""] * int(payload.get("total") or 1),
        }

    def _ws_send_chunk(self, payload: dict) -> None:
        message_id = payload.get("message_id")
        item = self.ws_incoming_chunks.get(message_id)
        if not item:
            return
        index = int(payload.get("index") or 0)
        if 0 <= index < len(item["chunks"]):
            item["chunks"][index] = base64.b64decode(payload.get("data") or "")

    async def _ws_send_end(self, payload: dict) -> None:
        message_id = payload.get("message_id")
        item = self.ws_incoming_chunks.pop(message_id, None)
        if not item:
            return
        ws_id = item["id"]
        session_item = self.ws_sessions.get(ws_id)
        if not session_item:
            return
        _session, ws = session_item
        data = b"".join(item["chunks"])
        if item["binary"]:
            await ws.send_bytes(data)
        else:
            await ws.send_str(data.decode("utf-8", errors="replace"))

    async def _ws_close(self, payload: dict) -> None:
        ws_id = payload.get("id")
        item = self.ws_sessions.pop(ws_id, None)
        if not item:
            return
        session, ws = item
        await ws.close()
        await session.close()

    async def _sse_open(self, payload: dict) -> None:
        sse_id = payload.get("id")
        task = asyncio.create_task(self._sse_reader(payload))
        self.sse_tasks[sse_id] = task

    async def _sse_reader(self, payload: dict) -> None:
        sse_id = payload.get("id")
        try:
            import aiohttp

            path = self._normalize_proxy_path(payload.get("path"))
            url = f"{self.base_http_url}{path}"
            async with aiohttp.ClientSession() as session:
                headers = self._apply_browser_headers({"Accept": "text/event-stream"}, payload)
                async with session.get(url, headers=headers) as resp:
                    async for chunk in resp.content.iter_chunked(8192):
                        self.send_json({
                            "type": "sse.chunk",
                            "id": sse_id,
                            "data": chunk.decode("utf-8", errors="replace"),
                        })
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"P2P SSE proxy failed: {e}")
        finally:
            self.sse_tasks.pop(sse_id, None)
            self.send_json({"type": "sse.closed", "id": sse_id})

    async def _sse_close(self, payload: dict) -> None:
        sse_id = payload.get("id")
        task = self.sse_tasks.pop(sse_id, None)
        if task:
            task.cancel()

    async def _ws_send_chunked_message(self, ws_id, binary, data) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        data = bytes(data)
        total = max(1, (len(data) + HTTP_BODY_CHUNK - 1) // HTTP_BODY_CHUNK)
        message_id = f"{ws_id}-{time.time_ns()}"
        self.send_json({
            "type": "ws.message.start",
            "id": ws_id,
            "message_id": message_id,
            "binary": binary,
            "total": total,
        })
        for index in range(total):
            chunk = data[index * HTTP_BODY_CHUNK:(index + 1) * HTTP_BODY_CHUNK]
            self.send_json({
                "type": "ws.message.chunk",
                "id": ws_id,
                "message_id": message_id,
                "index": index,
                "data": base64.b64encode(chunk).decode("ascii"),
            })
        self.send_json({"type": "ws.message.end", "id": ws_id, "message_id": message_id})


class WebRTCRemoteAccessProvider(RemoteAccessProvider):
    def __init__(self, ssh_provider: SSHRemoteAccessProvider) -> None:
        self.ssh_provider = ssh_provider
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self.info = RemoteAccessInfo(connection_state="stopped")
        self._missing_dependency = ""

    def start(self) -> None:
        with self._lock:
            if self.thread is not None and self.thread.is_alive():
                return

            self.stop_event.clear()
            if not self.ssh_provider.is_alive():
                self.ssh_provider.start()

            self.thread = threading.Thread(target=self._thread_main, daemon=False)
            self.thread.start()

    def _wait_for_ssh_info(self) -> bool:
        started = time.time()
        while time.time() - started < P2P_SETUP_TIMEOUT:
            if self.stop_event.is_set():
                return False
            if self.ssh_provider.info.address:
                return True
            if not self.ssh_provider.is_alive() and self.ssh_provider.get_state() in (0, 3):
                return False
            time.sleep(0.2)
        return False

    def _thread_main(self) -> None:
        logger.info("Start WebRTC remote access service")
        try:
            if not self._wait_for_ssh_info():
                self.info.error = "SSH fallback is not ready"
                return

            self.info.address = self.ssh_provider.info.address
            self.info.fallback_address = self.ssh_provider.info.fallback_address
            self.info.peer_id = self.ssh_provider.info.peer_id
            self.info.signal_url = self.ssh_provider.info.signal_url or _signal_url_from_ssh_server()
            self.info.ice_servers = self.ssh_provider.info.ice_servers
            if not self.info.peer_id:
                raise ParseError("localshare 服务端未返回 peer_id，无法启用 P2P")
            self.info.connection_state = "signaling"
            asyncio.run(self._run_signal_loop())
        except RemoteDependencyError as e:
            self._missing_dependency = str(e)
            self.info.error = str(e)
            self.info.connection_state = "dependency_missing"
            logger.warning(f"WebRTC remote access disabled: {e}")
        except RemoteSignalError as e:
            self.info.error = str(e)
            self.info.connection_state = "ssh_forward"
            logger.warning(str(e))
        except Exception as e:
            self.info.error = str(e)
            self.info.connection_state = "failed"
            logger.exception(e)
        logger.info("Exit WebRTC remote access service thread")

    async def _run_signal_loop(self) -> None:
        try:
            import aiohttp
            from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
            from aiortc.sdp import candidate_from_sdp
        except ImportError as e:
            raise RemoteDependencyError("缺少 aiortc/aiohttp 依赖，已退回 SSH 转发") from e

        signal_url = self.info.signal_url
        if not signal_url:
            raise ParseError("SignalingServer is empty")

        ice_servers = _configured_ice_servers(self.info.ice_servers)
        rtc_servers = []
        for item in ice_servers:
            urls = item.get("urls")
            if isinstance(urls, str):
                urls = [urls]
            rtc_servers.append(
                RTCIceServer(
                    urls=urls or [],
                    username=item.get("username"),
                    credential=item.get("credential"),
                )
            )
        rtc_config = RTCConfiguration(iceServers=rtc_servers)
        peer_connections = set()
        peer_connections_by_viewer = {}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(signal_url, heartbeat=30) as ws:
                    await ws.send_json({
                        "type": "register",
                        "peer_id": self.info.peer_id,
                        "fallback_url": self.info.fallback_address,
                    })
                    keepalive_task = asyncio.create_task(self._signal_keepalive(ws))
                    self.info.connection_state = "waiting_peer"

                    try:
                        async for msg in ws:
                            if self.stop_event.is_set():
                                break
                            if msg.type != aiohttp.WSMsgType.TEXT:
                                continue
                            data = json.loads(msg.data)
                            msg_type = data.get("type")
                            if msg_type == "registered":
                                self.info.address = data.get("address") or self.info.address
                                self.info.fallback_address = data.get("fallback_url") or self.info.fallback_address
                                self.info.connection_state = "waiting_peer"
                                logger.info(f"P2P remote access url: {self.info.address}")
                            elif msg_type == "offer":
                                pc = RTCPeerConnection(configuration=rtc_config)
                                peer_connections.add(pc)
                                viewer_id = data.get("viewer_id")
                                if viewer_id:
                                    old_pc = peer_connections_by_viewer.pop(viewer_id, None)
                                    if old_pc is not None:
                                        peer_connections.discard(old_pc)
                                        await old_pc.close()
                                    peer_connections_by_viewer[viewer_id] = pc

                                @pc.on("datachannel")
                                def on_datachannel(channel):
                                    logger.info(f"P2P datachannel opened: {channel.label}")
                                    tunnel = WebRTCTunnel(
                                        _local_host(),
                                        State.deploy_config.WebuiPort,
                                        channel,
                                        self.info.peer_id,
                                    )

                                    @channel.on("message")
                                    def on_message(message):
                                        try:
                                            payload = json.loads(message)
                                        except Exception as e:
                                            logger.warning(f"P2P channel message parse failed: {e}")
                                            return
                                        asyncio.create_task(tunnel.handle(payload))

                                @pc.on("connectionstatechange")
                                async def on_connectionstatechange():
                                    if pc.connectionState == "connected":
                                        self.info.connection_state = "direct_p2p"
                                    elif pc.connectionState in ("failed", "closed", "disconnected"):
                                        peer_connections.discard(pc)
                                        if viewer_id and peer_connections_by_viewer.get(viewer_id) is pc:
                                            peer_connections_by_viewer.pop(viewer_id, None)
                                        await pc.close()

                                offer = RTCSessionDescription(sdp=data.get("sdp"), type=data.get("kind", "offer"))
                                await pc.setRemoteDescription(offer)
                                answer = await pc.createAnswer()
                                await pc.setLocalDescription(answer)
                                await self._wait_ice_complete(pc)
                                await ws.send_json({
                                    "type": "answer",
                                    "viewer_id": viewer_id,
                                    "sdp": pc.localDescription.sdp,
                                    "kind": pc.localDescription.type,
                                })
                            elif msg_type == "candidate":
                                viewer_id = data.get("viewer_id")
                                pc = peer_connections_by_viewer.get(viewer_id)
                                if pc is None:
                                    continue
                                raw_candidate = data.get("candidate")
                                if not raw_candidate:
                                    await pc.addIceCandidate(None)
                                    continue
                                candidate = candidate_from_sdp(raw_candidate.get("candidate", ""))
                                candidate.sdpMid = raw_candidate.get("sdpMid")
                                candidate.sdpMLineIndex = raw_candidate.get("sdpMLineIndex")
                                await pc.addIceCandidate(candidate)
                            elif msg_type == "viewer_state":
                                state = data.get("state")
                                if state in ("direct_p2p", "turn_relay"):
                                    self.info.connection_state = state
                            elif msg_type == "error":
                                self.info.error = data.get("message", "")
                                logger.warning(f"P2P signaling error: {self.info.error}")
                    finally:
                        keepalive_task.cancel()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            raise RemoteSignalError(_format_signal_error(e, signal_url)) from e
        finally:
            for pc in list(peer_connections):
                await pc.close()

    @staticmethod
    async def _wait_ice_complete(pc, timeout=3.5) -> None:
        if pc.iceGatheringState == "complete":
            return
        done = asyncio.Event()

        @pc.on("icegatheringstatechange")
        def on_icegatheringstatechange():
            if pc.iceGatheringState == "complete":
                done.set()

        try:
            await asyncio.wait_for(done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    async def _signal_keepalive(self, ws) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(25)
            if ws.closed:
                return
            await ws.send_json({"type": "ping", "peer_id": self.info.peer_id})

    def stop(self) -> None:
        self.stop_event.set()
        self.ssh_provider.stop()

    def is_alive(self) -> bool:
        return self.ssh_provider.is_alive() and self.thread is not None and self.thread.is_alive()

    def get_state(self) -> int:
        if self.is_alive():
            return 1 if self.info.address else 2
        if self._missing_dependency:
            return 4
        return self.ssh_provider.get_state()

    def get_entry_point(self) -> Optional[str]:
        if self.is_alive() and self.info.address:
            return self.info.address
        return self.ssh_provider.get_entry_point()

    def get_connection_state(self) -> str:
        if self.is_alive():
            return self.info.connection_state
        if self._missing_dependency:
            return "dependency_missing"
        return self.ssh_provider.get_connection_state()

    def get_error(self) -> str:
        return self.info.error or self.ssh_provider.get_error()


class AutoRemoteAccessProvider(RemoteAccessProvider):
    def __init__(self) -> None:
        self.ssh = SSHRemoteAccessProvider()
        self.webrtc = WebRTCRemoteAccessProvider(self.ssh)

    def active(self) -> RemoteAccessProvider:
        mode = _remote_mode()
        return self.ssh if mode == "ssh" else self.webrtc

    def start(self) -> None:
        self.active().start()

    def stop(self) -> None:
        self.webrtc.stop()
        self.ssh.stop()

    def is_alive(self) -> bool:
        return self.active().is_alive()

    def get_state(self) -> int:
        return self.active().get_state()

    def get_entry_point(self) -> Optional[str]:
        return self.active().get_entry_point()

    def get_connection_state(self) -> str:
        return self.active().get_connection_state()

    def get_error(self) -> str:
        return self.active().get_error()


_provider = AutoRemoteAccessProvider()


def start_remote_access_service(**kwargs):
    """兼容旧调用入口。"""
    if kwargs:
        logger.debug(f"Ignore legacy remote access kwargs: {kwargs}")
    _provider.start()
    return True


class RemoteAccess:
    @staticmethod
    def keep_ssh_alive():
        task_handler: TaskHandler
        task_handler = yield
        while True:
            if _provider.is_alive():
                yield
                continue
            logger.info("Remote access service is not running, starting now")
            try:
                start_remote_access_service()
            except ParseError as e:
                logger.exception(e)
                task_handler.remove_current_task()
            yield

    @staticmethod
    def kill_ssh_process():
        _provider.stop()

    @staticmethod
    def is_alive():
        return _provider.is_alive()

    @staticmethod
    def get_state():
        return _provider.get_state()

    @staticmethod
    def get_entry_point():
        return _provider.get_entry_point()

    @staticmethod
    def get_connection_state():
        return _provider.get_connection_state()

    @staticmethod
    def get_error():
        return _provider.get_error()
