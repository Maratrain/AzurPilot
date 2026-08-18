"""情绪管理系统。

追踪和管理舰队的情绪值（心情值）。碧蓝航线中，舰船在战斗中会消耗情绪，
情绪过低会导致经验加成失效、出现负面表情等。情绪通过以下方式恢复：
- 港区休息（不在后宅）：每 6 分钟恢复 20 点
- 后宅一楼：每 6 分钟恢复 40 点
- 后宅二楼：每 6 分钟恢复 50 点
- 誓约加成：额外 +10 点/6分钟
- 温泉加成：额外 +10 点/6分钟

情绪控制策略：
- 保持开心加成（>120）：最大化经验加成
- 防止绿脸（>40）：避免负面效果
- 防止黄脸（>30）：避免严重负面效果
- 防止红脸（>2）：最低限度保护

游戏客户端存在已知 bug：长时间运行后情绪计算不准确，需要定期重启。
"""

from datetime import datetime, timedelta
from time import sleep

import numpy as np

from module.base.decorator import cached_property
from module.base.utils import random_normal_distribution_int
from module.config.config import AzurLaneConfig
from module.config.time_source import now as current_time
from module.exception import ScriptEnd, ScriptError, RequestHumanTakeover
from module.logger import logger

# 情绪控制阈值：当情绪低于此值时触发等待/延迟
DIC_LIMIT = {
    'keep_exp_bonus': 120,     # 保持经验加成（心情开心）
    'prevent_green_face': 40,  # 防止绿脸
    'prevent_yellow_face': 30, # 防止黄脸
    'prevent_red_face': 2,     # 防止红脸
}
# 情绪恢复速度：每 6 分钟恢复的点数
DIC_RECOVER = {
    'not_in_dormitory': 20,    # 港区休息
    'dormitory_floor_1': 40,   # 后宅一楼
    'dormitory_floor_2': 50,   # 后宅二楼
}
# 情绪上限
DIC_RECOVER_MAX = {
    'not_in_dormitory': 119,
    'dormitory_floor_1': 150,
    'dormitory_floor_2': 150,
}
OATH_RECOVER = 10    # 誓约额外恢复速度
ONSEN_RECOVER = 10   # 温泉额外恢复速度


