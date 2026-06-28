import asyncio
import json
import sys
import time
import uuid
from typing import Any

from module.logger import logger


LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
COMMAND_TIMEOUT = 10
CONNECTION_EXPIRE = 45


def is_windows() -> bool:
    return sys.platform == "win32"


def is_local_request(request) -> bool:
    client = getattr(request, "client", None)
    host = getattr(client, "host", "") if client is not None else ""
    header_host = _normalize_host(request.headers.get("host", ""))
    return host in LOCAL_HOSTS and header_host in LOCAL_HOSTS


def _normalize_host(host: str) -> str:
    host = str(host or "").strip().lower()
    if host.startswith("["):
        return host[1:].split("]", maxsplit=1)[0]
    return host.split(":", maxsplit=1)[0]


class LauncherControl:
    """维护 WebUI 与外部启动器之间的本地命令通道。"""

    def __init__(self) -> None:
        self._queue = asyncio.Queue()
        self._pending: dict[str, asyncio.Future] = {}
        self.connected = False
        self.last_seen = 0.0
        self.autostart_enabled: bool | None = None
        self.autostart_supported = is_windows()
        self.last_error = ""

    def _is_connected(self) -> bool:
        if not self.connected:
            return False
        if time.time() - self.last_seen > CONNECTION_EXPIRE:
            self.connected = False
            return False
        return True

    def status(self, request_local: bool = True) -> dict[str, Any]:
        return {
            "success": True,
            "platform": sys.platform,
            "windows": is_windows(),
            "request_local": request_local,
            "launcher_connected": self._is_connected(),
            "autostart_supported": self.autostart_supported,
            "autostart_enabled": self.autostart_enabled,
            "last_error": self.last_error,
        }

    async def mark_connected(self) -> None:
        self.connected = True
        self.last_seen = time.time()
        await self._queue.put(self._build_command("startup.query"))

    def mark_disconnected(self) -> None:
        self.connected = False
        self.last_seen = time.time()

    def keep_alive(self) -> None:
        self.connected = True
        self.last_seen = time.time()

    async def next_command(self) -> dict[str, Any]:
        return await self._queue.get()

    async def set_autostart(self, enabled: bool) -> dict[str, Any]:
        if not is_windows():
            return self._error("当前平台不支持开机自启动")
        if not self._is_connected():
            return self._error("启动器未连接，请通过启动器打开 AzurPilot")

        command = self._build_command(
            "startup.set",
            {"enabled": bool(enabled), "mode": "tray"},
        )
        future = asyncio.get_running_loop().create_future()
        self._pending[command["id"]] = future
        await self._queue.put(command)

        try:
            result = await asyncio.wait_for(future, timeout=COMMAND_TIMEOUT)
        except asyncio.TimeoutError:
            self._pending.pop(command["id"], None)
            self.last_error = "等待启动器响应超时"
            return self._error(self.last_error)

        if result.get("success"):
            data = result.get("data") or {}
            if "enabled" in data:
                self.autostart_enabled = bool(data["enabled"])
            self.last_error = ""
            return {"success": True, "data": self.status()}

        self.last_error = str(result.get("error") or "启动器设置失败")
        return self._error(self.last_error)

    async def report(self, data: dict[str, Any]) -> dict[str, Any]:
        self.keep_alive()
        command_id = data.get("id")
        payload = data.get("data") or {}
        success = bool(data.get("success"))

        if success and "enabled" in payload:
            self.autostart_enabled = bool(payload["enabled"])
            self.last_error = ""
        elif not success:
            self.last_error = str(data.get("error") or "启动器命令执行失败")

        if command_id:
            future = self._pending.pop(command_id, None)
            if future is not None and not future.done():
                future.set_result(data)

        return {"success": True}

    @staticmethod
    def event(command: dict[str, Any]) -> str:
        return f"data: {json.dumps(command, ensure_ascii=False)}\n\n"

    @staticmethod
    def keepalive_event() -> str:
        return ": keepalive\n\n"

    @staticmethod
    def _build_command(command_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "id": uuid.uuid4().hex,
            "type": command_type,
            "payload": payload or {},
        }

    @staticmethod
    def _error(message: str) -> dict[str, Any]:
        logger.warning(message)
        return {"success": False, "error": message}


launcher_control = LauncherControl()
