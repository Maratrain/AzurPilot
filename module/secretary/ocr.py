from module.ocr.ocr import Digit, crop


class SecretaryDigit(Digit):

    def ocr(self, image, direct_ocr=False):

        if direct_ocr:
            image_list = [
                self.pre_process(i)
                for i in image
            ]
        else:
            image_list = [
                self.pre_process(crop(image, area))
                for area in self.buttons
            ]

        # 关键：跳过 crop_to_text
        # image_list = [crop_to_text(i) for i in image_list]

        result_list = self.cnocr.atomic_ocr_for_single_lines(
            image_list,
            self.alphabet
        )

        result_list = [
            ''.join(result)
            for result in result_list
        ]

        result_list = [
            self.after_process(result)
            for result in result_list
        ]

        if len(self.buttons) == 1:
            result_list = result_list[0]

        return result_list