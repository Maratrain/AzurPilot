from module.base.decorator import cached_property
from module.ocr.al_ocr import AlOcr


class OcrModel:
    @cached_property
    def azur_lane(self):
        return AlOcr(name='azur_lane')

    @cached_property
    def azur_lane_jp(self):
        return AlOcr(name='azur_lane_jp')

    @cached_property
    def ppocr_v6(self):
        return AlOcr(name='ppocr_v6')

    @cached_property
    def cnocr(self):
        return AlOcr(name='cn')

    @cached_property
    def jp(self):
        return AlOcr(name='jp')

    @cached_property
    def tw(self):
        return AlOcr(name='tw')

OCR_MODEL = OcrModel()
