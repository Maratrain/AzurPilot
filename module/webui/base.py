"""
Web界面基础框架。

提供 Base 和 Frame 两个核心类。Base 管理页面生命周期和后台任务调度，
Frame 实现侧边栏、菜单导航和内容区域的切换逻辑。
"""

import functools
import json
import threading

from pywebio.output import clear, put_html, put_scope, put_text, use_scope
from pywebio.session import defer_call, info, run_js

from module.webui.utils import Icon, WebIOTaskHandler, set_localstorage


def render_locked(func):
    """序列化页面渲染。

    PyWebIO 的线程会话为每个按钮回调新建独立线程，后台 TaskHandler
    任务也在独立线程运行。页面渲染均为"清空 scope → 逐条输出"两阶段，
    若两个线程并发渲染同一区域，清空与输出指令会交错，导致：
    控件重复、布局错位、同名 Pin widget 被标记失效，以及
    `use_scope` 对已消失 scope 自动在 ROOT 下创建孤儿容器
    （离开总览页后 10s 刷新任务仍渲染一次即永久残留）。

    使用会话级可重入锁 `self.render_lock` 串行化所有渲染入口；
    远控 P2P 高延迟会大幅拉长竞态窗口，本锁在服务端消除交错。
    """

    @functools.wraps(func)
    def inner(self, *args, **kwargs):
        # 生产类均继承 Base 拥有 render_lock；测试替身等无锁对象直接调用。
        lock = getattr(self, "render_lock", None)
        if lock is None:
            return func(self, *args, **kwargs)
        with lock:
            return func(self, *args, **kwargs)

    # functools.wraps 默认把 __wrapped__ 指向紧内层的包装函数（如 use_scope
    # 的 wrapper）；tests 通过 `__wrapped__` 绕过全部装饰器直达原实现。
    # 沿用被装饰者已有的 __wrapped__ 链，避免装饰叠加后测试误触会话依赖。
    if getattr(func, "__wrapped__", None) is not None:
        inner.__wrapped__ = func.__wrapped__
    return inner


class Base:
    """WebUI 应用的基础类，管理生命周期和任务调度。"""

    def __init__(self) -> None:
        self.alive = True
        # 窗口是否可见（切换页面时置为 False 阻止旧页面的任务继续执行）
        self.visible = True
        # 是否为移动端设备
        self.is_mobile = info.user_agent.is_mobile
        # 任务处理器，用于管理后台异步任务
        self.task_handler = WebIOTaskHandler()
        defer_call(self.stop)

    def stop(self) -> None:
        self.alive = False
        self.task_handler.stop()


class Frame(Base):
    """WebUI 页面框架，管理侧边栏、菜单和内容区域的切换与导航。"""

    def __init__(self) -> None:
        super().__init__()
        self.page = "Home"
        # 可重入：ui_alas 持锁切换实例时，内部 alas_overview→init_menu 会再次申请。
        self._page_lock = threading.RLock()

    @staticmethod
    def cleanup_client_resources(*registry_names: str) -> None:
        """调用前端资源清理器，释放已替换视图持有的事件回调。"""
        if not registry_names:
            return

        run_js(
            f"""
            (function (keys) {{
                keys.forEach(function (key) {{
                    var cleanups = window[key];
                    if (!cleanups) return;
                    Object.keys(cleanups).forEach(function (id) {{
                        if (typeof cleanups[id] === 'function') cleanups[id]();
                    }});
                }});
            }})({json.dumps(registry_names)});
            """
        )

    def init_aside(self, expand_menu: bool = True, name: str = None) -> None:
        """
        侧边栏按钮点击时的初始化回调。

        展开菜单并高亮指定按钮。菜单由目标页面准备完成后替换。

        Args:
            expand_menu: 是否展开菜单。
            name: 需要高亮的按钮名称（标签）。
        """
        self.visible = True
        self.task_handler.remove_pending_task()
        if expand_menu:
            self.expand_menu()
        if name:
            self.active_button("aside", name)
            set_localstorage("aside", name)

    def init_menu(self, collapse_menu: bool = True, name: str = None) -> None:
        """
        菜单按钮点击时的初始化回调。

        清空内容区域，折叠菜单，并高亮指定按钮。

        Args:
            collapse_menu: 是否折叠菜单。
            name: 需要高亮的按钮名称（标签）。
        """
        self.visible = True
        self.task_handler.remove_pending_task()
        with self._page_lock:
            self.page = name
            clear("content")
        self.set_statistics_content_visible(name == "Stat")
        if collapse_menu:
            self.collapse_menu()
        if name:
            self.active_button("menu", name)

    @staticmethod
    def set_statistics_content_visible(visible: bool) -> None:
        """在普通内容区与可复用的统计内容区之间切换。"""
        run_js(
            """
            (function () {
                var content = document.getElementById("pywebio-scope-content");
                var statistics = document.getElementById(
                    "pywebio-scope-statistics-content"
                );
                if (content) content.style.display = visible ? "none" : "";
                if (statistics) statistics.style.display = visible ? "" : "none";
            })();
            """,
            visible=visible,
        )

    @staticmethod
    @use_scope("ROOT", clear=True)
    def _show() -> None:
        put_scope(
            "header",
            [
                put_html(Icon.ALAS).style("--header-icon--"),
                put_text("港区OA").style("--header-text--"),
                put_scope("header_status"),
                put_scope("header_title"),
            ],
        )
        put_scope(
            "contents",
            [
                put_scope("aside"),
                put_scope("menu"),
                put_scope("content"),
                put_scope("statistics-content").style("display: none;"),
            ],
        )

    @staticmethod
    @use_scope("header_title", clear=True)
    def set_title(text=""):
        put_text(text)

    @staticmethod
    def collapse_menu() -> None:
        run_js(
            """
            $("#pywebio-scope-menu").addClass("container-menu-collapsed");
            $(".container-content-collapsed").removeClass("container-content-collapsed");
        """
        )

    @staticmethod
    def expand_menu() -> None:
        run_js(
            """
            $(".container-menu-collapsed").removeClass("container-menu-collapsed");
            $("#pywebio-scope-content, #pywebio-scope-statistics-content")
                .addClass("container-content-collapsed");
        """
        )

    @staticmethod
    def active_button(position, value) -> None:
        run_js(
            f"""
            $("button.btn-{position}").removeClass("btn-{position}-active");
            $("div[style*='--{position}-{value}--']>button").addClass("btn-{position}-active");
        """
        )

    @staticmethod
    def pin_set_invalid_mark(keys) -> None:
        if isinstance(keys, str):
            keys = [keys]
        keys = ["_".join(key.split(".")) for key in keys]
        js = "".join(
            [
                f"""$(".form-control[name='{key}']").addClass('is-invalid');"""
                for key in keys
            ]
        )
        if js:
            run_js(js)

    @staticmethod
    def pin_remove_invalid_mark(keys) -> None:
        if isinstance(keys, str):
            keys = [keys]
        keys = ["_".join(key.split(".")) for key in keys]
        js = "".join(
            [
                f"""$(".form-control[name='{key}']").removeClass('is-invalid');"""
                for key in keys
            ]
        )
        if js:
            run_js(js)
