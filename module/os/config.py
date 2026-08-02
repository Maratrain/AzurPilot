"""大世界（Operation Siren）配置类。

定义大世界地图操作所需的配置参数，包括：
- 地图检测参数（透视检测、网格识别）
- 滑动参数（滑动倍率、最小距离）
- 战斗相关配置（塞壬检测、情绪管理等）
- 故事选项配置

大世界的配置参数与主线战役不同，需要单独定义。
OSConfig 被 OperationSiren 等大世界模块使用。
"""


class OSConfig:
    """大世界配置参数类。

    定义大世界地图操作所需的所有配置参数。
    这些参数覆盖了地图检测、滑动控制和战斗管理的默认值。

    Attributes:
        STORY_OPTION (int): 剧情选项，-2 表示自动选择。
        MAP_FOCUS_ENEMY_AFTER_BATTLE (bool): 战斗后是否聚焦到敌人位置。
        MAP_HAS_SIREN (bool): 地图是否有塞壬敌人。
        MAP_HAS_FLEET_STEP (bool): 地图是否有步数限制。
        IGNORE_LOW_EMOTION_WARN (bool): 是否忽略低情绪警告。
        MAP_GRID_CENTER_TOLERANCE (float): 网格中心对齐容差。
        MAP_SWIPE_DROP (float): 最小滑动距离阈值。
        MAP_SWIPE_MULTIPLY (tuple): 滑动距离倍率。
        DETECTION_BACKEND (str): 检测后端（'perspective' 或 'homography'）。
    """
    STORY_OPTION = -2

    MAP_FOCUS_ENEMY_AFTER_BATTLE = True
    MAP_HAS_SIREN = True
    MAP_HAS_FLEET_STEP = True
    IGNORE_LOW_EMOTION_WARN = False

    MAP_GRID_CENTER_TOLERANCE = 0.3
    MAP_SWIPE_DROP = 0.35
    MAP_SWIPE_MULTIPLY = (1.174, 1.200)
    MAP_SWIPE_MULTIPLY_MINITOUCH = (1.135, 1.160)
    MAP_SWIPE_MULTIPLY_MAATOUCH = (1.102, 1.126)

    DETECTION_BACKEND = 'perspective'
    MID_DIFF_RANGE_H = (103 - 3, 103 + 3)
    MID_DIFF_RANGE_V = (103 - 3, 103 + 3)
    INTERNAL_LINES_FIND_PEAKS_PARAMETERS = {
        'height': (80, 255 - 40),
        'width': (1.5, 10),
        'prominence': 35,
        'distance': 35,
    }
    EDGE_LINES_FIND_PEAKS_PARAMETERS = {
        'height': (255 - 40, 255),
        'prominence': 10,
        'distance': 50,
        'wlen': 1000
    }
    INTERNAL_LINES_HOUGHLINES_THRESHOLD = 75
    EDGE_LINES_HOUGHLINES_THRESHOLD = 75

    HOMO_EDGE_DETECT = True
    HOMO_CANNY_THRESHOLD = (40, 60)
    HOMO_EDGE_HOUGHLINES_THRESHOLD = 300

    MAP_ENEMY_GENRE_DETECTION_SCALING = {
        'DD': 0.8,
        'CL': 0.8,
        'CA': 0.8,
        'CV': 0.8,
        'BB': 0.8,
    }
    MAP_SWIPE_PREDICT = False
