"""渠道服（4399）启动悬浮球处理。

4399 等渠道服客户端启动后，屏幕顶部会出现 SDK 悬浮球（半透明圆盘，
直径约 70px，截图里几乎不可见），顶部带绿色「○○○」标志（三个小圆点）。
悬浮球每次启动停靠位置不固定，通过绿标动态定位球中心后拖拽到屏幕
中下触发「隐藏悬浮球」对话框，并点击「隐藏」按钮将其彻底关闭。
"""
import time

import cv2
import numpy as np

from module.base.base import ModuleBase
from module.base.button import Button
from module.base.utils import crop
from module.base.timer import Timer
from module.config.deep import deep_get
from module.handler.assets import LOGIN_CHECK
from module.logger import logger
from module.ui.page import page_main_white

# 悬浮球识别区域（1280x720 逻辑坐标，运行时按实际分辨率缩放）。
# SDK 悬浮球停靠在屏幕顶部一带，顶部带绿色「○○○」标志（三个小圆点横排）。
# 悬浮球每次启动停靠位置不固定（实测出现过绿标位于 (299~349,0~9) 与
# (220,45) 附近两种），因此采用动态定位而非硬编码坐标。
CHANNEL_FLOAT_AREA = (0, 0, 640, 100)
# 排除区域（逻辑坐标）：游戏头像框旁的绿色箭头（原生UI，约 (85~120, 60~90)），
# 颜色特征与悬浮球绿标相同，需显式排除避免误定位
CHANNEL_FLOAT_EXCLUDE_AREA = (80, 55, 125, 95)
# 绿色像素计数阈值：实测有球约330、无球0，取 20 作为安全阈值
CHANNEL_FLOAT_GREEN_THRESHOLD = 20
# 绿标中心到球中心的垂直偏移（逻辑坐标）：球直径约70px，绿标位于球顶部
CHANNEL_FLOAT_BALL_CENTER_OFFSET_Y = 30
# 悬浮球拖拽终点（逻辑坐标，屏幕中下偏下）：
# 「隐藏悬浮球」对话框的触发区域位于屏幕下方，实测终点需压到
# y=680 附近才能稳定触发（660 仍偏浅，松手后悬浮球弹回原位）
CHANNEL_FLOAT_SWIPE_END = (640, 680)
# 拖到终点后按住停留时长：悬浮球需停留片刻再松手才会触发「隐藏悬浮球」
# 对话框，立即松手会被判定为甩动；drag 后端另有约 0.28s 的内置停顿
CHANNEL_FLOAT_HOLD_DURATION = 0.2
CHANNEL_FLOAT_MAX_ATTEMPTS = 4
# 对话框白色主体占屏幕面积的最小比例（实测 1280x720 为 18.6%、1600x900 为 11.7%）
CHANNEL_FLOAT_DIALOG_WHITE_RATIO = 0.04
# 「隐藏」绿字连通域的最小像素数（实测两分辨率下每字约 300px）
CHANNEL_FLOAT_HIDE_GREEN_THRESHOLD = 50


def _scale_area(area, width, height):
    """将 1280x720 逻辑坐标区域缩放到实际分辨率。

    Args:
        area: 逻辑坐标区域 (x0, y0, x1, y1)。
        width: 实际图像宽度。
        height: 实际图像高度。

    Returns:
        tuple: 实际分辨率下的区域 (x0, y0, x1, y1)。
    """
    return (int(area[0] * width / 1280), int(area[1] * height / 720),
            int(area[2] * width / 1280), int(area[3] * height / 720))


