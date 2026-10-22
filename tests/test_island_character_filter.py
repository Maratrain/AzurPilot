"""岛屿角色体力筛选与 WorkerJuu 查找流程的离线回归测试。

用例借鉴自 wess09/AzurPilot 提交 1d6f86fa9
（fix(island): 修复套餐目标库存死锁与 WorkerJuu 筛选时序 #1307）。

原实现是「点击一次筛选按钮后固定 sleep 并盲点」：云手机角色页标题先渲染、
底部筛选按钮稍后才出现时，第一次 appear_then_click 落空即判定失败；且
WorkerJuu 默认位于列表末尾，未完成体力排序就直接查找会误判不可用。

与上游原测试文件的差异（本地化改写）：

- 上游同文件内的「角色确认补点」测试依赖本地不存在的
  ISLAND_CHARACTER_CONFIRM_RETRY_WAIT（更早的上游功能，未移植），
  故只保留筛选流程相关用例；
- StubIsland 只绑定筛选路径用到的方法，不绑定确认 helper。
"""
import unittest
from types import MethodType
from unittest.mock import Mock

import numpy as np

import module.base.timer as timer_module
from module.base.utils import load_image, random_rectangle_point
from module.island.assets import (
    ISLAND_SELECT_CHARACTER_CHECK,
    ISLAND_SELECT_PRODUCT_CHECK,
    POST_ADD_ORDER,
    SELECT_UI_CONFIRM,
)
from module.island.island_select_character import SelectCharacter
from module.island_select_character.assets import (
    SELECT_CHARACTER_FILTER,
    SELECT_CHARACTER_FILTER_CONFIRM,
    SELECT_CHARACTER_FILTER_STAMINA,
)


def build_frame(*assets):
    """叠加资源图，还原“页面上存在这些元素”的测试截图。"""
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    for asset in assets:
        frame = np.maximum(frame, load_image(asset.file))
    return frame


CHARACTER_PAGE = build_frame(ISLAND_SELECT_CHARACTER_CHECK, SELECT_UI_CONFIRM)
CHARACTER_PAGE_READY = build_frame(
    ISLAND_SELECT_CHARACTER_CHECK, SELECT_UI_CONFIRM, SELECT_CHARACTER_FILTER,
)
CHARACTER_FILTER_POPUP = build_frame(
    SELECT_CHARACTER_FILTER_STAMINA, SELECT_CHARACTER_FILTER_CONFIRM,
)
PRODUCT_PAGE = build_frame(ISLAND_SELECT_PRODUCT_CHECK, POST_ADD_ORDER)


class FakeClock:
    def __init__(self):
        # 起点取正值：Timer.start() 以 _start <= 0 判定“未启动”
        self.now = 1000.0

    def advance(self, seconds):
        self.now += seconds


class FakeDevice:
    """按时间线提供截图，截图与点击都推进虚拟时间。"""

    def __init__(self, clock, timeline, screenshot_cost=0.4, click_cost=0.2):
        self.clock = clock
        self.t0 = clock.now
        self.timeline = timeline
        self.screenshot_cost = screenshot_cost
        self.click_cost = click_cost
        self.image = timeline(0.0)
        self.clicks = []

    def screenshot(self):
        self.clock.advance(self.screenshot_cost)
        self.image = self.timeline(self.clock.now - self.t0)
        return self.image

    def click(self, button, control_check=True):
        point = random_rectangle_point(button.button)
        self.clicks.append((round(self.clock.now - self.t0, 2), button.name, point))
        self.clock.advance(self.click_cost)

    def sleep(self, seconds):
        self.clock.advance(seconds)

    def stuck_record_add(self, button):
        return None