class FleetEmotion:
    """单个舰队的情绪追踪器。

    管理一个舰队的情绪值、恢复速度和控制阈值。
    支持独立配置和公海舰队（Public Fleet）模式。

    Attributes:
        config (AzurLaneConfig): 配置对象。
        fleet (str): 舰队索引（1、2 或 'Public'）。
        current (int): 当前计算的情绪值。
    """

    def __init__(self, config, fleet):
        """
        Args:
            config (AzurLaneConfig):
            fleet (str): 舰队索引。
        """
        self.config = config
        self.fleet = fleet
        self.current = 0

    @property
    def _key_prefix(self):
        if self.fleet == 'Public':
            return 'PublicEmotion_Fleet'
        return f'Emotion_Fleet{self.fleet}'

    @property
    def value(self):
        """
        Returns:
            int: 0 到 150。
        """
        return getattr(self.config, f'{self._key_prefix}Value')

    @property
    def value_name(self):
        """
        Returns:
            str:
        """
        return f'{self._key_prefix}Value'

    @property
    def record(self):
        """
        Returns:
            datetime.datetime:
        """
        return getattr(self.config, f'{self._key_prefix}Record')

    @property
    def recover(self):
        """
        Returns:
            str: not_in_dormitory、dormitory_floor_1、dormitory_floor_2。
        """
        return getattr(self.config, f'{self._key_prefix}Recover')

    @property
    def control(self):
        """
        Returns:
            str: keep_exp_bonus、prevent_green_face、prevent_yellow_face、prevent_red_face。
        """
        return getattr(self.config, f'{self._key_prefix}Control')

    @property
    def oath(self):
        """
        Returns:
            bool: 是否所有舰船已誓约。
        """
        return getattr(self.config, f'{self._key_prefix}Oath')

    @property
    def onsen(self):
        """
        Returns:
            bool: 是否所有舰船在温泉中。
        """
        return getattr(self.config, f'{self._key_prefix}Onsen')

    @property
    def speed(self):
        """
        Returns:
            int: 每 6 分钟的恢复速度。
        """
        speed = DIC_RECOVER[self.recover]
        if self.oath:
            speed += OATH_RECOVER
        if self.onsen:
            speed += ONSEN_RECOVER
        return speed // 10

    @property
    def limit(self):
        """
        Returns:
            int: 情绪控制的最低阈值。
        """
        return DIC_LIMIT[self.control]

    @property
    def max(self):
        """
        Returns:
            int: 最大情绪值。
        """
        return DIC_RECOVER_MAX[self.recover]

    def update(self):
        """根据实际经过时间计算情绪恢复。

        使用连续时间恢复计算，保留浮点恢复量以累积分数部分。
        游戏服务端按实际经过时间精确计算恢复，每6分钟恢复speed点。
        使用 int() 截断恢复量的整数部分，未满1点的余数通过
        _fractional_seconds 保留，由 record() 回扣到 Record 时间戳中，
        确保余数可跨次累积。int() 截断会导致计算值略低于实际值，
        符合情绪控制的安全方向（宁可低估也不高估）。
        """
        time_diff = current_time().timestamp() - self.record.timestamp()
        time_diff = max(time_diff, 0)
        # speed 为每360秒的恢复量，换算为每秒恢复 speed/360 点
        recovery = self.speed * time_diff / 360
        self.current = min(max(self.value, 0) + int(recovery), self.max)
        # 保留未满1点的恢复余数对应的秒数，用于 record() 回扣
        self._fractional_seconds = recovery - int(recovery)

    def get_recovered(self, expected_reduce=0):
        """计算情绪恢复到控制阈值的时间。

        Args:
            expected_reduce (int): 预期的情绪减少量。

        Returns:
            datetime.datetime: 情绪 >= 控制阈值的时间。如果已经恢复，则返回过去的时间。
        """
        if self.control == 'keep_exp_bonus' and self.recover == 'not_in_dormitory':
            logger.critical(f'[战斗] 舰队 {self.fleet} 的情绪控制设置为"保持开心加成"，且恢复地点设置为"港区"，两者不能同时使用，请检查情绪设置')
            raise RequestHumanTakeover
        # 在 14-4 使用双倍经验书时，预期情绪减少为 32，无法保持开心加成（>120）
        # 否则会导致无限任务延迟
        if self.control == 'keep_exp_bonus' and expected_reduce >= 29:
            expected_reduce = 29
            logger.info(f'[情绪-舰队] 舰队 {self.fleet} 预期扣减限制为29 '
                        f'当情绪控制="保持快乐奖励"时')

        emotion_needed = self.limit + expected_reduce - self.current
        if emotion_needed <= 0:
            return current_time()
        # speed 为每360秒的恢复量，换算恢复所需秒数
        seconds_needed = emotion_needed * 360 / self.speed
        return current_time() + timedelta(seconds=seconds_needed)

