"""联盟活动沉船刷分模块，专门处理联盟沉船战斗的结算逻辑。
针对 D 评价沉船场景进行优化，控制心情扣减和战斗结束判定，
并处理沉船专用的结算弹窗与确认操作。"""

from module.combat.assets import (
    BATTLE_STATUS_D, BATTLE_STATUS_A, BATTLE_STATUS_B, BATTLE_STATUS_S,
    OPTS_INFO_D,
    EXP_INFO_D, EXP_INFO_A, EXP_INFO_B, EXP_INFO_S
)
from module.coalition.assets import *
from module.coalition.combat import CoalitionCombat
from module.coalition.coalition import Coalition
from module.exception import ScriptEnd, ScriptError
from module.logger import logger
from module.ui.page import page_coalition


class CoalitionScuttleCombat(CoalitionCombat):
    """联盟沉船战斗结算处理，优先识别沉船专用结算按钮并处理确认弹窗。"""

    triggered_normal_end = False
    _is_shipwreck = False  # 当前战斗是否为沉船D评价
    _is_s_rank = False  # 当前战斗是否为S评价

    def auto_search_combat_execute(self, emotion_reduce=True, fleet_index=1, expected_end=None):
        """
        重写自动搜索战斗执行，联盟沉船不额外扣减心情。

        联盟沉船中一个关卡包含多次战斗（1/2/3/4队），
        但游戏只在整个关卡进入时扣1次2点心情，不按内部战斗次数扣减。
        D评价也不执行 shipwreck=True 的额外扣减。

        Args:
            emotion_reduce (bool): 是否扣减心情（仅在第一场战斗时为True）。
            fleet_index (int): 舰队编号。
            expected_end (callable): 自定义结束条件。
        """
        from module.base.timer import Timer
        from module.combat.assets import OPTS_INFO_D
        from module.combat.auto_search_combat import AutoSearchCombat
        from module.exception import CampaignEnd

        self.device.stuck_record_clear()
        self.device.click_record_clear()

        # 联盟沉船仅在第一场战斗时扣减2心情（关卡进入代价）
        # 后续战斗（2/3/4队）不再扣减，与游戏服务端行为一致
        if emotion_reduce:
            self.emotion.reduce(fleet_index)

        auto = self.config.Fleet_Fleet1Mode if fleet_index == 1 else self.config.Fleet_Fleet2Mode
        confirm_timer = Timer(10)
        confirm_timer.start()

        while 1:
            self.device.screenshot()

            if self.handle_submarine_call('do_not_use', call=False):
                continue
            if self.handle_combat_auto(auto):
                continue
            if self.handle_combat_manual(auto):
                continue
            if self.handle_popup_confirm('AUTO_SEARCH_COMBAT_EXECUTE'):
                continue
            if not self._withdraw and self.handle_urgent_commission():
                continue
            if self.handle_story_skip():
                continue
            if self.handle_guild_popup_cancel():
                continue
            if self.handle_vote_popup():
                continue
            if self.handle_mission_popup_ack():
                continue

            # 结束条件
            if self.is_in_auto_search_menu() or self._handle_auto_search_menu_missing():
                self.device.screenshot_interval_set()
                raise CampaignEnd
            if self.is_combat_executing():
                confirm_timer.reset()
                continue
            if self.handle_get_ship():
                continue

            # D评价沉船：不额外扣减心情
            if self.appear_then_click(OPTS_INFO_D, offset=(30, 30), interval=2):
                self._withdraw = True
                self._is_shipwreck = True
                break
            # D评价结算界面：S/A/B评价的动画过渡帧可能短暂误匹配D评价模板，
            # 但只有真正的沉船才会出现OPTS_INFO_D弹窗。
            # 此处不设置沉船标记（未经过OPTS_INFO_D确认），让后续S/A/B条件覆盖。
            if self.appear(BATTLE_STATUS_D) or self.appear(EXP_INFO_D):
                break
            if confirm_timer.reached():
                self._withdraw = True
                self._is_shipwreck = True
                self.device.click(OPTS_INFO_D)
                confirm_timer.reset()
                break

            # A/B/S评价：联盟沉船中不额外扣减心情
            # 游戏服务端只在整个关卡进入时扣1次2点，不按战斗结算类型扣减
            if self.appear(BATTLE_STATUS_A) or self.appear(BATTLE_STATUS_B) \
                    or self.appear(EXP_INFO_A) or self.appear(EXP_INFO_B):
                break

            # S评价或自动搜索运行中
            if self.appear(BATTLE_STATUS_S) or self.appear(EXP_INFO_S) \
                    or self.is_auto_search_running():
                self._is_s_rank = True
                self.device.screenshot_interval_set()
                break

            if callable(expected_end):
                if expected_end():
                    self.device.screenshot_interval_set()
                    break

    def coalition_combat(self):
        """
        联盟沉船战斗执行，仅在第一场战斗扣减2心情。

        联盟沉船一个关卡包含多次战斗（1/2/3/4队），
        但游戏只在整个关卡进入时扣1次2点心情，后续战斗不再扣减。
        """
        from module.exception import CampaignEnd

        self.battle_count = 0
        self.combat_preparation(emotion_reduce=False)

        try:
            while 1:
                logger.hr(f'{self.FUNCTION_NAME_BASE}{self.battle_count}', level=2)
                self._is_shipwreck = False
                self._is_s_rank = False
                # 仅第一场战斗扣减2心情（关卡进入代价），后续战斗不再扣减
                self.auto_search_combat_execute(
                    emotion_reduce=self.battle_count == 0,
                    fleet_index=1,
                    expected_end=self.auto_search_combat_end
                )
                self.coalition_combat_re_enter()
                self.battle_count += 1
        except CampaignEnd:
            logger.info('联动战斗结束。')

    def handle_battle_status(self, drop=None):
        """
        处理联盟沉船的战斗结算画面，优先识别沉船专用结算按钮。

        沉船结算流程：BATTLE_STATUS_D → OPTS_INFO_D → SCUTTLE_CONFIRM → 父类结算。
        识别到标准结算（非D类）时标记 triggered_normal_end 表示舰船被完全击沉。

        Args:
            drop (DropImage): 掉落物图像处理器。

        Returns:
            bool: 是否成功识别并处理了战斗结算。
        """
        if self.is_combat_executing():
            return False
        if self.appear(BATTLE_STATUS_D, interval=self.battle_status_click_interval):
            if drop:
                drop.handle_add(self)
            else:
                self.device.sleep((0.25, 0.5))
            self.device.click(BATTLE_STATUS_D)
            return True
        if self.appear(OPTS_INFO_D, interval=self.battle_status_click_interval):
            if drop:
                drop.handle_add(self)
            else:
                self.device.sleep((0.25, 0.5))
            self.device.click(OPTS_INFO_D)
            return True
        # 沉船结算后的确认按钮
        if self.appear_then_click(SCUTTLE_CONFIRM, offset=(20, 20), interval=2):
            return True
        if super().handle_battle_status(drop=drop):
            logger.warning("触发正常结束")
            self.triggered_normal_end = True
            return True

        return False

    def handle_exp_info(self):
        """
        处理联盟沉船的经验结算画面。

        Returns:
            bool: 是否成功识别并处理了经验结算。
        """
        if self.is_combat_executing():
            return False
        if self.appear_then_click(EXP_INFO_D):
            self.device.sleep((0.25, 0.5))
            return True
        if super().handle_exp_info():
            return True

        return False

    def coalition_combat_re_enter(self, skip_first_screenshot=True):
        """
        联盟沉船重新进入战斗，在原有逻辑基础上增加确认按钮处理。

        Pages:
            in: battle_status
            out: is_combat_executing
        """
        from module.base.timer import Timer
        from module.os_ash.assets import BATTLE_STATUS

        logger.info('[联动-扫荡] 联动自沉战斗重新进入')
        status_clicked = False
        click_timer = Timer(0.3)
        click_last = Timer(2)
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # End
            if self.is_combat_loading():
                break
            if self.is_combat_executing():
                break
            if self.in_coalition():
                from module.exception import CampaignEnd
                raise CampaignEnd

            if self.appear_then_click(BATTLE_STATUS, offset=(80, 20), interval=2):
                continue
            if self.appear_then_click(COALITION_REWARD_CONFIRM, offset=(20, 20), interval=2):
                status_clicked = False
                continue
            # 沉船结算确认按钮
            if self.appear_then_click(SCUTTLE_CONFIRM, offset=(20, 20), interval=2):
                continue
            if self.handle_get_ship():
                continue
            if self.handle_battle_status():
                status_clicked = True
                click_last.reset()
                continue
            if status_clicked:
                if click_timer.reached() and not click_last.reached():
                    self.device.click(BATTLE_STATUS)
                    click_timer.reset()


