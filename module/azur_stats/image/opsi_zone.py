"""
大世界区域截图识别。

从大世界地图截图中识别当前区域名称和类型，使用 OCR 读取地图名称，
并通过 ZoneManager 将显示名称映射为标准化的英文区域标识。
"""

from dataclasses import dataclass

from module.azur_stats.image.base import ImageBase
from module.base.decorator import cached_property
from module.exception import ScriptError
from module.ocr.ocr import Ocr
from module.os.assets import MAP_NAME
from module.os.globe_zone import ZoneManager
from module.os_handler.assets import IN_MAP
from module.statistics.utils import ImageError

OCR_OPSI_ZONE = Ocr(MAP_NAME, lang='cnocr', letter=(214, 231, 255), threshold=127, name='OCR_OS_MAP_NAME')


@dataclass
class DataOpsiZone:
    # Standardized zone name in English
    zone: str
    # UNKNOWN, DANGEROUS, SAFE, OBSCURE, ABYSSAL, STRONGHOLD, ARCHIVE
    zone_type: str
    # Zone ID in game
    zone_id: int
    # 1 to 6
    hazard_level: int


class OpsiZoneInvalid(ImageError):
    """ Unknown zone name """
    pass


class OpsiZone(ImageBase):
    def is_opsi_zone(self, image) -> bool:
        return bool(self.classify_server(IN_MAP, image, offset=(200, 5)))

    def parse_opsi_zone(self, image) -> DataOpsiZone:
        name = OCR_OPSI_ZONE.ocr(image)
        return self._opsi_zone_name_convert(name)

    @cached_property
    def _opsi_zone_manager(self) -> ZoneManager:
        return ZoneManager()

    def _opsi_zone_name_convert(self, name: str) -> DataOpsiZone:
        """
        Args:
            name: Zone name from OCR.

        Returns:
            DataOpsiZone:
        """
        types = 'UNKNOWN'
        if '安全' in name:
            types = 'SAFE'
        elif '隐秘' in name:
            types = 'OBSCURE'
        elif '深渊' in name:
            types = 'ABYSSAL'
        elif '要塞' in name:
            types = 'STRONGHOLD'
        elif '档案' in name:
            types = 'ARCHIVE'
        elif name.endswith('-'):
            pass
        else:
            types = 'DANGEROUS'

        if '-' in name:
            prefix = name.split('-')[0]
        else:
            prefix = name.rstrip('安全隐秘塞壬要塞深渊海域-')
        try:
            zone = self._opsi_zone_manager.name_to_zone(prefix)
        except ScriptError:
            raise OpsiZoneInvalid(f'Unknown zone name: {name}')

        return DataOpsiZone(
            zone=zone.en,
            zone_type=types,
            zone_id=zone.zone_id,
            hazard_level=zone.hazard_level,
        )
