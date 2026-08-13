"""登录流程处理器。

管理碧蓝航线的登录和游戏重启流程，包括：
- 应用启动和登录画面检测
- 各种登录弹窗处理（公告、活动、签到等）
- 游戏崩溃/卡死时的重启恢复
- 服务器连接异常处理

登录流程覆盖了游戏启动后的各种 UI 状态，
通过截图循环检测并处理所有可能出现的弹窗和确认框，
最终确保游戏回到主界面。

继承自 UI，利用页面导航能力处理跨页面的弹窗。
"""

# 基于原版 login.py 增加了智能的游戏重启逻辑
# 用于处理登录流程中的各种弹窗、公告以及在应用崩溃时执行重启恢复操作。
# 最后更新: 2025-08-25 20:41
import time

import numpy as np
from scipy.signal import find_peaks
# 在导入 adbutils 和 uiautomator2 之前修补 pkg_resources
from module.device.pkg_resources import get_distribution
from uiautomator2 import UiObject
from uiautomator2.exceptions import XPathElementNotFoundError
from uiautomator2.xpath import XPath, XPathSelector

_ = get_distribution

import module.config.server as server
from module.base.button import Button
from module.base.timer import Timer
from module.base.utils import color_similarity_2d, crop
from module.config.deep import deep_get
from module.handler.assets import *
from module.logger import logger
from module.map.assets import *
from module.ui.assets import *
from module.ui.page import page_campaign_menu
from module.ui.ui import UI


# 应用重启恢复策略：3 次启动失败后进入观察阶段，观察期间仍无恢复则
# 由上层调度器执行模拟器重启，避免长时间无效重试。
RESTART_TRIES = 3
RESTART_FIRST_TRY_WAIT_SECONDS = 30
RESTART_SUBSEQUENT_TRY_WAIT_SECONDS = 20
RESTART_OBSERVE_SECONDS = 180
RESTART_OBSERVE_INTERVAL = 15


