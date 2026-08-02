"""指挥喵训练模块。

处理指挥喵训练相关的所有操作，包括：
- 训练队列管理：自动将指挥喵猫箱排队进行训练
- 收集已训练完成的指挥喵
- 根据库存情况选择升序或降序排队策略

排队策略说明：
- 升序排队（ascending=True）：优先使用普通猫箱（蓝/紫），保留金色猫箱
  - 当普通猫箱库存 > 20 时使用，避免金色猫箱被优先消耗
- 降序排队（ascending=False）：优先使用稀有猫箱（金/紫/蓝），系统自动分配

训练模式（`MeowfficerTrain_Mode`）：
- `seamlessly`（无缝模式）：训练与强化无缝衔接，使用降序排队
- 默认模式：周日收集全部，工作日收集单个

配置项前缀：`MeowfficerTrain_*`
"""

from copy import deepcopy

from module.base.button import ButtonGrid
from module.base.timer import Timer
from module.logger import logger
from module.meowfficer.assets import *
from module.meowfficer.collect import MeowfficerCollect
from module.meowfficer.enhance import MeowfficerEnhance
from module.ocr.ocr import Digit, DigitCounter

MEOWFFICER_CAPACITY = DigitCounter(OCR_MEOWFFICER_CAPACITY, letter=(131, 121, 123), threshold=64)
MEOWFFICER_QUEUE = DigitCounter(OCR_MEOWFFICER_QUEUE, letter=(131, 121, 123), threshold=64)
MEOWFFICER_BOX_GRID = ButtonGrid(
    origin=(460, 210), delta=(160, 0), button_shape=(30, 30), grid_shape=(3, 1),
    name='MEOWFFICER_BOX_GRID')
MEOWFFICER_BOX_COUNT_GRID = ButtonGrid(
    origin=(776, 21), delta=(133, 0), button_shape=(65, 27), grid_shape=(3, 1),
    name='MEOWFFICER_BOX_COUNT_GRID')
MEOWFFICER_BOX_COUNT = Digit(MEOWFFICER_BOX_COUNT_GRID.buttons,
                             letter=(99, 69, 41), threshold=128,
                             name='MEOWFFICER_BOX_COUNT')


