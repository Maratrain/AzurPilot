"""退役强化状态机（_enhance_choose）的单元测试。

重点回归两个场景：
1. 等待强化材料装填动画、装备拆解弹窗加载时的正常轮询，
   不应触发"状态机循环次数过多"保护（对应线上卡死问题：
   强化在即将成功前被误中断，残留模态弹窗最终卡死重启）；
2. 状态来回切换不推进的真实 DFA 振荡，仍需被上限拦截。
"""

import unittest
from unittest.mock import Mock, PropertyMock, patch

from module.exception import GameStuckError
from module.retire import enhancement as enhancement_module
from module.retire.enhancement import Enhancement


class StepClock:
    """以循环内截图次数作为迭代时钟，复现日志中的状态时间轴。"""

    def __init__(self):
        self.n = 0

    def tick(self):
        self.n += 1
        return self.n


def build_enhancer(clock):
    """构造绕过 __init__ 的 Enhancement 实例，界面交互全部打桩。"""
    az = Enhancement.__new__(Enhancement)
    az.config = Mock()
    az.device = Mock()
    az.device.click_record = []
    az.device.screenshot = Mock(side_effect=lambda: clock.tick())
    az.equip_side_navbar_ensure = Mock(return_value=True)
    az.wait_until_appear = Mock()
    az.info_bar_count = Mock(return_value=0)
    az.handle_popup_confirm = Mock(return_value=False)
    az._enhance_confirm = Mock()
    return az


class TestEnhanceChooseStateMachine(unittest.TestCase):
    def test_survives_material_loading_and_confirm_lag(self):
        """复现线上卡死时间轴：ready 轮询装填动画 19 轮 + attempt 轮询拆解弹窗 5 轮。

        旧实现按迭代计数，第 31 次状态调用即抛 GameStuckError，
        强化在拆解弹窗即将出现前被误中断；新实现按状态迁移计数，
        全程仅 9 次迁移，应正常强化成功。
        """
        clock = StepClock()

        def fake_appear_then_click(button, **kwargs):
            name = button.name
            if name == 'ENHANCE_RECOMMEND':
                # 第 1 次点击推荐后进入装填动画，按钮隐藏 19 轮后重现并二次点击
                return clock.n in (1, 21)
            if name == 'ENHANCE_CONFIRM':
                # 确认强化一次，随后等待拆解弹窗加载
                return clock.n == 23
            return False

        def fake_appear(button, **kwargs):
            # 装备拆解弹窗在强化确认约 6 轮后才加载完成
            return button.name == 'EQUIP_CONFIRM' and clock.n >= 30

        az = build_enhancer(clock)
        az.appear_then_click = Mock(side_effect=fake_appear_then_click)
        az.appear = Mock(side_effect=fake_appear)

        with \
                patch.object(enhancement_module, 'EMPTY_ENHANCE_SLOT_PLUS') as fake_slot, \
                patch.object(Enhancement, '_retire_keep_common_cv',
                             new_callable=PropertyMock, return_value=''):
            # 第 22 轮前材料槽为空（装填动画未完成），之后材料就绪
            fake_slot.match.side_effect = lambda image, offset=None: clock.n < 22

            result, ship_count = az._enhance_choose(ship_count=5)

        self.assertTrue(result)
        self.assertEqual(ship_count, 5)
        # 强化成功后应执行收尾（拆解弹窗确认 + 奖励收取）
        az._enhance_confirm.assert_called_once()

    def test_raises_on_real_state_oscillation(self):
        """状态来回切换不推进（推荐↔就绪振荡且材料永不就绪）仍应被上限拦截。"""
        clock = StepClock()

        def fake_appear_then_click(button, **kwargs):
            # 推荐按钮始终可点，但材料槽始终为空，构成真实振荡
            return button.name == 'ENHANCE_RECOMMEND'

        az = build_enhancer(clock)
        az.appear_then_click = Mock(side_effect=fake_appear_then_click)
        az.appear = Mock(return_value=False)

        with \
                patch.object(enhancement_module, 'EMPTY_ENHANCE_SLOT_PLUS') as fake_slot, \
                patch.object(Enhancement, '_retire_keep_common_cv',
                             new_callable=PropertyMock, return_value=''):
            fake_slot.match.side_effect = lambda image, offset=None: True

            with self.assertRaises(GameStuckError):
                az._enhance_choose(ship_count=5)


if __name__ == '__main__':
    unittest.main()
