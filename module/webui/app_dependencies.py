"""WebUI 依赖兼容层：合并轻量层 + 懒加载重依赖层。

保持原有 `from module.webui.app_dependencies import ...` 语法不变。
首次访问重依赖符号时才加载 app_dependencies_heavy。
"""

# 显式导入轻量层公共符号（类型、协议、常量、工具函数、标准库重导出）
from module.webui.app_dependencies_light import (
    # 常量
    IS_ON_PHONE_CLOUD,
    RESTRICTED_DEVICE_IDS,
    RESTRICTED_DEVICE_MESSAGE,
    PUBLIC_WEBUI_PASSWORD_GENERATE_FAILED_MESSAGE,
    # 工具函数
    readable_time,
    time_delta,
    # 标准库类型别名
    Dict,
    List,
    Optional,
    Callable,
    Any,
    Protocol,
    cast,
    runtime_checkable,
    # 标准库模块
    os,
    re,
    argparse,
    json,
    queue,
    secrets,
    string,
    threading,
    time,
    base64,
    datetime,
    timedelta,
    timezone,
    Path,
    partial,
)

# 重依赖符号集合（来自 app_dependencies_heavy）
_HEAVY_SYMBOLS = {
    # pywebio 相关
    "pywebio_output",
    "pywebio_pin",
    "webconfig",
    "actions",
    "file_upload",
    "input_group",
    "Output",
    "clear",
    "close_popup",
    "popup",
    "put_button",
    "put_buttons",
    "put_collapse",
    "put_column",
    "put_error",
    "put_html",
    "put_link",
    "put_loading",
    "put_markdown",
    "put_row",
    "put_table",
    "put_text",
    "put_warning",
    "toast",
    "use_scope",
    "pin",
    "download",
    "go_app",
    "info",
    "local",
    "register_thread",
    "run_js",
    "set_env",
    "eval_js",
    "put_scope",
    "pin_on_change",
    # 配置 & 核心模块
    "lang",
    "AzurLaneConfig",
    "Function",
    "deep_get",
    "deep_iter",
    "deep_set",
    "to_server",
    "parse_task_priority",
    "task_priority_from_config",
    "DEFAULT_CONFIG_NAME",
    "alas_instance",
    "alas_template",
    "dict_to_kv",
    "filepath_args",
    "filepath_config",
    "is_oobe_needed",
    "read_file",
    "LogRes",
    "logger",
    "start_ocr_server_process",
    "stop_ocr_server_process",
    "load_config",
    "get_config_mod",
    "ProcessManager",
    "RemoteAccess",
    "asgi_app",
    "init_discord_rpc",
    "close_discord_rpc",
    "t",
    "_t",
    "put_checkbox",
    "put_input",
    "put_select",
    "BinarySwitchButton",
    "Icon",
    "Switch",
    "TaskHandler",
    "get_alas_config_listen_path",
    "get_localstorage",
    "get_localstorage_values",
    "load_webui_styles",
    "set_localstorage",
    "get_window_visibility_state",
    "login",
    "parse_pin_value",
    "raise_exception",
    "re_fullmatch",
    "to_pin_value",
    "RichLog",
    "put_icon_buttons",
    "put_loading_text",
    "put_none",
    "put_output",
    "get_dashboard_scope_id",
    "get_group_scope_id",
    "build_error_html",
    "build_event_calculator_html",
    "build_event_calculator_js",
    "load_event_calculator",
    "get_device_id",
    "RESTRICTED_DEVICE_IDS",
    "RESTRICTED_DEVICE_MESSAGE",
    "PUBLIC_WEBUI_PASSWORD_GENERATE_FAILED_MESSAGE",
    "IS_ON_PHONE_CLOUD",
    "State",
    "task_handler",
    "updater",
    "current_time",
    "time_delta",
    "time_source_status",
    # 类型别名 & 协议（来自 widgets、base 等，运行时真实实现）
    "T_Output_Kwargs",
    "Frame",
    # 副作用函数（module-level 执行）
    "patch_executor",
    "patch_mimetype",
    "fix_py37_subprocess_communicate",
    "import_fake_pil_module",
}


def __getattr__(name: str):
    """懒加载重依赖符号。"""
    if name in _HEAVY_SYMBOLS:
        from module.webui import app_dependencies_heavy as heavy

        return getattr(heavy, name)
    # 兜底：轻量层已显式导入的符号不会触发 __getattr__
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    from module.webui import app_dependencies_light as light

    return sorted(list(globals().keys()) + list(_HEAVY_SYMBOLS) + [k for k in dir(light) if not k.startswith("_")])