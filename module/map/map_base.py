"""战役地图基础数据结构。

本模块定义了战役地图的核心数据模型，包括地图对象 ``CampaignMap``、
格子集合 ``SelectedGrids``（来自 map_grids）以及单个战役格子 ``CampaignGrid``。

主要职责：
- 存储和解析地图数据（海洋、陆地、出生点、Boss 等格子类型）
- 管理地图机制数据（传送门、墙壁、迷宫、堡垒、陆基等）
- 提供基于 Dijkstra 的寻路算法和路径优化
- 管理敌人刷新数据（spawn_data）和缺失敌人预测
- 处理地图更新（合并摄像头扫描的局部数据到全局地图）
"""

import copy

from module.base.utils import location2node, node2location
from module.logger import logger
from module.map.map_grids import SelectedGrids
from module.map.utils import *
from module.map_detection.grid_info import GridInfo


class CampaignMap:
    """战役地图数据结构。

    管理整个战役地图的格子信息、机制数据、寻路逻辑和敌人刷新预测。
    每个战役关卡对应一个 CampaignMap 实例，包含地图形状、格子数据、
    传送门、墙壁、迷宫、堡垒等机制的完整描述。

    Attributes:
        name (str): 地图名称。
        grid_class: 格子对象的类，默认为 ``GridInfo``。
        grids (dict[tuple, GridInfo]): 以坐标 ``(x, y)`` 为键的格子字典。
        _shape (tuple[int, int]): 地图尺寸 ``(width, height)``。
        _map_data (str): 默认地图数据文本。
        _map_data_loop (str): 快进/清理模式地图数据文本。
        _weight_data (str): 格子权重数据文本。
        _wall_data (str): 墙壁数据文本。
        _portal_data (list[tuple]): 传送门数据 ``[(start, end), ...]``。
        _land_based_data (list): 陆基机制数据。
        _maze_data (list): 迷宫机制数据。
        maze_round (int): 迷宫机制所需的回合数。
        _fortress_data (list): 堡垒数据 ``[enemy_grids, block_grids]``。
        _bouncing_enemy_data (list[SelectedGrids]): 弹跳敌人路线数据。
        _spawn_data (list[dict]): 默认敌人刷新数据。
        _spawn_data_stack (list[dict]): 累积刷新统计。
        _spawn_data_loop (list[dict]): 快进模式刷新数据。
        _spawn_data_use_loop (bool): 是否使用快进模式刷新数据。
        _camera_data (SelectedGrids): 相机位置数据。
        _camera_data_spawn_point (SelectedGrids): 出生点检测专用相机位置。
        _map_covered (SelectedGrids): 被覆盖的格子集合。
        _ignore_prediction (list): 忽略的错误预测列表。
        poor_map_data (bool): 地图数据是否不完整。
        camera_sight (tuple[int, int, int, int]): 相机视野范围。
        grid_connection (dict): 格子连接关系。
    """

    def __init__(self, name=None):
        self.name = name
        self.grid_class = GridInfo
        self.grids = {}
        self._shape = (0, 0)
        self._map_data = ''
        self._map_data_loop = ''
        self._weight_data = ''
        self._wall_data = ''
        self._portal_data = []
        self._land_based_data = []
        self._maze_data = []
        self.maze_round = 9
        self._fortress_data = [(), ()]
        self._bouncing_enemy_data = []
        self._spawn_data = []
        self._spawn_data_stack = []
        self._spawn_data_loop = []
        self._spawn_data_use_loop = False
        self._camera_data = []
        self._camera_data_spawn_point = []
        self._map_covered = SelectedGrids([])
        self._ignore_prediction = []
        self.in_map_swipe_preset_data = None
        self.poor_map_data = False
        self.camera_sight = (-3, -1, 3, 2)
        self.grid_connection = {}

    def __iter__(self):
        """迭代地图中所有格子。

        Yields:
            GridInfo: 地图中的每个格子对象。
        """
        return iter(self.grids.values())

    def __getitem__(self, item):
        """
        Args:
            item: 网格坐标。

        Returns:
            GridInfo:
        """
        return self.grids[tuple(item)]

    def __contains__(self, item):
        """判断坐标是否在地图范围内。

        Args:
            item: 网格坐标。

        Returns:
            bool: 坐标是否存在于地图中。
        """
        return tuple(item) in self.grids

    @staticmethod
    def _parse_text(text):
        """解析文本格式的网格数据。

        Args:
            text (str): 以空格分隔、换行分隔的网格数据文本。

        Yields:
            tuple[tuple[int, int], str]: ((x, y), data) 坐标与数据对。
        """
        text = text.strip()
        for y, row in enumerate(text.split('\n')):
            row = row.strip()
            for x, data in enumerate(row.split(' ')):
                yield (x, y), data

    @property
    def shape(self):
        """地图尺寸。

        设置时会根据尺寸初始化所有格子，生成默认相机数据，并将权重设为 10。

        Returns:
            tuple[int, int]: 地图尺寸 ``(width, height)``。
        """
        return self._shape

    @shape.setter
    def shape(self, scale):
        self._shape = node2location(scale.upper())
        for y in range(self._shape[1] + 1):
            for x in range(self._shape[0] + 1):
                grid = self.grid_class()
                grid.location = (x, y)
                self.grids[(x, y)] = grid

        # camera_data 可以自动生成，但手动设置效果更好
        self.camera_data = [location2node(loca) for loca in camera_2d((0, 0, *self._shape), sight=self.camera_sight)]
        self.camera_data_spawn_point = []
        # weight_data 默认设为 10
        for grid in self:
            grid.weight = 10.

    @property
    def map_data(self):
        """默认地图数据。

        设置时会自动解析并加载地图格子信息。

        Returns:
            str: 默认地图数据文本。
        """
        return self._map_data

    @map_data.setter
    def map_data(self, text):
        self._map_data = text
        self._load_map_data(text)

    @property
    def map_data_loop(self):
        """快进/清理模式地图数据。

        Returns:
            str: 快进模式地图数据文本。
        """
        return self._map_data_loop

    @map_data_loop.setter
    def map_data_loop(self, text):
        self._map_data_loop = text

    def load_map_data(self, use_loop=False):
        """
        Args:
            use_loop (bool): 是否为清理模式。
                             清理模式（正确名称）== 快进模式（旧版 Alas）== loop（lua 文件中）
        """
        has_loop = bool(len(self.map_data_loop))
        logger.info(f'[地图-数据] 加载地图数据, 有回路={has_loop}, 使用回路={use_loop}')
        if has_loop and use_loop:
            self._load_map_data(self.map_data_loop)
        else:
            self._load_map_data(self.map_data)

    def _load_map_data(self, text):
        """将文本格式的地图数据解析并写入格子。

        如果格子尚未初始化，会先根据数据尺寸设置地图形状。

        Args:
            text (str): 以空格分隔、换行分隔的网格数据文本。
        """
        if not len(self.grids.keys()):
            grids = np.array([loca for loca, _ in self._parse_text(text)])
            self.shape = location2node(tuple(np.max(grids, axis=0)))

        for loca, data in self._parse_text(text):
            self.grids[loca].decode(data)

    @property
    def wall_data(self):
        """墙壁数据文本。

        设置时仅保存文本，实际加载由 ``grid_connection_initial(wall=True)`` 执行。

        Returns:
            str: 墙壁数据文本。
        """
        return self._wall_data

    @wall_data.setter
    def wall_data(self, text):
        self._wall_data = text

    @property
    def portal_data(self):
        """传送门数据。

        设置时会解析传送门对并标记源格子为传送门。

        Returns:
            list[tuple]: 传送门数据 ``[(start_location, end_location), ...]``。
        """
        return self._portal_data

    @portal_data.setter
    def portal_data(self, portal_list):
        """
        Args:
            portal_list (list[tuple]): [(start, end),]
        """
        for nodes in portal_list:
            node1, node2 = location_ensure(nodes[0]), location_ensure(nodes[1])
            self._portal_data.append((node1, node2))
            self[node1].is_portal = True

    @property
    def land_based_data(self):
        """陆基机制数据。

        Returns:
            list: 陆基数据，每个元素为 ``[grid_node, rotation]``。
        """
        return self._land_based_data

    @land_based_data.setter
    def land_based_data(self, data):
        self._land_based_data = data

    def _load_land_base_data(self, data):
        """
        land_based_data 需要在 map_data 之后设置。

        Args:
            data (list[list[str]]): 例如 [['H7', 'up'], ['D5', 'left'], ['G3', 'down'], ['C2', 'right']]
        """
        rotation_dict = {
            'up': [(0, -1), (0, -2), (0, -3)],
            'down': [(0, 1), (0, 2), (0, 3)],
            'left': [(-1, 0), (-2, 0), (-3, 0)],
            'right': [(1, 0), (2, 0), (3, 0)],
        }
        self._land_based_data = data
        for land_based in data:
            grid, rotation = land_based
            grid = self.grids[location_ensure(grid)]
            trigger = self.grid_covered(grid=grid, location=[(0, -1), (0, 1), (-1, 0), (1, 0)]).select(is_land=False)
            block = self.grid_covered(grid=grid, location=rotation_dict[rotation]).select(is_land=False)
            trigger.set(is_mechanism_trigger=True, mechanism_trigger=trigger, mechanism_block=block)
            block.set(is_mechanism_block=True)

    @property
    def maze_data(self):
        """迷宫机制数据。

        Returns:
            list: 迷宫数据，每个元素为包含三组坐标的元组。
        """
        return self._maze_data

    @maze_data.setter
    def maze_data(self, data):
        self._maze_data = data

    def _load_maze_data(self, data):
        """加载迷宫机制数据并标记相关格子。

        为每个迷宫组设置 ``is_maze`` 标记和回合范围，并计算迷宫格子附近的可达区域。

        Args:
            data (list): 迷宫数据，例如 [('D5', 'I4', 'J6'), ('C4', 'E4', 'D8'), ('C2', 'G2', 'G6')]
        """
        self._maze_data = data
        self.maze_round = len(data) * 3
        for index, maze in enumerate(data):
            maze = self.to_selected(maze)
            maze.set(is_maze=True, maze_round=tuple(list(range(index * 3, index * 3 + 3))))
            for grid in maze:
                self.find_path_initial(grid, has_ambush=False)
                grid.maze_nearby = self.select(cost=1).add(self.select(cost=2)).select(is_land=False)

    @property
    def fortress_data(self):
        """堡垒机制数据。

        Returns:
            list: ``[enemy_grids, block_grids]``，敌人格子和阻挡格子。
        """
        return self._fortress_data

    @fortress_data.setter
    def fortress_data(self, data):
        enemy, block = data
        if not isinstance(enemy, SelectedGrids):
            enemy = self.to_selected((enemy,) if not isinstance(enemy, (tuple, list)) else enemy)
        if not isinstance(block, SelectedGrids):
            block = self.to_selected((block,) if not isinstance(block, (tuple, list)) else block)
        self._fortress_data = [enemy, block]

    def _load_fortress_data(self, data):
        """加载堡垒机制数据并标记相关格子。

        将敌人格子标记为 ``is_fortress=True``，将阻挡格子标记为 ``is_mechanism_block=True``。

        Args:
            data (list): [fortress_enemy, fortress_block]，可以是字符串或字符串的元组/列表。
                例如 [('B5', 'E2', 'H5', 'E8'), 'G3'] 或 ['F5', 'G1']
        """
        self._fortress_data = data
        enemy, block = data
        enemy.set(is_fortress=True)
        block.set(is_mechanism_block=True)

    @property
    def bouncing_enemy_data(self):
        """弹跳敌人路线数据。

        Returns:
            list[SelectedGrids]: 弹跳敌人路线列表，每条路线为一个格子集合。
        """
        return self._bouncing_enemy_data

    @bouncing_enemy_data.setter
    def bouncing_enemy_data(self, data):
        self._bouncing_enemy_data = [self.to_selected(route) for route in data]

    def _load_bouncing_enemy_data(self, data):
        """
        Args:
            data (list[SelectedGrids]): 敌人弹跳路线经过的格子。
                [enemy_route, enemy_route, ...]，例如 [(C2, C3, C4), ]
        """
        for route in data:
            route.set(may_bouncing_enemy=True)

    def load_mechanism(self, land_based=False, maze=False, fortress=False, bouncing_enemy=False):
        """加载地图机制数据。

        根据标志位决定加载哪些机制数据到地图格子上。

        Args:
            land_based (bool): 是否加载陆基机制。
            maze (bool): 是否加载迷宫机制。
            fortress (bool): 是否加载堡垒机制。
            bouncing_enemy (bool): 是否加载弹跳敌人机制。
        """
        logger.info(f'[地图-数据] 加载机制, land_base={land_based}, maze={maze}, fortress={fortress}, '
                    f'bouncing_enemy={bouncing_enemy}')
        if land_based:
            self._load_land_base_data(self.land_based_data)
        if maze:
            self._load_maze_data(self.maze_data)
        if fortress:
            self._load_fortress_data(self._fortress_data)
        if bouncing_enemy:
            self._load_bouncing_enemy_data(self._bouncing_enemy_data)

    def grid_connection_initial(self, wall=False, portal=False):
        """
        Args:
            wall (bool): 是否使用 wall_data
            portal (bool): 是否使用 portal_data

        Returns:
            bool: 是否使用了墙壁数据。
        """
        logger.info(f'[地图-连接] 格子连接: 墙壁={wall}, 传送门={portal}')

        # 生成格子连接关系
        total = set([grid for grid in self.grids.keys()])
        for grid in self:
            connection = set()
            for arr in np.array([(0, -1), (0, 1), (-1, 0), (1, 0)]):
                arr = tuple(arr + grid.location)
                if arr in total:
                    connection.add(arr)
            self.grid_connection[grid.location] = connection

        # 使用 wall_data 删除连接
        if wall and self._wall_data:
            wall = []
            for y, line in enumerate([l for l in self._wall_data.split('\n') if l]):
                for x, letter in enumerate(line[4:-2]):
                    if letter != ' ':
                        wall.append((x, y))
            wall = np.array(wall)
            vert = wall[np.all([wall[:, 0] % 4 == 2, wall[:, 1] % 2 == 0], axis=0)]
            hori = wall[np.all([wall[:, 0] % 4 == 0, wall[:, 1] % 2 == 1], axis=0)]
            disconnect = []
            for loca in (vert - (2, 0)) // (4, 2):
                disconnect.append([loca, loca + (1, 0)])
            for loca in (hori - (0, 1)) // (4, 2):
                disconnect.append([loca, loca + (0, 1)])
            for g1, g2 in disconnect:
                g1 = tuple(g1.tolist())
                g2 = tuple(g2.tolist())
                self.grid_connection[g1].remove(g2)
                self.grid_connection[g2].remove(g1)

        # 创建传送门连接
        for start, end in self._portal_data:
            if portal:
                self.grid_connection[start].add(end)
                self[start].is_portal = True
                self[start].portal_link = end
            else:
                if end in self.grid_connection[start]:
                    self.grid_connection[start].remove(end)
                self[start].is_portal = False
                self[start].portal_link = None

        return True

    def fixup_submarine_fleet(self):
        """修正潜艇出生点的错误识别。

        当一个格子被识别为舰队但不在出生点上，而其上方的格子是潜艇出生点时，
        将舰队识别修正为潜艇识别。同时清除同时被标记为敌人和舰队的格子。
        """
        # 修正潜艇出生点
        # 如果一个格子被识别为潜艇，其下方的格子可能被误识别为舰队，因为它们有相同的弹药图标
        for grid in self.select(is_fleet=True):
            if grid.is_spawn_point:
                continue
            for upper in self.grid_covered(grid, location=[(0, -1)]):
                if upper.is_submarine_spawn_point:
                    logger.info(f'[地图-潜艇] 修正潜艇出生点, 舰队={grid} -> 潜艇={upper}')
                    grid.is_fleet = False
                    grid.is_current_fleet = False
                    upper.is_submarine = True
        # 初始化时不允许一个格子同时是 is_enemy 和 is_fleet
        # 这可能是上方的潜艇
        for grid in self.select(is_enemy=True, is_fleet=True):
            grid.is_fleet = False
            grid.is_current_fleet = False

    def show(self):
        """在日志中显示地图网格。

        以文本表格形式打印整个地图，使用格子的 ``str`` 属性表示每个格子的状态。
        """
        # logger.info('Showing grids:')
        logger.info('[地图-显示] ' + ' '.join([' ' + chr(x + 64 + 1) for x in range(self.shape[0] + 1)]))
        for y in range(self.shape[1] + 1):
            text = str(y + 1).rjust(2) + ' ' + ' '.join(
                [self[(x, y)].str if (x, y) in self else '  ' for x in range(self.shape[0] + 1)])
            logger.info(text)

    def update(self, grids, camera, mode='normal'):
        """将局部扫描结果合并到全局地图。

        通过摄像头偏移将局部格子数据映射到全局坐标，进行预测校验后合并。
        如果错误预测少于 2 个，则执行实际合并。

        Args:
            grids (MapGrids): 局部扫描得到的格子集合。
            camera (tuple): 摄像头在全局地图中的位置。
            mode (str): 扫描模式，如 'init'、'normal'、'carrier'、'movable'。

        Returns:
            bool: 合并是否成功。
        """
        offset = np.array(camera) - np.array(grids.center_loca)
        # grids.show()

        failed_count = 0
        for grid in grids.grids.values():
            loca = tuple(offset + grid.location)
            if loca in self.grids:
                if self.ignore_prediction_match(globe=loca, local=grid):
                    continue
                if not copy.copy(self.grids[loca]).merge(grid, mode=mode):
                    logger.warning(f'[地图-预测] 预测错误. {self.grids[loca]} = "{grid.str}"')
                    failed_count += 1

        # 如果错误预测少于 2 个，执行实际合并
        if failed_count < 2:
            for grid in grids.grids.values():
                loca = tuple(offset + grid.location)
                if loca in self.grids:
                    if self.ignore_prediction_match(globe=loca, local=grid):
                        continue
                    self.grids[loca].merge(grid, mode=mode)
            if mode == 'init':
                self.fixup_submarine_fleet()
            return True
        else:
            logger.warning('[地图-预测] 预测错误过多')
            return False

    def reset(self):
        """重置所有格子的状态。"""
        for grid in self:
            grid.reset()

    def reset_fleet(self):
        """重置所有格子的当前舰队标记。"""
        for grid in self:
            grid.is_current_fleet = False

    @property
    def camera_data(self):
        """
        Returns:
            SelectedGrids: 相机数据。
        """
        return self._camera_data

    @camera_data.setter
    def camera_data(self, nodes):
        """
        Args:
            nodes (list): 包含字符串节点名。
        """
        self._camera_data = SelectedGrids([self[node2location(node)] for node in nodes])

    @property
    def camera_data_spawn_point(self):
        """额外的 camera_data，用于检测出生点的舰队。

        Returns:
            SelectedGrids: 用于检测出生点舰队的额外相机数据。
        """
        return self._camera_data_spawn_point

    @camera_data_spawn_point.setter
    def camera_data_spawn_point(self, nodes):
        """
        Args:
            nodes (list): 包含字符串节点名。
        """
        self._camera_data_spawn_point = SelectedGrids([self[node2location(node)] for node in nodes])

    @property
    def spawn_data(self):
        """
        Returns:
            list[dict]: 敌人刷新数据列表。
        """
        if self._spawn_data_use_loop:
            return self._spawn_data_loop
        else:
            return self._spawn_data

    @spawn_data.setter
    def spawn_data(self, data_list):
        self._spawn_data = data_list

    @property
    def spawn_data_loop(self):
        """快进模式敌人刷新数据。

        Returns:
            list[dict]: 快进模式下的敌人刷新数据列表。
        """
        return self._spawn_data_loop

    @spawn_data_loop.setter
    def spawn_data_loop(self, data_list):
        self._spawn_data_loop = data_list

    @property
    def spawn_data_stack(self):
        """累积的敌人刷新统计数据。

        Returns:
            list[dict]: 每次刷新后的累积敌人统计列表。
        """
        return self._spawn_data_stack

    def load_spawn_data(self, use_loop=False):
        """加载敌人刷新数据并构建累积统计。

        Args:
            use_loop (bool): 是否使用快进模式的刷新数据。
        """
        has_loop = bool(len(self._spawn_data_loop))
        logger.info(f'[地图-数据] 加载出生点数据, 有回路={has_loop}, 使用回路={use_loop}')
        if has_loop and use_loop:
            self._spawn_data_use_loop = True
            self._load_spawn_data(self._spawn_data_loop)
        else:
            self._spawn_data_use_loop = False
            self._load_spawn_data(self._spawn_data)

    def _load_spawn_data(self, data_list):
        """解析刷新数据并构建累积统计栈。

        Args:
            data_list (list[dict]): 敌人刷新数据列表，每项包含 'battle'、'enemy'、
                'mystery'、'siren'、'boss' 等字段。
        """
        spawn = {'battle': 0, 'enemy': 0, 'mystery': 0, 'siren': 0, 'boss': 0}
        for data in data_list:
            spawn['battle'] = data['battle']
            spawn['enemy'] += data.get('enemy', 0)
            spawn['mystery'] += data.get('mystery', 0)
            spawn['siren'] += data.get('siren', 0)
            spawn['boss'] += data.get('boss', 0)
            self._spawn_data_stack.append(spawn.copy())

    @property
    def weight_data(self):
        """格子权重数据。

        设置时自动解析并写入每个格子的权重值。

        Returns:
            str: 格子权重数据文本。
        """
        return self._weight_data

    @weight_data.setter
    def weight_data(self, text):
        self._weight_data = text
        for loca, data in self._parse_text(text):
            self[loca].weight = float(data)

    @property
    def map_covered(self):
        """
        Returns:
            SelectedGrids: 被覆盖的格子集合。
        """
        covered = []
        for grid in self:
            covered += self.grid_covered(grid).grids
        return SelectedGrids(covered).add(self._map_covered)

    @map_covered.setter
    def map_covered(self, nodes):
        """
        Args:
            nodes (list): 包含字符串节点名。
        """
        self._map_covered = SelectedGrids([self[node2location(node)] for node in nodes])

    def ignore_prediction(self, globe, **local):
        """
        Args:
            globe (GridInfo, tuple, str): 全局地图中的网格。
            **local: 局部网格的任意属性。

        Examples:
            MAP.ignore_prediction(D5, enemy_scale=1, enemy_genre='Enemy')
            将忽略 D5 上的 `1E` 敌人。
        """
        globe = location_ensure(globe)
        self._ignore_prediction.append((globe, local))

    def ignore_prediction_match(self, globe, local):
        """
        Args:
            globe (tuple): 全局坐标。
            local (GridInfo): 局部网格信息。

        Returns:
            bool: 是否匹配到错误预测。
        """
        for wrong_globe, wrong_local in self._ignore_prediction:
            if wrong_globe == globe:
                if all([local.__getattribute__(k) == v for k, v in wrong_local.items()]):
                    return True

        return False

    @property
    def is_map_data_poor(self):
        """判断地图数据是否不完整。

        Returns:
            bool: 地图数据是否不完整。
        """
        if not self.select(may_enemy=True) or not self.select(may_boss=True) or not self.select(is_spawn_point=True):
            return False
        if not len(self.spawn_data):
            return False
        return True

    def show_cost(self):
        """在日志中显示地图各格子的寻路代价。"""
        logger.info('   ' + ' '.join(['   ' + chr(x + 64 + 1) for x in range(self.shape[0] + 1)]))
        for y in range(self.shape[1] + 1):
            text = str(y + 1).rjust(2) + ' ' + ' '.join(
                [str(self[(x, y)].cost).rjust(4) if (x, y) in self else '    ' for x in range(self.shape[0] + 1)])
            logger.info(text)

    def show_connection(self):
        """在日志中显示地图各格子的寻路连接关系。"""
        logger.info('[地图-显示] ' + ' '.join([' ' + chr(x + 64 + 1) for x in range(self.shape[0] + 1)]))
        for y in range(self.shape[1] + 1):
            text = str(y + 1).rjust(2) + ' ' + ' '.join(
                [location2node(self[(x, y)].connection) if (x, y) in self and self[(x, y)].connection else '  ' for x in
                 range(self.shape[0] + 1)])
            logger.info(text)

    def find_path_initial(self, location, has_ambush=True, has_enemy=True):
        """
        Args:
            location (tuple[int]): 网格坐标。
            has_ambush (bool): 是否有伏击。
            has_enemy (bool): 是否考虑敌人，False 表示仅考虑海洋和陆地。
        """
        location = location_ensure(location)
        ambush_cost = 10 if has_ambush else 1
        for grid in self:
            grid.cost = 9999
            grid.connection = None
        start = self[location]
        start.cost = 0
        visited = [start]
        visited = set(visited)

        while 1:
            new = visited.copy()
            for grid in visited:
                for arr in self.grid_connection[grid.location]:
                    arr = self[arr]
                    if arr.is_land or arr.is_mechanism_block:
                        continue
                    cost = ambush_cost if arr.may_ambush else 1
                    cost += grid.cost

                    if cost < arr.cost:
                        arr.cost = cost
                        arr.connection = grid.location
                    elif cost == arr.cost:
                        if abs(arr.location[0] - grid.location[0]) == 1:
                            arr.connection = grid.location
                    if arr.is_sea or not has_enemy:
                        new.add(arr)
            if len(new) == len(visited):
                break
            visited = new

        # self.show_cost()
        # self.show_connection()

    def find_path_initial_multi_fleet(self, location_dict, current, has_ambush):
        """
        Args:
            location_dict (dict): 键为舰队索引(int)，值为网格坐标 tuple[int])。
            current (tuple): 当前位置。
            has_ambush (bool): 是否有伏击。
        """
        location_dict = sorted(location_dict.items(), key=lambda kv: (int(kv[1] == current),))
        for fleet, location in location_dict:
            if location == ():
                continue
            self.find_path_initial(location, has_ambush=has_ambush)
            attr = f'cost_{fleet}'
            for grid in self:
                grid.__setattr__(attr, grid.cost)

    def _find_path(self, location):
        """
        Args:
            location (tuple): 目标坐标。

        Returns:
            list[tuple]: 行走路线。

        Examples:
            MAP_7_2._find_path(node2location('H2'))
            [(2, 2), (3, 2), (4, 2), (5, 2), (6, 2), (6, 1), (7, 1)]  # ['C3', 'D3', 'E3', 'F3', 'G3', 'G2', 'H2']
        """
        if self[location].cost == 0:
            return [location]
        if self[location].connection is None:
            return None
        res = [location]
        while 1:
            location = self[location].connection
            if len(res) > 30:
                logger.warning('[地图-路径] 路径过长')
                logger.warning(res)
                # exit(1)
            if location is not None:
                res.append(location)
            else:
                break
        res.reverse()

        if len(res) == 0:
            logger.warning('[地图-路径] 未找到路径。目的地: %s' % str(location))
            return [location, location]

        return res

    def _find_route_node(self, route, step=0, turning_optimize=False):
        """
        Args:
            route (list[tuple]): 网格坐标列表。
            step (int): 活动地图中的舰队步数，默认为 0。
            turning_optimize (bool): 为 True 时优化路线以减少伏击。

        Returns:
            list[tuple]: 行走节点列表。

        Examples:
            MAP_7_2._find_route_node([(2, 2), (3, 2), (4, 2), (5, 2), (6, 2), (6, 1), (7, 1)])
            [(6, 2), (7, 1)]
        """
        if turning_optimize:
            res = []
            diff = np.abs(np.diff(route, axis=0))
            turning = np.diff(diff, axis=0)[:, 0]
            indexes = np.where(turning == -1)[0] + 1
            for index in indexes:
                if not self[route[index]].is_fleet:
                    res.append(index)
                else:
                    logger.info(f'[地图-路径] 避让路径节点: {self[route[index]]}')
                    if (index > 1) and (index - 1 not in indexes):
                        res.append(index - 1)
                    if (index < len(route) - 2) and (index + 1 not in indexes):
                        res.append(index + 1)
            res.append(len(route) - 1)
            # res = [4, 6]
            if step == 0:
                return [route[index] for index in res]
        else:
            if step == 0:
                return [route[-1]]
            # 最后一个节点的索引
            # res = [6]
            res = [max(len(route) - 1, 0)]

        res.insert(0, 0)
        inserted = []
        for left, right in zip(res[:-1], res[1:]):
            for index in list(range(left, right, step))[1:]:
                way_node = self[route[index]]
                if way_node.is_fleet or way_node.is_portal or way_node.is_flare:
                    logger.info(f'[地图-路径] 避让路径节点: {way_node}')
                    if (index > 1) and (index - 1 not in res):
                        inserted.append(index - 1)
                    if (index < len(route) - 2) and (index + 1 not in res):
                        inserted.append(index + 1)
                else:
                    inserted.append(index)
            inserted.append(right)
        res = inserted
        # res = [3, 6, 8]
        return [route[index] for index in res]

    def find_path(self, location, step=0, turning_optimize=False):
        """计算从当前舰队位置到目标位置的路径。

        先通过 Dijkstra 算法找到最短路径，然后处理传送门和迷宫分段，
        最后对每段路径提取关键行走节点。

        Args:
            location (str, tuple): 目标网格坐标或节点名。
            step (int): 活动地图中的舰队步数，默认为 0（仅走到终点）。
            turning_optimize (bool): 为 True 时优化路线以减少伏击。

        Returns:
            list[tuple]: 行走节点列表，每个元素为网格坐标。
        """
        location = location_ensure(location)

        path = self._find_path(location)
        if path is None or not len(path):
            logger.warning('[地图-路径] 未找到路径，返回目的地')
            return [location]
        logger.info('[地图-路径] 完整路径: %s' % '[' + ', ' .join([location2node(grid) for grid in path]) + ']')

        portal_path = []
        index = [0]
        for i, loca in enumerate(zip(path[:-1], path[1:])):
            grid = self[loca[0]]
            if grid.is_portal and grid.portal_link == loca[1]:
                index += [i, i + 1]
            if grid.is_maze and i != 0:
                index += [i]
        if len(path) not in index:
            index.append(len(path))
        for start, end in zip(index[:-1], index[1:]):
            if end - start == 1 and self[path[start]].is_portal and self[path[start]].portal_link == path[end]:
                continue
            local_path = path[start:end + 1]
            local_path = self._find_route_node(local_path, step=step, turning_optimize=turning_optimize)
            portal_path += local_path
            logger.info('[地图-路径] 路径: %s' % '[' + ', ' .join([location2node(grid) for grid in local_path]) + ']')
        path = portal_path

        return path

    def grid_covered(self, grid, location=None):
        """
        Args:
            grid (GridInfo): 格子对象。
            location (list[tuple[int]]): 被覆盖格子的相对坐标。

        Returns:
            SelectedGrids: 被覆盖的格子集合。
        """
        if location is None:
            covered = [tuple(np.array(grid.location) + upper) for upper in grid.covered_grid()]
        else:
            covered = [tuple(np.array(grid.location) + upper) for upper in location]
        covered = [self[upper] for upper in covered if upper in self]
        return SelectedGrids(covered)

    def missing_get(self, battle_count, mystery_count=0, siren_count=0, carrier_count=0, mode='normal'):
        """计算缺失和可能出现的敌人数量。

        根据当前战斗次数和已识别的敌人，计算各种敌人类型（普通敌人、
        神秘、塞壬、Boss、航母）的缺失数量和可能出现的数量。

        Args:
            battle_count (int): 当前战斗次数。
            mystery_count (int): 已遇到的神秘格子数。
            siren_count (int): 已击败的塞壬数。
            carrier_count (int): 已识别的航母数。
            mode (str): 扫描模式。

        Returns:
            tuple[dict, dict]: ``(may, missing)``，may 为各类型可能出现的数量，
                missing 为各类型缺失的数量。
        """
        try:
            missing = self.spawn_data_stack[battle_count].copy()
        except IndexError:
            missing = self.spawn_data_stack[-1].copy()
        may = {'enemy': 0, 'mystery': 0, 'siren': 0, 'boss': 0, 'carrier': 0}
        missing['enemy'] -= battle_count - siren_count
        missing['mystery'] -= mystery_count
        missing['siren'] -= siren_count
        missing['carrier'] = carrier_count - self.select(is_enemy=True, may_enemy=False).count \
            if mode == 'carrier' else 0
        for grid in self:
            for attr in ['enemy', 'mystery', 'siren', 'boss']:
                if grid.__getattribute__('is_' + attr):
                    missing[attr] -= 1
        missing['enemy'] += len(self.fortress_data[0]) - self.select(is_fortress=True).count
        for route in self.bouncing_enemy_data:
            if not route.select(may_bouncing_enemy=True):
                # 弹跳敌人已清除，重新计为一个敌人
                missing['enemy'] += 1

        for upper in self.map_covered:
            if (upper.may_enemy or mode == 'movable') and not upper.is_enemy:
                may['enemy'] += 1
            if upper.may_mystery and not upper.is_mystery:
                may['mystery'] += 1
            if (upper.may_siren or mode == 'movable') and not upper.is_siren:
                may['siren'] += 1
            if upper.may_boss and not upper.is_boss:
                may['boss'] += 1
            if upper.may_carrier:
                may['carrier'] += 1

        logger.attr('缺失敌人',
                    ', '.join([f'{k[:2].upper()}:{str(v).rjust(2)}' for k, v in missing.items() if k != 'battle']))
        logger.attr('可能敌人',
                    ', '.join([f'{k[:2].upper()}:{str(v).rjust(2)}' for k, v in may.items()]))
        return may, missing

    def missing_is_none(self, battle_count, mystery_count=0, siren_count=0, carrier_count=0, mode='normal'):
        """判断是否所有敌人已被发现（无缺失）。

        Args:
            battle_count (int): 当前战斗次数。
            mystery_count (int): 已遇到的神秘格子数。
            siren_count (int): 已击败的塞壬数。
            carrier_count (int): 已识别的航母数。
            mode (str): 扫描模式。

        Returns:
            bool: 是否所有敌人都已被发现。
        """
        if self.poor_map_data:
            return False

        may, missing = self.missing_get(battle_count, mystery_count, siren_count, carrier_count, mode)

        for key in may.keys():
            if missing[key] != 0:
                return False

        return True

    def missing_predict(self, battle_count, mystery_count=0, siren_count=0, carrier_count=0, mode='normal'):
        """根据缺失数量预测未探索格子中的敌人。

        当某个格子可能是某种敌人且缺失数量等于可能出现的数量时，
        直接将该格子预测为该类型敌人。

        Args:
            battle_count (int): 当前战斗次数。
            mystery_count (int): 已遇到的神秘格子数。
            siren_count (int): 已击败的塞壬数。
            carrier_count (int): 已识别的航母数。
            mode (str): 扫描模式。
        """
        if self.poor_map_data:
            return False

        may, missing = self.missing_get(battle_count, mystery_count, siren_count, carrier_count, mode)

        # predict
        for upper in self.map_covered:
            for attr in ['enemy', 'mystery', 'siren', 'boss']:
                if upper.__getattribute__('may_' + attr) and missing[attr] > 0 and missing[attr] == may[attr]:
                    logger.info('[地图-预测] 预测 %s 为 %s' % (location2node(upper.location), attr))
                    upper.__setattr__('is_' + attr, True)
            if carrier_count:
                if upper.may_carrier and missing['carrier'] > 0 and missing['carrier'] == may['carrier']:
                    logger.info('[地图-预测] 预测 %s 为敌舰' % location2node(upper.location))
                    upper.__setattr__('is_enemy', True)

    def select(self, **kwargs):
        """
        Args:
            **kwargs: 格子属性键值对。

        Returns:
            SelectedGrids: 符合条件的格子集合。
        """
        result = []
        for grid in self:
            flag = True
            for k, v in kwargs.items():
                if grid.__getattribute__(k) != v:
                    flag = False
            if flag:
                result.append(grid)

        return SelectedGrids(result)

    def to_selected(self, grids):
        """
        Args:
            grids (list): 坐标列表。

        Returns:
            SelectedGrids: 格子集合。
        """
        return SelectedGrids([self[location_ensure(loca)] for loca in grids])

    def flatten(self):
        """
        Returns:
            list[GridInfo]: 所有格子的列表。
        """
        return self.grids.values()
