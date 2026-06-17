# 此文件专门用于处理设备端的文本输入功能。
# 封装了检查输入法窗口状态以及向安卓组件发送文本指令的逻辑。
from module.device.method.uiautomator_2 import Uiautomator2
from module.logger import logger
import time


class Input(Uiautomator2):

    def ime_shown(self) -> bool:
        _, shown = self.u2_current_ime()
        return shown

    def text_input_and_confirm(self, text: str, clear: bool = False):
        """
        Clipboard模式
        不依赖FastInputIME
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