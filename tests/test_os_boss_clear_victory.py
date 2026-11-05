"""深渊海域 boss_clear 兜底判据（塞壬战胜利）与 S 评价延迟分路的回归测试。

背景（2026-11-04 22:47~22:49 用户日志）：深渊坐标进图后 Boss 被正常打掉
（SIREN_PREPARATION → 战斗评价 S → 6 次掉落），但战后雷达上既没有敌人也
没有问号（深渊海域浓雾叠加视野/雷达覆盖不足）。`boss_clear()` 唯一成功判据
是「雷达上还有问号」，于是继续轮换舰队，每支舰队只点一次就「无Boss可前往」，
4 支舰队的行动机会在 10 秒内全部空耗，最终误报
`无法击败boss，舰队已耗尽` → `RequestHumanTakeover` → 脚本退出。

修复后：
1. 战斗侧把「打了塞壬强敌」和「战斗胜利」记下来（`os_boss_defeated()`），
   `boss_clear()` 在雷达判据失效时用它兜底，但**只在非月度 Boss 生效**
   （月度 Boss 需要多支舰队接力消耗，打赢一场不等于清完）。
2. 顺带修「打完深渊在战斗胜利页停 20 秒」：那个延迟本是给自律寻敌
   （游戏会自行推进 S 评价页）用的，Boss/手动作战路径不该跟着等。
   手动作战改为**零等待**——`BATTLE_STATUS_S`（评价页）出现本身就是战斗
   结束的信号，检测到即点击：掉落截图由 DropImage.handle_add() 自带的
   WAIT_BEFORE_SAVING_SCREEN_SHOT（默认 1s）保证，点击节流由
   battle_status_click_interval（OS 路径恒为 7s）保证。
   注：曾试过「战果页画面静止即点」，但战果页自带光效/闪动，整帧永远不会
   完全一致，该判据在真机上等于永远不成立，已废弃——测试里钉死不许回退。
"""
import ast
import inspect
import textwrap
import unittest
from types import MethodType, SimpleNamespace

import numpy as np

from module.os.fleet import BossFleet, OSFleet
from module.os_combat.combat import Combat


class TestOsBossDefeatedFlag(unittest.TestCase):
    """战斗结果标记的语义。"""

    def setUp(self):
        # 只测标记语义，不需要 config/device，直接绕过 __init__
        self.handler = object.__new__(Combat)
        self.handler.os_battle_result_reset()

    def test_reset_clears_both_flags(self):
        """复位后两个标记都必须为假。"""
        self.assertFalse(self.handler.os_boss_defeated())

    def test_siren_battle_without_victory_is_not_defeated(self):
        """只出现塞壬准备界面、没打出评价（战斗未结束）不算击败。"""
        self.handler._os_siren_battle = True
        self.assertFalse(self.handler.os_boss_defeated())

    def test_victory_without_siren_is_not_defeated(self):
        """打赢路过的一支普通敌舰不算击败 Boss。"""
        self.handler._os_battle_victory = True
        self.assertFalse(self.handler.os_boss_defeated())

    def test_siren_victory_is_defeated(self):
        """塞壬强敌战胜利即视为 Boss 已被打掉。"""
        self.handler._os_siren_battle = True
        self.handler._os_battle_victory = True
        self.assertTrue(self.handler.os_boss_defeated())

    def test_flags_survive_on_fresh_object_without_reset(self):
        """未调用过复位方法时也不能抛异常（MRO 里别的类可能先用到）。"""
        handler = object.__new__(Combat)
        self.assertFalse(handler.os_boss_defeated())


class FakeClickDevice:
    """记录点击与洪水豁免的 device 替身。"""

    def __init__(self):
        self.image = None
        self.clicks = 0
        self.removed = 0

    def click(self, button):
        self.clicks += 1

    def sleep(self, *args, **kwargs):
        return None

    def screenshot(self):
        return None

    def click_record_remove(self, button):
        self.removed += 1


class FakeRadar:
    """只提供 `select()` 的雷达替身。"""

    def __init__(self, questions=()):
        self.questions = list(questions)

    def select(self, **kwargs):
        if kwargs.get('is_enemy'):
            # 月度 Boss 分支要求「雷达已看见敌人」，否则会去追问号找 Boss
            return ['enemy']
        return list(self.questions)


class FakeDrop:
    """掉落记录替身。"""

    def __init__(self):
        self.count = 0
        self.added = 0

    def add(self, image):
        self.added += 1


