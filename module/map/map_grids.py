"""地图格子集合操作。

本模块提供了 ``SelectedGrids`` 和 ``RoadGrids`` 两个核心集合类，
用于对地图上的格子进行批量查询、过滤、排序和集合运算。

``SelectedGrids`` 是格子的有序集合，支持属性过滤、索引查询、
左连接、集合运算（并集、交集、差集）以及多种排序策略。
``RoadGrids`` 用于表示路径上的障碍格子组合，支持路障检测。
"""

import operator
import typing as t


class SelectedGrids:
    """地图格子的有序集合。

    封装一组格子对象，提供丰富的查询、过滤、排序和集合运算方法。
    支持迭代、索引、包含检查等 Python 标准协议。

    Attributes:
        grids (list): 格子对象列表。
        indexes (dict): 预计算的索引缓存，由 ``create_index()`` 构建。
    """

    def __init__(self, grids):
        self.grids = grids
        self.indexes: t.Dict[tuple, SelectedGrids] = {}

    def __iter__(self):
        """迭代集合中的所有格子。

        Yields:
            格子对象。
        """
        return iter(self.grids)

    def __getitem__(self, item):
        """按索引或切片获取格子。

        Args:
            item (int | slice): 整数索引返回单个格子，切片返回新的 SelectedGrids。

        Returns:
            GridInfo | SelectedGrids: 单个格子或格子子集。
        """
        if isinstance(item, int):
            return self.grids[item]
        else:
            return SelectedGrids(self.grids[item])

    def __contains__(self, item):
        """判断格子是否在集合中。

        Args:
            item: 格子对象。

        Returns:
            bool: 格子是否在集合中。
        """
        return item in self.grids

    def __str__(self):
        """返回集合中所有格子的字符串表示。

        Returns:
            str: 以逗号分隔的格子字符串列表。
        """
        # return str([str(grid) for grid in self])
        return '[' + ', '.join([str(grid) for grid in self]) + ']'

    def __len__(self):
        """返回集合中的格子数量。

        Returns:
            int: 格子数量。
        """
        return len(self.grids)

    def __bool__(self):
        """判断集合是否非空。

        Returns:
            bool: 集合是否包含至少一个格子。
        """
        return self.count > 0

    # def __getattr__(self, item):
    #     return [grid.__getattribute__(item) for grid in self.grids]

    @property
    def location(self):
        """获取集合中所有格子的坐标。

        Returns:
            list[tuple]: 坐标列表，每个元素为 ``(x, y)``。
        """
        return [grid.location for grid in self.grids]

    @property
    def cost(self):
        """获取集合中所有格子的寻路代价。

        Returns:
            list[int]: 代价列表。
        """
        return [grid.cost for grid in self.grids]

    @property
    def weight(self):
        """获取集合中所有格子的权重。

        Returns:
            list[int]: 权重列表。
        """
        return [grid.weight for grid in self.grids]

    @property
    def count(self):
        """获取集合中的格子数量。

        Returns:
            int: 格子数量。
        """
        return len(self.grids)

    def select(self, **kwargs):
        """按属性值过滤格子。

        返回一个新集合，仅包含所有指定属性与给定值匹配的格子。
        属性值要求类型和值都相等。

        Args:
            **kwargs: 格子属性键值对，如 ``is_enemy=True``, ``may_boss=True``。

        Returns:
            SelectedGrids: 符合条件的格子子集。
        """
        def matched(obj):
            flag = True
            for k, v in kwargs.items():
                obj_v = obj.__getattribute__(k)
                if type(obj_v) != type(v) or obj_v != v:
                    flag = False
            return flag

        return SelectedGrids([grid for grid in self.grids if matched(grid)])

    def create_index(self, *attrs):
        """根据指定属性创建索引。

        将格子按给定属性的值进行分组，建立索引以加速后续的 ``indexed_select`` 查询。

        Args:
            *attrs: 要索引的属性名。

        Returns:
            dict: 索引字典，键为属性值元组，值为对应的 SelectedGrids。
        """
        indexes = {}
        # index_keys = [(grid.__getattribute__(attr) for attr in attrs) for grid in self.grids]
        for grid in self.grids:
            k = tuple(grid.__getattribute__(attr) for attr in attrs)
            try:
                indexes[k].append(grid)
            except KeyError:
                indexes[k] = [grid]

        indexes = {k: SelectedGrids(v) for k, v in indexes.items()}
        self.indexes = indexes
        return indexes

    def indexed_select(self, *values):
        """使用预计算索引查询格子。

        Args:
            *values: 索引键值，与 ``create_index`` 中的属性顺序对应。

        Returns:
            SelectedGrids: 匹配的格子集合，无匹配时返回空集合。
        """
        return self.indexes.get(values, SelectedGrids([]))

    def left_join(self, right, on_attr, set_attr, default=None):
        """对右侧集合执行左连接操作。

        根据 ``on_attr`` 指定的属性将左侧（self）和右侧格子进行匹配，
        并将右侧格子的 ``set_attr`` 属性复制到左侧格子上。

        Args:
            right (SelectedGrids): 右侧集合（要连接的集合）。
            on_attr (list[str]): 连接条件的属性名列表。
            set_attr (list[str]): 需要从右侧复制到左侧的属性名列表。
            default: 当右侧无匹配时，设置的默认值。

        Returns:
            SelectedGrids: self，属性已被修改。
        """
        right.create_index(*on_attr)
        for grid in self:
            attr_value = tuple([grid.__getattribute__(attr) for attr in on_attr])
            right_grid = right.indexed_select(*attr_value).first_or_none()
            if right_grid is not None:
                for attr in set_attr:
                    grid.__setattr__(attr, right_grid.__getattribute__(attr))
            else:
                for attr in set_attr:
                    grid.__setattr__(attr, default)

        return self

    def filter(self, func):
        """使用函数过滤格子。

        Args:
            func (callable): 过滤函数，接收一个格子对象并返回 bool。

        Returns:
            SelectedGrids: 满足条件的格子子集。
        """
        return SelectedGrids([grid for grid in self if func(grid)])

    def set(self, **kwargs):
        """批量设置集合中所有格子的属性。

        Args:
            **kwargs: 要设置的属性键值对。
        """
        for grid in self:
            for key, value in kwargs.items():
                grid.__setattr__(key, value)

    def get(self, attr):
        """获取集合中所有格子的指定属性值。

        Args:
            attr (str): 属性名。

        Returns:
            list: 各格子的属性值列表。
        """
        return [grid.__getattribute__(attr) for grid in self.grids]

    def call(self, func, **kwargs):
        """对集合中每个格子调用指定方法并收集返回值。

        Args:
            func (str): 方法名。
            **kwargs: 传递给方法的关键字参数。

        Returns:
            list: 各格子调用结果的列表。
        """
        return [grid.__getattribute__(func)(**kwargs) for grid in self]

    def first_or_none(self):
        """获取集合中的第一个格子，如果集合为空则返回 None。

        Returns:
            GridInfo | None: 第一个格子或 None。
        """
        try:
            return self.grids[0]
        except IndexError:
            return None

    def add(self, grids):
        """与另一个集合合并（使用 ``__hash__`` 去重）。

        Args:
            grids (SelectedGrids): 要合并的格子集合。

        Returns:
            SelectedGrids: 合并后的格子集合。
        """
        return SelectedGrids(list(set(self.grids + grids.grids)))

    def add_by_eq(self, grids):
        """与另一个集合合并，使用 ``__eq__`` 去重（而非 ``__hash__``）。

        当格子对象未正确实现 ``__hash__`` 时使用此方法替代 ``add()``。

        Args:
            grids (SelectedGrids): 要合并的格子集合。

        Returns:
            SelectedGrids: 合并后的格子集合。
        """
        new = []
        for grid in self.grids + grids.grids:
            if grid not in new:
                new.append(grid)

        return SelectedGrids(new)

    def intersect(self, grids):
        """与另一个集合取交集（使用 ``__hash__`` 比较）。

        Args:
            grids (SelectedGrids): 要取交集的格子集合。

        Returns:
            SelectedGrids: 交集格子集合。
        """
        return SelectedGrids(list(set(self.grids).intersection(set(grids.grids))))

    def intersect_by_eq(self, grids):
        """与另一个集合取交集，使用 ``__eq__`` 比较（而非 ``__hash__``）。

        Args:
            grids (SelectedGrids): 要取交集的格子集合。

        Returns:
            SelectedGrids: 交集格子集合。
        """
        new = []
        for grid in self.grids:
            if grid in grids.grids:
                new.append(grid)

        return SelectedGrids(new)

    def delete(self, grids):
        """从集合中删除指定格子。

        Args:
            grids (SelectedGrids): 要删除的格子集合。

        Returns:
            SelectedGrids: 删除后的格子集合。
        """
        g = [grid for grid in self.grids if grid not in grids]
        return SelectedGrids(g)

    def sort(self, *args):
        """按指定属性对格子排序。

        Args:
            *args (str): 用于排序的属性名，按优先级从高到低排列。

        Returns:
            SelectedGrids: 排序后的格子集合。
        """
        if not self:
            return self
        if len(args):
            grids = sorted(self.grids, key=operator.attrgetter(*args))
            return SelectedGrids(grids)
        else:
            return self

    def sort_by_camera_distance(self, camera):
        """按与相机位置的曼哈顿距离排序格子。

        Args:
            camera (tuple): 相机位置坐标 ``(x, y)``。

        Returns:
            SelectedGrids: 按距离从近到远排序的格子集合。
        """
        import numpy as np
        if not self:
            return self
        location = np.array(self.location)
        diff = np.sum(np.abs(location - camera), axis=1)
        # grids = [x for _, x in sorted(zip(diff, self.grids))]
        grids = tuple(np.array(self.grids)[np.argsort(diff)])
        return SelectedGrids(grids)

    def sort_by_clock_degree(self, center=(0, 0), start=(0, 1), clockwise=True):
        """按时钟角度排序格子。

        以 center 为原点，以 start 方向为 0 度，按角度对格子进行排序。
        默认顺时针排序。

        Args:
            center (tuple): 原点坐标。
            start (tuple): 起始方向坐标，此方向被视为 theta=0。
            clockwise (bool): True 为顺时针，False 为逆时针。

        Returns:
            SelectedGrids: 按角度排序的格子集合。
        """
        import numpy as np
        if not self:
            return self
        vector = np.subtract(self.location, center)
        theta = np.arctan2(vector[:, 1], vector[:, 0]) / np.pi * 180
        vector = np.subtract(start, center)
        theta = theta - np.arctan2(vector[1], vector[0]) / np.pi * 180
        if not clockwise:
            theta = -theta
        theta[theta < 0] += 360
        grids = tuple(np.array(self.grids)[np.argsort(theta)])
        return SelectedGrids(grids)