def channel_float_position(image):
    """定位悬浮球：返回球中心坐标，未识别到返回 None。

    悬浮球半透明难以直接识别，但其顶部带有绿色「○○○」标志
    （三个小圆点横排）。在左上识别区内统计绿色像素并排除头像框旁
    绿色箭头（原生UI），绿标中心向下偏移约 30px 为球中心。

    为避免识别区内其他绿色元素（UI图标、活动角标等）拉偏质心，
    先做连通域分析，将同一水平线（y 中心差<=15px）且彼此邻近
    （x 中心距<=60px）的小块聚为一组，多点横排的组优先（三点
    标志特征），其次取最靠上的一组，组内按像素加权求质心。

    Args:
        image: 当前截图。

    Returns:
        tuple: 球中心坐标 (x, y)；未识别到时 None。
    """
    height, width = image.shape[:2]
    area = _scale_area(CHANNEL_FLOAT_AREA, width, height)
    area_img = crop(image, area, copy=False)
    r = area_img[:, :, 0].astype(np.int16)
    g = area_img[:, :, 1].astype(np.int16)
    b = area_img[:, :, 2].astype(np.int16)
    green = (g > r + 15) & (g > b + 15) & (g > 100)
    ex = _scale_area(CHANNEL_FLOAT_EXCLUDE_AREA, width, height)
    x0 = max(ex[0] - area[0], 0)
    y0 = max(ex[1] - area[1], 0)
    x1 = min(ex[2] - area[0], green.shape[1])
    y1 = min(ex[3] - area[1], green.shape[0])
    green[y0:y1, x0:x1] = False
    count = int(green.sum())
    if count < CHANNEL_FLOAT_GREEN_THRESHOLD:
        logger.info(f'[渠道悬浮球] 绿色标志像素 {count}，未识别到悬浮球')
        return None
    n, _, stats, centroids = cv2.connectedComponentsWithStats(green.astype(np.uint8), 8)
    y_tol = int(15 * height / 720)
    x_tol = int(60 * width / 1280)
    comps = []
    for i in range(1, n):
        a = stats[i, cv2.CC_STAT_AREA]
        if a >= 8:
            comps.append((centroids[i][0], centroids[i][1], a))
    groups = []
    for cx, cy, _ in sorted(comps, key=lambda c: (c[1], c[0])):
        for group in groups:
            if any(abs(cy - cy2) <= y_tol and abs(cx - cx2) <= x_tol
                   for cx2, cy2, _ in group):
                group.append((cx, cy, _))
                break
        else:
            groups.append([(cx, cy, _)])
    # 多点横排（三点标志）优先，其次最靠上，再次面积最大
    best = max(
        groups,
        key=lambda group: (
            len(group) >= 2,
            -sum(c[1] for c in group) / len(group),
            sum(c[2] for c in group)))
    total = sum(c[2] for c in best)
    cx = sum(c[0] * c[2] for c in best) / total + area[0]
    cy = sum(c[1] * c[2] for c in best) / total + area[1]
    offset_y = int(CHANNEL_FLOAT_BALL_CENTER_OFFSET_Y * height / 720)
    ball = (int(cx), int(cy) + offset_y)
    logger.info(f'[渠道悬浮球] 绿色标志像素 {count}，绿标组 {len(best)}/{len(groups)}，'
                f'定位球中心 {ball}')
    return ball


