"""
获得物品截图识别。

从战斗结算画面中识别获得的物品列表，支持单排/双排布局检测，
处理物品网格定位和数量 OCR，以及信息栏遮挡的异常情况。
"""

import numpy as np
import typing as t

from module.azur_stats.image.base import ImageBase
from module.base.button import ButtonGrid
from module.base.decorator import cached_property
from module.base.utils import crop, rgb2gray
from module.combat.assets import GET_ITEMS_1, GET_ITEMS_2, GET_ITEMS_3
from module.handler.assets import INFO_BAR_1
from module.logger import logger
from module.statistics.assets import GET_ITEMS_ODD
from module.statistics.item import Item, ItemGrid
from module.statistics.utils import ImageError

ITEM_GRIDS_1_ODD = ButtonGrid(origin=(336, 298), delta=(128, 0), button_shape=(96, 96), grid_shape=(5, 1))
ITEM_GRIDS_1_EVEN = ButtonGrid(origin=(400, 298), delta=(128, 0), button_shape=(96, 96), grid_shape=(4, 1))
ITEM_GRIDS_2 = ButtonGrid(origin=(336, 227), delta=(128, 142), button_shape=(96, 96), grid_shape=(5, 2))
ITEM_GRIDS_3 = ButtonGrid(origin=(336, 223), delta=(128, 149), button_shape=(96, 96), grid_shape=(5, 2))


class GetItemsCoveredByInfoBar(ImageError):
    pass


class GetItemsInvalid(ImageError):
    """ Trying to detect a non get_items image """
    pass


class ZeroAmountError(ImageError):
    """ Item amount equal 0 """
    pass


class TooManyNewTemplate(ImageError):
    """ New item templates >= 2 """
    pass


def merge_get_items(item_list_1: t.Iterable[Item], item_list_2: t.Iterable[Item]):
    """
    Args:
        item_list_1:
        item_list_2:

    Yields:
        Item:
    """
    items = set(list(item_list_1) + list(item_list_2))
    for item in items:
        yield item


def has_odd_items(image):
    """
    Args:
        image (np.ndarray):

    Returns:
        bool: If there're 1/3/5 items in one row
    """
    image = crop(image, GET_ITEMS_ODD.area)
    return np.mean(rgb2gray(image) > 127) > 0.1


class GetItems(ImageBase):
    ITEM_TEMPLATE_FOLDER = f'./assets/stats_basic'
    # True when extracting templates for new scene
    # False at normal run
    ALLOW_TOO_MANY_NEW_TEMPLATE = False

    @cached_property
    def item_grid(self) -> ItemGrid:
        grid = ItemGrid(None, {}, template_area=(40, 21, 89, 70), amount_area=(60, 71, 91, 92))
        grid.item_class = Item
        grid.similarity = 0.92
        grid.amount_area = (60, 71, 91, 92)
        grid.load_template_folder(self.ITEM_TEMPLATE_FOLDER)
        return grid

    def is_get_items(self, image):
        return bool(self.classify_server(GET_ITEMS_1, image)) or bool(self.classify_server(GET_ITEMS_2, image))

    def parse_get_items(self, image, name=True, amount=True, tag=True) -> t.Iterator[Item]:
        """
        Args:
            image (np.ndarray):
            name (bool):
            amount (bool):
            tag (bool):

        Yields:
            Item:
        """
        self._get_items_load(image)

        if self.item_grid.grids is None:
            return
        else:
            self.item_grid.predict(image, name=name, amount=amount, tag=tag)
            items = self.before_revise_items(self.item_grid.items)
            for item in items:
                before = str(item)
                item = self.revise_item(item)
                after = str(item)
                if before != after:
                    logger.info(f'[统计-物品] 物品 {before} 修正为 {after}')
                if item.amount == 0:
                    raise ZeroAmountError(f'Invalid item amount: {item}')
                yield item

    def extract_item_template(self, image, folder=None):
        """
        Args:
            image:
            folder: Folder to save new templates.
                If None, use self.ITEM_TEMPLATE_FOLDER
        """
        if folder is None:
            folder = self.ITEM_TEMPLATE_FOLDER
        self._get_items_load(image)
        if self.item_grid.grids is not None:
            new = self.item_grid.extract_template(image, folder=folder)
            new = len(new.keys())
            if not GetItems.ALLOW_TOO_MANY_NEW_TEMPLATE and new >= 2:
                raise TooManyNewTemplate(f'Extracted {new} new templates')

    def before_revise_items(self, items: t.List[Item]) -> t.List[Item]:
        return items

    def revise_item(self, item: Item) -> Item:
        return item

    def _get_items_load(self, image):
        """
        Args:
            image (np.ndarray):
        """
        self.item_grid.grids = None
        if INFO_BAR_1.appear_on(image):
            raise GetItemsCoveredByInfoBar('get_items image has info_bar')
        elif self.classify_server(GET_ITEMS_1, image, offset=(5, 0)):
            self.item_grid.grids = ITEM_GRIDS_1_ODD if has_odd_items(image) else ITEM_GRIDS_1_EVEN
        elif self.classify_server(GET_ITEMS_2, image, offset=(5, 0)):
            self.item_grid.grids = ITEM_GRIDS_2
        elif self.classify_server(GET_ITEMS_3, image, offset=(5, 0)):
            self.item_grid.grids = ITEM_GRIDS_3
        else:
            raise GetItemsInvalid('Stat image is not a get_items image')

    def drop_has_get_items(self, images):
        """
        Whether the given images has at least one get_items

        Args:
            images (list):

        Returns:
            bool:
        """
        for image in images:
            if self.is_get_items(image):
                return True
        return False

    def parse_get_items_chain(self, images) -> t.Iterator[t.Iterator[Item]]:
        """
        Parse an image chain of get_items.
        GET_ITEMS_3 has 2 images, so this method merge them into 1.

        Yields:
            iter[iter[Item]]:
        """
        page1 = None
        for image in images:
            count = self.get_items_count(image)
            if count == 1:
                yield self.parse_get_items(image)
                page1 = None
            elif count == 2:
                if page1 is None:
                    # Normal 2 rows of items
                    yield self.parse_get_items(image)
                else:
                    # Second image, merge previous
                    page1 = self.parse_get_items(page1)
                    page2 = self.parse_get_items(image)
                    yield merge_get_items(page1, page2)
                page1 = None
            elif count == 3:
                # first image, add to cache
                page1 = image
