"""
觉醒（Awaken）模块。

自动化舰船觉醒流程，将舰船等级从 100 级提升至 120 级或 125 级。

主要功能：
    - 检测舰船当前等级（100~125）
    - 判断觉醒所需资源（金币、心智芯片、心智阵列）是否充足
    - 执行单次觉醒操作，包括确认、等待动画完成
    - 对单艘舰船循环觉醒直到等级上限或资源不足
    - 遍历船坞中所有可觉醒舰船，直到资源耗尽

觉醒等级机制：
    - 普通觉醒：消耗金币 + 心智芯片，等级上限 120
    - 觉醒+（觉醒拟合）：额外消耗心智阵列，等级上限 125
    - 优先执行觉醒+（使用心智阵列），再执行普通觉醒（使用心智芯片）

资源判断逻辑：
    - 通过按钮匹配和红字颜色检测判断资源是否充足
    - COST_ARRAY 不存在时，COST_COIN 和 COST_CHIP 按钮会右移 54px
    - 需要根据按钮位移情况验证结果有效性

继承关系：
    继承自 Dock（船坞操作），使用船坞过滤器筛选可觉醒舰船。

Pages:
    觉醒页面：is_in_awaken
    船坞页面：page_dock
"""

from module.awaken.assets import *
from module.base.timer import Timer
from module.exception import ScriptError
from module.logger import logger
from module.ocr.ocr import Digit
from module.retire.dock import DOCK_EMPTY, Dock
from module.ui.assets import BACK_ARROW
from module.ui.page import page_dock, page_main


class ShipLevel(Digit):
    """
    舰船等级 OCR 识别器。

    对标准 Digit OCR 进行后处理，只接受 100~125 范围内的等级值。
    超出范围的识别结果被视为无效并返回 0。
    """
    def after_process(self, result):
        result = super().after_process(result)
        if result < 100 or result > 125:
            logger.warning('[觉醒] 异常的舰船等级')
            result = 0
        return result


