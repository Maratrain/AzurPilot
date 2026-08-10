"""AzurPilot WebUI 的兼容入口和 ASGI 应用工厂。

提供 WebUI 的主应用类，通过多个 Mixin 组合实现各功能页面：
仪表盘（Dashboard）、开发者菜单、开发者设置、开发者工具、
版本更新、活动工具等。同时提供 ASGI 应用创建和路由注册。

该模块是 WebUI 的顶层入口，被 gui.py 启动时引用。

设计上采用延迟加载策略：module-level 仅保留静态资源哈希计算，
所有重依赖（pywebio、mixin 等）延迟到 app() 被调用时才加载。
MCP 子应用进一步推迟到首次 /mcp 请求时加载。
"""

from hashlib import sha256
from pathlib import Path
from threading import Lock

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _LazyMCPApp:
    """延迟加载 mcp_server_sse 的 ASGI 包装器。

    MCP 子应用在 app() 构建时不导入，仅当首次收到 /mcp 请求时才加载。
    """

    def __init__(self):
        self._app = None
        self._lock = Lock()

    async def __call__(self, scope, receive, send):
        if self._app is None:
            with self._lock:
                if self._app is None:
                    from mcp_server_sse import app as mcp_app
                    self._app = mcp_app
        await self._app(scope, receive, send)


def _versioned_static_asset(relative_path: str) -> str:
    """返回带内容哈希的相对静态资源地址。"""
    digest = sha256((PROJECT_ROOT / relative_path).read_bytes()).hexdigest()[:12]
    return f"static/{relative_path}?v={digest}"


INITIAL_WEBUI_CSS = _versioned_static_asset("assets/gui/css/alas.css")
WEBUI_THEME_STYLE_NAMES = {
    "dark": ("dark-alas",),
    "advanced_material": ("advanced-material-alas",),
    "dark_advanced_material": (
        "advanced-material-alas",
        "dark-advanced-material-overrides-alas",
    ),
}
INITIAL_LOADING_JS = """
(function () {
    var observer = null;
    function markReady() {
        var root = document.getElementById("pywebio-scope-ROOT");
        var inputs = document.getElementById("input-cards");
        var hasContent = (root && root.firstElementChild)
            || (inputs && inputs.firstElementChild)
            || document.querySelector(".modal");
        if (!hasContent) return;
        document.documentElement.classList.add("alas-initial-ready");
        if (observer) observer.disconnect();
    }
    observer = new MutationObserver(markReady);
    observer.observe(document.body, {childList: true, subtree: true});
    markReady();
})();
"""


def _initial_style_names(theme: str) -> tuple[str, ...]:
    """返回首屏必须通过 HTML 预加载的样式名称。"""
    return (
        "alas",
        "entry-alas",
        *WEBUI_THEME_STYLE_NAMES.get(theme, ("light-alas",)),
    )


def _initial_loading_css(theme: str) -> str:
    """生成在 PyWebIO 首条可见输出前展示的轻量加载骨架。"""
    if theme in ("dark", "dark_advanced_material"):
        background = "#202225"
        foreground = "#f2f3f5"
        accent = "#8b89d8"
        track = "rgba(139, 137, 216, .22)"
    else:
        background = "#f4f5f7"
        foreground = "#34343d"
        accent = "#4e4c97"
        track = "rgba(78, 76, 151, .22)"
    return f"""
html:not(.alas-initial-ready) #pywebio-scope-ROOT:empty {{
    position: fixed;
    inset: 0;
    z-index: 2147483000;
    display: grid;
    place-items: center;
    min-height: 100vh;
    background: {background};
    color: {foreground};
}}
html:not(.alas-initial-ready) #pywebio-scope-ROOT:empty::before {{
    width: 34px;
    height: 34px;
    content: "";
    border: 3px solid {track};
    border-top-color: {accent};
    border-radius: 50%;
    animation: alas-initial-spin .72s linear infinite;
}}
html:not(.alas-initial-ready) #pywebio-scope-ROOT:empty::after {{
    position: absolute;
    top: calc(50% + 34px);
    content: "AzurPilot";
    font: 600 14px/1.5 system-ui, sans-serif;
    letter-spacing: .04em;
}}
@keyframes alas-initial-spin {{
    to {{ transform: rotate(360deg); }}
}}
@media (prefers-reduced-motion: reduce) {{
    html:not(.alas-initial-ready) #pywebio-scope-ROOT:empty::before {{
        animation-duration: 1.8s;
    }}
}}
"""


