"""指挥喵任务调度模块。

作为指挥喵系统（Meowfficer）的顶层任务入口，负责编排指挥喵相关的所有子任务：
- 指挥喵购买（Buy）：消耗金币购买指挥喵猫箱
- 指挥喵要塞（Fort）：执行要塞日常任务
- 指挥喵训练（Train）：收集已训练的指挥喵并排队训练新猫箱
- 指挥喵强化（Enhance）：消耗多余指挥喵为目标指挥喵提供经验值

根据配置决定执行哪些子任务，并安排下次执行的调度时间。
训练模式下，强化操作在每周日或无缝模式下自动执行。

配置项前缀：`Meowfficer_*`、`MeowfficerTrain_*`
"""

from module.meowfficer.buy import MeowfficerBuy
from module.meowfficer.fort import MeowfficerFort
from module.meowfficer.train import MeowfficerTrain
from module.ui.page import page_meowfficer


class RewardMeowfficer(MeowfficerBuy, MeowfficerFort, MeowfficerTrain):
    """指挥喵任务调度器。

    继承购买、要塞、训练三个子模块，按配置依次执行对应的指挥喵操作。
    所有子任务完成后，根据是否启用训练功能安排不同的延迟时间：
    - 训练模式：延迟 2.5~3.5 小时（等待训练完成）
    - 非训练模式：延迟至服务器更新时间

    继承关系：
        MeowfficerBuy: 指挥喵购买功能
        MeowfficerFort: 指挥喵要塞功能
        MeowfficerTrain: 指挥喵训练功能
    """
    def run(self):
        """
        Execute buy, enhance, train, and fort operations
        if enabled in configurations

        Pages:
            in: Any page
            out: page_meowfficer
        """
        if self.config.Meowfficer_BuyAmount <= 0 \
                and not self.config.Meowfficer_FortChoreMeowfficer \
                and not self.config.MeowfficerTrain_Enable:
            self.config.Scheduler_Enable = False
            self.config.task_stop()

        self.ui_ensure(page_meowfficer)
        self.wait_meowfficer_buttons()  # Wait for the ui to load fully

        if self.config.Meowfficer_BuyAmount > 0:
            self.meow_buy()
        if self.config.Meowfficer_FortChoreMeowfficer:
            self.meow_fort()

        # Train
        if self.config.MeowfficerTrain_Enable:
            self.meow_train()
            if self.config.MeowfficerTrain_Mode == 'seamlessly':
                self.meow_enhance()
            elif self.meow_is_sunday():
                self.meow_enhance()
            else:
                pass

        # Scheduler
        if self.config.MeowfficerTrain_Enable:
            # Meowfficer training duration:
            # - Blue, 2.0h ~ 2.5h
            # - Purple, 5.5h ~ 6.5h
            # - Gold, 9.5h ~ 10.5h
            # Delay 2.5h ~ 3.5h when having meowfficers under training
            self.config.task_delay(minute=(150, 210), server_update=True)
        else:
            self.config.task_delay(server_update=True)
