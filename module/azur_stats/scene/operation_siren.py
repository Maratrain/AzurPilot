"""
大世界场景分析。

组合大世界奖励、物品识别和区域检测，对大世界（Operation Siren）
的截图进行完整的场景级分析，提取掉落物品和区域信息。
"""

import typing as t
from dataclasses import dataclass

from module.azur_stats.image.auto_search_reward import AutoSearchItem
from module.azur_stats.image.get_items import GetItems
from module.azur_stats.image.opsi_reward import OpsiReward
from module.azur_stats.image.opsi_zone import OpsiZone, DataOpsiZone
from module.azur_stats.scene.base import SceneBase


@dataclass
class DataOpsiItems:
    imgid: str
    server: str

    # Standardized zone name in English
    zone: str
    # UNKNOWN, DANGEROUS, SAFE, OBSCURE, ABYSSAL, STRONGHOLD, ARCHIVE
    zone_type: str
    # Zone ID in game
    zone_id: int
    # 1 to 6
    hazard_level: int

    item: str
    amount: int
    tag: str


class SceneOperationSiren(SceneBase, OpsiReward, GetItems, OpsiZone):
    AUTO_SEARCH_ITEM_TEMPLATE_FOLDER = './assets/stats/opsi_reward_items'
    ITEM_TEMPLATE_FOLDER = './assets/stats/opsi_items'

    def extract_assets(self):
        zone = None
        for _, image in enumerate(self.images):
            if self.is_opsi_zone(image):
                zone = 1
                break
        if zone is None:
            return

        for image in self.images:
            if self.is_opsi_reward(image):
                self.extract_auto_search_item_template(image)
            if self.get_items_count(image):
                self.extract_item_template(image)

    def parse_scene(self):
        zone = None
        cleared = -1
        for index, image in enumerate(self.images):
            if self.is_opsi_zone(image):
                zone = self.parse_opsi_zone(image)
                cleared = index
                break
        if zone is None:
            return

        for index, image in enumerate(self.images):
            if index == cleared:
                continue
            elif index < cleared:
                if self.is_get_items(image):
                    items = self.parse_get_items(image)
                    for item in self._operation_siren_product(zone, items):
                        yield item
                if self.is_opsi_reward(image):
                    items = self.parse_auto_search_reward(image)
                    for item in self._operation_siren_product(zone, items):
                        yield item
            elif index > cleared:
                if self.is_get_items(image):
                    items = self.parse_get_items(image)
                    for item in self._operation_siren_product(zone, items, tag='log'):
                        yield item
                if self.is_opsi_reward(image):
                    items = self.parse_auto_search_reward(image)
                    for item in self._operation_siren_product(zone, items, tag='scan'):
                        yield item

    def _operation_siren_product(self, zone: DataOpsiZone, items: t.Iterable[AutoSearchItem], tag: str = None) \
            -> t.Iterable[DataOpsiItems]:
        for item in items:
            yield DataOpsiItems(
                imgid=self.imgid,
                server=self.server,
                zone=zone.zone,
                zone_type=zone.zone_type,
                zone_id=zone.zone_id,
                hazard_level=zone.hazard_level,
                item=item.name,
                amount=item.amount,
                tag=tag if tag else item.tag,
            )
