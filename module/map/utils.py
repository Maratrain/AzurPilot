"""地图工具函数。

本模块提供战役地图系统的辅助工具函数，包括：
- 坐标转换：``location_ensure`` 统一坐标格式（节点名/元组/GridInfo 对象）
- 相机位置计算：``camera_1d``、``camera_2d`` 计算覆盖地图所需的相机位置
- 活动区域检测：``get_map_active_area`` 获取非空格子的边界范围
- 出生点相机位置：``camera_spawn_point`` 计算出生点附近的最近相机位置
- 方向随机化：``random_direction`` 从方向描述生成随机方向向量
- 可移动敌人匹配：``match_movable`` 通过距离矩阵匹配移动前后的敌人
"""

import numpy as np

from module.base.utils import node2location
from module.map_detection.grid_info import GridInfo


def location_ensure(location):
    """将各种格式的坐标统一转换为元组格式。

    支持三种输入格式：
    - 带有 ``location`` 属性的对象（如 GridInfo）
    - 字符串节点名（如 'D5'）
    - 元组坐标（如 (3, 4)）

    Args:
        location: 网格坐标，可以是 GridInfo 对象、字符串节点名或元组。

    Returns:
        tuple[int]: 坐标元组，如 ``(4, 3)``。
    """
    if hasattr(location, 'location'):
        return location.location
    elif isinstance(location, str):
        return node2location(location)
    else:
        return location


def camera_1d(shape, sight):
    """计算一维方向上的相机位置序列。

    根据地图长度和相机视野范围，生成能覆盖整行/列的相机位置列表。

    Args:
        shape (int): 地图在该维度上的长度。
        sight (list[int]): 相机视野范围 ``[start, end]``，start 可为负数。

    Returns:
        list[int]: 相机位置列表。
    """
    start, step = abs(sight[0]), sight[1] - sight[0] + 1
    if shape <= start:
        out = shape // 2
    else:
        out = list(range(start, 26, step))
        out.append(shape - sight[1])
        out = [x for x in set(out) if x <= shape - sight[1]]
    return out


def camera_2d(area, sight):
    """计算二维地图上覆盖全部活动区域所需的相机位置网格。

    通过在 X 和 Y 方向上分别计算相机位置，再组合成二维网格。

    Args:
        area (tuple[int]): 地图活动区域 ``(左上X, 左上Y, 右下X, 右下Y)``。
            例如：地图形状为 I9，但第 1 行、第 9 行、A 列和 I 列为空时，
            area 为 ``(1, 1, 8, 8)``。
        sight (tuple[int]): 相机视野 ``(左上X, 左上Y, 右下X, 右下Y)``。

    Returns:
        list[tuple]: 相机位置列表，每个元素为 ``(x, y)`` 坐标。
    """
    x = camera_1d(shape=area[2] - area[0], sight=[sight[0], sight[2]])
    y = camera_1d(shape=area[3] - area[1], sight=[sight[1], sight[3]])
    out = np.array(np.meshgrid(x, y)).T.reshape(-1, 2) + area[:2]
    return [tuple(c) for c in out]


def get_map_active_area(grids):
    """获取地图活动区域的边界。

    遍历所有格子，排除海洋（``--``）和陆地（``++``）格子，
    计算剩余活动格子的最小包围矩形。

    Args:
        grids (dict): 格子字典，键为坐标元组，值为 GridInfo 或具有
            ``__str__`` 方法的对象。

    Returns:
        tuple: 活动区域 ``(左上X, 左上Y, 右下X, 右下Y)``。
    """

    def is_active(g):
        g = g.str if isinstance(g, GridInfo) else str(g)
        return g != '--' and g != '++'

    locations = [loca for loca, grid in grids.items() if is_active(grid)]
    bottom_right = np.max(locations, axis=0)
    upper_left = np.min(locations, axis=0)
    return np.append(upper_left, bottom_right)


def camera_spawn_point(camera_list, sp_list):
    """计算出生点附近的最近相机位置。

    对于每个出生点，找到曼哈顿距离最近的相机位置，
    用于在出生点位置生成摄像头扫描数据。

    Args:
        camera_list (list[tuple]): 已有的相机位置列表（CampaignMap.camera_data）。
        sp_list (list[tuple]): 出生点坐标列表。

    Returns:
        list[tuple]: 出生点检测专用的相机位置列表（去重后）。
    """
    camera_sp = []
    camera_list = np.array(camera_list)
    for sp in sp_list:
        diff = np.sum(np.abs(camera_list - sp), axis=1)
        camera_sp.append(tuple(camera_list[np.argmin(diff)].tolist()))

    return list(set(camera_sp))