class FakeStat:
    def __init__(self, drop):
        self.drop = drop

    def new(self, **kwargs):
        drop = self.drop

        class _Ctx:
            def __enter__(self_inner):
                return drop

            def __exit__(self_inner, *exc):
                return False

        return _Ctx()


class FakeBossClear:
    """`boss_clear()` 的最小运行环境。

    `boss_goto` 被替换成"模拟这一轮进攻发生了什么"，用 `battle` 描述:
    None 表示什么都没发生（打不到 Boss），'siren' 表示打了塞壬强敌并取胜，
    'normal' 表示只打赢了路过的一支普通敌舰。
    """

    def __init__(self, fleets, questions=(), battles=None, drop=None):
        # opsi_drop_record() 会读 config.DropRecord_<Task>，这里给个关闭值
        # （本地为单开关 DropRecord_OpsiRecord，与上游按任务拆分的开关不同）
        self.config = SimpleNamespace(
            task=SimpleNamespace(command='OpsiAbyssal'),
            DropRecord_OpsiRecord=False,
        )
        self.stat = FakeStat(drop or FakeDrop())
        self.drop = self.stat.drop
        self.radar = FakeRadar(questions=questions)
        self._fleets = fleets
        self._battles = list(battles or [])
        self.boss_goto_calls = []
        self.boss_leave_calls = 0
        self.map_exit_calls = 0
        self.reset_calls = 0
        self.device = SimpleNamespace(image=None)

    # --- 被测方法用到的成员 ---
    def parse_fleet_filter(self):
        return self._fleets

    def fleet_set(self, index):
        return True

    def handle_os_map_fleet_lock(self, enable=True):
        return None

    def fleet_low_resolve_appear(self):
        return False

    def boss_goto(self, **kwargs):
        self.boss_goto_calls.append(kwargs)
        # 只有「进攻 Boss」（location=(0, 0)）会打这一场仗；
        # 待命移动（location=standby_loca）不消耗战斗脚本
        if kwargs.get('location') != (0, 0):
            return
        battle = self._battles.pop(0) if self._battles else None
        if battle == 'siren':
            self._os_siren_battle = True
            self._os_battle_victory = True
        elif battle == 'normal':
            self._os_battle_victory = True

    def predict_radar(self):
        return None

    def relative_goto(self, **kwargs):
        """月度 Boss 的「先找 Boss 再打」分支用不到，这里只为防御性占位。"""
        return None

    def boss_leave(self):
        self.boss_leave_calls += 1

    def map_exit(self):
        self.map_exit_calls += 1
        return True

    def os_battle_result_reset(self):
        self.reset_calls += 1
        self._os_siren_battle = False
        self._os_battle_victory = False

    def os_boss_defeated(self):
        """真实的判据实现直接借过来，避免测试复制一份判据逻辑。"""
        return Combat.os_boss_defeated(self)


def bind_boss_clear(target):
    """把真实的 `boss_clear` 绑到替身上。"""
    target.boss_clear = MethodType(OSFleet.boss_clear, target)
    return target


