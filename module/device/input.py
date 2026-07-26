"""设备文本输入模块。

封装 Android 设备端的文本输入功能，包括输入法窗口状态检测
以及通过 uiautomator2 向安卓组件发送文本指令和确认操作。
"""
# 此文件专门用于处理设备端的文本输入功能。
# 封装了检查输入法窗口状态以及向安卓组件发送文本指令的逻辑。
from module.device.method.uiautomator_2 import Uiautomator2
from module.logger import logger
import time


class Input(Uiautomator2):
    """设备文本输入处理器。

    通过 uiautomator2 实现文本输入功能，包括输入法状态检测
    和带确认操作的文本输入。继承自 Uiautomator2 以获取底层输入接口。

    Methods:
        ime_shown: 检测输入法窗口是否显示。
        text_input_and_confirm: 输入文本并发送确认动作。
    """
    def ime_shown(self) -> bool:
        """检测当前输入法（IME）窗口是否正在显示。

        Returns:
            bool: 输入法窗口可见返回 True，否则返回 False。
        """
        _, shown = self.u2_current_ime()
        return shown

    def text_input_and_confirm(self, text: str, clear: bool=False):
        """向当前焦点输入框发送文本并按确认键（IME_ACTION_DONE）。

        失败时最多重试 3 次，适用于输入法偶尔无响应的场景。

        Args:
            text (str): 要输入的文本内容。
            clear (bool): 输入前是否清空输入框已有内容。
        """
        for fail_count in range(3):
            try:
                # 写入剪贴板
                self.set_clipboard(text)

                logger.info(f"Clipboard set: {text}")

                time.sleep(0.5)

                # 长按文本框通常已经获得焦点
                # 直接模拟Ctrl+V
                self.adb_shell([
                    "input",
                    "keyevent",
                    "279"
                ])

                time.sleep(0.5)

                # 回车确认
                self.adb_shell([
                    "input",
                    "keyevent",
                    "66"
                ])

                logger.info("Clipboard input success")
                return

            except Exception as e:
                if fail_count >= 2:
                    raise

                logger.exception(
                    f"{e} Retrying {fail_count + 1}/3"
                )
                time.sleep(1)