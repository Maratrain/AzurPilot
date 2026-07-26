"""联动活动 SP 关卡任务模块。

封装联动活动 SP 模式的任务调度逻辑。调用 Coalition 基类
以 SP 难度执行一次战斗，完成后根据执行结果设置下一次
任务延迟或直接停止任务。
"""

from module.coalition.coalition import Coalition
from module.config.config import TaskEnd


class CoalitionSP(Coalition):
    def run(self, *args, **kwargs):
        try:
            super().run(mode='sp', total=1)
        except TaskEnd:
            # Catch task switch
            pass
        if self.run_count > 0:
            self.config.task_delay(server_update=True)
        else:
            self.config.task_stop()
