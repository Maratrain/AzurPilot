"""
Copy from pywebio.platform.fastapi
"""

import asyncio
import logging
import os
import re
from collections import deque
from collections.abc import Mapping
from typing import Any, cast

import uvicorn
from pywebio import __version__ as PYWEBIO_VERSION
import pywebio.platform.fastapi as pywebio_fastapi
from pywebio.platform.fastapi import (
    STATIC_PATH,
    Session,
    cdn_validation,
    get_free_port,
    open_webbrowser_on_server_started,
    start_remote_access_service,
    webio_routes,
)
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocketDisconnect

ROBOTS_TXT = """\
User-agent: *
Disallow: /
"""

logger = logging.getLogger(__name__)

STATIC_ASSET_CACHE_CONTROL = "no-cache"
VERSIONED_STATIC_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
NO_CACHE_CONTROL = "no-cache"
HTTP_GZIP_MINIMUM_SIZE = 1024
HTTP_GZIP_COMPRESS_LEVEL = 5
WEBSOCKET_MAX_PENDING_MESSAGES = 2048
INITIAL_LOADING_STYLE_MARKER = "alas-initial-loading-critical"

_STYLESHEET_LINK_PATTERN = re.compile(rb'<link rel="stylesheet"[^>]*>')
_PYWEBIO_ASSET_PATTERN = re.compile(
    rb'(?P<url>(?:href|src)="pywebio_static/[^"?]+)(?P<quote>")'
)
_DEFERRED_STYLE_ATTRIBUTES = (
    b' media="print" '
    b'onload=\'this.media="all";this.onload=null\''
)


def _defer_stylesheet_link(match: re.Match[bytes]) -> bytes:
    """将首屏样式改为非阻塞加载，且不让其状态阻塞真实内容。"""
    tag = match.group(0)
    if b" media=" in tag:
        return tag
    return tag.replace(
        b'<link rel="stylesheet"',
        b'<link rel="stylesheet"' + _DEFERRED_STYLE_ATTRIBUTES,
        1,
    )


def _version_pywebio_asset(match: re.Match[bytes]) -> bytes:
    """为 PyWebIO 自带静态资源补充包版本，允许安全长期缓存。"""
    return (
        match.group("url")
        + b"?v="
        + PYWEBIO_VERSION.encode("ascii")
        + match.group("quote")
    )


def _optimize_initial_page(body: bytes) -> bytes:
    """让加载骨架先于外部样式绘制，同时优先展示已到达的内容。"""
    marker = INITIAL_LOADING_STYLE_MARKER.encode("ascii")
    marker_position = body.find(marker)
    if marker_position < 0:
        return body

    style_start = body.rfind(b"<style>", 0, marker_position)
    style_end = body.find(b"</style>", marker_position)
    first_stylesheet = body.find(b'<link rel="stylesheet"')
    if style_start < 0 or style_end < 0 or first_stylesheet < 0:
        return body

    style_end += len(b"</style>")
    if style_start > first_stylesheet:
        critical_style = body[style_start:style_end]
        body = body[:style_start] + body[style_end:]
        first_stylesheet = body.find(b'<link rel="stylesheet"')
        body = (
            body[:first_stylesheet]
            + critical_style
            + b"\n    "
            + body[first_stylesheet:]
        )

    body = _STYLESHEET_LINK_PATTERN.sub(_defer_stylesheet_link, body)
    return _PYWEBIO_ASSET_PATTERN.sub(_version_pywebio_asset, body)


def _optimize_initial_page_route(routes) -> None:
    """包装 PyWebIO 的 HTML 路由，不影响同路径的 WebSocket 路由。"""
    for index, route in enumerate(routes):
        if not isinstance(route, Route) or route.path != "/":
            continue

        endpoint = route.endpoint

        async def optimized_endpoint(request, original_endpoint=endpoint):
            response = await original_endpoint(request)
            body = getattr(response, "body", b"")
            optimized = _optimize_initial_page(body)
            if optimized != body:
                response.body = optimized
                response.headers["Content-Length"] = str(len(optimized))
            return response

        routes[index] = Route(
            route.path,
            optimized_endpoint,
            methods=route.methods,
            name=route.name,
        )
        return


class HeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        is_static_asset = path.startswith("/static/assets/") or path.startswith(
            "/pywebio_static/"
        )
        is_cacheable_response = (
            200 <= response.status_code < 300 or response.status_code == 304
        )
        if request.method in {"GET", "HEAD"} and is_static_asset and is_cacheable_response:
            if "v" in request.query_params:
                response.headers["Cache-Control"] = (
                    VERSIONED_STATIC_ASSET_CACHE_CONTROL
                )
            else:
                # 部分静态资源没有内容哈希，必须在每次使用前重新验证。
                response.headers["Cache-Control"] = STATIC_ASSET_CACHE_CONTROL
        else:
            response.headers["Cache-Control"] = NO_CACHE_CONTROL
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        return response


async def robots_txt(request):
    return PlainTextResponse(
        ROBOTS_TXT,
        media_type="text/plain",
        headers={"X-Robots-Tag": "noindex, nofollow, noarchive"},
    )


