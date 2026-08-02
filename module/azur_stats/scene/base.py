"""
截图场景分析基类。

提供 SceneBase 基类，将截图文件加载、随机 ID 生成和批量处理
抽象为统一接口。是所有场景级截图分析器的父类。
"""

import os
import random
import typing as t

import numpy as np
from tqdm import tqdm

from module.azur_stats.image.base import ImageBase
from module.base.decorator import cached_property
from module.base.resource import del_cached_property
from module.base.utils import load_image
from module.config.utils import iter_folder
from module.statistics.utils import unpack, ImageError


def random_imgid():
    """
    Returns:
        str: Random imgid.
    """
    return ''.join(random.sample('0123456789abcdef', 16))


class SceneBase(ImageBase):
    """
    Base class to detect a single screen shot
    """
    # Raw input from load_file()
    file = None
    # Drop record, a list of screenshots
    images: t.List[np.ndarray] = []

    def load_file(self, file):
        """
        Args:
            file: Image file, or image array, or list of image array

        Raises:
            ImageError: If unable to read image file
        """
        self.file = file
        self.clear_cache()

        if isinstance(file, str):
            self.images = unpack(load_image(file))
        elif isinstance(file, list):
            self.images = file
        elif isinstance(file, np.ndarray):
            self.images = unpack(file)
        else:
            raise ImageError(f'Unknown image file: {file}')

    def clear_cache(self):
        super().clear_cache()
        del_cached_property(self, 'first')
        del_cached_property(self, 'followings')
        del_cached_property(self, 'last')
        del_cached_property(self, 'imgid')

    def parse_scene(self):
        """
        Parse images.
        Scene should override this method.
        """
        return []

    def extract_assets(self):
        """
        Extract item templates.
        Scene that drop items should override this method.
        """
        return

    @cached_property
    def first(self) -> np.ndarray:
        """
        The first screenshot of a drop record
        """
        return self.images[0]

    @cached_property
    def followings(self) -> t.List[np.ndarray]:
        """
        The second screenshots and the behind
        """
        return self.images[1:]

    @cached_property
    def last(self) -> np.ndarray:
        """
        The last screenshot of a drop record
        """
        return self.images[-1]

    @cached_property
    def imgid(self) -> str:
        """
        `imgid` to identify this image
        """
        if isinstance(self.file, str):
            return os.path.splitext(os.path.basename(self.file))[0]
        else:
            return random_imgid()

    def apply_to_folder(self, folder, method):
        """
        Execute a method to all images in the given folder

        Args:
            folder (str):
            method (callable):

        Returns:

        """
        files = list(iter_folder(folder, ext='.png'))
        for file in tqdm(files):
            self.load_file(file)
            method()