class RoadGrids:
    """路径障碍格子组合。

    用于表示地图路径上的障碍点，每个障碍点可能对应多个候选格子（例如
    一个障碍点可能包含两选一的敌人格子）。支持路障检测和路线组合。

    Attributes:
        grids (list[SelectedGrids]): 障碍格子组列表，每个元素是一个候选格子集合。
    """

    def __init__(self, grids):
        """
        Args:
            grids (list):
        """
        self.grids = []
        for grid in grids:
            if isinstance(grid, list):
                self.grids.append(SelectedGrids(grids=grid))
            else:
                self.grids.append(SelectedGrids(grids=[grid]))

    def __str__(self):
        """返回路径障碍的字符串表示。

        Returns:
            str: 以 ' - ' 分隔的各障碍点字符串。
        """
        return str(' - '.join([str(grid) for grid in self.grids]))

    def roadblocks(self):
        """获取已确认的路障格子。

        当一个障碍点中所有格子都是敌人时，该障碍点被视为已确认的路障。

        Returns:
            SelectedGrids: 已确认路障的格子集合。
        """
        grids = []
        for block in self.grids:
            if block.count == block.select(is_enemy=True).count:
                grids += block.grids
        return SelectedGrids(grids)

    def potential_roadblocks(self):
        """获取潜在路障格子。

        当障碍点中仅有一个非敌人格子（即还需要击败一个敌人才能通过），
        且该障碍点中没有舰队或已清除的格子时，返回该障碍点中的敌人格子。

        Returns:
            SelectedGrids: 潜在路障中的敌人格子集合。
        """
        grids = []
        for block in self.grids:
            if any([grid.is_fleet for grid in block]):
                continue
            if any([grid.is_cleared for grid in block]):
                continue
            if block.count - block.select(is_enemy=True).count == 1:
                grids += block.select(is_enemy=True).grids
        return SelectedGrids(grids)

    def first_roadblocks(self):
        """获取第一个需要处理的路障格子。

        返回所有未清除且不含舰队的障碍点中的敌人格子。

        Returns:
            SelectedGrids: 需要处理的路障敌人格子集合。
        """
        grids = []
        for block in self.grids:
            if any([grid.is_fleet for grid in block]):
                continue
            if any([grid.is_cleared for grid in block]):
                continue
            if block.select(is_enemy=True).count >= 1:
                grids += block.select(is_enemy=True).grids
        return SelectedGrids(grids)

    def combine(self, road):
        """将两条路线的障碍点组合为笛卡尔积。

        对 self 和 road 中的每对障碍点取并集，生成所有可能的组合。

        Args:
            road (RoadGrids): 另一条路线的障碍组合。

        Returns:
            RoadGrids: 组合后的障碍集合。
        """
        out = RoadGrids([])
        for select_1 in self.grids:
            for select_2 in road.grids:
                select = select_1.add(select_2)
                out.grids.append(select)

        return out
