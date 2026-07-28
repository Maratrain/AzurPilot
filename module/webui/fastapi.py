"""
Copy from pywebio.platform.fastapi
"""

import asyncio
import logging
import os
from typing import Any, cast

import uvicorn
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

STATIC_ASSET_CACHE_CONTROL = "public, max-age=86400, must-revalidate"
NO_CACHE_CONTROL = "no-cache"
HTTP_GZIP_MINIMUM_SIZE = 1024
HTTP_GZIP_COMPRESS_LEVEL = 5


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
            # 静态资源路径未使用内容哈希，缓存一天后仍通过 ETag 重新验证。
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

    PyWebIO 默认实现会为每条消息创建独立 task，页面一次触发多条输出时，
    底层 drain 可能断言失败并打印 "Task exception was never retrieved"。
    """

    def __init__(self, websocket, ioloop):
        super().__init__(websocket, ioloop)
        self._send_lock = asyncio.Lock()

    async def _safe_send_json(self, message):
        async with self._send_lock:
            if self.closed():
                return
            try:
                await self.ws.send_json(message)
            except TypeError:
                logger.exception("PyWebIO 消息序列化失败，消息内容: %s", message)
            except AssertionError, RuntimeError, WebSocketDisconnect:
                logger.debug("WebSocket 已断开，跳过 PyWebIO 消息发送")
            except Exception as e:
                logger.debug("PyWebIO WebSocket 消息发送失败: %s", e)

    async def _safe_close(self):
        async with self._send_lock:
            if self.closed():
                return
            try:
                await self.ws.close()
            except AssertionError, RuntimeError, WebSocketDisconnect:
                logger.debug("WebSocket 已断开，跳过 PyWebIO 连接关闭")
            except Exception as e:
                logger.debug("PyWebIO WebSocket 连接关闭失败: %s", e)

    def write_message(self, message: dict):
        self.ioloop.create_task(self._safe_send_json(message))

    def close(self):
        self.ioloop.create_task(self._safe_close())


def patch_pywebio_websocket_connection():
    pywebio_fastapi.WebSocketConnection = SafeWebSocketConnection


def asgi_app(
    applications,
    cdn: str | bool = True,
    static_dir=None,
    debug: bool = False,
    allowed_origins=None,
    check_origin=None,
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
    routes.insert(0, Route("/robots.txt", robots_txt, methods=["GET", "HEAD"]))
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
    cdn: str | bool = True,
    static_dir=None,
    remote_access=False,
    debug=False,
    allowed_origins=None,
    check_origin=None,
    auto_open_webbrowser=False,
    **uvicorn_settings,
):

    app = asgi_app(
        applications,
        cdn=cdn,
        static_dir=static_dir,
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