def _build_alas_gui_class():
    """延迟构建 AlasGUI 类，合并所有 Mixin。"""
    from module.webui.app_dashboard import DashboardMixin
    from module.webui.app_dependencies import Dict, Frame, List, RichLog
    from module.webui.app_developer_menu import DeveloperMenuMixin
    from module.webui.app_developer_settings import DeveloperSettingsMixin
    from module.webui.app_developer_tools import DeveloperToolsMixin
    from module.webui.app_developer_update import DeveloperUpdateMixin
    from module.webui.app_event_tools import EventToolsMixin
    from module.webui.app_fleet_management import FleetManagementMixin
    from module.webui.app_home import HomeMixin
    from module.webui.app_instances import InstanceMixin
    from module.webui.app_overview import OverviewMixin
    from module.webui.app_shell import AppShellMixin
    from module.webui.app_stat_action_point import ActionPointStatisticsMixin
    from module.webui.app_stat_action_point_toolbar import ActionPointToolbarMixin
    from module.webui.app_stat_commission import CommissionIncomeStatisticsMixin
    from module.webui.app_stat_opsi import OpsiStatisticsMixin
    from module.webui.app_stat_opsi_export import OpsiExportMixin
    from module.webui.app_stat_resource import ResourceStatisticsMixin
    from module.webui.app_stat_ship import ShipExperienceStatisticsMixin
    from module.webui.app_statistics_page import StatisticsPageMixin
    from module.webui.app_task_config import TaskConfigMixin

    class AlasGUI(
        AppShellMixin,
        StatisticsPageMixin,
        ActionPointStatisticsMixin,
        ActionPointToolbarMixin,
        ResourceStatisticsMixin,
        OpsiStatisticsMixin,
        OpsiExportMixin,
        ShipExperienceStatisticsMixin,
        CommissionIncomeStatisticsMixin,
        FleetManagementMixin,
        TaskConfigMixin,
        EventToolsMixin,
        OverviewMixin,
        DashboardMixin,
        DeveloperMenuMixin,
        DeveloperUpdateMixin,
        DeveloperSettingsMixin,
        DeveloperToolsMixin,
        InstanceMixin,
        HomeMixin,
        Frame,
    ):
        """组合各 WebUI 视图的会话控制器。"""

        ALAS_MENU: Dict[str, Dict[str, List[str]]]
        ALAS_ARGS: Dict[str, Dict[str, Dict[str, Dict[str, str]]]]
        theme = "default"
        _log = RichLog

    return AlasGUI


def debug() -> None:
    """初始化 WebUI 后进入交互式调试会话。"""
    from module.webui.app_lifecycle import startup

    startup()
    _build_alas_gui_class()().run()


