from module.config.config import TaskEnd
from module.logger import logger
from module.os.tasks.scheduling import OpsiScheduling
from module.os_handler.action_point import ActionPointLimit


# 大世界行动力每 10 分钟自然回复 1 点。
ACTION_POINT_RECOVER_SECONDS = 600
# 大世界当前行动力自然上限。
NATURAL_ACTION_POINT_LIMIT = 200


class OpsiPreventActionPointOverflow(OpsiScheduling):
    """防止当前行动力溢出的任务。"""

    CONFIG_PATH_PREVENT_AP_OVERFLOW_TASK = 'OpsiPreventActionPointOverflow.OpsiPreventActionPointOverflow.Task'
    CONFIG_PATH_PREVENT_AP_OVERFLOW_UPPER = 'OpsiPreventActionPointOverflow.OpsiPreventActionPointOverflow.ActionPointUpperbound'
    CONFIG_PATH_PREVENT_AP_OVERFLOW_LOWER = 'OpsiPreventActionPointOverflow.OpsiPreventActionPointOverflow.ActionPointLowerbound'
    TASK_NAME_PREVENT_AP_OVERFLOW = 'OpsiPreventActionPointOverflow'

    def is_prevent_action_point_overflow_enabled(self):
        """判断防止行动力溢出任务是否启用。"""
        return self.config.is_task_enabled(self.TASK_NAME_PREVENT_AP_OVERFLOW)

    def _set_prevent_action_point_overflow_enabled(self, enabled):
        """启用或关闭防止行动力溢出任务。"""
        self.config.cross_set(
            keys=f'{self.TASK_NAME_PREVENT_AP_OVERFLOW}.Scheduler.Enable',
            value=bool(enabled),
        )

    def _get_prevent_action_point_overflow_thresholds(self):
        """读取防溢出任务的当前行动力上下界。"""
        upper = self.config.cross_get(
            keys=self.CONFIG_PATH_PREVENT_AP_OVERFLOW_UPPER,
            default=NATURAL_ACTION_POINT_LIMIT,
        )
        lower = self.config.cross_get(
            keys=self.CONFIG_PATH_PREVENT_AP_OVERFLOW_LOWER,
            default=0,
        )
        try:
            upper = int(upper)
        except (TypeError, ValueError):
            upper = NATURAL_ACTION_POINT_LIMIT
        try:
            lower = int(lower)
        except (TypeError, ValueError):
            lower = 0

        upper = max(1, min(NATURAL_ACTION_POINT_LIMIT, upper))
        lower = max(0, min(upper, lower))
        return upper, lower

    def _get_prevent_action_point_overflow_task(self):
        """读取防溢出任务要代跑的一轮任务。"""
        task = self.config.cross_get(
            keys=self.CONFIG_PATH_PREVENT_AP_OVERFLOW_TASK,
            default=self.TASK_NAME_SCHEDULING,
        )
        if task not in (
            self.TASK_NAME_SCHEDULING,
            self.TASK_NAME_HAZARD1_LEVELING,
            self.TASK_NAME_MEOWFFICER_FARMING,
        ):
            logger.warning(f'[大世界-防溢出] 防止行动力溢出的执行任务无效: {task}，回退到智能调度')
            task = self.TASK_NAME_SCHEDULING
        return task

    def _get_current_action_point_for_overflow(self):
        """读取当前真实行动力，仅供防止行动力溢出功能使用。"""
        self.action_point_enter()
        self.action_point_safe_get()
        current = int(getattr(self, '_action_point_current', 0) or 0)
        total = int(getattr(self, '_action_point_total', 0) or 0)
        self.action_point_quit()
        logger.info(f'[大世界-防溢出] 防止行动力溢出检查：当前行动力={current}, 总行动力={total}')
        return current

    def update_prevent_action_point_overflow_schedule(self, current_ap=None, enable=True):
        """
        按当前真实行动力更新防溢出任务下次运行时间。

        Args:
            current_ap (int | None): 当前真实行动力。为 None 时现场 OCR。
            enable (bool): 是否同时启用防溢出任务。
        """
        if current_ap is None:
            current_ap = self._get_current_action_point_for_overflow()
        try:
            current_ap = int(current_ap)
        except (TypeError, ValueError):
            current_ap = 0
        current_ap = max(0, min(NATURAL_ACTION_POINT_LIMIT, current_ap))

        upper, _ = self._get_prevent_action_point_overflow_thresholds()
        if current_ap >= upper:
            delay_minutes = 1
        else:
            delay_minutes = max(1, (upper - current_ap) * ACTION_POINT_RECOVER_SECONDS / 60)

        logger.info(
            f'按当前行动力更新防溢出任务：当前={current_ap}, 上限={upper}, '
            f'{delay_minutes:.0f} 分钟后运行'
        )
        with self.config.multi_set():
            self._set_prevent_action_point_overflow_enabled(enable)
            self.config.task_delay(
                minute=delay_minutes,
                task=self.TASK_NAME_PREVENT_AP_OVERFLOW,
            )

    def os_prevent_action_point_overflow(self):
        """防止行动力溢出任务入口。"""
        self.run_prevent_action_point_overflow()

    def _run_with_prevent_action_point_overflow_context(self, task_name, func, *args, **kwargs):
        """以防溢出任务上下文代跑一轮大世界子任务。"""
        previous_context = getattr(self, '_prevent_action_point_overflow_context', None)
        previous_config_context = getattr(self.config, '_prevent_action_point_overflow_context', None)
        self._prevent_action_point_overflow_context = True
        self.config._prevent_action_point_overflow_context = True
        try:
            return self._run_with_opsi_task_context(task_name, func, *args, **kwargs)
        finally:
            if previous_context is None:
                if hasattr(self, '_prevent_action_point_overflow_context'):
                    delattr(self, '_prevent_action_point_overflow_context')
            else:
                self._prevent_action_point_overflow_context = previous_context

            if previous_config_context is None:
                if hasattr(self.config, '_prevent_action_point_overflow_context'):
                    delattr(self.config, '_prevent_action_point_overflow_context')
            else:
                self.config._prevent_action_point_overflow_context = previous_config_context

    def _run_scheduled_coin_task_once(self, task_name, ap_preserve):
        """由防溢出上下文直接执行一轮补黄币任务。"""
        if self.is_running_prevent_action_point_overflow_task():
            logger.info(f'[大世界-防溢出] 直接执行一轮{self.TASK_NAMES.get(task_name, task_name)}')
        return super()._run_scheduled_coin_task_once(task_name, ap_preserve)

    def _run_prevent_action_point_overflow_target_once(self, task_name, lowerbound):
        """按配置执行一轮防溢出目标任务。"""
        with self.config.temporary(
            OS_ACTION_POINT_BOX_USE=False,
            OpsiGeneral_BuyActionPointLimit=0,
        ):
            if task_name == self.TASK_NAME_SCHEDULING:
                self._run_with_prevent_action_point_overflow_context(
                    self.TASK_NAME_SCHEDULING,
                    self.run_smart_scheduling_once,
                )
            elif task_name == self.TASK_NAME_HAZARD1_LEVELING:
                self._run_with_prevent_action_point_overflow_context(
                    self.TASK_NAME_HAZARD1_LEVELING,
                    self.run_hazard1_leveling_once,
                    ap_preserve=lowerbound,
                )
            elif task_name == self.TASK_NAME_MEOWFFICER_FARMING:
                self._run_with_prevent_action_point_overflow_context(
                    self.TASK_NAME_MEOWFFICER_FARMING,
                    self.run_meowfficer_farming_once,
                    ap_preserve=lowerbound,
                )
            else:
                raise ValueError(f'未知防溢出目标任务: {task_name}')

    def run_prevent_action_point_overflow(self):
        """防止当前行动力溢出。"""
        logger.hr('Opsi prevent action point overflow', level=1)
        upperbound, lowerbound = self._get_prevent_action_point_overflow_thresholds()
        task_name = self._get_prevent_action_point_overflow_task()
        logger.attr('ActionPointUpperbound', upperbound)
        logger.attr('ActionPointLowerbound', lowerbound)
        logger.attr('OverflowTask', task_name)

        started = False
        while True:
            current_ap = self._get_current_action_point_for_overflow()
            if not started:
                if current_ap < upperbound:
                    logger.info(f'[大世界-防溢出] 当前行动力未达到上限 ({current_ap} < {upperbound})，更新下次运行时间')
                    self.update_prevent_action_point_overflow_schedule(current_ap=current_ap, enable=True)
                    self.config.task_stop()
            elif current_ap < lowerbound:
                logger.info(f'[大世界-防溢出] 当前行动力已低于下限 ({current_ap} < {lowerbound})，停止防溢出任务')
                self.update_prevent_action_point_overflow_schedule(current_ap=current_ap, enable=True)
                self.config.task_stop()

            started = True
            try:
                self._run_prevent_action_point_overflow_target_once(task_name, lowerbound)
            except ActionPointLimit as e:
                current = getattr(e, 'current', None)
                if current is None:
                    current = current_ap
                logger.info(f'[大世界-防溢出] 当前行动力无法进入目标海域，停止防溢出任务: current={current}, error={e}')
                self.update_prevent_action_point_overflow_schedule(current_ap=current, enable=True)
                self.config.task_stop()
            except TaskEnd:
                try:
                    current_ap = self._get_current_action_point_for_overflow()
                except Exception:
                    logger.debug('[大世界-防溢出] 防溢出任务结束后刷新当前行动力失败，使用运行前数值', exc_info=True)
                self.update_prevent_action_point_overflow_schedule(current_ap=current_ap, enable=True)
                raise
