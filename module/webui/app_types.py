"""WebUI 拆分视图的静态会话契约。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


class WebUIMixinBase:
    """为组合式 WebUI 视图提供静态会话边界。

    ``AlasGUI`` 在运行时由多个视图 Mixin 和 ``Frame`` 组合而成。单独分析
    任一视图时，类型检查器无法获知其余组件提供的方法和属性。以下声明仅在
    ``TYPE_CHECKING`` 中生效，不会修改运行时 MRO 或吞掉实际的属性访问异常。
    """

    if TYPE_CHECKING:
        # 常见会话状态需要显式声明，避免对动态写入的正常状态产生误报。
        alas: Any
        alas_config: Any
        alas_name: str
        alas_mod: str
        ALAS_MENU: Any
        ALAS_ARGS: Any
        task_handler: Any
        simulator: Any
        state_switch: Any
        visible: bool
        alive: bool
        is_mobile: bool
        _log: Any
        _update_notified: bool
        _overview_snapshot: Any
        _overview_log: Any
        _overview_log_config_name: str | None
        _ap_chart_view: str
        _commission_income_period: str
        _statistics_cache_key: Any
        _statistics_source_signature: Any
        _statistics_refresh_pending: bool

        def __getattr__(self, name: str) -> Any:
            """描述跨 Mixin 和 Frame 的动态成员解析。"""
            ...
