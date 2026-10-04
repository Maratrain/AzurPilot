from module.config.config import TaskEnd
from module.logger import logger
from module.os.fleet import BossFleet
from module.os.map import OSMap
from module.os_handler.assets import OS_SUBMARINE_EMPTY
from module.os.tasks.scheduling import CoinTaskMixin
from module.ui.page import page_os


class OpsiStronghold(CoinTaskMixin, OSMap):
    
    def clear_stronghold(self):
        """
        清理一个塞壬要塞。

        在地球仪地图上找到塞壬要塞，进入并清理，完成后在港口修理舰队。
        如果没有找到要塞，会标记本轮无可执行内容。

        Raises:
            ActionPointLimit: 行动力不足。
            TaskEnd: 没有更多要塞。
            RequestHumanTakeover: 无法击败 Boss，舰队耗尽。

        Pages:
            in: page_os, 大世界地球仪
            out: page_os, 大世界地图
        """
        logger.hr('大世界-塞壬要塞', level=1)

        # 上次清理塞壬要塞后 27 分钟冷却内，跳过本轮进攻（游戏内要塞重置间隔）
        cooldown_remain = self._get_stronghold_cooldown_remain_minutes()
        if cooldown_remain > 0:
            self.config.OpsiStronghold_HasStronghold = False
            log_msg = f'塞壬要塞清理后冷却中（剩余约{cooldown_remain}分钟），跳过本轮进攻'
            if self.is_running_smart_scheduling_task():
                if self._handle_coin_task_no_content('塞壬要塞', log_msg):
                    return
            else:
                logger.info(f'[大世界-塞壬要塞] {log_msg}，延迟剩余时间后重跑')
                self.config.task_delay(minute=cooldown_remain + 1, task='OpsiStronghold')
                self.config.task_stop()

        with self.config.multi_set():
            self.config.OpsiStronghold_HasStronghold = True
            self.cl1_ap_preserve()

            self.os_map_goto_globe()
            self.globe_update()
            zone = self.find_siren_stronghold()
            if zone is None:
                self.config.OpsiStronghold_HasStronghold = False
                self.os_globe_goto_map()
                if self._handle_coin_task_no_content('塞壬要塞', '塞壬要塞没有可执行内容'):
                    return

        self.globe_enter(zone)
        self.zone_init()
        self.os_order_execute(recon_scan=True, submarine_call=False)
        self.run_stronghold(submarine=self.config.OpsiStronghold_SubmarineEveryCombat)

        if self.config.OpsiStronghold_SubmarineEveryCombat:
            if self.zone.is_azur_port:
                logger.info('[大世界-要塞] 已在碧蓝港口')
            else:
                self.globe_goto(self.zone_nearest_azur_port(self.zone))
        self.handle_fleet_repair_by_config(revert=False)
        self.handle_fleet_resolve(revert=False)

        # 检查是否还有更多要塞
        self.os_map_goto_globe()
        self.globe_update()
        next_zone = self.find_siren_stronghold()
        if next_zone is None:
            self.config.OpsiStronghold_HasStronghold = False
            self.os_globe_goto_map()
            if self._handle_coin_task_no_content('塞壬要塞', '塞壬要塞没有更多可执行内容'):
                return
        else:
            # 找到更多要塞：受游戏内 27 分钟重置间隔限制，本轮不再进攻，
            # 设置冷却并回大世界地图。
            # 必须回地图：此前找到后直接结束会停留在地球仪/作战总览界面，
            # 后续任务 zone_init 识别到「作战总览」导致 MapDetectionError 卡死。
            logger.info(
                f'[大世界-要塞] 发现更多塞壬要塞 [{next_zone.zone_id}]，'
                f'设置 {self.STRONGHOLD_COOLDOWN_MINUTES} 分钟冷却，待下轮进攻'
            )
            self._set_stronghold_cooldown()
            self.os_globe_goto_map()

    def os_stronghold(self):
        while True:
            self.clear_stronghold()
            self.config.check_task_switch()

    def os_sumbarine_empty(self):
        return self.match_template_color(OS_SUBMARINE_EMPTY, offset=(20, 20))

    def stronghold_interrupt_check(self):
        return self.os_sumbarine_empty() and self.no_meowfficer_searching()

    def run_stronghold_one_fleet(self, fleet, submarine=False):
        """
        使用单支舰队清理要塞。最多尝试 3 次（舰队可能卡在迷雾中）。

        Args:
            fleet (BossFleet): 舰队对象。
            submarine (bool): 是否每场战斗都呼叫潜艇。

        Returns:
            bool: 是否全部清理完毕。
        """
        self.config.override(
            OpsiGeneral_DoRandomMapEvent=False,
            HOMO_EDGE_DETECT=False,
            STORY_OPTION=0
        )
        interrupt = [self.stronghold_interrupt_check, self.is_meowfficer_searching] if submarine else None
        # 尝试 3 次，因为舰队可能卡在迷雾中
        for _ in range(3):
            # 攻击
            self.fleet_set(fleet.fleet_index)
            try:
                self.run_auto_search(question=False, rescan=False, interrupt=interrupt)
            except TaskEnd:
                self.ui_ensure(page_os)
            self.hp_reset()
            self.hp_get()

            # 判断结果
            if self.get_stronghold_percentage() == '0':
                logger.info('[大世界-要塞] Boss已清除')
                return True
            elif any(self.need_repair):
                logger.info('[大世界-要塞] 自动搜索停止，因为舰队阵亡')
                # 重新进入以重置舰队位置
                prev = self.zone
                self.globe_goto(self.zone_nearest_azur_port(self.zone))
                self.handle_fog_block(repair=True)
                self.globe_goto(prev, types='STRONGHOLD')
                return False
            elif submarine and self.os_sumbarine_empty():
                logger.info('[大世界-要塞] 潜艇弹药耗尽，等待下次清理')
                # 潜艇弹药耗尽，等待下次清理
                self.globe_goto(self.zone_nearest_azur_port(self.zone))
                return True
            else:
                logger.info('[大世界-要塞] 自动搜索停止，因为舰队卡住')
                # 重新进入以重置舰队位置
                prev = self.zone
                self.globe_goto(self.zone_nearest_azur_port(self.zone))
                self.handle_fog_block(repair=False)
                self.globe_goto(prev, types='STRONGHOLD')
                continue

    def run_stronghold(self, submarine=False):
        """
        所有舰队轮流攻击塞壬要塞。

        Args:
            submarine (bool): 是否每场战斗都呼叫潜艇。

        Returns:
            bool: 是否成功清理。

        Pages:
            in: 塞壬日志仪（深渊），Boss 已出现。
            out: 成功时为危险或安全海域，失败时仍在深渊中。
        """
        logger.hr('塞壬要塞清理', level=1)
        fleets = self.parse_fleet_filter()
        for fleet in fleets:
            logger.hr(f'[大世界-要塞] 回合: {fleet}', level=2)
            if not isinstance(fleet, BossFleet):
                self.os_order_execute(recon_scan=False, submarine_call=True)
                continue

            result = self.run_stronghold_one_fleet(fleet, submarine=submarine)
            if result:
                return True
            else:
                continue

        logger.critical('[大世界-塞壬要塞] 无法击败boss，舰队已耗尽')
        return False
