"""舰队管理 WebUI 页面。"""

from module.webui.app_dependencies import (
    BinarySwitchButton,
    deep_get,
    put_scope,
    put_table,
    put_text,
    t,
    toast,
    use_scope,
)
from module.webui.app_types import WebUIMixinBase


class FleetManagementMixin(WebUIMixinBase):
    """提供舰队扫描触发与已保存舰队信息展示。"""

    RESULT_PATH = "FleetInfo.FleetInfo.Result"
    RECORD_PATH = "FleetInfo.FleetInfo.Record"
    CATEGORIES = (
        ("main", "Gui.FleetManagement.Main"),
        ("vanguard", "Gui.FleetManagement.Vanguard"),
        ("submarine", "Gui.FleetManagement.Submarine"),
    )

    def _fleet_scan_running(self) -> bool:
        return bool(getattr(self, "alas", None) and self.alas.alive)

    def _fleet_scan_start(self) -> None:
        if self._fleet_scan_running():
            toast(t("Gui.FleetManagement.ScanAlreadyRunning"), color="warn")
            return

        self.alas.start("FleetScan")
        if self._fleet_scan_running():
            toast(t("Gui.FleetManagement.ScanStarted"), color="info")
        else:
            toast(t("Gui.FleetManagement.ScanStartFailed"), color="error")

    def _fleet_scan_running_click(self) -> None:
        toast(t("Gui.FleetManagement.ScanAlreadyRunning"), color="warn")

    @use_scope("content", clear=True)
    def fleet_scan_page(self, task: str = "FleetScan") -> None:
        """展示舰队扫描的一次性触发入口。"""
        self.init_menu(name=task)
        self.set_title(t(f"Task.{task}.name"))
        put_scope("fleet_scan_button")

        button = BinarySwitchButton(
            get_state=self._fleet_scan_running,
            label_on=t("Gui.FleetManagement.Scanning"),
            label_off=t("Gui.FleetManagement.StartScan"),
            onclick_on=self._fleet_scan_running_click,
            onclick_off=self._fleet_scan_start,
            color_on="off",
            color_off="on",
            scope="fleet_scan_button",
        )
        self.task_handler.add(button.g(), 1, True)

    @use_scope("content", clear=True)
    def fleet_info_page(self, task: str = "FleetInfo") -> None:
        """展示最近一次舰队扫描写入配置的数据。"""
        self.init_menu(name=task)
        self.set_title(t(f"Task.{task}.name"))

        config = self.alas_config.read_file(self.alas_name)
        result = deep_get(config, self.RESULT_PATH, default={})
        record = deep_get(config, self.RECORD_PATH)
        if not isinstance(result, dict) or not result:
            put_text(t("Gui.FleetManagement.NoResult"))
            return

        put_text(f"{t('Gui.FleetManagement.LastScan')}: {record}")
        for category, category_name in self.CATEGORIES:
            fleets = result.get(category, {})
            if not isinstance(fleets, dict):
                continue
            rows = []
            for fleet, ships in fleets.items():
                if not isinstance(ships, list) or not ships:
                    continue
                rows.append([str(fleet), "\n".join(str(ship) for ship in ships)])
            if rows:
                put_text(t(category_name))
                put_table(
                    [[t("Gui.FleetManagement.Fleet"), t("Gui.FleetManagement.Ships")]]
                    + rows
                )
