"""大世界战略搜索处理器。

管理大世界战略搜索（Strategic Search）功能的交互流程。
提供战略搜索面板的进入、标签页切换（已净化/未净化）、
搜索选项勾选、确认弹窗处理以及滚动条控制等操作。
"""
from module.base.utils import get_color
from module.logger import logger
from module.os_handler.assets import *
from module.os_handler.map_event import MapEventHandler
from module.ui.scroll import Scroll

STRATEGIC_SEARCH_SCROLL = Scroll(STRATEGIC_SEARCH_SCROLL_AREA, color=(247, 211, 66), name='STRATEGIC_SEARCH_SCROLL')


class StrategicSearchHandler(MapEventHandler):
    def strategy_search_enter(self):
        logger.info('[大世界-策略] 进入策略搜索')
        self.interval_clear(STRATEGIC_SEARCH_MAP_OPTION_OFF)
        for _ in self.loop():
            # End
            if self.appear(STRATEGIC_SEARCH_POPUP_CHECK, offset=(20, 20)):
                return True
            if self.handle_map_event():
                continue
            if self.appear(AUTO_SEARCH_REWARD, offset=(50, 50)):
                continue
            if self.match_template_color(STRATEGIC_SEARCH_MAP_OPTION_OFF, offset=(20, 20), interval=2):
                self.device.click(STRATEGIC_SEARCH_MAP_OPTION_OFF)
                continue

    def strategic_search_set_tab(self):
        logger.info('[大世界-策略] 设置策略搜索标签')
        for _ in self.loop():
            if get_color(self.device.image, STRATEGIC_SEARCH_TAB_SECURED.area)[2] <= 150:
                self.device.click(STRATEGIC_SEARCH_TAB_SECURED)
                continue
            if get_color(self.device.image, STRATEGIC_SEARCH_TAB_SECURED.area)[2] > 150:
                break

    def _strategy_search_scroll_appear(self):
        """
        Returns:
            bool: If it still exists
        """
        for _ in self.loop(timeout=2):
            if STRATEGIC_SEARCH_SCROLL.appear(main=self):
                return True
            else:
                logger.warning('[大世界-策略] 策略搜索滚动条消失')
        else:
            logger.warning('[大世界-策略] 策略搜索滚动条消失确认')
            return False

    def _strategy_option_selected(self, button):
        """
        Check if a button is selected
        """
        return self.image_color_count(button.button, color=(156, 255, 82), count=30)

    def strategic_search_set_option(self):
        """
        Returns:
            If success. False if strategic settings closed for unknown reason.
        """
        logger.info('[大世界-策略] 设置策略搜索选项')
        for _ in self.loop():
            if self._strategy_option_selected(STRATEGIC_SEARCH_ZONEMODE_REPEAT) \
                    and self._strategy_option_selected(STRATEGIC_SEARCH_MERCHANT_STOP):
                logger.attr('区域模式', '重复')
                logger.attr('遭遇商人', '停止')
                break
            if self._strategy_option_selected(STRATEGIC_SEARCH_ZONEMODE_RANDOM):
                logger.attr('区域模式', '随机')
                self.device.click(STRATEGIC_SEARCH_ZONEMODE_REPEAT)
                continue
            if self._strategy_option_selected(STRATEGIC_SEARCH_MERCHANT_CONTINUE):
                logger.attr('遭遇商人', '继续')
                self.device.click(STRATEGIC_SEARCH_MERCHANT_STOP)
                continue

        STRATEGIC_SEARCH_SCROLL.drag_threshold = 0.1
        STRATEGIC_SEARCH_SCROLL.set(0.5, main=self)
        if not self._strategy_search_scroll_appear():
            return False

        for _ in self.loop():
            self.appear(STRATEGIC_SEARCH_DEVICE_CHECK, offset=(20, 200), similarity=0.7)
            STRATEGIC_SEARCH_DEVICE_STOP.load_offset(STRATEGIC_SEARCH_DEVICE_CHECK)
            STRATEGIC_SEARCH_DEVICE_CONTINUE.load_offset(STRATEGIC_SEARCH_DEVICE_CHECK)

            if self._strategy_option_selected(STRATEGIC_SEARCH_DEVICE_STOP):
                logger.attr('遭遇装置', '停止')
                break
            if self._strategy_option_selected(STRATEGIC_SEARCH_DEVICE_CONTINUE):
                logger.attr('遭遇装置', '继续')
                self.device.click(STRATEGIC_SEARCH_DEVICE_STOP)
                continue

        STRATEGIC_SEARCH_SCROLL.drag_threshold = 0.05
        STRATEGIC_SEARCH_SCROLL.edge_add = (0.5, 0.8)
        STRATEGIC_SEARCH_SCROLL.set_bottom(main=self)
        if not self._strategy_search_scroll_appear():
            return False

        for _ in self.loop():
            self.appear(STRATEGIC_SEARCH_SUBMIT_CHECK, offset=(20, 20), similarity=0.7)
            STRATEGIC_SEARCH_SUBMIT_OFF.load_offset(STRATEGIC_SEARCH_SUBMIT_CHECK)
            STRATEGIC_SEARCH_SUBMIT_ON.load_offset(STRATEGIC_SEARCH_SUBMIT_CHECK)

            if self._strategy_option_selected(STRATEGIC_SEARCH_SUBMIT_ON):
                logger.attr('自动提交', '开启')
                break
            if self._strategy_option_selected(STRATEGIC_SEARCH_SUBMIT_OFF):
                logger.attr('自动提交', '关闭')
                self.device.click(STRATEGIC_SEARCH_SUBMIT_ON)
                continue

        return True

    def strategic_search_confirm(self):
        logger.info('[大世界-策略] 策略搜索确认')
        for _ in self.loop():
            if self.appear(STRATEGIC_SEARCH_POPUP_CHECK, offset=(20, 20)) \
                    and self.handle_popup_confirm(offset=(30, 30), name='STRATEGIC_SEARCH'):
                continue
            if self.is_in_map():
                return True

    def strategic_search_start(self, skip_first_screenshot=True):
        """
        Args:
            skip_first_screenshot (bool): Skip first screenshot or not

        Returns:
            If success.

        Pages:
            in: IN_MAP
            out: IN_MAP, with strategic search running
        """
        logger.hr('策略搜索开始')
        for _ in range(3):
            self.strategy_search_enter()
            self.strategic_search_set_tab()
            success = self.strategic_search_set_option()
            if not success:
                continue
            self.strategic_search_confirm()
            return True

        logger.warning('[大世界-策略] 策略搜索启动失败')
        return False