class TestBossClearJudgement(unittest.TestCase):
    """`boss_clear()` 的成功判据。"""

    def test_radar_question_still_clears_boss(self):
        """原判据不变：战后雷达出现问号即清除。"""
        target = bind_boss_clear(FakeBossClear([BossFleet(1)], questions=[object()]))
        self.assertTrue(target.boss_clear(has_fleet_step=True))
        self.assertEqual(target.map_exit_calls, 1)
        self.assertEqual(target.boss_leave_calls, 0)

    def test_siren_victory_clears_boss_without_question(self):
        """2026-11-04 事故场景：雷达无问号但本轮塞壬战已胜利，必须判定清除。"""
        target = bind_boss_clear(FakeBossClear([BossFleet(1)], battles=['siren']))
        self.assertTrue(
            target.boss_clear(has_fleet_step=True),
            '塞壬战已胜利时仍判失败，会重现 4 支舰队空耗 + 误报舰队耗尽的事故',
        )
        self.assertEqual(target.map_exit_calls, 1)
        self.assertEqual(target.boss_leave_calls, 0, '已判定清除就不该再离开 Boss 换舰队')

    def test_normal_battle_victory_does_not_clear_boss(self):
        """只打赢路过的一支普通敌舰不能当成 Boss 已清除。"""
        target = bind_boss_clear(FakeBossClear([BossFleet(1)], battles=['normal']))
        self.assertFalse(target.boss_clear(has_fleet_step=True))
        self.assertEqual(target.map_exit_calls, 0)

    def test_nothing_happens_keeps_failure(self):
        """既无问号也没打仗：保持原有失败语义（报舰队耗尽）。"""
        target = bind_boss_clear(FakeBossClear([BossFleet(1)]))
        self.assertFalse(target.boss_clear(has_fleet_step=True))
        self.assertEqual(target.map_exit_calls, 0)

    def test_month_boss_ignores_siren_victory(self):
        """月度 Boss 要多支舰队接力消耗，打赢一场不等于清完，兜底判据必须不生效。"""
        target = bind_boss_clear(FakeBossClear([BossFleet(1)], battles=['siren']))
        self.assertFalse(
            target.boss_clear(has_fleet_step=True, is_month=True),
            '月度 Boss 用塞壬战胜利兜底会提前结束，把剩下的阶段留在场上',
        )
        self.assertEqual(target.map_exit_calls, 0)

    def test_result_flag_reset_before_each_fleet(self):
        """每支舰队进攻前都要复位结果标记，否则上一支的战果会污染下一轮判定。"""
        fleet1 = BossFleet(1)
        fleet1.standby_loca = (-1, -1)
        fleet2 = BossFleet(2)
        fleet2.standby_loca = (0, 0)
        target = bind_boss_clear(FakeBossClear([fleet1, fleet2], battles=['normal', 'siren']))
        self.assertTrue(target.boss_clear(has_fleet_step=True))
        self.assertEqual(target.reset_calls, 2, '每支舰队的进攻都要各自复位一次')
        self.assertEqual(target.map_exit_calls, 1)
        self.assertEqual(target.boss_leave_calls, 1, '第一支舰队未清掉 Boss，才需要离开换舰队')


FRAME_A = np.zeros((720, 1280, 3), dtype=np.uint8)
FRAME_B = np.full((720, 1280, 3), 255, dtype=np.uint8)