def hide_button(image):
    """定位「隐藏悬浮球」对话框中的「隐藏」按钮，返回动态构造的 Button。

    对话框白色主体位置随分辨率/排版变化（1280x720 标定的固定按钮区
    在 1600x900 下完全落空，且对话框非等比缩放），因此改为两步动态定位：
    1. 全屏找白色大块（亮度>220 的最大连通域，面积>=屏幕4%）即对话框；
    2. 质心落在白区范围内的绿色文字块为对话框内元素，「隐藏」按钮是
       其中最靠下的一块（实测 1280x720 与 1600x900 布局一致：上部两块
       绿字+中部绿图形+底部「隐藏」二字），与其 y 范围重叠的绿块合并
       取质心。绿字底色并非纯白，不能按像素落在白区 mask 上判定，
       按质心归属；主界面右下「出击」绿字在白区范围外，天然排除。
    Args:
        image: 当前截图。

    Returns:
        Button: 「隐藏」按钮（点击热区为绿字质心附近）；未找到时 None。
    """
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    white = gray > 220
    n, labels, stats, _ = cv2.connectedComponentsWithStats(white.astype(np.uint8), 8)
    if n <= 1:
        logger.info('[渠道悬浮球] 未检测到白色对话框')
        return None
    best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[best, cv2.CC_STAT_AREA] < CHANNEL_FLOAT_DIALOG_WHITE_RATIO * width * height:
        logger.info('[渠道悬浮球] 白色区域过小，非「隐藏悬浮球」对话框')
        return None
    wx, wy, ww, wh = (stats[best, 0], stats[best, 1],
                      stats[best, 2], stats[best, 3])

    r = image[:, :, 0].astype(np.int16)
    g = image[:, :, 1].astype(np.int16)
    b = image[:, :, 2].astype(np.int16)
    green = (g > r + 15) & (g > b + 15) & (g > 100)
    n2, labels2, stats2, centroids2 = cv2.connectedComponentsWithStats(
        green.astype(np.uint8), 8)
    blocks = []
    for i in range(1, n2):
        x, y, w2, h2, a = stats2[i]
        if a < CHANNEL_FLOAT_HIDE_GREEN_THRESHOLD:
            continue
        cx0, cy0 = centroids2[i]
        if wx <= cx0 <= wx + ww and wy <= cy0 <= wy + wh:
            blocks.append((y + h2 / 2, x, y, w2, h2, centroids2[i]))
    if not blocks:
        logger.info('[渠道悬浮球] 对话框内未找到「隐藏」绿字')
        return None
    # 最靠下的绿字块为主块，合并与其 y 范围重叠>=50% 的相邻块（「隐藏」两字）
    blocks.sort(key=lambda t: t[0], reverse=True)
    main_y0, main_y1 = blocks[0][2], blocks[0][2] + blocks[0][4]
    xs, ys = [], []
    for _, x, y, w2, h2, c in blocks:
        overlap = min(main_y1, y + h2) - max(main_y0, y)
        if overlap >= 0.5 * h2:
            xs.append(c[0])
            ys.append(c[1])
    cx, cy = int(np.mean(xs)), int(np.mean(ys))
    pad = int(12 * height / 720)
    button = Button(
        area=(cx - pad, cy - pad, cx + pad, cy + pad),
        color=(),
        button=(cx - pad, cy - pad, cx + pad, cy + pad),
        name=f'CHANNEL_FLOAT_HIDE_DYNAMIC ({cx}, {cy})',
    )
    logger.info(f'[渠道悬浮球] 定位「隐藏」按钮 ({cx}, {cy})，绿字块 {len(xs)} 个')
    return button


