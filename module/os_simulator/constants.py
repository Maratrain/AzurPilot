"""大世界模拟器常量定义。

定义大世界模拟器使用的所有常量，包括状态数组索引（行动力、
代币、状态、已用时间等）、明石商店奖励矩阵、行动力恢复速率、
各海域行动力消耗以及状态枚举值（净化/明石/崩溃/完成）。
"""
import numpy as np

AP = 0
COIN = 1
STATUS = 2
USED_TIME = 3
HAS_CRASHED = 4
MEOW_COUNT = 5
CL1_COUNT = 6
PASSED_DAYS = 7

STATUS_CL1 = 0
STATUS_MEOW = 1
STATUS_CRASHED = 2
STATUS_DONE = 3

AKASHI = np.array([20.0, 40.0, 50.0, 100.0, 100.0, 200.0] + [0.0] * 18)
AKASHI_EXPECT_REWARD_6 = 127.5  # Precomputed: (20+40+50+100+100+200) / 24 * 6 = 127.5

AP_RECOVER = 1 / 600
AP_COSTS = np.array([0, 5, 10, 15, 20, 30, 40], dtype=np.int32)
