"""大世界战斗处理器模块。

针对大世界（Operation Siren）场景定制战斗流程，继承标准战斗处理器
和地图事件处理器。提供连续战斗检测（塞壬扫描装置场景）、S 评价
延迟点击、大世界专用物品获取逻辑，以及战斗统计计时等功能。
"""
from module.combat.assets import *
from module.combat.combat import Combat as Combat_
from module.logger import logger
from module.os_combat.assets import *
from module.os_handler.assets import *
from module.os_handler.map_event import MapEventHandler
from module.base.timer import Timer
from module.exception import GameBugError
from module.statistics.opsi_runtime import finish_battle_timer, start_battle_timer


class ContinuousCombat(Exception):
    """
    大世界连续战斗异常。

    在塞壬扫描装置等场景中，两个敌人无间隔出现，
    战斗结束后立即进入下一场战斗时抛出此异常，
    由 combat() 方法捕获并循环重试。
    """
    pass


class Combat(Combat_, MapEventHandler):
    """
    大世界战斗处理器。

    继承标准战斗和地图事件处理器，针对大世界场景定制战斗流程：
    连续战斗检测、S 评价延迟点击、大世界专用物品获取逻辑。

    Attributes:
        battle_status_s_autoclick_delay (int): 自律寻敌路径下 S 评价页面的兜底等待（秒）。
            只服务自律寻敌（塞壬要塞、侵蚀1练级等）：那条链路上游戏会自行推进
            评价页，抢着点会打架。
            手动作战只有深渊海域与月困（月度 Boss）两处，不使用任何等待，见
            _handle_auto_battle_status_s()。
        _os_siren_battle (bool): 本轮进攻是否出现过塞壬强敌的战斗准备界面。
        _os_battle_victory (bool): 本轮进攻是否取得了战斗评价（即战斗胜利）。
    """
    # 这个值最初也是唯一的用意：自律寻敌（塞壬要塞、侵蚀1练级等）会自行推进
    # S 评价页，等它自己走完再兜底点。2026-11-04 深渊坐标日志暴露了它的副作用——
    # 大世界里真正不能自律、只能手动作战的只有深渊海域和月困（月度 Boss），
    # 这两个场景游戏根本不会自己推进 S 评价页，脚本却在上面死等 20.1 秒
    # （22:48:56 结算 → 22:49:16 才点）。现在手动作战零等待，这里只留给自律路径。
    # 也曾试过"画面静止即点"，但战果页自带光效/闪动，整帧永远不会完全一致，
    # 那个判据在真机上等于永远不成立，已废弃。
    battle_status_s_autoclick_delay = 20
    # 手动作战（深渊海域 / 月困）点击 S 评价页被游戏吞掉后的重试间隔。
    # 评价页是"点掉就前进"的页面，点空一次等 7 秒毫无意义，1 秒重试即可；
    # 连续点同一按钮会被设备层的点击洪水检测（15 帧窗口内同按钮/同网格 ≥12 次）
    # 误判成卡死，所以每次点完用 click_record_remove() 豁免该按钮。
    battle_status_s_retry_interval = 1

    def combat_appear(self):
        """
        检测是否进入战斗。

        依次检查地图状态、战斗加载、战斗执行、战斗准备和塞壬准备界面，
        任一条件满足即判定为已进入战斗。

        Pages:
            in: 大世界地图或战斗过渡画面
            out: 战斗准备界面或战斗执行中

        Returns:
            bool: 是否进入战斗。
        """
        if self.is_in_map():
            return False

        if self.is_combat_loading():
            return True

        # 检查是否已在战斗执行中（暂停按钮可见）
        # 处理自动搜索跳过战斗准备界面的情况
        if self.is_combat_executing():
            return True

        if self.appear(BATTLE_PREPARATION):
            return True
        if self.appear(SIREN_PREPARATION, offset=(20, 20)):
            return True
        if self.appear(BATTLE_PREPARATION_WITH_OVERLAY) and self.handle_combat_automation_confirm():
            return True

        return False

    def _battle_status_s_timer(self):
        """
        获取或重建 S 评价延迟点击计时器。

        只服务自律寻敌路径（等游戏自行推进评价页）；手动作战路径不使用这个等待。

        Returns:
            Timer: S 评价延迟计时器实例。
        """
        delay = self.battle_status_s_autoclick_delay
        try:
            timer = self._os_battle_status_s_timer
        except AttributeError:
            timer = Timer(delay)
            self._os_battle_status_s_timer = timer
        if timer.limit != delay:
            timer = Timer(delay)
            self._os_battle_status_s_timer = timer
        return timer

    def _clear_battle_status_s_timer(self):
        """
        重置 S 评价延迟计时器，在每次新的战斗状态出现时调用。
        """
        self._battle_status_s_timer().clear()

    def os_battle_result_reset(self):
        """
        清空上一场战斗留下的结果标记。

        在每次针对 Boss 发起进攻前调用，保证「塞壬战胜利」这个兜底判据
        只反映本轮进攻，不会被上一支舰队的战斗结果污染。
        """
        self._os_siren_battle = False
        self._os_battle_victory = False

    def os_boss_defeated(self):
        """
        本轮进攻是否已经把 Boss 打掉。

        判据：战斗中出现了塞壬强敌的战斗准备界面（SIREN_PREPARATION），
        且随后取得了战斗评价（S/A/B/C/D 任一，出现评价页即本场胜利）。

        这是 boss_clear() 里「战后雷达上还有问号」之外的兜底判据。
        2026-11-04 深渊坐标事故：Boss 确实被打掉（S 评价 + 6 次掉落），
        但战后雷达上既没有敌人也没有问号（深渊海域浓雾叠加视野/雷达覆盖不足），
        旧逻辑只认问号，于是把 4 支舰队的行动机会全部空耗在「无Boss可前往」上，
        最终误报「无法击败boss，舰队已耗尽」并请求人工介入。

        Returns:
            bool: 本轮进攻是否已击败 Boss。
        """
        siren = getattr(self, '_os_siren_battle', False)
        victory = getattr(self, '_os_battle_victory', False)
        return bool(siren and victory)

    def _handle_auto_battle_status_s(self, drop=None, timer=None):
        """
        点击 S 评价页（BATTLE_STATUS_S），点击时机按路径区分。

        自律寻敌路径（调用方传入 timer）：游戏会自行推进评价结果页面，
        等满 battle_status_s_autoclick_delay 再兜底点击，避免与自动推进冲突。
        手动作战路径（使用内部 timer，实际不参与判定）：大世界里不能自律、只能
        手动推图打 Boss 的只有深渊海域与月困（月度 Boss），这两处游戏不会自己
        推进评价页，「战斗评价页出现」本身就是战斗结束的信号，检测到即点击，
        不做任何时间等待（塞壬要塞走自律寻敌，属于上面的 auto_advance 分支，不受影响）：
          - 掉落截图自带等待：DropImage.handle_add() 会先 sleep
            WAIT_BEFORE_SAVING_SCREEN_SHOT（默认 1s）再取图；
          - 点击被吞就重试：按 battle_status_s_retry_interval（1s）重试，
            直到评价页消失；点完用 click_record_remove() 豁免该按钮，
            不会被设备层的点击洪水检测误判成卡死；
          - 卡死兜底：combat_status() 的结算阶段超时（300s）兜底。

        Args:
            drop (DropImage): 掉落图像对象，用于处理战斗掉落。
            timer (Timer): 可选的外部计时器，为 None 时使用内部计时器
                （即手动作战路径，此时不使用等待）。

        Returns:
            tuple: (是否出现, 是否点击)。
        """
        auto_advance = timer is not None
        timer = timer or self._battle_status_s_timer()
        if not self.appear(BATTLE_STATUS_S):
            timer.clear()
            return False, False

        if auto_advance:
            # 自律寻敌：让位给游戏的自动推进，只在上限到达时兜底点击
            timer.start()
            if not timer.reached():
                return True, False
            handled = self._handle_single_battle_status(BATTLE_STATUS_S, 'S', drop)
        else:
            # 手动作战：评价页出现即点；点空（被转场吞掉）就 1 秒后再点，
            # 一直重试到页面消失为止，不再等任何固定秒数。
            handled = self._handle_single_battle_status(
                BATTLE_STATUS_S, 'S', drop, interval=self.battle_status_s_retry_interval)
            if handled:
                # 「点掉就前进」的页面重复点同一按钮是正常行为，把它从点击洪水
                # 记录里剔除，否则 1 秒重试会在 12 秒内被误判成卡死。
                self.device.click_record_remove(BATTLE_STATUS_S)

        timer.clear()
        return True, handled

    def combat_preparation(self, balance_hp=False, emotion_reduce=False, auto='combat_auto', fleet_index=1):
        """
        大世界战斗准备阶段。

        循环处理战斗准备界面，设置自动战斗模式、处理退役和弹窗确认，
        直到检测到战斗执行界面出现。

        Pages:
            in: 战斗准备界面（BATTLE_PREPARATION 或 SIREN_PREPARATION）
            out: 战斗执行中界面

        Args:
            balance_hp (bool): 是否平衡血量。
            emotion_reduce (bool): 是否减少情绪值。
            auto (str): 自动战斗模式。
            fleet_index (int): 舰队索引。
        """
        logger.info('战斗准备。')
        self.device.stuck_record_clear()
        self.device.click_record_clear()
        skip_first_screenshot = True

        for _ in self.loop():

            if self.appear(BATTLE_PREPARATION):
                if self.handle_combat_automation_set(auto=auto == 'combat_auto'):
                    continue
            if self.handle_retirement():
                continue
            if self.appear_then_click(BATTLE_PREPARATION, interval=2):
                continue
            if self.appear_then_click(SIREN_PREPARATION, offset=(20, 20), interval=2):
                # 只有塞壬强敌才走这个准备界面，用它标记「本轮进攻打的是 Boss」，
                # 供 boss_clear() 在雷达判据失效时兜底判定
                self._os_siren_battle = True
                continue
            if self.handle_popup_confirm('ENHANCED_ENEMY'):
                continue
            if self.handle_combat_automation_confirm():
                continue
            if self.handle_story_skip():
                continue

            # 结束
            pause = self.is_combat_executing()
            if pause:
                logger.attr('战斗界面', pause)
                break

    def _get_exp_info_sleep(self):
        """
        返回经验结算页面点击后的随机等待时间范围。

        有掉落物时等待更久（1.5-2秒），让掉落识别完成；
        无掉落时快速跳过（0.25-0.5秒）。

        Returns:
            tuple: (最小秒数, 最大秒数)。
        """
        return (1.5, 2) if self.__os_combat_drop else (0.25, 0.5)

    def handle_exp_info(self):
        """
        处理战斗结束后的经验结算页面（S/A/B/C/D 评价）。

        点击对应评价按钮后清除 S 评价计时器，并等待随机时间。
        战斗执行中时不处理。

        Pages:
            in: 战斗结束，经验结算界面
            out: 经验结算界面已关闭

        Returns:
            bool: 是否点击了经验结算按钮。
        """
        if self.is_combat_executing():
            return False
        sleep = self._get_exp_info_sleep()
        if self.appear_then_click(EXP_INFO_S, interval=self.exp_info_click_interval):
            self._clear_battle_status_s_timer()
            self.device.sleep(sleep)
            return True
        if self.appear_then_click(EXP_INFO_A, interval=self.exp_info_click_interval):
            self._clear_battle_status_s_timer()
            self.device.sleep(sleep)
            return True
        if self.appear_then_click(EXP_INFO_B, interval=self.exp_info_click_interval):
            self._clear_battle_status_s_timer()
            self.device.sleep(sleep)
            return True
        if self.appear_then_click(EXP_INFO_C, interval=self.exp_info_click_interval):
            self._clear_battle_status_s_timer()
            self.device.sleep(sleep)
            return True
        if self.appear_then_click(EXP_INFO_D, interval=self.exp_info_click_interval):
            self._clear_battle_status_s_timer()
            self.device.sleep(sleep)
            return True

        return False

    def handle_get_items(self, drop=None):
        """
        点击安全区域关闭物品获取弹窗，而非直接点击按钮本身。

        Args:
            drop (DropImage): 掉落图像对象。

        Returns:
            bool: 是否处理了物品获取弹窗。
        """
        if getattr(self, '_disable_handle_get_items', False):
            return False
        if self.appear(GET_ITEMS_1, offset=5, interval=self.battle_status_click_interval):
            if drop:
                drop.handle_add(self, before=2)
            self.device.click(CLICK_SAFE_AREA)
            self._clear_battle_status_s_timer()
            self.interval_reset(BATTLE_STATUS_S)
            self.interval_reset(BATTLE_STATUS_A)
            self.interval_reset(BATTLE_STATUS_B)
            return True
        if self.appear(GET_ITEMS_2, offset=5, interval=self.battle_status_click_interval):
            if drop:
                drop.handle_add(self, before=2)
            self.device.click(CLICK_SAFE_AREA)
            self._clear_battle_status_s_timer()
            self.interval_reset(BATTLE_STATUS_S)
            self.interval_reset(BATTLE_STATUS_A)
            self.interval_reset(BATTLE_STATUS_B)
            return True
        if self.appear(GET_ADAPTABILITY, offset=5, interval=self.battle_status_click_interval):
            if drop:
                drop.handle_add(self, before=2)
            self.device.click(CLICK_SAFE_AREA)
            self._clear_battle_status_s_timer()
            self.interval_reset(BATTLE_STATUS_S)
            self.interval_reset(BATTLE_STATUS_A)
            self.interval_reset(BATTLE_STATUS_B)
            return True

        return False

    def _os_combat_expected_end(self):
        """
        判断大世界战斗是否已结束并回到地图。

        处理地图事件后检测是否仍在战斗中，若连续战斗则抛出异常；
        否则确认已回到大世界地图。

        Pages:
            in: 战斗结束结算或地图事件
            out: 大世界地图

        Returns:
            bool: 是否已回到大世界地图。
        """
        if self.handle_map_event(drop=self.__os_combat_drop):
            return False
        if self.combat_appear():
            raise ContinuousCombat

        return self.handle_os_in_map()

    __os_combat_drop = None

    def combat_status(self, drop=None, expected_end=None):
        """
        大世界战斗状态处理。

        禁用标准物品获取弹窗处理，仅使用大世界专用的地图物品获取逻辑。
        通过 _disable_handle_get_items 标志临时禁用 handle_get_items，
        使掉落物通过 handle_map_get_items 路径处理。

        Pages:
            in: 战斗执行中或战斗结束结算
            out: 大世界地图

        Args:
            drop (DropImage): 掉落图像对象。
            expected_end (callable): 自定义战斗结束判断函数，默认为 _os_combat_expected_end。
        """
        self.__os_combat_drop = drop
        if expected_end is None:
            expected_end = self._os_combat_expected_end
        # 禁用 handle_get_items，仅使用 handle_map_get_items
        self._disable_handle_get_items = True
        try:
            super().combat_status(drop=drop, expected_end=expected_end)
        finally:
            self._disable_handle_get_items = False

    def combat(self, *args, save_get_items=False, **kwargs):
        """
        处理大世界中的连续战斗。

        在塞壬扫描装置中，有 2 个无间隔的伏击敌人。
        舰队前往塞壬扫描装置，攻击一个敌人，跳过 TB，攻击另一个。
        标准 combat 函数需要确认战斗已结束且已返回地图，
        但处理塞壬扫描装置时会在第二场战斗中卡住。
        此函数通过捕获 ContinuousCombat 异常实现最多 3 次重试。

        Pages:
            in: 大世界地图，即将进入战斗
            out: 大世界地图，连续战斗全部结束

        Args:
            *args: 传递给父类 combat 的位置参数。
            save_get_items (bool): 是否保存物品获取截图。
            **kwargs: 传递给父类 combat 的关键字参数。
        """
        # 每场战斗独立记账：清掉上一场的结果标记，避免跨战斗污染 boss_clear() 的判据
        self.os_battle_result_reset()
        for count in range(3):
            self._clear_battle_status_s_timer()
            if count >= 2:
                logger.warning('[大世界战斗] 连续战斗过多')

            try:
                super().combat(*args, save_get_items=save_get_items, **kwargs)
                break
            except ContinuousCombat:
                logger.info('[大世界战斗] 检测到连续战斗')
                continue
            finally:
                self._clear_battle_status_s_timer()

    def _handle_single_battle_status(self, status_button, status_letter, drop, interval=None):
        """
        处理单个战斗评价状态按钮（S/A/B/C/D）。

        检测到对应评价按钮后，记录掉落数据并点击按钮关闭评价页面。
        S 评价记录为 info 级别，其余评价记录为 warning 级别。

        Args:
            status_button (Button): 评价状态按钮资源。
            status_letter (str): 评价等级字母（'S'/'A'/'B'/'C'/'D'）。
            drop (DropImage): 掉落图像对象，为 None 时仅等待随机时间。
            interval (int): 点击节流秒数，None 时使用 battle_status_click_interval。

        Returns:
            bool: 是否检测到并点击了评价按钮。
        """
        interval = self.battle_status_click_interval if interval is None else interval
        if self.appear(status_button, interval=interval):
            if status_letter == 'S':
                logger.info(f'[大世界战斗] 战斗评价 {status_letter}')
            else:
                logger.warning(f'[大世界战斗] 战斗评价 {status_letter}')
            if drop:
                drop.handle_add(self)
            else:
                self.device.sleep((0.25, 0.5))
            self.device.click(status_button)
            # 出现战斗评价页即本场战斗胜利（战斗失败只会走舰队阵亡/撤退路径，
            # 不会出现 S/A/B/C/D 评价页）
            self._os_battle_victory = True
            return True
        return False

    def handle_battle_status(self, drop=None):
        """
        处理大世界战斗评价状态页面（S/A/B/C/D）。

        战斗执行中时重置 S 评价计时器并跳过处理。
        优先通过延迟机制处理 S 评价，再依次检查 A/B/C/D 评价。

        Pages:
            in: 战斗评价页面（S/A/B/C/D 任一可见）
            out: 评价页面已关闭

        Args:
            drop (DropImage): 掉落图像对象。

        Returns:
            bool: 是否处理了评价状态页面。
        """
        if self.is_combat_executing():
            self._clear_battle_status_s_timer()
            return False

        appeared, clicked = self._handle_auto_battle_status_s(drop=drop)
        if clicked:
            return True
        if appeared:
            return True

        for status_button, status_letter in [
            (BATTLE_STATUS_A, 'A'),
            (BATTLE_STATUS_B, 'B'),
            (BATTLE_STATUS_C, 'C'),
            (BATTLE_STATUS_D, 'D'),
        ]:
            if self._handle_single_battle_status(status_button, status_letter, drop):
                return True
        return False

    def handle_auto_search_battle_status(self, drop=None, battle_status_s_timer=None):
        """
        自动搜索模式下的战斗评价状态处理。

        与 handle_battle_status 类似，但使用外部传入的 S 评价计时器，
        且不检查 is_combat_executing 状态（由调用方保证）。

        Pages:
            in: 战斗评价页面（S/A/B/C/D 任一可见）
            out: 评价页面已关闭

        Args:
            drop (DropImage): 掉落图像对象。
            battle_status_s_timer (Timer): S 评价延迟计时器。

        Returns:
            bool: 是否处理了评价状态页面。
        """
        _, clicked = self._handle_auto_battle_status_s(
            drop=drop, timer=battle_status_s_timer
        )
        if clicked:
            return True

        for status_button, status_letter in [
            (BATTLE_STATUS_A, 'A'),
            (BATTLE_STATUS_B, 'B'),
            (BATTLE_STATUS_C, 'C'),
            (BATTLE_STATUS_D, 'D'),
        ]:
            if self._handle_single_battle_status(status_button, status_letter, drop):
                return True
        return False

    def handle_auto_search_exp_info(self):
        """
        自动搜索模式下的经验结算页面处理。

        依次检查 S/A/B/C/D 经验结算按钮并点击，点击后清除 S 评价计时器。
        点击沿用战斗结算的点击冷却，避免同一页面被连点；页面是否关闭由调用方
        继续截图确认，不再用固定时长休眠猜测界面状态。

        Pages:
            in: 经验结算页面（S/A/B/C/D 任一可见）
            out: 经验结算页面已关闭

        Returns:
            bool: 是否点击了经验结算按钮。
        """
        for exp_info_button in [EXP_INFO_S, EXP_INFO_A, EXP_INFO_B, EXP_INFO_C, EXP_INFO_D]:
            if self.appear_then_click(exp_info_button, interval=self.battle_status_click_interval):
                self._clear_battle_status_s_timer()
                return True
        return False

    def auto_search_combat(self, drop=None):
        """
        大世界自动搜索战斗处理。

        分为加载和执行两个阶段：加载阶段等待战斗界面就绪，
        执行阶段处理潜艇呼叫、战斗评价、经验结算和地图事件。
        CL1 指标模式下设置 5 分钟超时限制。

        Pages:
            in: 战斗加载中（is_combat_loading()）
            out: 大世界地图（战斗状态处理完毕）

        Args:
            drop (DropImage): 掉落图像对象。

        Returns:
            bool: 敌人是否已清除，舰队阵亡则返回 False。

        Raises:
            GameBugError: CL1 指标模式下战斗超过 5 分钟超时。
        """
        # 保持战斗专注于状态转换；指标层决定此任务是否应产生 CL1/short-meow 计时样本。
        battle_timer_source = start_battle_timer(self.config)
        
        cl1_combat_timer = Timer(300, count=300)
        
        logger.info('[大世界战斗] 自动搜索战斗加载中')
        self.device.stuck_record_clear()
        self.device.click_record_clear()
        self.device.screenshot_interval_set('combat')
        while 1:
            self.device.screenshot()

            if self.handle_combat_automation_confirm():
                continue

            # 结束
            if self.handle_os_auto_search_map_option(drop=drop):
                self._clear_battle_status_s_timer()
                break
            pause = self.is_combat_executing()
            if pause:
                logger.attr('战斗UI', pause)
                break
            if self.is_in_map():
                break

        logger.info('[大世界战斗] 自动搜索战斗执行')
        self.submarine_call_reset()
        self.device.stuck_record_clear()
        self.device.click_record_clear()
        submarine_mode = 'do_not_use'
        if self.config.Submarine_Fleet:
            submarine_mode = self.config.Submarine_Mode

        if battle_timer_source == 'cl1':
            cl1_combat_timer.start()

        success = True
        battle_status_s_timer = Timer(self.battle_status_s_autoclick_delay)
        while 1:
            self.device.screenshot()

            if battle_timer_source == 'cl1' and cl1_combat_timer.reached():
                logger.warning('[大世界战斗] CL1 战斗超时（5 分钟限制）')
                raise GameBugError('CL1 combat timeout')

            if self.handle_submarine_call(submarine_mode):
                continue
            # 失败时不更改自动搜索选项
            enable = success if success is not None else None
            if self.handle_os_auto_search_map_option(drop=drop, enable=enable):
                battle_status_s_timer.clear()
                continue

            # 结束
            if self.is_in_map():
                self.device.screenshot_interval_set()
                break
            if self.is_combat_executing():
                battle_status_s_timer.clear()
                continue
            # 自律被游戏或任务中断后，经验结算页需要主动确认才能回到地图，
            # 与是否启用自动修理无关；此前按修理阈值门控会在关闭修理时卡在结算页。
            if self.handle_auto_search_exp_info():
                battle_status_s_timer.clear()
                success = None
                continue
            if self.handle_auto_search_battle_status(drop=drop, battle_status_s_timer=battle_status_s_timer):
                success = None
                continue
            if self.handle_map_event():
                battle_status_s_timer.clear()
                continue
            
        logger.info('战斗结束。')
        # 刚回到地图时界面仍在重建，自律寻敌按钮可能是过渡外观；记录窗口，
        # 期间不操作地图自律开关，避免把仍在执行的自律寻敌点停。
        self._os_auto_search_battle_settle_start()
        
        # 通过相同的指标源结束，避免 CL1 和 short-meow 样本意外共享存储键。
        finish_battle_timer(self.config, battle_timer_source)
        
        return success