class MeowfficerTrain(MeowfficerCollect, MeowfficerEnhance):
    """指挥喵训练处理器。

    管理指挥喵的训练流程，包括猫箱排队、训练队列管理和已训练指挥喵的收集。
    支持两种排队模式：系统自动排队（降序）和手动排队（升序）。

    继承关系：
        MeowfficerCollect: 指挥喵收集功能（收集已训练的指挥喵）
        MeowfficerEnhance: 指挥喵强化功能（消耗多余指挥喵提供经验）

    Attributes:
        _box_count (list[int]): 三种猫箱（蓝/紫/金）的数量列表，
            在 `meow_train()` 中通过 OCR 读取。
    """
    _box_count = [0, 0, 0]

    def _meow_queue_enter(self, skip_first_screenshot=True):
        """
        Transition into the queuing window to
        enqueue meowfficer boxes
        May fail to enter so limit to 3 tries
        before giving up

        Args:
            skip_first_screenshot (bool):

        Returns:
            bool, whether able to enter into
            MEOWFFICER_TRAIN_FILL_QUEUE
        """
        timeout_count = 3
        self.handle_info_bar()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if not self.appear(MEOWFFICER_TRAIN_FILL_QUEUE, offset=(20, 20)) \
                    and self.appear(MEOWFFICER_TRAIN_START, offset=(20, 20), interval=3):
                if timeout_count > 0:
                    self.device.click(MEOWFFICER_TRAIN_START)
                    timeout_count -= 1
                else:
                    return False

            # End
            if self.appear(MEOWFFICER_TRAIN_FILL_QUEUE, offset=(20, 20)):
                return True
            if self.info_bar_count():
                logger.info('[指挥喵-训练] 没有更多训练栏位，退出')
                return False

    def _meow_nqueue(self, skip_first_screenshot=True):
        """
        Queue all remaining empty slots does
        so autonomously enqueuing rare boxes
        first

        Pages:
            in: MEOWFFICER_TRAIN
            out: MEOWFFICER_TRAIN
        """
        # Loop through possible screen transitions
        # as a result of the previous action
        confirm_timer = Timer(1.5, count=3).start()
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.info_bar_count():
                confirm_timer.reset()
                continue
            if self.appear_then_click(MEOWFFICER_TRAIN_FILL_QUEUE, offset=(20, 20), interval=5):
                self.device.sleep(0.3)
                self.device.click(MEOWFFICER_TRAIN_START)
                confirm_timer.reset()
                continue
            if self.handle_meow_popup_confirm():
                confirm_timer.reset()
                continue

            # End
            if self.appear(MEOWFFICER_TRAIN_START, offset=(20, 20)):
                if confirm_timer.reached():
                    break
            else:
                confirm_timer.reset()

    def _meow_rqueue(self):
        """
        Queue all remaining empty slots however does
        so manually in order to enqueue common
        boxes first

        Pages:
            in: MEOWFFICER_TRAIN
            out: MEOWFFICER_TRAIN
        """
        # Maintain local box count for
        # count/click accuracy
        local_count = deepcopy(self._box_count)
        buttons = MEOWFFICER_BOX_GRID.buttons
        while 1:
            # Number that can be queued
            current, remain, total = MEOWFFICER_QUEUE.ocr(self.device.image)
            if not remain:
                break

            # Loop as needed to queue boxes appropriately
            for i, j in ((0, 2), (1, 1)):
                logger.attr(f'训练中猫箱数量 (索引 {i})', local_count)
                count = local_count[i] - remain
                if count < 0:
                    self.device.multi_click(buttons[j], remain + count)
                    local_count[i] -= remain + count
                    remain = abs(count)
                else:
                    self.device.multi_click(buttons[j], remain)
                    local_count[i] -= remain
                    break

            logger.attr('训练完成猫箱数量', local_count)
            self.device.sleep((0.3, 0.5))
            self.device.screenshot()

        # Re-use mechanism to transition through screens
        self._meow_nqueue()

    def meow_queue(self, ascending=True):
        """
        Enter into training window and then
        choose appropriate queue method based
        on current stock

        Args:
            ascending (bool):
                True for Blue > Purple > Gold
                False for Gold > Purple > Blue

        Pages:
            in: MEOWFFICER_TRAIN
            out: MEOWFFICER_TRAIN
        """
        logger.hr('指挥喵队列', level=1)
        # Either can remain in same window or
        # enter the queuing window
        if not self._meow_queue_enter():
            return

        # Sum of common and elite/sr boxes
        # Ocr'ed earlier in meow_train else default
        common_sum = self._box_count[0] + self._box_count[1]

        # Check remains
        if sum(self._box_count) <= 0:
            logger.info('[指挥喵-训练] 没有更多猫箱可训练')
            return

        # Choose appropriate queue func based on
        # common box sum count
        # - <= 20, low stock; queue normally
        # - > 20, high stock; queue common boxes first
        if ascending:
            if common_sum > 20:
                logger.info('[指挥喵-训练] 升序队列 (蓝 > 紫 > 金)')
                self._meow_rqueue()
            else:
                logger.info('[指挥喵-训练] 普通猫箱库存不足')
                logger.info('[指挥喵-训练] 降序队列 (金 > 紫 > 蓝)')
                self._meow_nqueue()
        else:
            logger.info('[指挥喵-训练] 降序队列 (金 > 紫 > 蓝)')
            self._meow_nqueue()

    def meow_train(self):
        """
        Performs both retrieving a trained meowfficer and queuing
        meowfficer boxes for training

        Pages:
            in: page_meowfficer
            out: page_meowfficer
        """
        logger.hr('指挥喵训练', level=1)

        # Retrieve capacity to determine whether able to collect
        current, remain, total = MEOWFFICER_CAPACITY.ocr(self.device.image)
        logger.attr('剩余容量', remain)

        # Read box count, utilized in other helper funcs
        self._box_count = MEOWFFICER_BOX_COUNT.ocr(self.device.image)

        logger.attr('训练模式', self.config.MeowfficerTrain_Mode)
        collected = False
        if self.config.MeowfficerTrain_Mode == 'seamlessly':
            # Enter
            self.meow_enter(MEOWFFICER_TRAIN_ENTER, check_button=MEOWFFICER_TRAIN_START)
            # Collect
            if remain > 0:
                collected = self.meow_collect(collect_all=True)
            # Queue
            self.meow_queue(ascending=False)
            # Exit
            self.meow_menu_close()
        else:
            # Enter
            self.meow_enter(MEOWFFICER_TRAIN_ENTER, check_button=MEOWFFICER_TRAIN_START)
            # Collect
            if remain > 0:
                collected = self.meow_collect(collect_all=self.meow_is_sunday())
            # Queue
            self.meow_queue(ascending=False)
            # Exit
            self.meow_menu_close()

        return collected
