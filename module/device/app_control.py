"""应用生命周期控制模块。

管理 Android 应用（碧蓝航线）的启动、停止、缓存清除等操作，
以及 UI 层级结构（hierarchy）的获取和 XPath 元素查询。
根据控制方法和模拟器类型自动选择 ADB 或 uiautomator2 后端。
"""
from lxml import etree

from module.base.timer import Timer
from module.device.method.adb import Adb
from module.device.method.uiautomator_2 import Uiautomator2
from module.device.method.utils import HierarchyButton
from module.device.method.wsa import WSA
from module.exception import ScriptError
from module.logger import logger


class AppControl(Adb, WSA, Uiautomator2):
    """应用生命周期和 UI 层级管理器。

    通过多重继承组合 ADB、WSA 和 uiautomator2 后端，根据控制方法
    自动分发应用的启动、停止、状态查询操作。提供 UI 层级转储和
    XPath 元素查询功能用于界面状态检测。

    Attributes:
        hierarchy (etree._Element): 最近一次获取的 UI 层级树。
        _app_u2_family (list[str]): 需要使用 uiautomator2 后端的控制方法列表。
        _hierarchy_interval (Timer): 层级获取间隔计时器。
    """
    hierarchy: etree._Element
    _app_u2_family = ['uiautomator2', 'minitouch', 'scrcpy', 'MaaTouch', 'nemu_ipc']
    _hierarchy_interval = Timer(0.1)

    def app_current(self) -> str:
        """获取当前前台运行的应用包名。

        根据控制方法选择不同的获取方式：WSA 使用 WSA 后端，
        uiautomator2 家族方法使用 uiautomator2 后端，其他使用 ADB。

        Returns:
            str: 当前前台应用的包名字符串。
        """
        method = self.config.Emulator_ControlMethod
        if self.is_wsa:
            package = self.app_current_wsa()
        elif method in AppControl._app_u2_family:
            package = self.app_current_uiautomator2()
        else:
            package = self.app_current_adb()
        package = package.strip(' \t\r\n')
        return package

    def app_is_running(self) -> bool:
        """检查目标应用（碧蓝航线）是否正在前台运行。

        通过比较当前前台应用包名与配置中的包名来判断。

        Returns:
            bool: 应用在前台运行返回 True。
        """
        package = self.app_current()
        logger.attr('应用包名', package)
        return package == self.package

    def app_start(self):
        """启动目标应用（碧蓝航线）。

        根据设备类型和控制方法选择不同的启动方式：
        WSA 设备指定 display=0，uiautomator2 家族使用 uiautomator2 启动，
        其他使用 ADB am start。
        """
        method = self.config.Emulator_ControlMethod
        logger.info(f'应用启动: {self.package}')
        if self.config.Emulator_Serial == 'wsa-0':
            self.app_start_wsa(display=0)
        elif method in AppControl._app_u2_family:
            self.app_start_uiautomator2()
        else:
            self.app_start_adb()

    def app_stop(self):
        """停止目标应用（碧蓝航线）。

        根据控制方法选择 uiautomator2 或 ADB am force-stop 方式。
        """
        method = self.config.Emulator_ControlMethod
        logger.info(f'应用停止: {self.package}')
        if method in AppControl._app_u2_family:
            self.app_stop_uiautomator2()
        else:
            self.app_stop_adb()

    def app_clear(self):
        """清除目标应用的缓存目录。

        通过 ADB 删除 /sdcard/Android/data/{package}/cache/ 下的文件。
        """
        cache_path = f'/sdcard/Android/data/{self.package}/cache/*'
        logger.info(f'应用清除缓存: {cache_path}')
        result = self.adb_shell(['rm', '-rf', cache_path], timeout=30)
        if result:
            logger.info(f'[设备-应用] 应用清除缓存结果: {result}')

    def hierarchy_timer_set(self, interval=None):
        """设置 UI 层级获取的最小间隔时间。

        Args:
            interval (int, float, optional): 间隔秒数，None 使用默认值 0.1 秒。

        Raises:
            ScriptError: 间隔参数类型不正确时抛出。
        """
        if interval is None:
            interval = 0.1
        elif isinstance(interval, (int, float)):
            # 代码中手动设置时不限制
            pass
        else:
            logger.warning(f'[设备-应用] 未知的层级获取间隔: {interval}')
            raise ScriptError(f'[设备-应用] 未知的层级获取间隔: {interval}')

        if interval != self._hierarchy_interval.limit:
            logger.info(f'[设备-应用] 层级获取间隔设置为 {interval}s')
            self._hierarchy_interval.limit = interval

    def dump_hierarchy(self) -> etree._Element:
        """获取当前界面的 UI 层级结构。

        Returns:
            etree._Element: UI 层级元素，可使用 `self.hierarchy.xpath('//*[@text="Hermit"]')` 选取元素。
        """
        self._hierarchy_interval.wait()
        self._hierarchy_interval.reset()

        method = self.config.Emulator_ControlMethod
        if method in AppControl._app_u2_family:
            self.hierarchy = self.dump_hierarchy_uiautomator2()
        else:
            self.hierarchy = self.dump_hierarchy_adb()
        return self.hierarchy

    def xpath_to_button(self, xpath: str) -> HierarchyButton:
        """
        Args:
            xpath (str):

        Returns:
            HierarchyButton:
                An object with methods and properties similar to Button.
                If element not found or multiple elements were found, return None.
        """
        return HierarchyButton(self.hierarchy, xpath)
