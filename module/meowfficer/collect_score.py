"""指挥喵收集流程中的天赋评分扩展。

把 :mod:`module.meowfficer.collect` 里「有没有特殊天赋」的颜色粗判，升级成
**真实的天赋名识别 + 攻略口径评分**：

- 复用收集流程本来就会打开的天赋详情面板（``_meow_talent_cap_handle``），
  顺手 OCR 出天赋名，因此不需要任何新的游戏资源与页面导航。
- 由 ``MeowfficerTrain_ScoreTalents`` 开关控制，**默认关闭**，不影响既有行为。
- ``MeowfficerTrain_ScoreThreshold`` 大于 0 时，评分会参与「是否锁定保留」的判断。

开关关闭时，本模块的所有方法都是空操作，收集流程与原来完全一致。
"""

from module.logger import logger
from module.meowfficer.score import evaluate
from module.meowfficer.score_report import render_summary


class MeowfficerCollectScore:
    """给 ``MeowfficerCollect`` 用的评分混入类。

    混入类不定义 ``__init__``，状态在每次收集开始时由 :meth:`meow_score_reset`
    惰性初始化，避免影响原有构造流程。
    """

    def meow_score_enabled(self):
        """评分功能是否开启。"""
        return bool(getattr(self.config, 'MeowfficerTrain_ScoreTalents', False))

    def meow_score_reset(self):
        """开始收集一只新猫前清空上一次的识别结果。"""
        self._meow_score_talents = []
        self._meow_score_result = None
        self._meow_score_cat = None

    def _meow_score_ocr(self):
        """惰性创建并缓存 OCR 实例（首次加载模型较慢）。

        OCR 不可用时返回 ``None`` 并记住失败，避免每只猫都重试、也避免异常
        顺着收集流程抛出去把原有自动化带崩。
        """
        if getattr(self, '_meow_score_ocr_failed', False):
            return None
        ocr = getattr(self, '_meow_score_ocr_instance', None)
        if ocr is not None:
            return ocr
        try:
            from module.ocr.al_ocr import AlOcr
            ocr = AlOcr(name='cn')
            ocr.init()
        except Exception as e:
            logger.warning(f'[指挥喵-评分] OCR 初始化失败，本次跳过评分：{e}')
            self._meow_score_ocr_failed = True
            return None
        self._meow_score_ocr_instance = ocr
        return ocr

    def meow_score_capture(self, image):
        """从一张天赋详情面板截图里识别天赋并累加。

        识别失败只记警告：评分是附加功能，不应该影响收集本身。

        Args:
            image (np.ndarray): 当前天赋详情面板的截图。
        """
        if not self.meow_score_enabled():
            return
        ocr = self._meow_score_ocr()
        if ocr is None:
            return
        try:
            from module.meowfficer.score_ocr import recognize
            talents, cat = recognize(image, ocr=ocr)
        except Exception as e:
            logger.warning(f'[指挥喵-评分] 天赋识别失败，跳过这一条：{e}')
            return
        if cat and not getattr(self, '_meow_score_cat', None):
            self._meow_score_cat = cat
        known = {t.line for t in self._meow_score_talents}
        for talent in talents:
            if talent.line not in known:
                self._meow_score_talents.append(talent)
                known.add(talent.line)

    def meow_score_finish(self, cat=None):
        """本只猫的天赋都识别完后，评分并写日志。

        Args:
            cat (str): 指挥喵名字，用于挑选评分口径；未知时传 ``None``。

        Returns:
            ScoreResult: 评分结果；未开启评分或没识别到天赋时返回 ``None``。
        """
        if not self.meow_score_enabled():
            return None
        talents = getattr(self, '_meow_score_talents', None)
        if not talents:
            logger.info('[指挥喵-评分] 本次未识别到天赋，跳过评分')
            self._meow_score_result = None
            return None

        result = evaluate(talents, cat=cat or getattr(self, '_meow_score_cat', None))
        self._meow_score_result = result
        logger.info(f'[指挥喵-评分] {render_summary(result)}')
        return result

    def meow_score_passes(self):
        """评分是否达到「锁定保留」的门槛。

        Returns:
            bool: 未开启评分、门槛为 0、或评分失败时一律返回 ``True``，
            保证不改变原有的保留行为。
        """
        if not self.meow_score_enabled():
            return True
        threshold = int(getattr(self.config, 'MeowfficerTrain_ScoreThreshold', 0) or 0)
        result = getattr(self, '_meow_score_result', None)
        if threshold <= 0 or result is None:
            return True
        rubric = result.rubrics.get(result.primary[0]) if result.primary else None
        if rubric is None:
            return True
        passed = rubric.score100 >= threshold
        logger.attr('[指挥喵-评分] 是否达到保留门槛',
                    f'{rubric.score100} / {threshold} -> {passed}')
        return passed