class ChannelFloatHandler(ModuleBase):
    """检测并处理渠道服启动悬浮球。"""

    def _enabled(self) -> bool:
        """渠道服悬浮球处理是否启用。

        仅当游戏为 4399 渠道服（包名 com.bilibili.blhx.m4399 且服务器为
        cn_channel-*）时，开关 Restart.MoveChannelFloat 才生效；
        其他服务器即使开启开关也不会生效。

        Returns:
            bool: True 表示启用。
        """
        if not bool(deep_get(self.config.data, 'Restart.Restart.MoveChannelFloat', default=False)):
            logger.info('[渠道悬浮球] 未启用：开关 Restart.MoveChannelFloat 未开启')
            return False
        package = str(deep_get(self.config.data, 'Alas.Emulator.PackageName', default=''))
        server_name = str(deep_get(self.config.data, 'Alas.Emulator.ServerName', default=''))
        if package == 'com.bilibili.blhx.m4399' and server_name.startswith('cn_channel-'):
            return True
        logger.info(f'[渠道悬浮球] 未启用：非 4399 渠道服（server={server_name}, package={package}）')
        return False

    def handle_channel_float(self, ball_pos) -> bool:
        """拖拽悬浮球到屏幕中下，并在「隐藏」对话框弹出后点击「隐藏」。

        Args:
            ball_pos: 悬浮球中心坐标 (x, y)，由 channel_float_position 动态定位。

        Returns:
            bool: 固定返回 True，表示已执行处理。
        """
        height, width = self.device.image.shape[:2]
        swipe_end = (int(CHANNEL_FLOAT_SWIPE_END[0] * width / 1280),
                     int(CHANNEL_FLOAT_SWIPE_END[1] * height / 720))
        logger.info(
            f'[渠道悬浮球] 拖拽 {ball_pos} -> {swipe_end}, '
            f'终点停留 {CHANNEL_FLOAT_HOLD_DURATION}s')
        start = time.monotonic()
        self.device.drag(
            ball_pos, swipe_end,
            point_random=(0, 0, 0, 0), hold_duration=CHANNEL_FLOAT_HOLD_DURATION,
            name='CHANNEL_FLOAT_DRAG')
        logger.info(f'[渠道悬浮球] 拖拽完成，耗时 {time.monotonic() - start:.2f}s')
        # 等待「隐藏」按钮出现（截图循环，最多等 4 秒）
        dialog_timer = Timer(4).start()
        while 1:
            self.device.screenshot()
            button = hide_button(self.device.image)
            if button is not None:
                logger.info('[渠道悬浮球] 检测到「隐藏」按钮，点击')
                self.device.click(button)
                break
            if dialog_timer.reached():
                logger.info('[渠道悬浮球] 未见「隐藏」按钮，跳过点击')
                break
        return True

    def run(self) -> bool:
        """任务前的悬浮球检查入口（每个会话仅调用一次）。

        通过绿色标志检测悬浮球；识别到时自动拖拽并点击「隐藏」确认，
        最多尝试 CHANNEL_FLOAT_MAX_ATTEMPTS 次；未识别到悬浮球时不产生
        任何输入操作。

        Returns:
            bool: True 表示本回合检查已消费（无悬浮球或已处理）。
        """
        if not self._enabled():
            return True
        # 游戏进程未运行时直接跳过，避免阻塞调度器的 GameNotRunning -> Restart 流程
        if not self.device.app_is_running():
            logger.info('[渠道悬浮球] 游戏进程未运行，跳过检查')
            return True
        logger.hr('渠道悬浮球检查', level=2)
        # 等待进入主界面：游戏重启后可能停在服务器选择页，需要点击确认，
        # 未到主界面时自动点击 LOGIN_CHECK 进入（上限 60 秒，避免阻塞任务）
        wait_timer = Timer(60).start()
        while 1:
            self.device.screenshot()
            if self.appear(page_main_white.check_button, offset=(30, 30)):
                break
            if self.appear(LOGIN_CHECK, offset=(30, 30), interval=2):
                logger.info('[渠道悬浮球] 点击进入主界面（LOGIN_CHECK）')
                self.device.click(LOGIN_CHECK)
                continue
            if wait_timer.reached():
                logger.info('[渠道悬浮球] 等待主界面超时，跳过本会话')
                return True
        logger.attr('检测区域', CHANNEL_FLOAT_AREA)
        for attempt in range(CHANNEL_FLOAT_MAX_ATTEMPTS):
            self.device.screenshot()
            ball_pos = channel_float_position(self.device.image)
            if ball_pos is None:
                logger.info(
                    f'[渠道悬浮球] 第 {attempt + 1}/{CHANNEL_FLOAT_MAX_ATTEMPTS} 次：'
                    '未识别到悬浮球，跳过')
                return True
            logger.info(
                f'[渠道悬浮球] 第 {attempt + 1}/{CHANNEL_FLOAT_MAX_ATTEMPTS} 次：'
                '识别到悬浮球，开始处理')
            self.handle_channel_float(ball_pos)
        logger.info('[渠道悬浮球] 多次处理仍未消失，跳过本回合')
        return True
