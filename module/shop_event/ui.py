"""
活动商店界面导航与状态检测。

提供活动商店的页面检测、余额 OCR、滚动条控制和标签栏导航。
包含自定义滚动条 EventShopScroll 以适配活动商店特有样式，
支持商店页面可用性检测（时间窗口校验）和货币余额读取。

Pages: in: EVENT_SHOP
"""
import numpy as np
import re
from datetime import datetime, timedelta

import module.config.server as server
from module.base.button import ButtonGrid
from module.base.decorator import cached_property
from module.base.timer import Timer
from module.base.utils import rgb2luma, crop, color_similarity_2d
from module.config.time_source import now as current_time
from module.config.utils import server_time_offset
from module.exception import GameStuckError
from module.logger import logger
from module.meowfficer.assets import MEOWFFICER_GET_CHECK, MEOWFFICER_TRAIN_CLICK_SAFE_AREA
from module.meowfficer.collect import SWITCH_LOCK
from module.ocr.ocr import Ocr, Digit
from module.shop.assets import SHOP_OCR_BALANCE, SHOP_OCR_OIL_CHECK, SHOP_OCR_OIL
from module.shop_event.assets import *
from module.ui.navbar import Navbar
from module.ui.scroll import Scroll
from module.ui.ui import UI


class EventShopScroll(Scroll):
    def match_color(self, main):
        background_transparency = 0.2
        button_transparency = 0.5
        delta_x = 3
        area = (
            self.area[0] - delta_x,
            self.area[1],
            self.area[2] + delta_x,
            self.area[3]
        )
        image = main.image_crop(area, copy=False).astype(float)
        baseline_color = np.mean(image[:, [0, -1], :], axis=1)
        masked_color = image[:, image.shape[1] // 2, :]
        background_mask = background_transparency * np.array(self.color) + (1 - background_transparency) * baseline_color
        button_mask = button_transparency * np.array(self.color) + (1 - button_transparency) * baseline_color
        err_background = np.sum((masked_color - background_mask) ** 2, axis=1)
        err_button = np.sum((masked_color - button_mask) ** 2, axis=1)
        mask = err_button < err_background
        self.length = np.sum(mask)
        # print(mask)
        return mask


EVENT_SHOP_SCROLL = EventShopScroll(
    EVENT_SHOP_SCROLL_AREA,
    color=(44, 48, 56),
    name="EVENT_SHOP_SCROLL"
)
EVENT_SHOP_SCROLL.drag_threshold = 0.1
EVENT_SHOP_SCROLL.edge_threshold = 0.12


if server.server == 'tw':
    EVENT_SHOP_DEADLINE_COLOR = (102, 204, 255)
elif server.server == 'en':
    EVENT_SHOP_DEADLINE_COLOR = (255, 207, 129)
else:
    EVENT_SHOP_DEADLINE_COLOR = (96, 162, 62)
OCR_EVENT_SHOP_DEADLINE = Ocr(SHOP_EVENT_DEADLINE, lang='cnocr', letter=EVENT_SHOP_DEADLINE_COLOR,
                              alphabet='0123456789.:~-', name="OCR_EVENT_SHOP_DEADLINE")

OCR_EVENT_SHOP_PT = Digit(SHOP_OCR_BALANCE, letter=(100, 100, 100), name='OCR_EVENT_SHOP_PT')
OCR_EVENT_SHOP_URPT = Digit(SHOP_OCR_BALANCE_SECOND, letter=(100, 100, 100), name='OCR_EVENT_SHOP_URPT')


class EventShopUI(UI):
    @cached_property
    def event_shop_tab_count_and_navbar(self):
        gap_x = 33
        area = (206, 92, 1092, 134)
        image = crop(self.device.image, area)
        tab = color_similarity_2d(image, color=(232, 238, 240))
        index = np.where(np.average(tab > 221, axis=0) > 0.5)[0]
        count = (area[2] - area[0] + gap_x) // (len(index) + gap_x)
        logger.info(f"活动商店标签数: {count}")
        delta_x = (area[2] - area[0] + gap_x) // count - gap_x
        grid = ButtonGrid((206, 92), (delta_x + gap_x, 44),
                          (delta_x, 44), (count, 1),
                          "EVENT_SHOP_TAB_GRID")
        navbar = Navbar(grids=grid,
                        active_color=(232, 238, 240), inactive_color=(127, 141, 151),
                        active_count=delta_x * (area[3] - area[1]) // 2,
                        inactive_count=delta_x * (area[3] - area[1]) // 2)
        return count, navbar

    @cached_property
    def event_shop_has_urpt(self):
        if self.image_color_count(SHOP_OCR_BALANCE_SECOND, OCR_EVENT_SHOP_URPT.letter, count=15):
            logger.info("[活动商店-UI] 活动商店包含UR点数")
            return True
        else:
            logger.info("[活动商店-UI] 活动商店无UR点数")
            return False

    def _get_event_deadline(self):
        """读取服务器时区中的活动商店截止时间。"""
        period = OCR_EVENT_SHOP_DEADLINE.ocr(self.device.image)
        # OCR 结果类似“:2026.8.13~2026.9.3 23:59:59”，先移除末尾时间。
        period, _, _ = period.partition('23:59:59')
        pattern = r'(\d{4})\.(\d{1,2})\.(\d{1,2})'
        matches = re.findall(pattern, period)
        if not matches or len(matches) < 2:
            logger.warning(f"[活动商店-UI] 活动截止日期读取失败: {period}")
            return None
        y, m, d = matches[-1]
        deadline = datetime(int(y), int(m), int(d)) + timedelta(days=1)  # server deadline
        return deadline

    @cached_property
    def is_event_ended(self):
        if self.config.EVENT_SHOP_IGNORE_DEADLINE:
            return True

        for _ in self.loop(timeout=2):
            deadline = self._get_event_deadline()
            if deadline is not None:
                break
        else:
            logger.error('[活动商店-UI] 多次尝试后仍无法读取活动截止日期')
            return False

        server_now = current_time() - server_time_offset()
        return (deadline - server_now).days < 7

    def event_shop_load_ensure(self):
        ensure_timeout = Timer(3, count=6).start()
        for _ in self.loop():
            if self.image_color_count(SHOP_OCR_BALANCE, OCR_EVENT_SHOP_PT.letter, count=15):
                logger.info("活动商店已加载。")
                break
            if ensure_timeout.reached():
                raise GameStuckError('Waiting too long for EventShop to appear.')
        return True

    @cached_property
    def is_pt_reversed(self):
        return self.ui_process_check_button(check_button=[SHOP_EVENT_20240521])

    def event_shop_get_pt(self):
        if self.is_pt_reversed:
            return OCR_EVENT_SHOP_URPT.ocr(self.device.image)
        return OCR_EVENT_SHOP_PT.ocr(self.device.image)

    def event_shop_get_urpt(self):
        if self.is_pt_reversed:
            return OCR_EVENT_SHOP_PT.ocr(self.device.image)
        return OCR_EVENT_SHOP_URPT.ocr(self.device.image)

    def get_oil(self, skip_first_screenshot=True):
        """
        Returns:
            int: Oil amount
        """
        amount = 0
        timeout = Timer(1, count=2).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if timeout.reached():
                logger.warning('获取石油超时')
                break

            if not self.appear(SHOP_OCR_OIL_CHECK, offset=(10, 2)):
                logger.info('无石油图标')
                continue
            ocr = Digit(SHOP_OCR_OIL, name='OCR_OIL', letter=(247, 247, 247), threshold=128)
            amount = ocr.ocr(self.device.image)
            if amount >= 100:
                break

        return amount

    def handle_get_meowfficer(self):
        if self.appear(MEOWFFICER_GET_CHECK, offset=(40, 40), interval=3):
            logger.info(f'获取指挥喵奖励。')
            SWITCH_LOCK.set('lock', main=self)
            # Wait until info bar disappears
            self.ensure_no_info_bar(timeout=1)
            self.device.click(MEOWFFICER_TRAIN_CLICK_SAFE_AREA)
            return True
        return False
