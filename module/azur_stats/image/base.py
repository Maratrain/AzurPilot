"""
截图识别基类。

提供 ImageBase 基类用于单张截图的检测分析。包含服务器分类、
按钮缓存和通用图像处理方法，是所有截图识别器的父类。
"""

import typing as t

import numpy as np

from module.base.button import Button
from module.base.utils import color_similarity_2d, crop
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_3, GET_ITEMS_3_CHECK
from module.base.template import Template
from module.exception import ScriptError

# Key: Button object to cache
# Value: dict
#     Key: str, server name
#     Value: Button object of the specific server
CLASSIFY_CACHE: t.Dict[Button, t.Dict[str, Button]] = {}


class ImageBase:
    """
    Base class to detect a single screen shot
    """
    # cn, en, jp, tw, or '' if button not appear
    # This value if set at the first time classify_server() called
    server: str = ''

    def classify_server(self, button, image, offset=(20, 20), scaling=1.0, threshold=0.85):
        """
        Get server name if a button appear on image

        Args:
            button (Button, Template):
            image: Screenshot to detect, or None to use the `self.image`
            offset: For Button
            scaling: For Template
            threshold:

        Returns:
            str: cn, en, jp, tw, or '' if button not appear
        """
        if button not in CLASSIFY_CACHE:
            CLASSIFY_CACHE[button] = button.split_server()

        for server, server_button in CLASSIFY_CACHE[button].items():
            if isinstance(server_button, Button):
                if server_button.match(image, offset=offset, similarity=threshold):
                    if not self.server:
                        self.server = server
                    return server
            elif isinstance(server_button, Template):
                if server_button.match(image, scaling=scaling, similarity=threshold):
                    if not self.server:
                        self.server = server
                    return server
            else:
                raise ScriptError(f'classify_server() gets unknown button: {button}')

        # No match
        if not self.server:
            self.server = ''
        return ''

    def clear_cache(self):
        """
        This method is called when loading a new image.
        """
        self.server = ''

    def image_color_count(self, image, button, color, threshold=221, count=50):
        """
        Args:
            image:
            button (Button, tuple): Button instance or area.
            color (tuple): RGB.
            threshold: 255 means colors are the same, the lower the worse.
            count (int): Pixels count.

        Returns:
            bool:
        """
        if isinstance(button, Button):
            image = crop(image, button.area)
        else:
            image = crop(image, button)
        mask = color_similarity_2d(image, color=color) > threshold
        return np.sum(mask) > count

    def get_items_count(self, image):
        """
        Args:
            image:

        Returns:
            int: Rows of get items, 1 to 3, or 0 if it's not a get_items image
        """
        if self.classify_server(GET_ITEMS_3, image, offset=(5, 5)):
            if self.image_color_count(image, GET_ITEMS_3_CHECK, color=(255, 255, 255), threshold=221, count=100):
                return 3
            else:
                return 2
        if self.classify_server(GET_ITEMS_1, image, offset=(5, 5)):
            return 1
        return 0