def app():
    """创建供 Uvicorn 使用的 ASGI 应用工厂。

    采用延迟加载：所有重依赖（pywebio、mixin 等）在此函数调用时才加载。

    Returns:
        Starlette: 挂载 WebUI 页面和 MCP 子应用的 ASGI 应用。
    """
    import argparse
    import time
    from typing import List

    from module.webui.app_dependencies import (
        IS_ON_PHONE_CLOUD,
        PUBLIC_WEBUI_PASSWORD_GENERATE_FAILED_MESSAGE,
        ProcessManager,
        RESTRICTED_DEVICE_IDS,
        RESTRICTED_DEVICE_MESSAGE,
        State,
        asgi_app,
        get_device_id,
        get_localstorage_values,
        info,
        lang,
        load_webui_styles,
        local,
        logger,
        login,
        popup,
        run_js,
        set_env,
        updater,
        webconfig,
    )
    from module.webui.app_helpers import (
        ensure_public_webui_password,
        is_demo_mode,
        is_webui_password_set,
    )
    from module.webui.app_lifecycle import clearup, startup

    AlasGUI = _build_alas_gui_class()

    parser = argparse.ArgumentParser(description="Alas web service")
    parser.add_argument(
        "-k", "--key", type=str, help="Password of alas. No password by default"
    )
    parser.add_argument(
        "--cdn",
        action="store_true",
        help="Use jsdelivr cdn for pywebio static files (css, js). Self host cdn by default.",
    )
    parser.add_argument(
        "--run",
        nargs="+",
        type=str,
        help="Run alas by config names on startup",
    )
    args, _ = parser.parse_known_args()

    initial_style_names = _initial_style_names(AlasGUI.theme)
    initial_css_files = (
        INITIAL_WEBUI_CSS,
        *(
            _versioned_static_asset(f"assets/gui/css/{name}.css")
            for name in initial_style_names[1:]
        ),
    )
    initial_loading_css = _initial_loading_css(AlasGUI.theme)
    lang.LANG = State.deploy_config.Language
    key = args.key if is_webui_password_set(args.key) else State.deploy_config.Password
    key, password_error = ensure_public_webui_password(key)
    cdn: str | bool = args.cdn if args.cdn else State.deploy_config.CDN
    runs: List[str] | None = None
    if args.run:
        runs = args.run
    elif State.deploy_config.Run:
        tmp = State.deploy_config.Run.split(",")
        runs = [item.strip(" ['\"]") for item in tmp if item]
    instances: List[str] | None = runs

    logger.hr("[WebUI] WebUI 配置")
    logger.attr("主题", State.deploy_config.Theme)
    logger.attr("语言", lang.LANG)
    logger.attr("密码", is_webui_password_set(key))
    logger.attr("CDN", cdn)
    logger.attr("云手机", IS_ON_PHONE_CLOUD)

    from deploy.atomic import atomic_failure_cleanup

    atomic_failure_cleanup("./config")
    static_mounts = {
        "/static/assets": str(PROJECT_ROOT / "assets"),
        "/static/doc": str(PROJECT_ROOT / "doc"),
    }

    def _block_restricted_device() -> bool:
        if is_demo_mode():
            return False
        if get_device_id() not in RESTRICTED_DEVICE_IDS:
            return False
        popup(
            "安全保护",
            RESTRICTED_DEVICE_MESSAGE,
            implicit_close=False,
            closable=False,
        )
        return True

    def _block_public_webui_password_error() -> bool:
        if is_demo_mode() or password_error is None:
            return False
        popup(
            "安全保护",
            PUBLIC_WEBUI_PASSWORD_GENERATE_FAILED_MESSAGE,
            implicit_close=False,
            closable=False,
        )
        return True

    def _run_gui(initial_page: str = "home") -> None:
        AlasGUI.set_theme(theme=State.deploy_config.Theme)
        set_env(title="AzurPilot", output_animation=False)
        load_webui_styles(
            theme=AlasGUI.theme,
            is_mobile=info.user_agent.is_mobile,
            preloaded_styles=initial_style_names,
        )
        if _block_restricted_device() or _block_public_webui_password_error():
            return
        localstorage = None
        if is_webui_password_set(key):
            localstorage = get_localstorage_values(
                ("password", "clarity_notice_shown", "aside")
            )
        if is_webui_password_set(key) and not login(
            key, stored_password=localstorage.get("password")
        ):
            logger.warning(f"[WebUI] {info.user_ip} 登录失败")
            time.sleep(1.5)
            run_js("location.reload();")
            return
        gui = AlasGUI()
        local.gui = gui
        gui.run(initial_page=initial_page, localstorage=localstorage)

    @webconfig(
        css_file=initial_css_files,
        css_style=initial_loading_css,
        js_code=INITIAL_LOADING_JS,
    )
    def index() -> None:
        _run_gui()

    @webconfig(
        css_file=initial_css_files,
        css_style=initial_loading_css,
        js_code=INITIAL_LOADING_JS,
    )
    def manage() -> None:
        _run_gui(initial_page="manage")

    application = asgi_app(
        applications=[index, manage],
        cdn=cdn,
        static_mounts=static_mounts,
        debug=False,
        on_startup=[
            startup,
            lambda: ProcessManager.restart_processes(
                instances=instances, ev=updater.event
            ),
        ],
        on_shutdown=[clearup],
    )
    application.mount("/mcp", _LazyMCPApp())
    return application


# 兼容旧导入路径：updater.py 从 module.webui.app import clearup
# 注意：此处使用 lazy import 避免 module-level 触发 pywebio 加载
def __getattr__(name: str):
    if name == "clearup":
        from module.webui.app_lifecycle import clearup

        return clearup
    if name == "startup":
        from module.webui.app_lifecycle import startup

        return startup
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")