class Awaken(Dock):
    """
    觉醒任务处理器。

    管理舰船觉醒的完整流程，包括资源检测、觉醒执行和船坞遍历。
    继承自 Dock 以使用船坞过滤、排序和舰船选择功能。

    核心流程：
        1. 导航至船坞，按收藏和等级过滤可觉醒舰船
        2. 进入舰船详情，执行觉醒直到等级上限或资源不足
        3. 退出舰船详情，继续下一艘
        4. 无可觉醒舰船或资源耗尽时结束

    属性:
        无额外实例属性，所有状态通过方法参数和返回值传递

    配置项:
        Awaken_LevelCap: 觉醒等级上限，'level120' 或 'level125'
        Awaken_Favourite: 是否仅觉醒收藏舰船
    """
    def _get_button_state(self, button: Button):
        """
        获取指定资源按钮的状态。

        Args:
            button: COST_COIN、COST_CHIP 或 COST_ARRAY 按钮

        Returns:
            bool: 资源充足返回 True，不足返回 False，该资源不需要时返回 None
        """
        # 如果 COST_ARRAY 不存在，COST_COIN 和 COST_CHIP 会右移 54px
        if button.match(self.device.image, offset=(75, 20)):
            # Look down, see if there are red letters
            area = button.button
            area = (area[0], area[3], area[2], area[3] + 60)
            if self.image_color_count(area, color=(214, 53, 33), threshold=180, count=16):
                return False
            else:
                return True
        else:
            return None

    def _get_awaken_cost(self, use_array=False):
        """
        获取觉醒所需资源的状态。

        Args:
            use_array: True 表示觉醒到 125 级，False 表示 120 级

        Returns:
            bool or str:
                True 表示所有所需资源充足，
                False 表示任一资源不足，
                'unexpected_array' 表示不打算使用心智阵列但阵列出现了，
                'invalid' 表示结果无效
        """
        coin = self._get_button_state(COST_COIN)
        chip = self._get_button_state(COST_CHIP)
        array = self._get_button_state(COST_ARRAY)

        logger.attr('觉醒消耗', {'coin': coin, 'chip': chip, 'array': array})

        def is_right_moved(button):
            # 如果 COST_ARRAY 不存在，COST_COIN 和 COST_CHIP 会右移 54px
            return button.button[0] - button.area[0] > 20

        # 检查结果是否有效
        if array is not None:
            if not use_array:
                logger.warning('[觉醒] 不使用阵列但阵列存在')
                return 'unexpected_array'
            # 如果需要阵列，金币和芯片应该同时存在
            if coin is not None and not is_right_moved(COST_COIN) \
                    and chip is not None and not is_right_moved(COST_CHIP):
                result = coin and chip and array
                logger.attr('觉醒资源充足', result)
                return result
        else:
            # 如果不需要阵列，金币和芯片应该同时存在且右移
            if coin is not None and is_right_moved(COST_COIN) \
                    and chip is not None and is_right_moved(COST_CHIP):
                result = coin and chip
                logger.attr('觉醒资源充足', result)
                return result

        logger.warning('[觉醒] 无效的觉醒消耗')
        return 'invalid'

    def handle_awaken_finish(self):
        return self.appear_then_click(AWAKEN_FINISH, offset=(20, 20), interval=1)

    def is_in_awaken(self):
        return SHIP_LEVEL_CHECK.match_luma(self.device.image, similarity=0.7)

    def awaken_popup_close(self, skip_first_screenshot=True):
        logger.info('[觉醒] 觉醒弹窗关闭')
        self.interval_clear(AWAKEN_CANCEL)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.is_in_awaken():
                break
            if self.appear_then_click(AWAKEN_CANCEL, offset=(20, 20), interval=3):
                continue
            if self.handle_awaken_finish():
                continue

    def awaken_once(self, use_array=False, skip_first_screenshot=True):
        """
        执行一次觉醒操作。

        Args:
            use_array (bool): 是否使用心智阵列（觉醒到 125 级）
            skip_first_screenshot (bool): 是否跳过首次截图

        Returns:
            str: 结果状态，'no_exp'、'unexpected_array'、'insufficient'、'timeout'、'success'

        Pages:
            in: is_in_awaken
            out: is_in_awaken
        """
        logger.hr('觉醒一次', level=2)
        interval = Timer(3, count=6)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.appear(AWAKEN_CONFIRM):
                break
            if LEVEL_UP.match_luma(self.device.image):
                logger.info(f'[觉醒] 觉醒一次在 {LEVEL_UP} 结束')
                return 'no_exp'
            # 由于随机背景，降低相似度阈值
            if interval.reached() and AWAKENING.match_luma(self.device.image, similarity=0.7):
                self.device.click(AWAKENING)
                interval.reset()
                continue

        logger.info('[觉醒] 获取觉醒消耗')
        timeout = Timer(2, count=6).start()
        skip_first_screenshot = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            result = self._get_awaken_cost(use_array)
            if result == 'unexpected_array':
                # 这种情况不应该发生
                self.awaken_popup_close()
                return result
            elif result is False:
                logger.info('[觉醒] 资源不足无法觉醒')
                self.awaken_popup_close()
                return 'insufficient'
            elif result is True:
                # 资源充足
                break
            elif result == 'invalid':
                # 重试，同时检查超时
                pass
            else:
                raise ScriptError(f'Unexpected _get_awaken_cost result: {result}')
            if timeout.reached():
                logger.warning('[觉醒] 获取觉醒消耗超时')
                self.awaken_popup_close()
                return 'timeout'

        # 资源充足，确认觉醒
        logger.info('[觉醒] 觉醒确认')
        self.interval_clear(AWAKEN_CONFIRM)
        # 觉醒弹窗在经验足够时需要 10 秒才出现，点击关闭需要 2 秒
        # 因此此处超时设置较长
        timeout = Timer(30, count=30).start()
        finished = False
        skip_first_screenshot = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 结束条件
            if timeout.reached():
                logger.warning('[觉醒] 觉醒确认超时')
                self.awaken_popup_close()
                break
            if finished and self.is_in_awaken():
                logger.info('[觉醒] 觉醒完成')
                break
            # 点击操作
            if self.appear_then_click(AWAKEN_CONFIRM, offset=(20, 20), interval=3):
                continue
            if self.handle_popup_confirm('AWAKEN'):
                continue
            if self.handle_awaken_finish():
                finished = True
                continue

        self.device.click_record_clear()
        return 'success'

    def get_ship_level(self, skip_first_screenshot=True):
        """
        获取当前舰船的等级。

        Args:
            skip_first_screenshot (bool): 是否跳过首次截图

        Returns:
            int: 等级 100~125，出错时返回 0
        """
        ocr = ShipLevel(OCR_SHIP_LEVEL, letter=(255, 255, 255), threshold=128, name='ShipLevel')
        timeout = Timer(2, count=4).start()
        level = 0
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.is_in_awaken():
                level = ocr.ocr(self.device.image)
                if level > 0:
                    return level
            if timeout.reached():
                logger.warning('[觉醒] 获取舰船等级超时')
                return level

    def awaken_ship(self, use_array=False, skip_first_screenshot=True):
        """
        对单艘舰船执行觉醒，直到经验不足或达到目标等级。

        Args:
            use_array (bool): True 表示觉醒到 125 级，False 表示 120 级
            skip_first_screenshot (bool): 是否跳过首次截图

        Returns:
            str: 'level_max'、'insufficient'、'no_exp'、'timeout'

        Pages:
            in: is_in_awaken
            out: is_in_awaken
        """
        logger.hr('觉醒舰船', level=1)
        logger.info(f'[觉醒] 觉醒舰船, 使用阵列={use_array}')

        if use_array:
            stop_level = 125
        else:
            stop_level = 120

        if not skip_first_screenshot:
            self.device.screenshot()

        for _ in range(7):
            level = self.get_ship_level()
            if level > 0:
                if level >= stop_level:
                    logger.info(f'[觉醒] 觉醒舰船在停止等级结束')
                    return 'level_max'
                else:
                    result = self.awaken_once(use_array)
                    # 'no_exp'、'unexpected_array'、'insufficient'、'timeout'、'success'
                    if result == 'success':
                        continue
                    if result in ['insufficient', 'no_exp']:
                        # 直接返回原始结果
                        return result
                    if result == 'unexpected_array':
                        # 可能只是误入觉醒确认界面，重新执行 awaken_once 会重新检查
                        continue
                    if result == 'timeout':
                        # 获取资源超时，重试应该能修复
                        continue
                    raise ScriptError(f'Unexpected awaken_once result: {result}')
            else:
                # 获取等级超时，请求退出
                return 'timeout'

        # 错误，请求退出
        logger.warning('[觉醒] 单艘舰船觉醒尝试过多')
        return 'timeout'

    def awaken_exit(self, skip_first_screenshot=True):
        """
        退出觉醒界面，返回船坞。

        Pages:
            in: is_in_awaken
            out: DOCK_CHECK
        """
        logger.info('[觉醒] 觉醒退出')
        interval = Timer(3)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            if self.ui_page_appear(page_dock):
                logger.info(f'[觉醒] 觉醒退出在 {page_dock}')
                break
            if interval.reached() and self.is_in_awaken():
                logger.info(f'[觉醒] 在觉醒中 -> {BACK_ARROW}')
                self.device.click(BACK_ARROW)
                interval.reset()
                continue
            if self.handle_awaken_finish():
                continue
            if self.appear_then_click(AWAKEN_CANCEL, offset=(20, 20), interval=3):
                continue
            if self.is_in_main(interval=5):
                self.device.click(page_main.links[page_dock])
                continue

    def awaken_run(self, use_array=False, favourite=False):
        """
        觉醒船坞中所有舰船，直到资源耗尽。

        Args:
            use_array (bool): True 表示觉醒到 125 级，False 表示 120 级
            favourite (bool): True 表示仅觉醒收藏舰船，False 表示觉醒所有舰船

        Returns:
            str: 'insufficient'、'finish'、'timeout'

        Pages:
            in: Any
            out: page_dock
        """
        logger.hr('觉醒运行', level=1)
        self.ui_ensure(page_dock)
        self.dock_favourite_set(enable=favourite, wait_loading=False)
        self.dock_sort_method_dsc_set(wait_loading=False)
        if use_array:
            extra = ['can_awaken_plus']
        else:
            extra = ['can_awaken']
        self.dock_filter_set(extra=extra)

        while 1:
            # 在 page_dock 页面
            if self.appear(DOCK_EMPTY, offset=(20, 20)):
                logger.info('[觉醒] 觉醒运行完成，无舰船可觉醒')
                result = 'finish'
                break

            # page_dock -> SHIP_DETAIL_CHECK
            entered = self.dock_enter_first()
            if not entered:
                logger.info('[觉醒] 觉醒运行完成，无舰船可觉醒')
                result = 'finish'
                break

            # 在 is_in_awaken 页面
            result = self.awaken_ship(use_array)
            self.awaken_exit()
            # 'insufficient'、'no_exp'、'timeout'
            if result in ['no_exp', 'level_max']:
                # Awaken next ship
                continue
            if result == 'insufficient':
                logger.info('[觉醒] 觉醒运行完成，资源耗尽')
                break
            if result == 'timeout':
                logger.info(f'[觉醒] 觉醒运行完成, 结果={result}')
                break
            raise ScriptError(f'Unexpected awaken_ship result: {result}')

        return result

    def run(self):
        # 优先执行觉醒+（使用心智阵列）
        favourite = self.config.Awaken_Favourite
        if self.config.Awaken_LevelCap == 'level125':
            # 使用心智阵列
            result = self.awaken_run(use_array=True, favourite=favourite)
            # 使用心智芯片
            if result != 'timeout':
                self.awaken_run(favourite=favourite)
        elif self.config.Awaken_LevelCap == 'level120':
            # 使用心智芯片
            self.awaken_run(favourite=favourite)
        else:
            raise ScriptError(f'Unknown Awaken_LevelCap={self.config.Awaken_LevelCap}')

        # 重置船坞筛选器
        logger.hr('觉醒运行退出', level=1)
        if favourite:
            self.dock_favourite_set(wait_loading=False)
        self.dock_filter_set(wait_loading=False)

        # 调度下一次运行
        self.config.task_delay(server_update=True)
