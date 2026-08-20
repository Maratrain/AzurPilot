"""WebUI 统计页装配器。"""

from datetime import date
from pathlib import Path

import module.webui.lang as lang
from module.webui.app_dependencies import put_button, put_scope, run_js, t, use_scope
from module.webui.app_types import WebUIMixinBase


class StatisticsPageMixin(WebUIMixinBase):
    """惰性装配并复用统计子视图。"""

    def alas_set_stat(self) -> None:
        """显示统计页，已装配的内容会直接复用。"""
        self.init_menu(name="Stat")
        self.set_title(t("Gui.Overview.Stat"))
        if not hasattr(self, "_ap_chart_view"):
            self._ap_chart_view = "line"
        if not hasattr(self, "_commission_income_period"):
            self._commission_income_period = "month"

        cache_key = self._get_statistics_cache_key()
        cached_key = getattr(self, "_statistics_cache_key", None)

        if cached_key != cache_key:
            self._mount_statistics_page(cache_key)
        else:
            self._refresh_statistics_if_changed()

        # 原先的 5 个任务会在进页后立即各重绘一次。现在仅轮询本地数据源
        # 版本并提示存在新数据，不再打断用户正在查看的图表状态。
        self.task_handler.add(self._refresh_statistics_if_changed, 15, True)

    def _mount_statistics_page(self, cache_key) -> None:
        """为当前实例首次创建统计内容。"""
        with self._page_lock:
            if getattr(self, "page", None) != "Stat":
                return
            if getattr(self, "_statistics_cache_key", None) is not None:
                self.cleanup_client_resources(
                    "__apChartCleanups",
                    "__resourceChartCleanups",
                )

            with use_scope("statistics-content", clear=True):
                put_scope(
                    "statistics-toolbar",
                    [
                        put_button(
                            t("Gui.Stat.Refresh"),
                            onclick=self._refresh_statistics_page,
                            color="off",
                        )
                    ],
                ).style(
                    "display:flex;justify-content:flex-end;margin-bottom:.5rem;"
                )
                put_scope("ap_chart", [])
                put_scope("resource_chart", [])
                put_scope("opsi_stats", [])
                put_scope("ship_exp_table", [])
                put_scope("commission_income", [])

            self._statistics_cache_key = cache_key
            self._render_statistics_sections()
            self._statistics_source_signature = (
                self._get_statistics_source_signature()
            )
            self._statistics_refresh_pending = False

    def _refresh_statistics_page(self) -> None:
        """刷新已挂载的全部统计模块。"""
        with self._page_lock:
            if getattr(self, "page", None) != "Stat":
                return
            if getattr(self, "_statistics_cache_key", None) is None:
                return

            self._render_statistics_sections()
            self._statistics_source_signature = (
                self._get_statistics_source_signature()
            )
            self._set_statistics_refresh_pending(False)

    def _refresh_statistics_if_changed(self) -> None:
        """检测当前实例的本地统计数据变化并更新刷新提示。"""
        if getattr(self, "page", None) != "Stat":
            return
        if (
            getattr(self, "_statistics_cache_key", None)
            != self._get_statistics_cache_key()
        ):
            return

        source_signature = self._get_statistics_source_signature()
        self._set_statistics_refresh_pending(
            source_signature
            != getattr(self, "_statistics_source_signature", None)
        )

    def _set_statistics_refresh_pending(self, pending: bool) -> None:
        """只更新刷新提示，不替换用户正在查看的统计 DOM。"""
        if pending == getattr(self, "_statistics_refresh_pending", False):
            return
        self._statistics_refresh_pending = pending
        run_js(
            """
            (function () {
                var toolbar = document.getElementById(
                    "pywebio-scope-statistics-toolbar"
                );
                var button = toolbar && toolbar.querySelector("button");
                if (!button) return;
                button.classList.toggle("statistics-refresh-pending", pending);
                button.title = pending ? refreshHint : "";
                button.setAttribute(
                    "aria-label",
                    pending ? refreshHint : button.textContent.trim()
                );
            })();
            """,
            pending=pending,
            refreshHint=t("Gui.Stat.NewDataAvailable"),
        )

    def _render_statistics_sections(self) -> None:
        """统一刷新各统计子视图。"""
        self._render_ap_chart()
        self._render_resource_chart()
        self._render_opsi_stats()
        self._render_ship_exp()
        self._render_commission_income()

    def _get_statistics_cache_key(self):
        """返回会影响统计页文案与数据归属的键。"""
        return getattr(self, "alas_name", None), lang.LANG

    def _get_statistics_source_signature(self):
        """以廉价的文件版本检查代替重复解析和绘图。"""
        project_root = Path(__file__).resolve().parents[2]
        instance_name = getattr(self, "alas_name", None) or "default"
        paths = (
            project_root / "config" / "cl1_data.db",
            project_root / "config" / "cl1_data.db-wal",
            project_root / "config" / "azurstats_local.db",
            project_root / "config" / "azurstats_local.db-wal",
            project_root / "log" / "cl1" / instance_name / "ship_exp_data.json",
        )
        return date.today().isoformat(), tuple(
            self._get_statistics_file_version(path) for path in paths
        )

    @staticmethod
    def _get_statistics_file_version(path: Path):
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size