def random_direction(direction):
    """从方向描述生成随机方向向量。

    根据方向字符串确定固定轴的方向，未指定的轴随机生成。
    空字符串表示完全随机方向。

    Args:
        direction (str): 方向描述，如 'upper-left'、'upper-right'、'bottom-left'、
            'bottom-right'、'upper'、'bottom'、'left'、'right' 等。

    Returns:
        tuple[int]: 方向向量，如 ``(-1, 1)`` 表示左下方。
    """
    direction = direction.lower()
    x = 1 if np.random.uniform() > 0.5 else -1
    y = 1 if np.random.uniform() > 0.5 else -1
    if 'left' in direction:
        x = -1
    elif 'right' in direction:
        x = 1
    if 'upper' in direction:
        y = -1
    elif 'bottom' in direction:
        y = 1
    return (x, y)


def combine(before, after, limit):
    """组合排列候选索引。

    为匹配算法生成所有可能的索引组合，确保同一索引不会重复出现。

    Args:
        before (list[list[int]]): 已有的组合列表。
        after (list[int]): 候选索引列表。
        limit (int): 索引上限，等于候选数量。

    Yields:
        list[int]: 组合后的索引列表。
    """
    after += [limit]
    for b in before:
        for a in after:
            index = b + [a]
            match = [m for m in index if m < limit]
            if len(set(match)) == len(match):
                yield index


def match_movable(before, spawn, after, fleets, fleet_step=2):
    """匹配移动前后的可移动敌人（如塞壬）。

    通过构建距离矩阵和排列搜索，将移动前的敌人位置与移动后的位置
    进行最优匹配。用于追踪移动型敌人的位移。

    Args:
        before (list[tuple]): 移动前的敌人位置列表。
        spawn (list[tuple]): 可能新增的敌人出生点列表。
        after (list[tuple]): 移动后的敌人位置列表。
        fleets (list[tuple]): 舰队位置列表。
        fleet_step (int): 舰队/敌人的最大移动步数，默认为 2。

    Returns:
        tuple[list[tuple], list[tuple]]: 匹配成功的位置对
            ``(matched_before, matched_after)``。

    Examples:
        >>> before = [(0, 2), (0, 0), (1, 0), (2, 4), (7, 19)]
        >>> after = [(7, 9), (0, 3), (0, 1), (1, 1), (2, 5)]
        >>> match_movable(before, [], after, [])
        ([(0, 2), (0, 0), (1, 0), (2, 4)], [(0, 3), (0, 1), (1, 1), (2, 5)])
    """
    base_weight = -10000
    encourage_weight = -100
    before_len = len(before)
    after_len = len(after)
    before = before + spawn
    after = after + fleets
    x = len(after)
    y = len(before)
    distance = np.ones((y, x), dtype=int) * base_weight
    for i1, g1 in enumerate(before):
        for i2, g2 in enumerate(after):
            distance[i1, i2] = fleet_step - sum(abs(np.subtract(g1, g2)))

    distance[distance < 0] = base_weight
    distance[before_len:, :] += encourage_weight
    distance[:, after_len:] += encourage_weight
    distance = np.maximum(distance, base_weight)
    # print(distance)
    # [[-100    1    1    0 -100]
    #  [-100 -100    1    0 -100]
    #  [-100 -100    0    1 -100]
    #  [-100 -100 -100 -100    1]
    #  [-100 -100 -100 -100 -100]]

    permutations = [[]]
    for row in distance:
        match = np.where(row >= encourage_weight)[0].tolist()
        permutations = list(combine(permutations, match, limit=x))
        if not len(permutations):
            permutations = [[x]]

    if len(permutations) == 0 or len(permutations[0]) == 0:
        return [], []
    else:
        permutations = np.array(permutations)
        permutations = permutations[np.argsort(np.sum(permutations, axis=1))]
        distance = np.pad(distance, ((0, 0), (0, 1)), mode='constant', constant_values=base_weight)
        index_x = permutations
        index_y = list(range(y)) * int(index_x.shape[0])
        match = distance[index_y, index_x.ravel()].reshape(-1, y)
        match = np.sum(match, axis=1)
        best_match = permutations[int(np.argmax(match))]
        before = [before[index] for index, match in enumerate(best_match) if match < x]
        after = [after[index] for index in best_match if index < x]
        return before, after