class CoalitionScuttleRun(Coalition, CoalitionScuttleCombat):
    """联盟沉船主循环，沉船任务进入关卡只扣1次2点心情。"""

    def handle_combat_low_emotion(self):
        """
        重写红脸出击警告弹窗处理。

        沉船任务中牺牲船必然低心情，红脸弹窗出现时点击确认继续出击。
        """
        return self.handle_popup_confirm('IGNORE_LOW_EMOTION')

    def coalition_execute_once(self, event, stage, fleet):
        """执行一次联盟沉船战斗。

        覆盖父类方法，将心情预估从多场战斗改为1场（整个关卡只扣1次2点）。
        联盟沉船虽然内部有多次战斗（1/2/3/4队），但游戏只在整个关卡进入时扣1次心情。

        Args:
            event: 活动名称。
            stage: 关卡名称。
            fleet: 舰队模式。
        """
        self.config.override(
            Campaign_Name=f'{event}_{stage}',
            Campaign_UseAutoSearch=False,
            Fleet_FleetOrder='fleet1_all_fleet2_standby',
        )
        if self.config.Coalition_Fleet == 'single' and self.config.Emotion_Fleet1Control == 'prevent_red_face':
            logger.warning('AL does not allow single coalition with emotion < 30, '
                           'emotion control is forced to prevent_yellow_face')
            self.config.override(Emotion_Fleet1Control='prevent_yellow_face')
        if stage == 'sp':
            self.config.override(Coalition_Fleet='multi')

        # 联盟沉船：整个关卡只扣1次2点心情，不按内部战斗次数预估
        try:
            self.emotion.check_reduce(battle=1)
        except ScriptEnd:
            self.coalition_map_exit(event)
            raise

        if self._coalition_has_oil_icon and self.triggered_stop_condition(oil_check=True, coin_check=True):
            self.coalition_map_exit(event)
            raise ScriptEnd

        self.enter_map(event=event, stage=stage, mode=fleet)
        self.coalition_combat()

    def triggered_stop_condition(self, oil_check=False, pt_check=False, coin_check=False):
        """
        检查是否触发了停止条件。

        联盟沉船不因 triggered_normal_end（舰船被击沉）而停止任务，
        由 RunCount 控制何时停止。D评价和非D评价都算1次有效战斗。

        Returns:
            bool: 是否触发了停止条件。
        """
        if super().triggered_stop_condition(oil_check=oil_check, pt_check=pt_check, coin_check=coin_check):
            return True

        return False

    def run(self, event='', mode='', fleet='', total=0):
        """
        运行联盟沉船主循环，沉船任务不扣减心情。

        SP关卡特殊逻辑：
        - D评价（沉船）：视为未通过，继续出击
        - 非D评价（成功）：视为已通过，延迟至服务器刷新

        Args:
            event (str): 活动名称，为空时从配置读取。
            mode (str): 关卡名称，为空时从配置读取。
            fleet (str): 舰队模式，为空时从配置读取。
            total (int): 总运行次数上限，0 表示不限。
        """
        event = event if event else self.config.Campaign_Event
        mode = mode if mode else self.config.Coalition_Mode
        fleet = fleet if fleet else self.config.Coalition_Fleet
        if not event or not mode or not fleet:
            raise ScriptError(f'CoalitionScuttle arguments unfilled. name={event}, mode={mode}, fleet={fleet}')

        event, mode = self.handle_stage_name(event, mode)
        self.run_count = 0
        self.run_limit = self.config.StopCondition_RunCount

        while 1:
            # 达到指定运行次数则结束
            if total and self.run_count == total:
                break
            if self.event_time_limit_triggered():
                self.config.task_stop()

            # 日志输出
            logger.hr(f'{event}_{mode}', level=2)
            if self.config.StopCondition_RunCount > 0:
                logger.info(f'剩余次数: {self.config.StopCondition_RunCount}')
            else:
                logger.info(f'计数: {self.run_count}')

            # 无燃油图标时，先在战役菜单检查停止条件
            if not self._coalition_has_oil_icon:
                from module.ui.page import page_campaign_menu
                self.ui_goto(page_campaign_menu)
                if self.triggered_stop_condition(oil_check=True, coin_check=True):
                    break

            # 确保进入联盟页面
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            self.ui_goto_coalition()
            self.disable_event_on_raid()
            self.coalition_ensure_mode(event, 'battle')

            # 检查 PT 和金币停止条件
            if self.triggered_stop_condition(pt_check=True, coin_check=True):
                break

            # 执行战斗
            self.device.stuck_record_clear()
            self.device.click_record_clear()
            try:
                self.coalition_execute_once(event=event, stage=mode, fleet=fleet)
            except ScriptEnd as e:
                logger.hr('脚本结束')
                logger.info(str(e))
                break

            # 战斗结束后更新计数
            self.run_count += 1
            if self.config.StopCondition_RunCount:
                self.config.StopCondition_RunCount -= 1

            # SP关卡仅S评价视为已通过，延迟至服务器刷新
            # A/B/C/D评价均视为未通过，继续出击
            if mode == 'sp' and self._is_s_rank and not self._is_shipwreck:
                logger.info('SP以S评价通过')
                self.config.task_delay(server_update=True)
                self.config.task_stop()

            # 检查停止条件
            if self.triggered_stop_condition(pt_check=True, coin_check=True):
                break
            # 检查调度器是否切换了任务
            if self.config.task_switched():
                self.config.task_stop()
