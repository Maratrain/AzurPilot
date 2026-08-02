"""地图检测后端示例模块。展示如何实现自定义地图检测后端，
定义 load/detect/set_backend 等接口供 MapDetector 调用。"""

import numpy as np

from module.config.config import AzurLaneConfig


class DetectionBackendExample:
    """
    Example for map detection backend.
    """

    def __init__(self, config):
        """
        Args:
            config (AzurLaneConfig):
        """
        self.config = config

    """
    Input
    """
    def load(self, image):
        """
        Args:
            image (np.ndarray): Shape (720, 1280, 3)
        """
        self.image = image
        pass  # Do map detection here.

    """
    Output
    """
    image: np.ndarray
    config: AzurLaneConfig
    # Four edges in bool, or has attribute __bool__
    left_edge: bool
    right_edge: bool
    lower_edge: bool
    upper_edge: bool

    # A method that yield grid location and corners.
    def generate(self):
        """
        Yields (tuple): ((x, y), [upper-left, upper-right, bottom-left, bottom-right])
        """
        for x in range(8):
            for y in range(5):
                yield (x, y), [(0, 0), (100, 0), (0, 100), (100, 100)]