class SafeWebSocketConnection(pywebio_fastapi.WebSocketConnection):
    """
    Starlette/websockets 不允许同一连接并发 send。

    使用单一发送协程保持消息顺序，避免为每条 PyWebIO 指令创建一个异步任务。
    慢客户端积压超过上限时主动断开，防止一个浏览器拖垮整个事件循环。
    """

    def __init__(self, websocket, ioloop):
        super().__init__(websocket, ioloop)
        self._pending_messages = deque()
        self._sender_task = None
        self._close_requested = False

    def _transport_closed(self) -> bool:
        return super().closed()

    def closed(self) -> bool:
        return self._close_requested or self._transport_closed()

    def _ensure_sender(self) -> None:
        if self._sender_task is None or self._sender_task.done():
            self._sender_task = self.ioloop.create_task(self._drain_messages())

    async def _drain_messages(self) -> None:
        current_task = asyncio.current_task()
        try:
            while self._pending_messages and not self._transport_closed():
                message = self._pending_messages.popleft()
                try:
                    await self.ws.send_json(message)
                except TypeError:
                    logger.exception("PyWebIO 消息序列化失败，消息内容: %s", message)
                except (AssertionError, RuntimeError, WebSocketDisconnect):
                    logger.debug("WebSocket 已断开，跳过 PyWebIO 消息发送")
                    self._pending_messages.clear()
                    return
                except Exception as e:
                    logger.debug("PyWebIO WebSocket 消息发送失败: %s", e)
                    self._pending_messages.clear()
                    return

            if self._close_requested and not self._transport_closed():
                await self._close_transport()
        finally:
            if self._sender_task is current_task:
                self._sender_task = None

    async def _close_transport(self) -> None:
        if self._transport_closed():
            return
        try:
            await self.ws.close()
        except (AssertionError, RuntimeError, WebSocketDisconnect):
            logger.debug("WebSocket 已断开，跳过 PyWebIO 连接关闭")
        except Exception as e:
            logger.debug("PyWebIO WebSocket 连接关闭失败: %s", e)

    async def _abort_slow_client(self, sender_task) -> None:
        if sender_task is not None and not sender_task.done():
            sender_task.cancel()
            try:
                await sender_task
            except asyncio.CancelledError:
                pass
        await self._close_transport()

    def write_message(self, message: dict):
        if self.closed():
            return
        if len(self._pending_messages) >= WEBSOCKET_MAX_PENDING_MESSAGES:
            logger.warning(
                "PyWebIO 客户端发送积压超过 %d 条，主动断开慢连接",
                WEBSOCKET_MAX_PENDING_MESSAGES,
            )
            self._pending_messages.clear()
            self._close_requested = True
            sender_task = self._sender_task
            self._sender_task = self.ioloop.create_task(
                self._abort_slow_client(sender_task)
            )
            return
        self._pending_messages.append(message)
        self._ensure_sender()

    def close(self):
        if self._close_requested:
            return
        self._close_requested = True
        self._ensure_sender()


def patch_pywebio_websocket_connection():
    pywebio_fastapi.WebSocketConnection = SafeWebSocketConnection


def asgi_app(
    applications,
    cdn: str | bool = False,
    static_dir=None,
    debug: bool = False,
    allowed_origins=None,
    check_origin=None,
    static_mounts: Mapping[str, str] | None = None,
    **starlette_settings,
):
    debug = bool(os.environ.get("PYWEBIO_DEBUG", debug))
    Session.debug = debug
    validated_cdn: str | bool = cdn_validation(cdn, "warn")
    if validated_cdn is False:
        validated_cdn = "pywebio_static"
    patch_pywebio_websocket_connection()
    routes = webio_routes(
        applications,
        # PyWebIO 支持 CDN 地址字符串，但其运行时类型推断仅保留了 bool。
        cdn=cast(Any, validated_cdn),
        allowed_origins=allowed_origins,
        check_origin=check_origin,
    )
    _optimize_initial_page_route(routes)
    routes.insert(0, Route("/robots.txt", robots_txt, methods=["GET", "HEAD"]))
    if static_mounts:
        for mount_path, directory in static_mounts.items():
            routes.append(Mount(mount_path, app=StaticFiles(directory=directory)))
    if static_dir:
        routes.append(
            Mount("/static", app=StaticFiles(directory=static_dir), name="static")
        )
    routes.append(
        Mount(
            "/pywebio_static",
            app=StaticFiles(directory=STATIC_PATH),
            name="pywebio_static",
        )
    )

    try:
        from module.webui.api import api_routes

        routes.extend(api_routes)
    except Exception as e:
        import logging

        logging.getLogger(__name__).error(f"Failed to load api routes: {e}")

    middleware = [
        # 仅处理 HTTP 响应；WebSocket 不经过该中间件，Starlette 也会跳过 SSE。
        Middleware(
            GZipMiddleware,
            minimum_size=HTTP_GZIP_MINIMUM_SIZE,
            compresslevel=HTTP_GZIP_COMPRESS_LEVEL,
        ),
        Middleware(HeaderMiddleware),
    ]
    return Starlette(
        routes=routes, middleware=middleware, debug=debug, **starlette_settings
    )


def start_server(
    applications,
    port=0,
    host="",
    cdn: str | bool = False,
    static_dir=None,
    remote_access=False,
    debug=False,
    allowed_origins=None,
    check_origin=None,
    auto_open_webbrowser=False,
    static_mounts: Mapping[str, str] | None = None,
    **uvicorn_settings,
):

    app = asgi_app(
        applications,
        cdn=cdn,
        static_dir=static_dir,
        static_mounts=static_mounts,
        debug=debug,
        allowed_origins=allowed_origins,
        check_origin=check_origin,
    )

    if auto_open_webbrowser:
        asyncio.get_event_loop().create_task(
            open_webbrowser_on_server_started("localhost", port)
        )

    if not host:
        host = "0.0.0.0"

    if port == 0:
        port = get_free_port()

    if remote_access:
        start_remote_access_service(local_port=port)

    uvicorn.run(app, host=host, port=port, **uvicorn_settings)
