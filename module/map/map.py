"""地图探索和战斗编排模块。

整合舰队管理、路径规划和敌人优先级系统，
提供完整的地图探索和战斗编排逻辑。

核心功能：
- 敌人清除：按优先级选择并清除地图上的敌人
- 神秘格子处理：踩踏神秘格子获取道具/弹药
- Boss 战：定位并挑战 Boss
- 关卡全清：清除地图上所有可击败的敌人

敌人优先级系统：
- 通过 EnemyPriority 配置控制敌人选择策略
- 支持按敌人规模、类型、距离等因素排序
- 支持可移动敌人（塞壬）的追踪和预测

继承自 Fleet，组合了舰队管理、相机控制和战斗系统。
"""

import itertools
import re

from module.base.filter import Filter
from module.exception import MapEnemyMoved
from module.logger import logger
from module.map.fleet import Fleet
from module.map.map_grids import RoadGrids, SelectedGrids
from module.map_detection.grid_info import GridInfo

# 敌人过滤器
ENEMY_FILTER = Filter(regex=re.compile('^(.*?)$'), attr=('str',))


class Map(Fleet):
    """地图探索和战斗编排器。

    管理地图上的敌人清除、神秘格子处理和 Boss 战。
    通过敌人优先级系统智能选择下一个目标。
    """
    def clear_chosen_enemy(self, grid, expected=''):
        """
        Args:
            grid (GridInfo): 目标格子。
            expected (str): 预期结果类型。

        Returns:
            int: 是否清除了敌人。
        """
        logger.info('[地图-策略] 目标敌舰规模权重:%s' % (self.config.EnemyPriority_EnemyScaleBalanceWeight))
        logger.info('[地图-战斗] 清除敌舰: %s' % grid)
        expected = f'combat_{expected}' if expected else 'combat'
        battle_count = self.battle_count
        self.show_fleet()
        if self.emotion.is_calculate and self.config.Campaign_UseFleetLock:
            self.emotion.wait(fleet_index=self.fleet_current_index)
        self.goto(grid, expected=expected)

        self.full_scan()
        self.find_path_initial()
        self.map.show_cost()
        return self.battle_count >= battle_count

    def clear_chosen_mystery(self, grid):
        """
        Args:
            grid (GridInfo): 目标格子。
        """
        logger.info('[地图-战斗] 清除神秘点: %s' % grid)
        self.show_fleet()
        self.goto(grid, expected='mystery')
        # self.mystery_count += 1
        self.map.show_cost()

    def pick_up_ammo(self, grid=None):
        """
        Args:
            grid (GridInfo): 弹药格子，为 None 时自动选择。
        """
        if grid is None:
            grid = self.map.select(may_ammo=True)
            if not grid:
                logger.info('[地图-弹药] 地图无弹药点')
                return False
            grid = grid[0]

        if self.ammo_count > 0 and grid.is_accessible:
            logger.info('[地图-弹药] 拾取弹药: %s' % grid)
            self.goto(grid, expected='')
            self.ensure_no_info_bar()

            # self.ammo_count -= 5 - self.battle_count
            recover = 5 - self.fleet_ammo
            recover = 3 if recover > 3 else recover
            logger.attr('获得弹药', recover)

            self.ammo_count -= recover
            self.fleet_ammo += recover

    def clear_mechanism(self, grids=None):
        """
        Args:
            grids (SelectedGrids): 触发机关的格子。为 None 时选择所有机关触发器。

        Returns:
            bool: 始终返回 False，因为未清除任何敌人。
        """
        if not self.config.MAP_HAS_LAND_BASED:
            return False

        if not grids:
            grids = self.map.select(is_mechanism_trigger=True, is_mechanism_block=False)
        else:
            grids = grids.select(is_mechanism_trigger=True, is_mechanism_block=False)
        grids = self.select_grids(grids, is_accessible=True, sort=('weight', 'cost'))

        for grid in grids:
            logger.info(f'[地图-机关] 清除机关: {grid}')
            self.goto(grid)
            self.map.show_cost()
            logger.info(f'[地图-机关] 机关触发释放: {grid.mechanism_trigger}')
            logger.info(f'[地图-机关] 机关障碍释放: {grid.mechanism_block}')
            raise MapEnemyMoved

        logger.info('[地图-机关] 所有机关已清除')
        return False

    @staticmethod
    def select_grids(grids, nearby=False, is_accessible=True, scale=(), genre=(), strongest=False, weakest=False,
                     sort=('weight', 'cost'), ignore=None):
        """
        Args:
            grids (SelectedGrids): 待筛选的格子集合。
            nearby (bool): 是否仅选择附近的格子。
            is_accessible (bool): 是否仅选择可达的格子。
            scale (tuple[int], list[int]): 敌人规模，元组表示无序选择，列表表示有序选择。
            genre (tuple[str], list[str]): 敌人类型：light、main、carrier、treasure（不区分大小写）。
            strongest (bool): 是否优先选择最强敌人。
            weakest (bool): 是否优先选择最弱敌人。
            sort (tuple(str)): 排序依据。
            ignore (SelectedGrids): 需要忽略的格子。

        Returns:
            SelectedGrids: 筛选后的格子集合。
        """
        if nearby:
            grids = grids.select(is_nearby=True)
        if is_accessible:
            grids = grids.select(is_accessible=True)
        if ignore is not None:
            grids = grids.delete(grids=ignore)
        if len(scale):
            enemy = SelectedGrids([])
            for enemy_scale in scale:
                enemy = enemy.add(grids.select(enemy_scale=enemy_scale))
                if isinstance(scale, list) and enemy:
                    break
            grids = enemy
        if len(genre):
            enemy = SelectedGrids([])
            for enemy_genre in genre:
                # enemy_genre should be camel case
                enemy_genre = enemy_genre[0].upper() + enemy_genre[1:] if enemy_genre[0].islower() else enemy_genre
                enemy = enemy.add(grids.select(enemy_genre=enemy_genre))
                if isinstance(genre, list) and enemy:
                    break
            grids = enemy
        if strongest:
            for scale in [3, 2, 1, 0]:
                enemy = grids.select(enemy_scale=scale)
                if enemy:
                    grids = enemy
                    break
        if weakest:
            for scale in [1, 2, 3, 0]:
                enemy = grids.select(enemy_scale=scale)
                if enemy:
                    grids = enemy
                    break

        if grids:
            grids = grids.sort(*sort)

        return grids

    @staticmethod
    def show_select_grids(grids, **kwargs):
        length = 3
        keys = list(kwargs.keys())
        for index in range(0, len(keys), length):
            text = [f'{key}={kwargs[key]}' for key in keys[index:index + length]]
            text = ', '.join(text)
            logger.info(text)

        logger.info(f'[地图] 格子: {grids}')

    def clear_all_mystery(self, **kwargs):
        """拾取所有神秘事件的方法。

        Returns:
            bool: 始终返回 False，因为未清除任何敌人。
        """
        kwargs['sort'] = ('cost',)
        while 1:
            grids = self.map.select(is_mystery=True)
            grids = self.select_grids(grids, **kwargs)

            if not grids:
                break

            logger.hr('清除所有神秘点')
            self.show_select_grids(grids, **kwargs)
            self.clear_chosen_mystery(grids[0])

        return False

    def clear_enemy(self, **kwargs):
        """清除一个敌人的方法。如果没有合适的敌人则不做任何操作。

        Returns:
            bool: 是否清除了敌人。
        """
        grids = self.map.select(is_enemy=True, is_boss=False)

        target = self.config.EnemyPriority_EnemyScaleBalanceWeight
        if target == 'S3_enemy_first':
            kwargs['strongest'] = True
        elif target == 'S1_enemy_first':
            kwargs['weakest'] = True
        elif self.config.MAP_CLEAR_ALL_THIS_TIME:
            kwargs['strongest'] = True
        grids = self.select_grids(grids, **kwargs)

        if grids:
            logger.hr('清除敌舰')
            self.show_select_grids(grids, **kwargs)
            self.clear_chosen_enemy(grids[0])
            return True

        return False

    def clear_roadblocks(self, roads, **kwargs):
        """清除路障。

        Args:
            roads (list[RoadGrids]): 路线列表。

        Returns:
            bool: 是否清除了敌人。
        """
        grids = SelectedGrids([])
        for road in roads:
            grids = grids.add(road.roadblocks())

        target = self.config.EnemyPriority_EnemyScaleBalanceWeight
        if target == 'S3_enemy_first':
            kwargs['strongest'] = True
        elif target == 'S1_enemy_first':
            kwargs['weakest'] = True
        elif self.config.MAP_CLEAR_ALL_THIS_TIME:
            kwargs['strongest'] = True
        grids = self.select_grids(grids, **kwargs)

        if grids:
            logger.hr('清除路障')
            self.show_select_grids(grids, **kwargs)
            self.clear_chosen_enemy(grids[0])
            return True

        return False

    def clear_potential_roadblocks(self, roads, **kwargs):
        """清除潜在路障，避免只有一个格子为空的情况。

        Args:
            roads (list[RoadGrids]): 路线列表。

        Returns:
            bool: 是否清除了敌人。
        """
        grids = SelectedGrids([])
        for road in roads:
            grids = grids.add(road.potential_roadblocks())

        target = self.config.EnemyPriority_EnemyScaleBalanceWeight
        if target == 'S3_enemy_first':
            kwargs['strongest'] = True
        elif target == 'S1_enemy_first':
            kwargs['weakest'] = True
        elif self.config.MAP_CLEAR_ALL_THIS_TIME:
            kwargs['strongest'] = True
        grids = self.select_grids(grids, **kwargs)

        if grids:
            logger.hr('避开潜在路障')
            self.show_select_grids(grids, **kwargs)
            self.clear_chosen_enemy(grids[0])
            return True

        return False

    def clear_first_roadblocks(self, roads, **kwargs):
        """确保每个路障都有一个已清除的格子。

        Args:
            roads (list[RoadGrids]): 路线列表。

        Returns:
            bool: 是否清除了敌人。
        """
        grids = SelectedGrids([])
        for road in roads:
            grids = grids.add(road.first_roadblocks())

        grids = self.select_grids(grids, **kwargs)

        if grids:
            logger.hr('清除首个路障')
            self.show_select_grids(grids, **kwargs)
            self.clear_chosen_enemy(grids[0])
            return True

        return False

    def clear_grids_for_faster(self, grids, **kwargs):
        """清除部分格子以缩短行走距离。

        Args:
            grids (SelectedGrids): 待清除的格子集合。

        Returns:
            bool: 是否清除了敌人。
        """

        grids = grids.select(is_enemy=True)
        grids = self.select_grids(grids, **kwargs)

        if grids:
            logger.hr('清除格子加速')
            self.show_select_grids(grids, **kwargs)
            self.clear_chosen_enemy(grids[0])
            return True

        return False

    def clear_boss(self):
        """清除 Boss。此方法已弃用，虽然在简单地图中仍然有效。
        复杂地图推荐使用 brute_clear_boss。

        Returns:
            bool: 是否成功清除 Boss。
        """
        grids = self.map.select(is_boss=True, is_accessible=True)
        grids = grids.add(self.map.select(may_boss=True, is_caught_by_siren=True))
        logger.info('[地图-Boss] 是否Boss: %s' % grids)
        if not grids.count:
            grids = grids.add(self.map.select(may_boss=True, is_enemy=True, is_accessible=True))
            logger.warning('[地图-Boss] 未检测到Boss，使用可能的Boss格子')
            logger.info('[地图-Boss] 可能的Boss: %s' % self.map.select(may_boss=True))
            logger.info('[地图-Boss] 可能的Boss且是敌舰: %s' % self.map.select(may_boss=True, is_enemy=True))

        if grids:
            self.submarine_move_near_boss(grids[0])
            logger.hr('清除Boss')
            grids = grids.sort('weight', 'cost')
            logger.info('[地图] 格子: %s' % str(grids))
            self.clear_chosen_enemy(grids[0], expected='boss')

        logger.warning('[地图-Boss] 未检测到Boss，尝试所有Boss出生点')
        return self.clear_potential_boss()

    def capture_clear_boss(self):
        """清除 Boss 并处理大捕获地图。此方法已弃用，虽然在简单地图中仍然有效。
        复杂地图推荐使用 brute_clear_boss。
        注意：大捕获地图的简易处理方法。

        Returns:
            bool: 是否成功清除 Boss。
        """

        grids = self.map.select(is_boss=True, is_accessible=True)
        grids = grids.add(self.map.select(may_boss=True, is_caught_by_siren=True))
        logger.info('[地图-Boss] 是否Boss: %s' % grids)
        if not grids.count:
            grids = grids.add(self.map.select(may_boss=True, is_enemy=True, is_accessible=True))
            logger.warning('[地图-Boss] 未检测到Boss，使用可能的Boss格子')
            logger.info('[地图-Boss] 可能的Boss: %s' % self.map.select(may_boss=True))
            logger.info('[地图-Boss] 可能的Boss且是敌舰: %s' % self.map.select(may_boss=True, is_enemy=True))

        if grids:
            logger.hr('清除Boss')
            grids = grids.sort('weight', 'cost')
            logger.info('[地图] 格子: %s' % str(grids))
            self.clear_chosen_enemy(grids[0])

        logger.warning('[地图-Boss] 检测到大世界捕获，撤退中')
        self.withdraw()

    def clear_potential_boss(self):
        """当 Boss 未被检测到时，踩踏所有 Boss 出生点的方法。
        """
        grids = self.map.select(may_boss=True, is_accessible=True).sort('weight', 'cost')
        logger.info('[地图-Boss] 可能的Boss: %s' % grids)
        battle_count = self.battle_count
        is_single_boss = self.map.select(may_boss=True).count == 1
        if is_single_boss:
            expected = 'boss'
        else:
            expected = ''

        for grid in grids:
            logger.hr('清除潜在Boss')
            grids = grids.sort('weight', 'cost')
            logger.info('[地图] 格子: %s' % str(grid))
            self.fleet_boss.clear_chosen_enemy(grid, expected=expected)
            if self.battle_count > battle_count:
                logger.info('[地图-Boss] Boss猜测正确')
                return True
            else:
                logger.info('[地图-Boss] Boss猜测错误')

        grids = self.map.select(may_boss=True, is_accessible=False).sort('weight', 'cost')
        logger.info('[地图-Boss] 可能的Boss: %s' % grids)

        for grid in grids:
            logger.hr('清除潜在Boss路障')
            roadblocks = self.brute_find_roadblocks(grid, fleet=self.fleet_boss_index)
            roadblocks = roadblocks.sort('weight', 'cost')
            logger.info('[地图] 格子: %s' % str(roadblocks))
            self.fleet_1.clear_chosen_enemy(roadblocks[0], expected=expected)
            return True

        return False

    def brute_clear_boss(self):
        """使用暴力搜索路障的方式清除 Boss。
        注意：此方法将使用两支舰队。
        """
        boss = self.map.select(is_boss=True)
        if boss:
            logger.info('[地图-Boss] 强制清除Boss')
            grids = self.brute_find_roadblocks(boss[0], fleet=self.fleet_boss_index)
            if grids:
                if self.brute_fleet_meet():
                    return True
                logger.info('[地图-Boss] 强制清除Boss路障')
                grids = grids.sort('weight', 'cost')
                logger.info('[地图] 格子: %s' % str(grids))
                self.clear_chosen_enemy(grids[0])
                return True
            else:
                return self.fleet_boss.clear_boss()
        elif self.map.select(may_boss=True, is_caught_by_siren=True):
            logger.info('[地图-Boss] Boss出现在舰队格子上')
            self.fleet_2.switch_to()
            return self.clear_chosen_enemy(self.map.select(may_boss=True, is_caught_by_siren=True)[0])
        else:
            logger.warning('[地图-Boss] 未检测到Boss，尝试所有Boss出生点')
            return self.clear_potential_boss()

    def brute_fleet_meet(self):
        """使用暴力搜索清除舰队之间的路障。
        """
        if self.fleet_boss_index != 2 or not self.fleet_2_location:
            return False
        grids = self.brute_find_roadblocks(self.map[self.fleet_2_location], fleet=1)
        if grids:
            logger.info('[地图-Boss] 强制清除舰队间路障')
            grids = grids.sort('weight', 'cost')
            logger.info('[地图] 格子: %s' % str(grids))
            self.clear_chosen_enemy(grids[0])
            return True
        else:
            return False

    def clear_siren(self, **kwargs):
        """清除塞壬敌人。

        Returns:
            bool: 是否清除了敌人。
        """
        if not self.config.MAP_HAS_SIREN and not self.config.MAP_HAS_FORTRESS:
            return False

        if self.config.FLEET_2:
            kwargs['sort'] = ('weight', 'cost_2')
        grids = self.map.select(is_siren=True)
        if self.config.MAP_HAS_FORTRESS:
            grids = grids.add(self.map.select(is_fortress=True))
        grids = self.select_grids(grids, **kwargs)

        if grids:
            logger.hr('清除塞壬')
            self.show_select_grids(grids, **kwargs)
            if grids[0].is_fortress:
                expected = 'fortress'
            else:
                expected = 'siren'
            self.clear_chosen_enemy(grids[0], expected=expected)
            return True

        return False

    def clear_any_enemy(self, **kwargs):
        """清除任意敌人。

        Returns:
            bool: 是否清除了敌人。
        """
        grids = self.map.select(is_enemy=True, is_boss=False)

        if self.config.MAP_HAS_SIREN:
            grids = grids.add(self.map.select(is_siren=True))
        if self.config.MAP_HAS_FORTRESS:
            grids = grids.add(self.map.select(is_fortress=True))

        grids = self.select_grids(grids, **kwargs)

        if grids:
            logger.hr('清除敌舰')
            self.show_select_grids(grids, **kwargs)
            grid = grids[0]
            if grid.is_fortress:
                expected = 'fortress'
            elif grid.is_siren:
                expected = 'siren'
            else:
                expected = ''
            self.clear_chosen_enemy(grid, expected=expected)
            return True

        return False

    def fleet_2_step_on(self, grids, roadblocks):
        """第二舰队踩踏格子以减少另一支舰队的伏击频率。
        当然也可以直接使用 'self.fleet_2.goto(grid)' 来实现相同效果，
        但道路可能被敌人阻挡，此方法可以处理这种情况。

        Args:
            grids (SelectedGrids): 目标格子集合。
            roadblocks (list[RoadGrids]): 路障路线列表。

        Returns:
            bool: 是否清除了敌人。
        """
        if not self.config.FLEET_2:
            return False
        for grid in grids:
            if self.fleet_at(grid=grid, fleet=2):
                return False
        # if grids.count == len([grid for grid in grids if grid.is_enemy or grid.is_cleared]):
        #     logger.info('Fleet 2 step on, no need')
        #     return False
        all_cleared = grids.select(is_cleared=True).count == grids.count

        logger.info('[地图-舰队] 第二舰队踩点')
        for grid in grids:
            if grid.is_enemy or (not all_cleared and grid.is_cleared):
                continue
            if self.check_accessibility(grid=grid, fleet=2):
                logger.info('[地图-舰队] 第二舰队踩点 %s' % grid)
                self.fleet_2.goto(grid)
                self.fleet_1.switch_to()
                return False

        logger.info('[地图-舰队] 第二舰队踩点遇到路障')
        clear = self.fleet_1.clear_roadblocks(roadblocks)
        self.fleet_1.clear_all_mystery()
        return clear

    def fleet_2_break_siren_caught(self):
        if self.fleet_boss_index != 2:
            return False
        if not self.config.MAP_HAS_SIREN or not self.config.MAP_HAS_MOVABLE_ENEMY:
            return False
        if not self.map.select(is_caught_by_siren=True):
            logger.info('[地图-舰队] 没有舰队被塞壬捕获')
            return False
        if not self.fleet_2_location or not self.map[self.fleet_2_location].is_caught_by_siren:
            logger.warning('[地图-舰队] 出现塞壬捕获，但不是第二舰队')
            for grid in self.map:
                grid.is_caught_by_siren = False
            return False

        logger.info(f'[地图-舰队] 打破塞壬捕获，第二舰队: {self.fleet_2_location}')
        self.fleet_2.switch_to()
        self.ensure_edge_insight()
        self.clear_chosen_enemy(self.map[self.fleet_2_location])
        self.fleet_1.switch_to()
        for grid in self.map:
            grid.is_caught_by_siren = False
        return True

    def fleet_2_push_forward(self):
        """将第二舰队移动到权重更低的格子。
        这将降低 Boss 舰队被敌人卡住的可能性，特别是对于第 7 到第 9 章的单行道地图。

        了解更多：
        9章道中战最小化路线规划
        https://wiki.biligame.com/blhx/9%E7%AB%A0%E9%81%93%E4%B8%AD%E6%88%98%E6%9C%80%E5%B0%8F%E5%8C%96%E8%B7%AF%E7%BA%BF%E8%A7%84%E5%88%92

        Returns:
            bool: 是否推进成功。
        """
        if self.fleet_boss_index != 2:
            return False

        logger.info('[地图-舰队] 第二舰队推进')
        grids = self.map.select(is_land=False).sort('weight', 'cost')
        if self.map[self.fleet_2_location].weight <= grids[0].weight:
            logger.info('[地图-舰队] 第二舰队已推送到目的地')
            self.fleet_1.switch_to()
            return False

        fleets = SelectedGrids([self.map[self.fleet_1_location], self.map[self.fleet_2_location]])
        grids = grids.select(is_accessible_2=True, is_sea=True).delete(fleets)
        if not grids:
            logger.info('[地图-舰队] 第二舰队无处可推')
            return False
        if self.map[self.fleet_2_location].weight <= grids[0].weight:
            logger.info('[地图-舰队] 第二舰队已推送到最近格子')
            return False

        logger.info(f'[地图] 格子: {grids}')
        logger.info(f'[地图-舰队] 推进: {grids[0]}')
        self.fleet_2.goto(grids[0])
        self.fleet_1.switch_to()
        return True

    def fleet_2_rescue(self, grid):
        """使用道中舰队救援 Boss 舰队。

        Args:
            grid (GridInfo): 目标格子，通常为 Boss 出生点。

        Returns:
            bool: 是否清除了敌人。
        """
        if self.fleet_boss_index != 2:
            return False

        grids = self.brute_find_roadblocks(grid, fleet=2)
        if not grids:
            return False
        logger.info('[地图-舰队] 第二舰队救援')
        grids = self.select_grids(grids)
        if not grids:
            return False

        self.clear_chosen_enemy(grids[0])
        return True

    def fleet_2_protect(self):
        """道中舰队在 Boss 舰队周围移动，清除逼近的塞壬。

        Returns:
            bool: 是否清除了敌人。
        """
        if not self.config.FLEET_2 or not self.config.MAP_HAS_MOVABLE_ENEMY:
            return False

        # When having 2 fleet
        for n in range(20):
            if not self.map.select(is_siren=True):
                return False

            nearby = self.map.select(cost_2=1).add(self.map.select(cost_2=2))
            approaching = SelectedGrids([])
            if self.config.MAP_HAS_MOVABLE_ENEMY:
                approaching = approaching.add(nearby.select(is_siren=True))
            if self.config.MAP_HAS_MOVABLE_NORMAL_ENEMY:
                approaching = approaching.add(nearby.select(is_enemy=True))
            if approaching:
                grids = self.select_grids(approaching, sort=('cost_2', 'cost_1'))
                self.clear_chosen_enemy(grids[0], expected='siren')
                return True
            else:
                grids = nearby.delete(self.map.select(is_fleet=True))
                grids = self.select_grids(grids, sort=('cost_2', 'cost_1'))
                self.goto(grids[0])
                continue

        logger.warning('[地图-舰队] 第二舰队保护：无塞壬接近')
        return False

    def clear_filter_enemy(self, string, preserve=0):
        """根据过滤器清除敌人。
        如果 EnemyPriority_EnemyScaleBalanceWeight != default_mode，则忽略敌人过滤器。
        如果 MAP_HAS_MOVABLE_NORMAL_ENEMY，则忽略敌人过滤器。

        Args:
            string (str): 用于筛选敌人的过滤器，从易到难排列。
            preserve (int): 保留几个最简单的敌人用于无弹药战斗。
                弹药耗尽时使用 0 来清除这些保留的敌人。

        Returns:
            bool: 是否清除了敌人。
        """
        if self.config.MAP_HAS_MOVABLE_NORMAL_ENEMY:
            if self.clear_any_enemy(sort=('cost_2',)):
                return True
            return False

        if self.config.EnemyPriority_EnemyScaleBalanceWeight == 'S3_enemy_first':
            string = '3L > 3M > 3E > 3C > 2L > 2M > 2E > 2C > 1L > 1M > 1E > 1C'
            preserve = 0
        elif self.config.EnemyPriority_EnemyScaleBalanceWeight == 'S1_enemy_first':
            string = '1L > 1M > 1E > 1C > 2L > 2M > 2E > 2C > 3L > 3M > 3E > 3C'

        ENEMY_FILTER.load(string)
        grids = self.map.select(is_enemy=True, is_accessible=True)
        if not grids:
            return False

        grids = ENEMY_FILTER.apply(grids.sort('weight', 'cost').grids)
        logger.info(f'[地图-战斗] 筛选敌舰: {grids}, 保留={preserve}')
        if preserve:
            grids = grids[preserve:]

        if grids:
            logger.hr('清除筛选敌舰')
            self.clear_chosen_enemy(grids[0])
            return True

        return False

    def clear_bouncing_enemy(self):
        """清除在固定路线上弹跳的敌人。
        此方法在清除一个敌人后将被禁用，因为地图上只有一个弹跳敌人。

        Returns:
            bool: 是否清除了敌人。
        """
        if not self.config.MAP_HAS_BOUNCING_ENEMY:
            return False

        route = None
        for a_route in self.map.bouncing_enemy_data:
            if a_route.select(may_bouncing_enemy=True, is_accessible=True):
                route = a_route
                break
        if route is None:
            return False

        logger.hr('清除弹跳敌舰')
        logger.info(f'[地图-战斗] 清除弹跳敌舰: {route}')
        self.show_fleet()
        prev = self.battle_count
        for n, grid in enumerate(itertools.cycle(route)):
            if self.emotion.is_calculate and self.config.Campaign_UseFleetLock:
                self.emotion.wait(fleet_index=self.fleet_current_index)
            self.goto(grid, expected='combat_nothing')

            if self.battle_count > prev:
                logger.info('[地图-战斗] 已清除一个弹跳敌舰')
                route.select(may_bouncing_enemy=True).set(may_bouncing_enemy=False)
                self.full_scan()
                self.find_path_initial()
                self.map.show_cost()
                return True
            if n >= 12:
                logger.warning('[地图-战斗] 尝试12次后仍无法清除弹跳敌舰')
                return False

        return False
