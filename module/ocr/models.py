"""OCR 模型实例的懒加载管理。

提供全局共享的 OCR 模型实例集合 `OCR_MODEL`，通过 `cached_property`
实现按需加载。各逻辑名称统一使用通用 PP-OCRv6 识别模型，
仅保留不同入口以便按语言上下文选择：

- azur_lane: 游戏 UI 数字/字母识别的默认入口
- azur_lane_jp: 日服运行的 azur_lane 入口
- ppocr_v6: 通用 PP-OCRv6 识别模型
- cnocr: 中文文本识别入口（别名 cn）
- jp: 日文识别入口
- tw: 繁体中文识别入口

使用示例:
    >>> from module.ocr.models import OCR_MODEL
    >>> text = OCR_MODEL.azur_lane.ocr(image)

模型在首次访问时自动初始化，后续访问复用已加载的实例。
通过 `del_cached_property` 可释放模型以节省内存。
"""

from module.base.decorator import cached_property
from module.ocr.al_ocr import AlOcr


class OcrModel:
    """OCR 模型集合，提供各语言识别模型的懒加载访问。

    每个属性返回一个 AlOcr 实例，首次访问时初始化模型。
    模型实例在进程生命周期内保持，直到被显式释放。
    """

    @cached_property
    def azur_lane(self):
        """碧蓝航线英文数字识别模型。用于游戏 UI 中的数字、等级、时间等。"""
        return AlOcr(name='azur_lane')

    @cached_property
    def azur_lane_jp(self):
        """日文服务器专用识别模型。"""
        return AlOcr(name='azur_lane_jp')

    @cached_property
    def ppocr_v6(self):
        """通用 PP-OCRv6 识别模型。"""
        return AlOcr(name='ppocr_v6')

    @cached_property
    def cnocr(self):
        """中文识别模型（中+英混合文本）。"""
        return AlOcr(name='cn')

    @cached_property
    def jp(self):
        """日文识别模型。"""
        return AlOcr(name='jp')

    @cached_property
    def tw(self):
        """繁体中文识别模型。"""
        return AlOcr(name='tw')


# 全局共享的 OCR 模型实例，所有模块通过此对象访问 OCR 功能
OCR_MODEL = OcrModel()
