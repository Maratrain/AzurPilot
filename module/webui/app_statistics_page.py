"""WebUI 统计页装配器。"""

from module.webui.app_dependencies import put_scope, t, use_scope


from module.webui.app_types import WebUIMixinBase


class StatisticsPageMixin(WebUIMixinBase):
    """注册统计子视图及其离页可取消的刷新任务。"""

    @use_scope("content", clear=True)
    def alas_set_stat(self) -> None:
        """显示统计页并注册各统计视图的周期刷新。"""
        self.init_menu(name="Stat")
        self.set_title(t("Gui.Overview.Stat"))
        if not hasattr(self, "_ap_chart_view"):
            self._ap_chart_view = "line"
        if not hasattr(self, "_commission_income_period"):
            self._commission_income_period = "month"

        # 独立 scope 使周期刷新不会清空其他统计区域。
        put_scope("ap_chart", [])
        self._render_ap_chart()
        self.task_handler.add(self._render_ap_chart, 60, True)
        put_scope("resource_chart", [])
        self._render_resource_chart()
        self.task_handler.add(self._render_resource_chart, 60, True)
        put_scope("opsi_stats", [])
        self._render_opsi_stats()
        self.task_handler.add(self._render_opsi_stats, 60, True)
        put_scope("ship_exp_table", [])
        self._render_ship_exp()
        self.task_handler.add(self._render_ship_exp, 60, True)
        put_scope("commission_income", [])
        self._render_commission_income()
        self.task_handler.add(self._render_commission_income, 60, True)
