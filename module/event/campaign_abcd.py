"""活动日常关卡（ABCD 等非 SP 关卡）执行模块。

按配置过滤器排序依次执行活动关卡，支持从上次中断的关卡继续执行。
每个关卡每日执行 1 次，全部完成后延迟到次日服务器刷新。

关卡扫描自 campaign/{event_name}/ 目录，通过 EventDaily_StageFilter 配置
筛选和排序要执行的关卡。执行进度通过 EventDaily_LastStage 持久化，
支持跨任务会话的断点续刷。

配置路径: EventDaily.StageFilter (关卡过滤器)
"""

import os

from module.config.config import TaskEnd
from module.config.utils import get_server_last_update
from module.event.base import STAGE_FILTER, EventBase, EventStage
from module.exception import ScriptEnd, RequestHumanTakeover
from module.logger import logger


class CampaignABCD(EventBase):
    """活动日常关卡（ABCD 等非 SP 关卡）的执行器。

    按过滤器排序依次执行活动关卡，支持从上次中断的关卡继续。
    每个关卡执行 1 次，全部完成后延迟到次日服务器刷新。

    Pages:
        in: page_event
        out: page_event
    """

    def run(self, *args, **kwargs):
        """执行活动日常关卡流程。

        流程：扫描活动目录 → 过滤关卡 → 从上次进度继续 → 依次执行 → 延迟到次日。
        """
        # 扫描活动目录下的所有地图文件
        stages = [EventStage(file) for file in os.listdir(f'./campaign/{self.config.Campaign_Event}')]
        stages = self.convert_stages(stages)
        logger.attr('关卡', [str(stage) for stage in stages])
        logger.attr('关卡过滤器', self.config.EventDaily_StageFilter)
        STAGE_FILTER.load(self.config.EventDaily_StageFilter)
        self.convert_stages(STAGE_FILTER)
        stages = [str(stage) for stage in STAGE_FILTER.apply(stages)]
        logger.attr('过滤排序', ' > '.join(stages))

        # 过滤后无可用关卡，禁用调度器并停止任务
        if not stages:
            logger.warning('无关卡满足当前筛选')
            self.config.Scheduler_Enable = False
            self.config.task_stop()

        # 从上次执行到的关卡继续，避免重复刷已完成的关卡
        logger.info(f'[活动-关卡] 上次关卡 {self.config.EventDaily_LastStage}，记录于 {self.config.Scheduler_NextRun}')
        if get_server_last_update(self.config.Scheduler_ServerUpdate) >= self.config.Scheduler_NextRun:
            logger.info('[活动-关卡] 上次关卡记录过期，重置')
            self.config.EventDaily_LastStage = 0
        else:
            last = str(self.config.EventDaily_LastStage).lower()
            last = self.convert_stages(last)
            if last in stages:
                # 跳到上次关卡之后的下一个关卡
                stages = stages[stages.index(last) + 1:]
                logger.attr('过滤排序', ' > '.join(stages))
            else:
                logger.info('从头开始')

        # 依次执行每个关卡
        for stage in stages:
            stage = str(stage)
            try:
                super().run(name=stage, folder=self.config.Campaign_Event, total=1)
            except TaskEnd:
                # 捕获任务切换，正常中断
                pass
            except ScriptEnd as e:
                # 来自 CampaignUI.ensure_campaign_ui() 的关卡名错误
                if str(e) == 'Campaign name error':
                    task = self.config.task.command
                    logger.critical(
                        f'无法找到关卡 "{stage}". '
                        f'任务 "{task}" 是用于 3 倍日常PT的，如果您还没有解锁 {stage}，'
                        f'应该使用任务 "Event" 来解锁它，而不是使用任务 "{task}"')
                    raise RequestHumanTakeover
                else:
                    raise

            # 关卡执行成功，记录进度并延迟到下一个关卡
            if self.run_count > 0:
                with self.config.multi_set():
                    self.config.EventDaily_LastStage = stage
                    self.config.task_delay(minute=0)
            else:
                self.config.task_stop()
            if self.config.task_switched():
                self.config.task_stop()

        # 所有关卡执行完毕，延迟到次日服务器刷新
        self.config.task_delay(server_update=True)