class LoginHandler(UI):
    """登录和游戏重启处理器。

    处理游戏启动后的登录流程，包括各种弹窗、公告、签到奖励的自动关闭，
    以及游戏崩溃后的重启恢复逻辑。

    主要方法：
    - _handle_app_login(): 完整的登录流程，从任意页面到主界面
    - app_restart(): 重启游戏应用
    - handle_app_login(): 带重试的登录入口
    """

    def _handle_app_login(self):
        """
        Pages:
            in: 任意页面
            out: page_main

        Raises:
            GameStuckError: 游戏卡死。
            GameTooManyClickError: 点击次数过多。
            GameNotRunningError: 游戏未运行。
        """
        logger.hr('应用登录')

        confirm_timer = Timer(1.5, count=4).start()
        orientation_timer = Timer(5)
        login_success = False
        self.device.stuck_record_clear()
        self.device.click_record_clear()

        while 1:
            # 监测设备屏幕旋转
            if not login_success and orientation_timer.reached():
                # 启动应用后屏幕可能会旋转
                self.device.get_orientation()
                orientation_timer.reset()

            self.device.screenshot()

            # 结束条件
            if self.is_in_main():
                if confirm_timer.reached():
                    logger.info('[登录] 登录到主界面确认')
                    break
            else:
                confirm_timer.reset()

            # 登录处理
            if self.match_template_color(LOGIN_CHECK, offset=(30, 30), interval=5):
                self.device.click(LOGIN_CHECK)
                if not login_success:
                    logger.info('[登录] 登录成功')
                    login_success = True
            if self.appear(ANDROID_NO_RESPOND, offset=(30, 30), interval=5):
                logger.warning('[登录] 模拟器无响应')
                self.device.click_record_add(ANDROID_NO_RESPOND)
                self.device.click_record_check()
                self.device.click(ANDROID_NO_RESPOND, control_check=False)
                continue
            if self.appear_then_click(LOGIN_ANNOUNCE, offset=(30, 30), interval=5):
                continue
            if self.appear_then_click(LOGIN_ANNOUNCE_2, offset=(30, 30), interval=5):
                continue
            if self.appear(EVENT_LIST_CHECK, offset=(30, 30), interval=5):
                self.device.click(BACK_ARROW)
                continue
            # 更新和维护
            if self.appear_then_click(MAINTENANCE_ANNOUNCE, offset=(30, 30), interval=5):
                continue
            if self.appear_then_click(LOGIN_GAME_UPDATE, offset=(30, 30), interval=5):
                continue
            if server.server == 'cn' and not login_success:
                if self.handle_cn_user_agreement():
                    continue
            # 回归玩家
            if self.appear_then_click(LOGIN_RETURN_SIGN, offset=(30, 30), interval=5):
                continue
            if self.appear_then_click(LOGIN_RETURN_INFO, offset=(30, 30), interval=5):
                continue
            if self.appear_then_click(AVATAR_EXPIRED, offset=(30, 30), interval=5):
                continue
            # 弹窗处理
            if self.handle_popup_confirm('LOGIN'):
                continue
            if self.handle_urgent_commission():
                continue
            # 主界面弹窗
            if self.ui_page_main_popups(get_ship=login_success):
                return True
            # 始终尝试返回主界面
            if self.appear_then_click(GOTO_MAIN, offset=(30, 30), interval=5):
                continue

        return True

    _user_agreement_timer = Timer(1, count=2)

    def handle_cn_user_agreement(self):
        if not self._user_agreement_timer.reached():
            return False

        right = self.image_color_button(
            area=(640, 360, 1280, 720), color=(78, 189, 234),
            color_threshold=245, encourage=25, name='AGREEMENT_CONFIRM')
        if right is None:
            return False
        # 2026.04.17 不再需要滚动，只需在点击确认前简单滑动
        # 如果屏幕右半部分有蓝色按钮而左半部分没有，则为确认按钮
        # 如果两侧都有，则是中间的登录确认按钮
        left = self.image_color_button(
            area=(0, 360, 640, 720), color=(78, 189, 234),
            color_threshold=245, encourage=25, name='AGREEMENT_CONFIRM')
        if left is None:
            # 用户协议
            # 在屏幕中间某处进行滑动
            box = (350, 230, 920, 430)
            self.device.swipe_vector((0, -150), box, name='AGREEMENT_SCROLL')
            self.device.swipe_vector((0, -150), box, name='AGREEMENT_SCROLL')
            self.device.click(right)
            self._user_agreement_timer.reset()
            return True
        else:
            # 用户登录确认
            self.device.click(right)
            self._user_agreement_timer.reset()
            return True

    def _login_wait_timeout(self):
        """
        获取登录等待阶段允许画面保持静态的最大秒数。

        对应配置项 Restart.LoginWaitTimeout，仅作用于 app_restart()/app_start()
        之后的登录等待阶段；正常任务仍使用 device 原始卡死检测阈值。

        直接读取跨任务配置路径 Restart.Restart.LoginWaitTimeout，而非依赖当前
        绑定的任务，确保在非 Restart 任务（如大世界、未知页面恢复）触发的
        登录等待中也能读到用户配置值。

        Returns:
            float: 登录等待宽容时间（秒），配置非法时回退默认 30 秒。
        """
        value = deep_get(self.config.data, 'Restart.Restart.LoginWaitTimeout', default=30)
        try:
            timeout = float(value)
        except (TypeError, ValueError):
            timeout = -1.0
        if not (timeout > 0):
            logger.warning(f'[登录] Restart.LoginWaitTimeout 配置非法（{value!r}），回退默认 30 秒')
            return 30.0
        if timeout > 3600:
            logger.warning(f'[登录] Restart.LoginWaitTimeout 超过上限（{value!r}），按 3600 秒处理')
            return 3600.0
        return timeout

    def handle_app_login(self):
        """
        处理应用登录流程。

        Returns:
            是否登录成功。

        Raises:
            GameStuckError: 游戏卡死。
            GameTooManyClickError: 点击次数过多。
            GameNotRunningError: 游戏未运行。
        """
        logger.info('[登录] 处理应用登录')
        self.device.screenshot_interval_set(1.0)
        login_wait_timeout = self._login_wait_timeout()
        logger.info(f'[登录] 登录等待宽容时间 {login_wait_timeout:g} 秒')
        try:
            # 登录等待阶段放宽卡死检测，避免后台模拟器慢启动时
            # 静态画面超过默认 30 秒就被误判为 GameStuckError 而陷入重启循环。
            with self.device.stuck_timeout_override(
                    image_stuck=login_wait_timeout,
                    long_wait=max(login_wait_timeout, self.device.stuck_timer_long.limit)):
                self._handle_app_login()
        finally:
            self.device.screenshot_interval_set()

    def app_stop(self):
        logger.hr('应用停止')
        self.device.app_stop()

    def app_start(self):
        logger.hr('应用启动')
        self.device.app_start()
        self.handle_app_login()
        # self.ensure_no_unfinished_campaign()

    # def app_restart(self):
    #     logger.hr('App restart')
    #     self.device.app_stop()
    #     self.device.app_start()
    #     self.handle_app_login()
    #     # self.ensure_no_unfinished_campaign()
    #     self.config.task_delay(server_update=True)

    def app_restart(self):
        logger.hr('应用重启')
        is_restart_success = False

        clear_cache = getattr(self.config, 'Restart_ClearCache', False)
        for i in range(RESTART_TRIES):
            logger.info(f"[重启] 应用重启尝试 {i + 1}/{RESTART_TRIES}...")
            self.device.app_stop()
            if clear_cache:
                self.device.app_clear()
            self.device.sleep(3)
            self.device.app_start()
            wait_seconds = RESTART_FIRST_TRY_WAIT_SECONDS if i == 0 else RESTART_SUBSEQUENT_TRY_WAIT_SECONDS
            logger.info(f"[重启] 等待 {wait_seconds} 秒让应用启动和稳定...")
            self.device.sleep(wait_seconds)

            # 用带超时的 ADB 检查验证应用是否已运行，
            # 避免 uiautomator2 重试在模拟器异常时阻塞恢复流程
            if self.device.app_is_running_bounded():
                logger.info("[重启] 应用启动成功并正在运行")
                is_restart_success = True
                break  # 成功启动，跳出循环
            else:
                logger.warning(f"[重启] 尝试 {i + 1} 失败。应用启动后未运行（可能崩溃）")
                if i < RESTART_TRIES - 1:
                    logger.info("[重启] 重试中...")

        # 连续失败后先进入观察阶段，给慢启动/游戏更新留出恢复时间
        if not is_restart_success:
            logger.critical(
                f"[重启] 应用重启连续失败 {RESTART_TRIES} 次，"
                f"进入观察阶段，最多等待 {RESTART_OBSERVE_SECONDS} 秒"
            )
            deadline = time.monotonic() + RESTART_OBSERVE_SECONDS
            while 1:
                if time.monotonic() >= deadline:
                    break
                if self.device.app_is_running_bounded():
                    logger.info("[重启] 观察阶段检测到应用恢复运行")
                    is_restart_success = True
                    break
                remaining = max(0, int(deadline - time.monotonic()))
                logger.info(f"[重启] 观察阶段应用仍未恢复，剩余 {remaining} 秒后触发模拟器重启")
                self.device.sleep(min(RESTART_OBSERVE_INTERVAL, remaining))

        # 观察阶段仍失败则抛出 EmulatorNotRunningError，
        # 由上层调度器触发模拟器重启流程，而非直接终止。
        if not is_restart_success:
            logger.critical(
                "[重启] 应用重启连续失败且观察阶段仍未恢复，"
                "判定模拟器或游戏环境异常，触发模拟器重启"
            )
            from module.exception import EmulatorNotRunningError
            raise EmulatorNotRunningError(
                f"[重启] 应用重启连续失败 {RESTART_TRIES} 次，"
                f"观察 {RESTART_OBSERVE_SECONDS} 秒后仍未恢复，"
                "判定模拟器或游戏环境异常，触发模拟器重启"
            )
        self.handle_app_login()
        # self.ensure_no_unfinished_campaign()

    def ensure_no_unfinished_campaign(self, confirm_wait=3):
        """
        Pages:
            in: page_main
            out: page_main

        确保没有未完成的战役，如有则撤退。
        """

        def ensure_campaign_retreat():
            if self.appear_then_click(WITHDRAW, offset=(30, 30), interval=5):
                return True
            if self.handle_popup_confirm('WITHDRAW'):
                return True

        def in_campaign():
            return self.appear(CAMPAIGN_CHECK, offset=(30, 30)) \
                   or self.appear(CAMPAIGN_MENU_CHECK, offset=(30, 30)) \
                   or self.appear(EVENT_CHECK, offset=(30, 30)) \
                   or self.appear(SP_CHECK, offset=(30, 30))

        skip_first_screenshot = True
        while 1:
            if skip_first_screenshot:
                skip_first_screenshot = False
            else:
                self.device.screenshot()

            # 结束条件
            if in_campaign():
                break

            # 点击操作
            if self.ui_main_appear_then_click(page_campaign_menu, interval=3):
                continue
            if ensure_campaign_retreat():
                continue

        self.ui_goto_main()

    def handle_user_agreement(self, xp, hierarchy):
        """
        处理用户协议弹窗（仅限国服）。

        国服客户端存在 bug，用户协议和隐私政策可能在已同意后再次弹出。
        此方法滑动到底部并点击同意按钮。

        Returns:
            是否处理了用户协议弹窗。
        """

        if server.server == 'cn':
            area_wait_results = self.get_for_any_ele([
                XPS('//*[@text="sdk协议"]', xp, hierarchy),
                XPS('//*[@content-desc="sdk协议"]', xp, hierarchy)])
            if area_wait_results is False:
                return False
            agree_wait_results = self.get_for_any_ele([
                XPS('//*[@text="同意"]', xp, hierarchy),
                XPS('//*[@content-desc="同意"]', xp, hierarchy)])
            start_padding_results = self.get_for_any_ele([
                XPS('//*[@text="隐私政策"]', xp, hierarchy), XPS('//*[@content-desc="隐私政策"]', xp, hierarchy),
                XPS('//*[@text="用户协议"]', xp, hierarchy), XPS('//*[@content-desc="用户协议"]', xp, hierarchy)])
            start_margin_results = self.get_for_any_ele([
                XPS('//*[@text="请滑动阅读协议内容"]', xp, hierarchy),
                XPS('//*[@content-desc="请滑动阅读协议内容"]', xp, hierarchy)])

            test_image_original = self.device.image
            image_handle_crop = crop(
                test_image_original, (start_padding_results[2], 0, start_margin_results[2], 720), copy=False)
            # Image.fromarray(image_handle_crop).show()
            sims = color_similarity_2d(image_handle_crop, color=(182, 189, 202))
            points = np.sum(sims >= 255)
            if points == 0:
                return False
            sims_height = np.mean(sims, axis=1)
            # pyplot.plot(sims_height, color='r')
            # pyplot.show()
            peaks, __ = find_peaks(sims_height, height=225)
            if len(peaks) == 2:
                peaks = (peaks[0] + peaks[1]) / 2
            start_pos = [(start_padding_results[2] + start_margin_results[2]) / 2, float(peaks)]
            end_pos = [(start_padding_results[2] + start_margin_results[2]) / 2, area_wait_results[3]]
            logger.info("[登录-协议] 用户协议位置查找结果: " + ', '.join(f'{pos:.2f}' for pos in start_pos))
            logger.info("[登录-协议] 用户协议区域预期:          " + 'x:963-973, y:259-279')

            self.device.drag(start_pos, end_pos, segments=2, shake=(0, 25), point_random=(0, 0, 0, 0),
                             shake_random=(0, -5, 0, 5))
            AGREE = Button(area=agree_wait_results, color=(), button=agree_wait_results, name='AGREE')
            self.device.click(AGREE)
            return True

    def handle_user_login(self, xp, hierarchy) -> bool:
        """处理用户登录按钮点击。"""
        login_wait_results = self.get_for_any_ele([
            XPS('//*[@text="登录"]', xp, hierarchy),
            XPS('//*[@content-desc="登录"]', xp, hierarchy)])
        if login_wait_results is False:
            return False
        else:
            USER_LOGIN_BTN = Button(area=login_wait_results, color=(), button=login_wait_results, name='USER_LOGIN_BTN')
            self.device.click(USER_LOGIN_BTN)
            return True

    @staticmethod
    def get_for_any_ele(list_u2_path: list) -> bool | tuple:
        """
        从候选 XPath 或 UiObject 列表中查找第一个存在的元素。

        Args:
            list_u2_path: UiObject 或 XPathSelector 的列表，长度 >= 1。

        Returns:
            False 表示未找到元素，tuple 表示找到的元素边界。
        """
        for path in list_u2_path:
            try:
                if isinstance(path, UiObject):
                    if path.exists():
                        return path.bounds()
                    elif not path.exists():
                        continue
                elif isinstance(path, XPathSelector):
                    if path.exists:
                        return path.bounds
                    elif not path.exists:
                        continue
            except XPathElementNotFoundError:
                continue
        return False

    def get_cn_xp_hierarchy(self) -> tuple:
        d = self.device.u2
        xp = XPath(d)
        hierarchy = d.dump_hierarchy()
        return xp, hierarchy


class XPS(XPathSelector):
    def __init__(self, xpath, parent, source):
        super().__init__(parent, xpath, source)
