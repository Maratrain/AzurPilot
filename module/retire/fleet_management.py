"""舰队管理扫描任务。"""

from module.config.time_source import now as current_time
from module.logger import logger
from module.retire.dock import Dock
from module.retire.scanner import FleetManagementScanner
from module.ui.page import page_dock


class FleetManagement(Dock):
    """扫描船坞内已编入舰队的舰船，并持久化舰队信息。"""

    SCAN_CATEGORIES = {
        "main": "main",
        "vanguard": "vanguard",
        "submarine": "ss",
    }
    RESULT_PATH = "FleetInfo.FleetInfo.Result"
    RECORD_PATH = "FleetInfo.FleetInfo.Record"

    @staticmethod
    def _normalize_result(result):
        """将舰队编号规范化为 JSON 对象可用的字符串键。"""
        return {
            str(fleet): [str(name) for name in names]
            for fleet, names in result.items()
        }

    def _save_result(self, result) -> None:
        """一次性保存全部扫描结果，避免留下不完整的分类数据。"""
        self.config.modified[self.RESULT_PATH] = result
        self.config.modified[self.RECORD_PATH] = current_time().replace(microsecond=0)
        self.config.save()

    def run(self) -> None:
        """执行一次舰队扫描。

        Pages:
            in: Any
            out: page_dock
        """
        logger.hr("舰队扫描", level=0)
        self.ui_ensure(page_dock)
        scanner = FleetManagementScanner()
        result = {}

        try:
            self.dock_favourite_set(False, wait_loading=False)
            self.dock_sort_method_dsc_set(False, wait_loading=False)
            for category, index in self.SCAN_CATEGORIES.items():
                logger.hr(f"舰队扫描-{category}", level=1)
                self.dock_filter_set(
                    sort="level",
                    index=index,
                    faction="all",
                    rarity="all",
                    extra="no_limit",
                )
                result[category] = self._normalize_result(scanner.scan(self.device.image))

            self._save_result(result)
        finally:
            # 扫描不改变用户后续使用船坞时的筛选和排序状态。
            self.dock_reset()
