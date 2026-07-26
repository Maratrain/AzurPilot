"""大世界代币任务 Mixin 模块。

从 scheduling 模块重新导出 CoinTaskMixin 类。
CoinTaskMixin 提供大世界代币任务的通用逻辑，包括：
- 代币（作战补给凭证）资源检查和保护
- 任务无内容时的延迟处理
- 与智能调度系统的协作

被多个大世界任务模块继承使用。
"""

from module.os.tasks.scheduling import CoinTaskMixin

__all__ = ['CoinTaskMixin']