class Emotion:
    """情绪管理主类。

    编排两个舰队（和可选的公海舰队）的情绪追踪、等待和扣减。
    在战役开始前检查情绪是否足够，在战斗后扣减情绪值，
    并在情绪不足时延迟任务执行。

    Attributes:
        total_reduced (int): 本轮运行中累计扣减的情绪值，用于触发客户端 bug 重启。
        map_is_2x_book (bool): 是否使用二倍经验书（影响情绪扣减量）。
        fleet_1 (FleetEmotion): 第一舰队的情绪追踪器。
        fleet_2 (FleetEmotion): 第二舰队的情绪追踪器。
        using_public (bool): 是否使用公海舰队统一情绪管理。
    """
    total_reduced = 0
    map_is_2x_book = False

    def __init__(self, config):
        """
        Args:
            config (AzurLaneConfig): 配置对象。
        """
        self.config = config
        self.fleet_1 = FleetEmotion(self.config, fleet=1)
        self.fleet_2 = FleetEmotion(self.config, fleet=2)
        self.fleets = [self.fleet_1, self.fleet_2]
        self.using_public = self._handle_public()
    
    def _handle_public(self):
        if not getattr(self.config, 'PublicEmotion_Enable'):
            return False
        
        tasks = getattr(self.config, 'PublicEmotion_Tasks')

        if not tasks:
            return False

        tasks = [task.strip() for task in tasks.split(',')]

        if self.config.task.command not in tasks:
            return False

        self.public_fleet = FleetEmotion(self.config, fleet='Public')
        return True

    @property
    def is_calculate(self):
        return 'calculate' in self.config.Emotion_Mode

    @property
    def is_ignore(self):
        return 'ignore' in self.config.Emotion_Mode

    def update(self):
        """更新情绪值。应在执行任何操作之前调用。"""
        if self.using_public:
            self.public_fleet.update()
            return
        
        for fleet in self.fleets:
            fleet.update()

    def record(self):
        """将当前情绪值保存到配置中。

        每次调用都更新 Value 和 Record 时间戳，确保不会因
        recovery + reduce 恰好使 value 不变时漏更新时间戳，
        导致下次 update() 从旧时间戳重复计算已消费的恢复量。

        Record 时间戳回扣 fractional_seconds 对应的等效秒数，
        使未满1点的恢复余数可在下次 update() 时继续累积。

        注意：FleetEmotion.value 和 FleetEmotion.record 是 @property，
        从 self.config 实时读取。setattr 到 config 后属性自动更新，无需手动赋值。
        """
        if self.using_public:
            fleet = self.public_fleet
            new_value = fleet.current
            record_time = current_time().replace(microsecond=0)
            fractional = getattr(fleet, '_fractional_seconds', 0)
            if fractional > 0:
                # 回扣 fractional_seconds 对应的秒数
                record_time = record_time - timedelta(seconds=fractional * 360 / fleet.speed)
            with self.config.multi_set():
                setattr(self.config, fleet.value_name, new_value)
                setattr(self.config, fleet.value_name.replace('Value', 'Record'), record_time)
            return

        with self.config.multi_set():
            for fleet in self.fleets:
                new_value = fleet.current
                record_time = current_time().replace(microsecond=0)
                fractional = getattr(fleet, '_fractional_seconds', 0)
                if fractional > 0:
                    record_time = record_time - timedelta(seconds=fractional * 360 / fleet.speed)
                setattr(self.config, fleet.value_name, new_value)
                setattr(self.config, fleet.value_name.replace('Value', 'Record'), record_time)

    def show(self):
        """显示当前计算的心情值（含时间恢复），而非上次保存值。"""
        if self.using_public:
            logger.attr(f'情绪公海舰队', self.public_fleet.current)
            return

        for fleet in self.fleets:
            logger.attr(f'情绪舰队_{fleet.fleet}', fleet.current)

    @property
    def reduce_per_battle(self):
        if self.map_is_2x_book:
            return 4
        else:
            return 2

    @property
    def reduce_per_battle_before_entering(self):
        if self.map_is_2x_book:
            return 4
        elif self.config.Campaign_Use2xBook:
            return 4
        else:
            return 2
    
    @property
    def reduce_shipwreck(self):
        return 10

    def _check_reduce(self, battle):
        """检查战斗带来的情绪减少。

        Returns:
            recovered (datetime): 预期恢复时间。
            delay (bool): 是否需要延迟。
        """
        if self.using_public:
            reduce = battle * self.reduce_per_battle_before_entering
            logger.info(f'[情绪-检查] 预期情绪扣减: {reduce}')

            self.update()
            self.record()
            self.show()
            recovered = self.public_fleet.get_recovered(reduce)
            delay = recovered > current_time()
            return recovered, delay

        method = self.config.Fleet_FleetOrder

        if method == 'fleet1_mob_fleet2_boss':
            battle = (battle - 1, 1)
        elif method == 'fleet1_boss_fleet2_mob':
            battle = (1, battle - 1)
        elif method == 'fleet1_all_fleet2_standby':
            battle = (battle, 0)
        elif method == 'fleet1_standby_fleet2_all':
            battle = (0, battle)
        else:
            raise ScriptError(f'Unknown fleet order: {method}')

        battle = tuple(np.array(battle) * self.reduce_per_battle_before_entering)
        logger.info(f'[情绪-检查] 预期情绪扣减: {battle}')

        self.update()
        self.record()
        self.show()
        recovered = max([f.get_recovered(b) for f, b in zip(self.fleets, battle)])
        delay = recovered > current_time()
        return recovered, delay

    def check_reduce(self, battle):
        """进入战役前检查情绪。

        Args:
            battle (int): 本次战役中的战斗次数。

        Raise:
            ScriptEnd: 延迟当前任务以防止未来的情绪控制问题。
        """
        if not self.is_calculate:
            return

        recovered, delay = self._check_reduce(battle)
        if delay:
            logger.info('[情绪-延迟] 延迟当前任务以防止未来的情绪控制问题')
            self.config.task_delay(target=recovered)
            raise ScriptEnd('[情绪-延迟] 情绪控制')

    def wait(self, fleet_index):
        """等待指定舰队的情绪恢复。应在进入任何战斗之前调用。

        Args:
            fleet_index (int): 舰队编号，1 或 2。
        """
        self.update()
        self.record()
        self.show()
        if self.using_public:
            fleet = self.public_fleet
        else:
            fleet = self.fleets[fleet_index - 1]

        recovered = fleet.get_recovered(expected_reduce=self.reduce_per_battle)
        if recovered > current_time():
            logger.hr('情绪等待')
            if self.using_public:
                logger.info(f'[情绪-等待] 公海舰队情绪将恢复到 {fleet.limit}，时间 {recovered}')
            else:
                logger.info(f'[情绪-等待] 舰队 {fleet_index} 情绪将恢复到 {fleet.limit}，时间 {recovered}')

            while 1:
                if current_time() > recovered:
                    break

                logger.attr('等待直到', recovered)
                sleep(60)

    def reduce(self, fleet_index, shipwreck=False):
        """减少指定舰队的情绪值。应在战斗执行完成后调用。
        服务端在战斗加载完成后即扣减情绪。

        Args:
            fleet_index (int): 舰队编号，1 或 2。
            shipwreck (bool): 舰队是否遭遇船难。
        """
        logger.hr('情绪扣减')
        self.update()

        if self.using_public:
            fleet = self.public_fleet
        else:
            fleet = self.fleets[fleet_index - 1]

        if not shipwreck:
            fleet.current -= self.reduce_per_battle
            self.total_reduced += self.reduce_per_battle
        else:
            fleet.current -= self.reduce_shipwreck
            self.total_reduced += self.reduce_shipwreck
        self.record()
        self.show()

    def emergency_reset(self):
        """心情清零保底。计算模式下出现红脸弹窗时调用。

        将所有舰队的心情值重置为0，强制下次任务等待心情恢复。
        这是计算模式下的异常保底措施，正常情况下不应被调用——
        计算模式会在进入战役前预检心情并延迟任务，红脸弹窗仅在
        ALAS计算错误或用户手动操作后才可能出现。

        重置内容：
        - FleetEmotion.current 设为 0
        - config 中的 Value 设为 0
        - config 中的 Record 设为当前时间（从0开始恢复计时）
        - _fractional_seconds 清零（丢弃未满1点的恢复余数）
        """
        if self.using_public:
            fleets = [self.public_fleet]
        else:
            fleets = self.fleets

        with self.config.multi_set():
            for fleet in fleets:
                fleet.current = 0
                fleet._fractional_seconds = 0
                record_time = current_time().replace(microsecond=0)
                setattr(self.config, fleet.value_name, 0)
                setattr(self.config, fleet.value_name.replace('Value', 'Record'),
                        record_time)
        logger.info('[心情-保底] 已将所有舰队心情清零')

    @cached_property
    def bug_threshold(self):
        """
        Returns:
            int: 情绪 bug 触发阈值。
        """
        return random_normal_distribution_int(55, 105, n=2)

    def bug_threshold_reset(self):
        """情绪 bug 触发后调用此方法重置阈值。"""
        del self.__dict__['bug_threshold']

    def triggered_bug(self):
        """检测碧蓝航线客户端情绪计算 bug。
        客户端在长时间运行后无法正确计算情绪，需要重启游戏客户端使其更新。
        """
        logger.attr('情绪Bug', f'{self.total_reduced}/{self.bug_threshold}')
        if self.total_reduced >= self.bug_threshold:
            logger.info('[情绪-Bug] 碧蓝航线客户端未正确计算情绪，这是一个Bug。'
                        '长时间运行后，需要重启游戏客户端让客户端更新情绪。')
            self.total_reduced = 0
            self.bug_threshold_reset()
            return True
        else:
            return False
