"""敏感信息遮罩模块。

在截图和日志输出前遮罩用户的敏感信息，保护隐私安全。

图像遮罩：
- 主界面：遮罩指挥官名称、UID 等个人信息
- 玩家信息页面：遮罩玩家 ID 和服务器信息

文本遮罩：
- 日志中的文件路径替换为假路径（C:\\fakepath\\AzurLaneAutoScript）
- ADB 路径中的真实路径被替换

使用 Mask 类实现区域遮罩，遮罩图像存储在 assets/mask/ 目录下。
"""

import re

from module.base.mask import Mask
from module.ui.assets import PLAYER_CHECK
from module.ui.page import MAIN_GOTO_CAMPAIGN_WHITE, MAIN_GOTO_FLEET

# 遮罩模板图像
MASK_MAIN = Mask('./assets/mask/MASK_MAIN.png')
MASK_MAIN_WHITE = Mask('./assets/mask/MASK_MAIN_WHITE.png')
MASK_PLAYER = Mask('./assets/mask/MASK_PLAYER.png')


def handle_sensitive_image(image):
    """对截图中的敏感信息区域应用遮罩。

    检测当前截图是否包含敏感信息页面（主界面、玩家信息等），
    如果是则应用对应的遮罩模板。

    Args:
        image (np.ndarray): 输入截图。

    Returns:
        np.ndarray: 应用遮罩后的截图。
    """
    if PLAYER_CHECK.match(image, offset=(30, 30)):
        image = MASK_PLAYER.apply(image)
    if MAIN_GOTO_FLEET.match(image, offset=(30, 30)):
        image = MASK_MAIN.apply(image)
    if MAIN_GOTO_CAMPAIGN_WHITE.match(image, offset=(30, 30)):
        image = MASK_MAIN_WHITE.apply(image)

    return image


def handle_sensitive_text(text):
    """对日志文本中的敏感路径信息进行脱敏处理。

    将日志中的真实文件路径替换为假路径，防止泄露用户的目录结构信息。

    Args:
        text (str): 输入文本。

    Returns:
        str: 脱敏后的文本。
    """
    text = re.sub('File \"(.*?)AzurLaneAutoScript', 'File \"C:\\\\fakepath\\\\AzurLaneAutoScript', text)
    text = re.sub('\[Adb_binary\] (.*?)AzurLaneAutoScript', '[Adb_binary] C:\\\\fakepath\\\\AzurLaneAutoScript', text)
    return text


def handle_sensitive_logs(logs):
    return [handle_sensitive_text(line) for line in logs]
