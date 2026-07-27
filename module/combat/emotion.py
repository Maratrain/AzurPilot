from datetime import datetime
from time import sleep

import numpy as np

from module.base.decorator import cached_property
from module.base.utils import random_normal_distribution_int
from module.config.config import AzurLaneConfig
from module.config.time_source import now as current_time
from module.exception import ScriptEnd, ScriptError, RequestHumanTakeover
from module.logger import logger

DIC_LIMIT = {
    'keep_exp_bonus': 120,
    'prevent_green_face': 40,
    'prevent_yellow_face': 30,
    'prevent_red_face': 2,
}
DIC_RECOVER = {
    'not_in_dormitory': 20,
    'dormitory_floor_1': 40,
    'dormitory_floor_2': 50,
}
DIC_RECOVER_MAX = {
    'not_in_dormitory': 119,
    'dormitory_floor_1': 150,
    'dormitory_floor_2': 150,
}
OATH_RECOVER = 10
ONSEN_RECOVER = 10


class FleetEmotion:
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
        旧方法用 int() 截断恢复量，每次 record() 重置时间戳后，
        未满1点的恢复余数被丢弃，长时间运行导致严重低估。
        现改为 floor() 取整保留整数部分，同时 record() 仅在整数变化时
        重置时间戳并回扣分数秒，确保余数可跨次累积。
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
            logger.critical(f'[战斗] 舰队 {self.fleet} 的情绪控制设置为”保持开心加成”，且恢复地点设置为”港区”，两者不能同时使用，请检查情绪设置')
            raise RequestHumanTakeover
        # 在 14-4 使用双倍经验书时，预期情绪减少为 32，无法保持开心加成（>120）
        # 否则会导致无限任务延迟
        if self.control == 'keep_exp_bonus' and expected_reduce >= 29:
            expected_reduce = 29
            logger.info(f'Fleet {self.fleet} expected_reduce is limited to 29 '
                        f'when Emotion Control=\"Keep Happy Bonus\"')

        recover_count = (self.limit + expected_reduce - self.current) // self.speed
        recovered = (int(current_time().timestamp()) // 360 + recover_count + 1) * 360
        return datetime.fromtimestamp(recovered)

class Emotion:
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

        仅在心情整数值发生变化时更新 Record 时间戳，
        并将 Record 回扣 fractional_seconds 对应的等效秒数，
        使未满1点的恢复余数可在下次 update() 时继续累积。

        注意：FleetEmotion.value 和 FleetEmotion.record 是 @property，
        从 self.config 实时读取。setattr 到 config 后属性自动更新，无需手动赋值。
        """
        if self.using_public:
            fleet = self.public_fleet
            old_value = fleet.value
            new_value = fleet.current
            # 仅在整数变化时重置时间戳，回扣分数秒
            if new_value != old_value:
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
                old_value = fleet.value
                new_value = fleet.current
                if new_value != old_value:
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
            logger.info(f'Expect emotion reduce: {reduce}')

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
        logger.info(f'Expect emotion reduce: {battle}')

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
            logger.info('Delay current task to prevent emotion control in the future')
            self.config.task_delay(target=recovered)
            raise ScriptEnd('Emotion control')

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
            logger.hr('Emotion wait')
            if self.using_public:
                logger.info(f'Emotion of PublicFleet will recover to {fleet.limit} at {recovered}')
            else:
                logger.info(f'Emotion of fleet {fleet_index} will recover to {fleet.limit} at {recovered}')

            while 1:
                if current_time() > recovered:
                    break

                logger.attr('Wait until', recovered)
                sleep(60)

    def reduce(self, fleet_index, shipwreck=False):
        """减少指定舰队的情绪值。应在战斗执行完成后调用。
        服务端在战斗加载完成后即扣减情绪。

        Args:
            fleet_index (int): 舰队编号，1 或 2。
            shipwreck (bool): 舰队是否遭遇船难。
        """
        logger.hr('Emotion reduce')
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
        logger.attr('Emotion_bug', f'{self.total_reduced}/{self.bug_threshold}')
        if self.total_reduced >= self.bug_threshold:
            logger.info('Azur Lane client does not calculate emotion correctly, which is a bug. '
                        'After a long run, we have to restart game client and let the client update it.')
            self.total_reduced = 0
            self.bug_threshold_reset()
            return True
        else:
            return False