class TestSBattleStatusClickTiming(unittest.TestCase):
    """手动作战零等待（评价页出现即点），自律寻敌保持 20 秒兜底。

    手动作战的三个保障都不靠"等秒数"：
    - 掉落截图：DropImage.handle_add() 自己 sleep WAIT_BEFORE_SAVING_SCREEN_SHOT；
    - 点击节流：battle_status_click_interval（OS 路径恒为 7s），点被吞也会重试；
    - 卡死兜底：combat_status() 结算阶段 300s 超时。
    """

    def setUp(self):
        from unittest.mock import patch

        from module.base.timer import Timer
        self.Timer = Timer
        self.clock = {'t': 1000.0}
        self.handler = object.__new__(Combat)
        self.handler.device = FakeClickDevice()
        self.handler.device.image = FRAME_A
        self.handler.appear = lambda *args, **kwargs: True
        self.clicked = 0

        def fake_click(*args, **kwargs):
            self.clicked += 1
            return True

        self.handler._handle_single_battle_status = fake_click
        self._patch = patch('module.base.timer.time', lambda: self.clock['t'])
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def frame(self, image=None, timer=None):
        """喂一帧画面（模拟一次截图，推进 0.6 秒）并跑一次点击判定。"""
        if image is not None:
            self.handler.device.image = image
        self.clock['t'] += 0.6
        kwargs = {'timer': timer} if timer is not None else {}
        return self.handler._handle_auto_battle_status_s(**kwargs)

    def test_manual_path_clicks_on_first_sight(self):
        """手动作战：第一次看到评价页就点，不做任何等待。"""
        appeared, clicked = self.frame(FRAME_A)
        self.assertTrue(appeared)
        self.assertTrue(clicked, '手动作战还在等时间，等于原样保留 20 秒白等')
        self.assertEqual(self.clicked, 1)
        self.assertLess(self.clock['t'] - 1000.0, 1, '不算截图耗时就该点掉')

    def test_manual_path_ignores_page_animation(self):
        """战果页有光效/闪动，帧帧不同也必须立刻点（这正是废弃静止判定的原因）。"""
        for index in range(4):
            self.clicked = 0
            appeared, clicked = self.frame(FRAME_A if index % 2 == 0 else FRAME_B)
            self.assertTrue(clicked)

    def test_manual_path_does_not_use_battle_status_timer(self):
        """手动作战分支不得再依据任何等待判据（计时器/画面静止）拖延点击。"""
        source = inspect.getsource(Combat._handle_auto_battle_status_s)
        tree = ast.parse(textwrap.dedent(source))
        body = tree.body[0].body
        branch = next(
            node for node in body
            if isinstance(node, ast.If) and ast.unparse(node.test) == 'auto_advance'
        )
        # 手动分支 = `if auto_advance:` 的 else 块
        manual = '\n'.join(ast.unparse(node) for node in branch.orelse)
        self.assertIn('_handle_single_battle_status', manual, '手动分支必须直接点击')
        self.assertNotIn(
            'timer.reached()', manual,
            '手动作战分支又出现了"等计时器"的判据，20 秒白等会回来',
        )
        self.assertNotIn(
            'settled', source,
            '战果页自带光效，整帧永远不会一致；静止判定已被证伪，不许回退',
        )

    def test_auto_search_path_keeps_waiting(self):
        """自律寻敌路径保持原样：评价页出现也不点，只等兜底上限。"""
        timer = self.Timer(Combat.battle_status_s_autoclick_delay)
        appeared, clicked = self.frame(FRAME_A, timer=timer)
        self.assertTrue(appeared)
        self.assertFalse(clicked, '自律路径提前点击会和游戏的自动推进打架')
        self.clock['t'] += Combat.battle_status_s_autoclick_delay + 1
        appeared, clicked = self.frame(FRAME_A, timer=timer)
        self.assertTrue(clicked)

    def test_manual_click_retries_at_one_second(self):
        """点击被吞后按 1 秒重试（不是 7 秒），并豁免点击洪水统计。"""
        handler = object.__new__(Combat)
        handler.device = FakeClickDevice()
        handler.device.image = FRAME_A
        timers = {}
        clock = self.clock

        def appear(button, offset=0, interval=0, **kwargs):
            # 复刻 base.appear 的 interval 语义：首次允许，匹配成功即重置计时
            if interval:
                timer = timers.setdefault(str(button), self.Timer(interval))
                if not timer.reached():
                    return False
                timer.reset()
            return True

        handler.appear = appear

        # 引擎侧不做节流替换：走真实的 _handle_single_battle_status
        for _ in range(6):
            clock['t'] += 0.6
            handler._handle_auto_battle_status_s()

        # 3.6 秒内按 1 秒节流应该点到 4 次左右，且每次点完都做一次洪水豁免
        self.assertGreaterEqual(handler.device.clicks, 3, '1 秒重试没有生效，点空一次要等到下一轮')
        self.assertLessEqual(handler.device.clicks, 5, '节流失效，点得比 1 秒还密')
        self.assertEqual(
            handler.device.removed, handler.device.clicks,
            '每次点击后必须 click_record_remove() 豁免，否则 12 秒后会被判点击洪水',
        )

    def test_manual_click_interval_is_one_second(self):
        """确认传给评价页点击的节流是 1 秒，不是 battle_status_click_interval(7s)。"""
        calls = []
        self.handler._handle_single_battle_status = lambda *a, **k: calls.append(k) or True
        self.handler.device = FakeClickDevice()
        self.handler.device.image = FRAME_A
        self.frame()
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0].get('interval'), Combat.battle_status_s_retry_interval,
            '评价页点击必须用 1 秒重试节流，7 秒等于点空一次白等 7 秒',
        )
        self.assertLess(Combat.battle_status_s_retry_interval, 3)

    def test_page_absent_returns_false(self):
        """评价页没出现时不能误点。"""
        self.handler.appear = lambda *args, **kwargs: False
        appeared, clicked = self.frame()
        self.assertFalse(appeared)
        self.assertFalse(clicked)


class TestOsCombatResultWiring(unittest.TestCase):
    """结果标记必须真的在战斗流程里被点亮。"""

    def test_siren_preparation_sets_flag(self):
        source = inspect.getsource(Combat.combat_preparation)
        self.assertIn('_os_siren_battle = True', source)

    def test_battle_status_sets_victory(self):
        source = inspect.getsource(Combat._handle_single_battle_status)
        self.assertIn('_os_battle_victory = True', source)

    def test_combat_resets_before_battle(self):
        source = inspect.getsource(Combat.combat)
        self.assertIn('self.os_battle_result_reset()', source)


if __name__ == '__main__':
    unittest.main()
