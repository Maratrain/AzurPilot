"""
突袭每日（Daily Raid）任务模块。

负责按配置依次刷完各难度（easy、normal、hard）的每日突袭次数。
支持以下功能：
- 通过 StageFilter 过滤器选择要刷的难度
- 自动检测剩余次数并循环执行
- EX 难度始终最后执行，并在执行前领取通关奖励
- RPG 类型突袭无每日模式，自动禁用调度器
"""
import re

from module.base.filter import Filter
from module.logger import logger
from module.raid.run import RaidRun
from module.reward.reward import Reward
from module.ui.page import page_raid


class RaidStage:
    """
    突袭难度阶段数据类。

    用于在 StageFilter 过滤器中表示一个突袭难度选项。

    Attributes:
        name (str): 难度名称，如 'easy'、'normal'、'hard'。
    """

    def __init__(self, name):
        self.name = name

    def __str__(self):
        return self.name


STAGES = ['easy', 'normal', 'hard']
STAGE_FILTER = Filter(regex=re.compile('(\w+)'), attr=['name'])


class RaidDaily(RaidRun):
    """
    突袭每日任务执行器。

    按配置依次执行各难度的突袭每日任务。执行流程：
    1. 检查是否为 RPG 类型（RPG 无每日模式，直接禁用）
    2. 使用 StageFilter 过滤要刷的难度（默认 easy > normal > hard）
    3. 按顺序刷完每个难度的 15 次每日次数
    4. 如果配置了 EX 难度，先领取通关奖励再执行 EX

    继承自 RaidRun，使用其战斗执行和停止条件检查逻辑。
    """
    def run(self, name=''):
        """
        运行突袭每日任务，依次刷完各难度次数。

        Args:
            name (str): 突袭活动名称，如 'raid_20200624'。
        """
        if self.is_raid_rpg():
            logger.info('[突袭-日常] RPG突袭没有每日任务')
            self.config.Scheduler_Enable = False
            self.config.task_stop()

        name = name if name else self.config.Campaign_Event
        stages = [RaidStage(name) for name in STAGES]
        STAGE_FILTER.load(self.config.RaidDaily_StageFilter)
        stages = STAGE_FILTER.apply(stages)

        self.ui_ensure(page_raid)

        for stage in stages:
            mode = stage.name
            logger.hr(mode, level=1)
            for _ in range(15):
                remain = self.get_remain(mode=mode)
                if remain <= 0:
                    break
                super().run(name=name, mode=mode, total=1)

        # 如果配置了 EX 难度，始终最后执行，因此不使用阶段过滤
        stages = [stage.lower().strip()\
            for stage in\
            self.config.RaidDaily_StageFilter.split('>')]
        if 'ex' in stages:
            # 领取通关任意难度 5 次和 10 次的突袭门票奖励
            self.ui_goto_main()
            Reward(self.config, self.device).reward_mission(
                   daily=self.config.Reward_CollectMission,
                   weekly=False)
            self.ui_ensure(page_raid)

            logger.hr('ex', level=1)
            super().run(name=name, mode='ex', total=self.get_remain('ex'))

        self.config.task_delay(server_update=True)
