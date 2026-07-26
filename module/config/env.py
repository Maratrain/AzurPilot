"""运行环境检测模块。

检测 AzurPilot 是否运行在云手机环境中。
云手机环境需要特殊处理某些配置项（如设备连接、ADB 路径等）。
"""

import os

# 检测云手机环境变量
IS_ON_PHONE_CLOUD = os.environ.get("cloudphone", "") == "cloudphone"
