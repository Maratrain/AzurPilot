from module.config.config import TaskEnd
from module.config.utils import get_os_reset_remain
from module.exception import RequestHumanTakeover, ScriptError
from module.logger import logger
from module.map.map_grids import SelectedGrids
from module.os.map import OSMap
from module.os_handler.action_point import ActionPointLimit
from module.os.tasks.scheduling import CoinTaskMixin
from module.os.tasks.smart_scheduling_utils import is_smart_scheduling_enabled


class OpsiMeowfficerFarming(CoinTaskMixin, OSMap):
    def _is_natural_ap_cleanup_mode(self):
        """判断是否由智能调度拉起短猫清理自然行动力。"""
        return self._config_enabled(
            keys=self.CONFIG_PATH_MEOW_NATURAL_AP_CLEANUP,
            default=False,
        )

    def _clear_natural_ap_cleanup_mode(self):
        """清理智能调度短猫自然行动力模式的一次性标记。"""
        if self._is_natural_ap_cleanup_mode():
            self.config.cross_set(keys=self.CONFIG_PATH_MEOW_NATURAL_AP_CLEANUP, value=False)

    def _apply_natural_ap_cleanup_runtime(self):
        """应用自然行动力清理的临时运行时配置。"""
        self._natural_ap_cleanup_backup = {
            'OS_ACTION_POINT_BOX_USE': self.config.OS_ACTION_POINT_BOX_USE,
            'OpsiGeneral_BuyActionPointLimit': self.config.OpsiGeneral_BuyActionPointLimit,
            'overridden': {
                key: self.config.overridden.get(key)
                for key in ('OS_ACTION_POINT_BOX_USE', 'OpsiGeneral_BuyActionPointLimit')
            },
        }
        self.config.overridden['OS_ACTION_POINT_BOX_USE'] = False
        self.config.overridden['OpsiGeneral_BuyActionPointLimit'] = 0
        object.__setattr__(self.config, 'OS_ACTION_POINT_BOX_USE', False)
        object.__setattr__(self.config, 'OpsiGeneral_BuyActionPointLimit', 0)

    def _restore_natural_ap_cleanup_runtime(self):
        """恢复自然行动力清理前的运行时配置。"""
        backup = getattr(self, '_natural_ap_cleanup_backup', None)
        if not backup:
            return
        for key, value in backup['overridden'].items():
            if value is None:
                self.config.overridden.pop(key, None)
            else:
                self.config.overridden[key] = value
        object.__setattr__(self.config, 'OS_ACTION_POINT_BOX_USE', backup['OS_ACTION_POINT_BOX_USE'])
        object.__setattr__(self.config, 'OpsiGeneral_BuyActionPointLimit', backup['OpsiGeneral_BuyActionPointLimit'])
        self._natural_ap_cleanup_backup = None

    def _record_natural_ap_cleanup_snapshot(self):
        """自然行动力清理结束时，用自然行动力校准智能调度。"""
        try:
            from module.statistics.opsi_runtime import record_ap_snapshot, refresh_action_point
            refresh_action_point(self)
            record_ap_snapshot(
                self.config,
                ap_current=self._action_point_current,
                ap_total=self._action_point_total,
                source='meow',
            )
        except Exception:
            logger.debug('[智能调度] 短猫自然行动力清理结束后校准失败', exc_info=True)

    def _meow_ap_and_scheduling_check(self, preserve, ap_checked):
        """
        行动力检查与智能调度检查。

        检查当前行动力是否充足，处理智能调度覆盖配置，
        并在行动力不足时执行延迟或任务切换。

        Args:
            preserve (int): 行动力保留值。
            ap_checked (bool): 是否已完成行动力检查。

        Returns:
            bool: 如果已完成检查返回 True，否则返回 ap_checked 的值。
        """
        natural_ap_cleanup = self._is_natural_ap_cleanup_mode()
        self.config.OS_ACTION_POINT_PRESERVE = preserve

        # ===== 智能调度：行动力保留覆盖 =====
        if is_smart_scheduling_enabled(self.config) and not natural_ap_cleanup:
            if hasattr(self, '_get_smart_scheduling_action_point_preserve'):
                smart_ap_preserve = self._get_smart_scheduling_action_point_preserve()
                if smart_ap_preserve > 0:
                    logger.info(f'[智能调度] 行动力保留使用智能调度配置: {smart_ap_preserve} (原配置: {self.config.OS_ACTION_POINT_PRESERVE})')
                    self.config.OS_ACTION_POINT_PRESERVE = smart_ap_preserve

        if natural_ap_cleanup:
            logger.info('[智能调度] 短猫自然行动力清理模式：不使用行动力箱子，不购买行动力')
            self.config.OS_ACTION_POINT_PRESERVE = 0

        if self.config.is_task_enabled('OpsiAshBeacon') \
                and not self._ash_fully_collected \
                and self.config.OpsiAshBeacon_EnsureFullyCollected:
            logger.info('余烬信标未收集满，暂时忽略行动力限制')
            self.config.OS_ACTION_POINT_PRESERVE = 0
        logger.attr('OS_ACTION_POINT_PRESERVE', self.config.OS_ACTION_POINT_PRESERVE)

        if not ap_checked:
            # 行动力前置检查，确保明日每日任务有足够行动力
            keep_current_ap = True
            check_rest_ap = True
            if self.is_cl1_enabled:
                return_threshold, _ = self._get_operation_coins_return_threshold()
                if return_threshold is not None:
                    yellow_coins = self.get_yellow_coins()
                    if yellow_coins >= return_threshold:
                        check_rest_ap = False

            if not self.is_cl1_enabled and self.config.OpsiGeneral_BuyActionPointLimit > 0:
                keep_current_ap = False

            if self.is_cl1_enabled and self.cl1_enough_yellow_coins:
                check_rest_ap = False
                try:
                    self.action_point_set(cost=0, keep_current_ap=keep_current_ap, check_rest_ap=check_rest_ap)
                except ActionPointLimit:
                    self.config.task_delay(server_update=True)
                    self.config.task_call('OpsiHazard1Leveling')
                    self.config.task_stop()
            else:
                self.action_point_set(cost=0, keep_current_ap=keep_current_ap, check_rest_ap=check_rest_ap)

            self.check_and_notify_action_point_threshold()

            # ===== 智能调度：行动力不足检查 =====
            if is_smart_scheduling_enabled(self.config) and not natural_ap_cleanup:
                ap_preserve = self.config.OpsiMeowfficerFarming_ActionPointPreserve
                if hasattr(self, '_get_smart_scheduling_action_point_preserve'):
                    smart_ap_preserve = self._get_smart_scheduling_action_point_preserve()
                    if smart_ap_preserve > 0:
                        ap_preserve = smart_ap_preserve

                if self._action_point_total < ap_preserve:
                    logger.info(f'[智能调度] 短猫相接行动力不足 ({self._action_point_total} < {ap_preserve})')
                    yellow_coins = self.get_yellow_coins()

                    if self.is_cl1_enabled:
                        self.notify_push(
                            title="[AzurPilot] 短猫相接 - 切换至侵蚀 1",
                            content=f"行动力 {self._action_point_total} 不足 (需要 {ap_preserve})\n补充凭证: {yellow_coins}\n推迟短猫 1 小时，切换至侵蚀 1"
                        )
                    else:
                        self.notify_push(
                            title="[AzurPilot] 短猫相接 - 行动力不足",
                            content=f"行动力 {self._action_point_total} 不足 (需要 {ap_preserve})\n凭证: {yellow_coins}\n任务推迟 1 小时"
                        )

                    logger.info('已推迟短猫相接 1 小时')
                    self.config.task_delay(minute=60)

                    if self.is_cl1_enabled:
                        logger.info('主动切换回侵蚀 1 任务')
                        with self.config.multi_set():
                            self.config.task_call('OpsiHazard1Leveling')

                    self.config.task_stop()
            return True
        return ap_checked

    def _meow_handle_traditional_zone(self):
        try:
            zone = self.name_to_zone(self.config.OpsiMeowfficerFarming_TargetZone)
        except ScriptError as e:
            logger.warning(f'目标海域输入错误: {self.config.OpsiMeowfficerFarming_TargetZone}')
            raise RequestHumanTakeover('输入海域无效，任务已停止') from e
        else:
            logger.hr(f'OS meowfficer farming, zone_id={zone.zone_id}', level=1)
            self.globe_goto(zone, types='SAFE', refresh=True)
            self.fleet_set(self.config.OpsiFleet_Fleet)
            self.meow_search_metrics_start()
            try:
                if self.run_strategic_search():
                    self._solved_map_event = set()
                    self._solved_fleet_mechanism = False
                    self.clear_question()
                    self.map_rescan()
                self.handle_after_auto_search()
            finally:
                self.meow_search_metrics_end()
            self.config.check_task_switch()

    def _meow_handle_stay_in_zone(self):
        if self.config.OpsiMeowfficerFarming_TargetZone == 0:
            logger.warning('已启用 StayInZone 但未设置 TargetZone，跳过本次任务')
            self.config.task_delay(server_update=True)
            self.config.task_stop()
        try:
            zone = self.name_to_zone(self.config.OpsiMeowfficerFarming_TargetZone)
        except ScriptError:
            logger.error('无法定位配置的目标海域，停止任务')
            self.config.task_delay(server_update=True)
            self.config.task_stop()
        
        logger.hr(f'OS meowfficer farming (stay in zone), zone_id={zone.zone_id}', level=1)
        self.get_current_zone()
        if self.zone.zone_id != zone.zone_id or not self.is_zone_name_hidden:
            self.globe_goto(zone, types='SAFE', refresh=True)

        keep_current_ap = True
        if self.config.OpsiGeneral_BuyActionPointLimit > 0:
            keep_current_ap = False

        self.action_point_set(cost=120, keep_current_ap=keep_current_ap, check_rest_ap=True)
        self.fleet_set(self.config.OpsiFleet_Fleet)
        self.os_order_execute(recon_scan=False, submarine_call=self.config.OpsiFleet_Submarine)

        self.meow_search_metrics_start()
        search_completed = False
        try:
            try:
                search_completed = self.run_strategic_search()
            except TaskEnd:
                raise
            except Exception as e:
                logger.warning(f'战略搜索异常: {e}')

            if search_completed:
                self._solved_map_event = set()
                self._solved_fleet_mechanism = False
                self.clear_question()
                self.map_rescan()

            try:
                self.handle_after_auto_search()
            except Exception:
                logger.exception('handle_after_auto_search 发生异常')
        finally:
            self.meow_search_metrics_end()

        self.config.check_task_switch()
        
        if self._check_yellow_coins_and_return_to_cl1("循环中", "短猫相接"):
            return True
        return False

    def _meow_handle_normal_search(self):
        hazard_level = self.config.OpsiMeowfficerFarming_HazardLevel
        zones = self.zone_select(hazard_level=hazard_level) \
            .delete(SelectedGrids([self.zone])) \
            .delete(SelectedGrids(self.zones.select(is_port=True))) \
            .sort_by_clock_degree(center=(1252, 1012), start=self.zone.location)

        if not zones:
            logger.warning(f'普通搜索模式：未找到符合条件的海域 (侵蚀等级 {hazard_level})')
            return

        logger.hr(f'OS meowfficer farming, zone_id={zones[0].zone_id}', level=1)

        self.globe_goto(zones[0])

        self.fleet_set(self.config.OpsiFleet_Fleet)
        self.os_order_execute(recon_scan=False, submarine_call=self.config.OpsiFleet_Submarine)

        self.meow_search_metrics_start()
        try:
            self.run_auto_search()
            self.handle_after_auto_search()
        finally:
            self.meow_search_metrics_end()

        self.config.check_task_switch()
        
    def os_meowfficer_farming(self):
        """执行大世界短猫相接（猫箱搜寻）任务。"""
        logger.hr(f'OS meowfficer farming, hazard_level={self.config.OpsiMeowfficerFarming_HazardLevel}', level=1)
        natural_ap_cleanup = self._is_natural_ap_cleanup_mode()
        if natural_ap_cleanup:
            self._apply_natural_ap_cleanup_runtime()
        
        # ===== 前置检查：黄币状态 =====
        if self.is_cl1_enabled and not natural_ap_cleanup:
            return_threshold, _ = self._get_operation_coins_return_threshold()
            if return_threshold is None:
                logger.info('凭证返回阈值为 0，禁用黄币检查')
            elif self._check_yellow_coins_and_return_to_cl1("任务开始前", "短猫相接"):
                return
        
        # ===== 行动力保留配置 =====
        if self.is_cl1_enabled and self.config.OpsiMeowfficerFarming_ActionPointPreserve < 500:
            logger.info('启用侵蚀 1 练级时，最低行动力保留自动调整为 500')
            self.config.OpsiMeowfficerFarming_ActionPointPreserve = 500
        
        preserve = min(self.get_action_point_limit(self.config.OpsiMeowfficerFarming_APPreserveUntilReset),
                       self.config.OpsiMeowfficerFarming_ActionPointPreserve)
        if natural_ap_cleanup:
            logger.info('[智能调度] 自然行动力清理模式下短猫保留行动力设为 0')
            preserve = 0
        if preserve == 0:
            self.config.override(OpsiFleet_Submarine=False)
            
        if self.is_cl1_enabled:
            # 侵蚀 1 练级模式下的必要覆盖项
            self.config.override(
                OpsiGeneral_DoRandomMapEvent=True,
                OpsiGeneral_AkashiShopFilter='ActionPoint',
                OpsiFleet_Submarine=False,
            )
            cd = self.nearest_task_cooling_down
            logger.attr('最近冷却中的任务', cd)
            
            remain = get_os_reset_remain()
            if cd is not None and remain > 0:
                logger.info(f'存在冷却中的任务，延迟短猫任务至 {cd.next_run} 后执行')
                self.config.task_delay(target=cd.next_run)
                self.config.task_stop()
                
        if self.is_in_opsi_explore():
            logger.warning(f'大世界探索正在运行，无法执行 {self.config.task.command}')
            self.config.task_delay(server_update=True)
            self.config.task_stop()

        if self.config.OpsiTarget_TargetFarming:
            if self.config.SERVER in ['cn', 'jp']:
                if hasattr(self, '_os_target'):
                    self._os_target()
            else:
                logger.info(f'Server {self.config.SERVER} does not support OpsiTarget yet, please contact the developers.')

        ap_checked = False
        try:
            while True:
                ap_checked = self._meow_ap_and_scheduling_check(preserve, ap_checked)

                # ===== 传统目标海域模式 =====
                if self.config.OpsiMeowfficerFarming_TargetZone != 0 and not self.config.OpsiMeowfficerFarming_StayInZone:
                    self._meow_handle_traditional_zone()
                    continue

                # ===== 指定海域计划作战 (StayInZone) =====
                if self.config.OpsiMeowfficerFarming_StayInZone:
                    if self._meow_handle_stay_in_zone():
                        return
                    continue

                # ===== 普通短猫搜索主逻辑 =====
                self._meow_handle_normal_search()

                # ===== 循环中黄币充足检查 =====
                if not natural_ap_cleanup and self._check_yellow_coins_and_return_to_cl1("循环中"):
                    return
                continue
        finally:
            if natural_ap_cleanup:
                self._record_natural_ap_cleanup_snapshot()
                self._restore_natural_ap_cleanup_runtime()
            self._clear_natural_ap_cleanup_mode()