class FilterFlowDevice(FakeDevice):
    """模拟角色页先出现、筛选按钮后渲染，并点击推进筛选弹窗。"""

    def __init__(self, clock):
        super().__init__(clock, lambda _: CHARACTER_PAGE)
        self.screenshot_count = 0

    def screenshot(self):
        self.clock.advance(self.screenshot_cost)
        self.screenshot_count += 1
        clicked = {name for _, name, _ in self.clicks}
        if "SELECT_CHARACTER_FILTER_CONFIRM" in clicked:
            self.image = CHARACTER_PAGE_READY
        elif "SELECT_CHARACTER_FILTER" in clicked:
            self.image = CHARACTER_FILTER_POPUP
        elif self.screenshot_count >= 2:
            self.image = CHARACTER_PAGE_READY
        else:
            self.image = CHARACTER_PAGE
        return self.image


class StubIsland:
    """绑定真实筛选逻辑的测试替身。"""

    select_character_filter = SelectCharacter.select_character_filter
    _select_first_available_character = SelectCharacter._select_first_available_character
    loop = SelectCharacter.loop

    # read_run_param 对 None config 回退默认值,测试默认使用内置节奏
    config = None

    def __init__(self, device):
        self.device = device

    def appear(self, button, offset=0, interval=0, similarity=0.85, threshold=10):
        if interval:
            timer_module.time()
        if isinstance(offset, tuple):
            return bool(button.match(self.device.image, offset=offset, similarity=similarity))
        return bool(button.appear_on(self.device.image, threshold=threshold))

    def appear_then_click(self, button, offset=0, interval=0, similarity=0.85, threshold=10):
        if self.appear(
                button,
                offset=offset,
                interval=interval,
                similarity=similarity,
                threshold=threshold,
        ):
            self.device.click(button)
            return True
        return False

    def interval_clear(self, button, interval=3):
        return None

    def find_specific_character(self, screenshot, character_name="WorkerJuu"):
        return (0, 0)


class CharacterFilterFlowTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self._real_time = timer_module.time
        timer_module.time = lambda: self.clock.now

    def tearDown(self):
        timer_module.time = self._real_time

    def bind_filter_helper(self, device):
        stub = StubIsland(device)
        for name in ("select_character_filter", "_select_first_available_character"):
            setattr(stub, name, MethodType(getattr(SelectCharacter, name), stub))
        return stub

    def test_filter_waits_until_button_is_rendered(self):
        """首帧只有角色页标题时不得判定筛选失败，应等按钮出现后继续。"""
        device = FilterFlowDevice(self.clock)
        stub = self.bind_filter_helper(device)

        self.assertTrue(stub.select_character_filter())
        self.assertEqual(
            [name for _, name, _ in device.clicks],
            [
                "SELECT_CHARACTER_FILTER",
                "SELECT_CHARACTER_FILTER_STAMINA",
                "SELECT_CHARACTER_FILTER_CONFIRM",
            ],
        )
        self.assertGreaterEqual(device.screenshot_count, 3)

    def test_workerjuu_requires_filter_before_lookup(self):
        """WorkerJuu 默认在列表末尾，必须先完成体力筛选再查找。"""
        device = FilterFlowDevice(self.clock)
        stub = self.bind_filter_helper(device)
        stub.select_character_filter = Mock(return_value=True)
        stub.find_specific_character = Mock(return_value=(0, 0))

        self.assertEqual(stub._select_first_available_character(["WorkerJuu"]), (0, 0))
        stub.select_character_filter.assert_called_once_with()
        stub.find_specific_character.assert_called()

    def test_workerjuu_lookup_is_skipped_when_filter_fails(self):
        """筛选界面失败时不能绕过筛选直接查找，避免误报可用位置。"""
        device = FilterFlowDevice(self.clock)
        stub = self.bind_filter_helper(device)
        stub.select_character_filter = Mock(return_value=False)
        stub.find_specific_character = Mock(return_value=(0, 0))

        self.assertIsNone(stub._select_first_available_character(["WorkerJuu"]))
        stub.find_specific_character.assert_not_called()


if __name__ == '__main__':
    unittest.main()
