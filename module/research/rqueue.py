"""
科研队列管理。

本模块管理科研系统的队列功能，包括：
- 将已启动的科研项目添加到队列
- 检测队列中各槽位的状态（已完成/运行中/等待中/空）
- 领取队列中已完成项目的奖励
- 获取队列中第一个项目的剩余时间和预计完成时间

科研队列最多容纳 5 个项目，采用 FIFO 顺序运行。
队列中第一个项目运行完成后，等待中的项目自动开始。

术语对照：
    科研队列(Research Queue): 最多容纳 5 个排队项目的队列
    槽位(Slot): 队列中的位置，从下到上编号 0-4
"""
from module.base.button import ButtonGrid
from module.base.decorator import cached_property, Config
from module.base.utils import get_color
from module.config.time_source import now as current_time
from module.exception import GameBugError
from module.logger import logger
from module.ocr.ocr import Duration
from module.research.assets import *
from module.research.ui import ResearchUI

OCR_QUEUE_REMAIN = Duration(QUEUE_REMAIN, letter=(255, 255, 255), threshold=128, name='OCR_QUEUE_REMAIN')


class ResearchQueue(ResearchUI):
    """
    科研队列管理器，负责队列操作和状态检测。

    提供队列项目的添加、状态检测、奖励领取和时间查询等功能。
    通过颜色检测识别队列左侧的状态图标来判断各槽位状态。

    Attributes:
        queue_status_grids (ButtonGrid): 队列状态图标的按钮网格，
            因各服务器 UI 布局差异，通过 @Config.when 按服务器分别定义。
    """
    def research_queue_add(self, skip_first_screenshot=True):
        """
        Returns:
            bool: True if success to add to queue,
                False if project requirements not satisfied, can't be added to queue

        Pages:
            in: RESEARCH_QUEUE_ADD (is_in_research, DETAIL_NEXT)
            out: is_in_research and stabled
        """
        logger.hr('加入科研队列')
        # POPUP_CONFIRM has just been clicked in research_project_start()
        self.popup_interval_clear()
        self.interval_clear([RESEARCH_QUEUE_ADD])
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            if self.is_research_stabled():
                break

            if self.appear(RESEARCH_QUEUE_ADD, offset=(20, 20), interval=5):
                if self._research_queue_add_available():
                    self.device.click(RESEARCH_QUEUE_ADD)
                    continue
                else:
                    logger.info('[科研-队列] 项目条件未满足，取消')
                    self.research_detail_cancel()
                    return False

            if self.handle_popup_confirm('RESEARCH_QUEUE'):
                self.interval_reset(RESEARCH_QUEUE_ADD)
                continue

        self.ensure_research_center_stable()
        return True

    def _research_queue_add_available(self):
        """
        Returns:
            bool: True if able add to queue,
                False if project requirements not satisfied, can't be added to queue
        """
        # RESEARCH_QUEUE_ADD.area is the letter `Queue`
        # RESEARCH_QUEUE_ADD.button is the entire clickable area of button
        # Available: (90, 142, 203)
        # Unavailable: (153, 160, 170)
        r, g, b = get_color(self.device.image, RESEARCH_QUEUE_ADD.button)
        if b - min(r, g) > 60:
            return True
        else:
            return False

    @cached_property
    @Config.when(SERVER='en')
    def queue_status_grids(self):
        """
        Status icons on the left
        """
        return ButtonGrid(
            origin=(8, 259), delta=(0, 40.5), button_shape=(25, 25), grid_shape=(1, 5), name='QUEUE_STATUS')

    @cached_property
    @Config.when(SERVER='jp')
    def queue_status_grids(self):
        """
        Status icons on the left
        """
        return ButtonGrid(
            origin=(18, 259), delta=(0, 40.5), button_shape=(25, 25), grid_shape=(1, 5), name='QUEUE_STATUS')

    @cached_property
    @Config.when(SERVER='tw')
    def queue_status_grids(self):
        """
        Status icons on the left
        """
        return ButtonGrid(
            origin=(8, 259), delta=(0, 40.5), button_shape=(25, 25), grid_shape=(1, 5), name='QUEUE_STATUS')

    @cached_property
    @Config.when(SERVER=None)
    def queue_status_grids(self):
        """
        Status icons on the left
        """
        return ButtonGrid(
            origin=(18, 259), delta=(0, 40.5), button_shape=(25, 25), grid_shape=(1, 5), name='QUEUE_STATUS')

    def _queue_status_detect(self, button):
        """
        Args:
            button: Button of status icon

        Returns:
            str:
                'finished': Orange ✓ surrounded by orange border
                'running': Black ✓ surrounded by research progress, gray and blue
                'waiting': Gray … surrounded by gray border
                'empty': Black … surrounded by black border or just nothing
        """
        center = button.crop((7, 7, 21, 21))
        if self.image_color_count(center, color=(255, 158, 57), threshold=180, count=20):
            return 'finished'
        if self.image_color_count(center, color=(90, 97, 132), threshold=221, count=10):
            return 'waiting'
        if self.image_color_count(center, color=(24, 24, 41), threshold=221, count=10):
            below = button.crop((7, 14, 21, 21))
            if self.image_color_count(below, color=(24, 24, 41), threshold=221, count=10):
                return 'running'
            else:
                return 'empty'
        logger.warning(f'[科研-队列] 未知的队列状态，来自 {button}，假设为运行中')
        return 'running'

    def get_queue_slot(self):
        """
        Returns:
            int: Number of empty slots in queue

        Pages:
            in: is_in_queue
        """
        status = [self._queue_status_detect(button) for button in self.queue_status_grids.buttons]
        logger.info(f'[科研-队列] 科研队列: {status}')
        status = status[::-1]
        for index, s in enumerate(status):
            if s != 'empty':
                logger.attr('科研队列槽位', index)
                return index
        index = len(status)
        logger.attr('科研队列槽位', index)
        return index

    def get_research_ended(self):
        """
        Returns:
            datetime: Time of the end of the first research in the queue.

        Pages:
            in: is_in_queue

        Raises:
            GameBugError:
        """
        if self.image_color_count(QUEUE_REMAIN, color=(123, 125, 123), threshold=235, count=100):
            logger.error('[科研-队列] 队列中第一个科研未运行，'
                         '可能是游戏bug，'
                         '重启游戏应该能修复。')
            raise GameBugError
        if not self.image_color_count(QUEUE_REMAIN, color=(255, 255, 255), threshold=221, count=100):
            logger.info('[科研-队列] 科研队列为空')
            return current_time()

        end_time = current_time() + OCR_QUEUE_REMAIN.ocr(self.device.image)
        logger.info(f'[科研-队列] 第一个科研结束时间: {end_time}')
        return end_time
