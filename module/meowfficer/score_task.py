"""指挥喵天赋评分工具（工具Plus）。

三种模式：

- ``screenshot``：扫描本地截图目录，逐张识别天赋并评分（默认）。
  可以直接指向 ``DropRecord_MeowfficerTalent`` 落盘的天赋截图，或自己的截图目录。
- ``device``：截图当前设备画面并评分，适合手动逐只翻猫时连续跑。
- ``scan``：自动遍历猫窝列表，逐只选中猫、打开天赋页截图识别，覆盖全部已拥有的猫。
  页面操作在 :mod:`module.meowfficer.scan` 里，本模块只负责评分与报告。

前两种模式**不操作游戏、不做页面导航**，因此按 ``module/daemon/ocr_benchmark.py`` 的形态
自写 ``__init__`` 而不继承 ``ModuleBase``，也就没有强制状态循环的约束；``scan`` 模式
的页面操作全部委托给 :class:`~module.meowfficer.scan.MeowfficerScanner`，同样不引入继承。

评分口径来自公开攻略（详见 :mod:`module.meowfficer.score`），不是游戏官方数值。
"""

import json
import os
import time
from datetime import datetime

from rich.table import Table

from module.config.config import AzurLaneConfig
from module.exception import RequestHumanTakeover
from module.logger import logger
from module.meowfficer.score import evaluate
from module.meowfficer.score_report import render_text

# 支持的图片后缀
IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')


class MeowfficerScore:
    """指挥喵天赋评分任务。

    Attributes:
        config (AzurLaneConfig): 配置实例。
        device (Device): 设备实例，仅 ``device`` 模式使用，可为 ``None``。
        results (list): 本次运行收集到的评分结果，元素为
            ``(来源名, ScoreResult)``。
    """

    def __init__(self, config, device=None, task=None):
        """
        Args:
            config: ``AzurLaneConfig`` 实例，或配置名（如 ``'alas'``）。
            device: 设备实例；``screenshot`` 模式下不会被使用。
            task: 任务名，传入时会 ``init_task`` 以绑定配置。
        """
        if isinstance(config, AzurLaneConfig):
            self.config = config
            if task is not None:
                self.config.init_task(task)
        else:
            self.config = AzurLaneConfig(config, task=task)
        self.device = device
        self.results = []

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------

    def _cfg(self, name, default=None):
        """读取本任务的配置项，缺省时回退到默认值。"""
        return getattr(self.config, f'MeowfficerScore_{name}', default)

    # ------------------------------------------------------------------
    # 核心流程
    # ------------------------------------------------------------------

    def _collect_images(self, folder):
        """列出待评分的截图。

        Args:
            folder: 截图所在目录。

        Returns:
            list[str]: 按修改时间升序排列的图片绝对路径。
        """
        if not folder or not os.path.isdir(folder):
            logger.warning(f'[指挥喵-评分] 截图目录不存在：{folder}')
            return []
        limit = int(self._cfg('MaxImages', 50) or 50)
        files = []
        for name in os.listdir(folder):
            if name.lower().endswith(IMAGE_EXT):
                files.append(os.path.join(folder, name))
        files.sort(key=lambda p: os.path.getmtime(p))
        if len(files) > limit:
            logger.info(f'[指挥喵-评分] 共 {len(files)} 张截图，按配置只取最近 {limit} 张')
            files = files[-limit:]
        return files

    def _score_image(self, image, ocr, name, cat=None):
        """识别单张截图并评分。

        Args:
            image: BGR 图像。
            ocr: 已初始化的 OCR 实例。
            name: 来源名（文件名或设备截图序号），用于日志与报告。
            cat: 手动指定的猫名；为 ``None`` 时自动识别。

        Returns:
            ``ScoreResult``；这张图里没有天赋时返回 ``None``。
        """
        from module.meowfficer.score_ocr import recognize

        try:
            talents, detected_cat = recognize(image, ocr=ocr)
        except Exception as e:
            logger.warning(f'[指挥喵-评分] {name}：识别失败，跳过（{e}）')
            return None
        if not talents:
            logger.info(f'[指挥喵-评分] {name}：未识别到天赋，跳过')
            return None
        result = evaluate(talents, cat=cat or detected_cat)
        score = result.rubrics[result.primary[0]] if result.primary else None
        logger.attr(f'{name} 猫名', result.cat or '未知')
        if score is not None:
            logger.attr(f'{name} 评分', f'{score.label} {score.tier} {score.score100}/100')
        self.results.append((name, result))
        return result

    def _load_ocr(self):
        """加载中文 OCR 模型。

        首次运行需要联网下载模型；失败时给出可操作的提示，而不是抛原始堆栈 ——
        否则每个模式都会在每张图上报一次 warning、最后"成功"却没有任何结果。
        """
        from module.ocr.al_ocr import AlOcr
        try:
            ocr = AlOcr(name='cn')
            ocr.init()
        except Exception as e:
            raise RequestHumanTakeover(
                f'OCR 模型加载失败（首次运行需联网下载，请检查网络后重试）：{e}') from e
        return ocr

    def _run_screenshots(self):
        """``screenshot`` 模式：批量评分本地截图。"""
        folder = self._cfg('Folder', './screenshots/meowfficer_talent')
        files = self._collect_images(folder)
        if not files:
            logger.warning(f'[指挥喵-评分] 目录里没有图片：{folder}')
            return
        logger.info(f'[指挥喵-评分] 待评分截图 {len(files)} 张，目录：{folder}')

        import cv2

        ocr = self._load_ocr()

        for index, path in enumerate(files, 1):
            logger.hr(f'第 {index}/{len(files)} 张', level=2)
            image = cv2.imread(path)
            if image is None:
                logger.warning(f'[指挥喵-评分] 读图失败：{path}')
                continue
            self._score_image(image, ocr, os.path.basename(path))

    @staticmethod
    def _fingerprint(image):
        """画面粗指纹：用于跳过没有变化的画面，避免空转 OCR。

        Args:
            image: BGR 图像。

        Returns:
            str: 下采样后的 MD5，画面不变则指纹不变。
        """
        import hashlib

        import numpy as np
        coarse = np.ascontiguousarray(image[::8, ::8])
        return hashlib.md5(coarse.tobytes()).hexdigest()

    def _run_device(self):
        """``device`` 模式：自动跟拍当前画面，检测到新的天赋面板就评分。

        不需要按任何按键：工具每隔 ``DeviceInterval`` 秒截一次屏，画面没变化就跳过，
        出现没见过的天赋组合（猫名 + 天赋集合）才识别评分，累计到 ``DeviceShots``
        只不同的猫为止。用户在游戏里翻猫即可，也可以直接停掉任务。
        """
        if self.device is None:
            raise RequestHumanTakeover(
                'device 模式需要连接设备：请确认模拟器已启动且 ADB 可连，'
                '或把「评分来源」改成「本地截图」')
        wanted = max(1, int(self._cfg('DeviceShots', 1) or 1))
        interval = max(0.5, float(self._cfg('DeviceInterval', 2) or 2))

        from module.meowfficer.score_ocr import recognize
        ocr = self._load_ocr()

        logger.info(f'[指挥喵-评分] 自动跟拍已开始：目标 {wanted} 只猫，轮询间隔 {interval}s，'
                    '请在游戏里逐只打开指挥喵天赋页')
        seen = set()
        last_fp = None
        polls = 0
        while len(self.results) < wanted:
            self.device.screenshot()
            fingerprint = self._fingerprint(self.device.image)
            if fingerprint == last_fp:
                time.sleep(interval)
                continue
            last_fp = fingerprint
            polls += 1

            try:
                talents, cat = recognize(self.device.image, ocr=ocr)
            except Exception as e:
                logger.warning(f'[指挥喵-评分] 识别失败，继续跟拍：{e}')
                time.sleep(interval)
                continue
            if not talents:
                time.sleep(interval)
                continue

            # 同一只猫的同一套天赋只评一次；OCR 抖动不会重复计数
            key = (cat, tuple(sorted(f'{t.line}:{t.level}' for t in talents)))
            if key in seen:
                time.sleep(interval)
                continue
            seen.add(key)

            name = f'auto_{len(self.results) + 1:02d}_{datetime.now().strftime("%H%M%S")}'
            self._score_image(self.device.image, ocr, name, cat=cat)
            logger.info(f'[指挥喵-评分] 进度 {len(self.results)}/{wanted}'
                        f'（已轮询 {polls} 次，共截图 {len(self.results)} 只）')
            time.sleep(interval)

        logger.info(f'[指挥喵-评分] 自动跟拍结束，共评出 {len(self.results)} 只')

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------

    def _log_summary(self):
        """用 rich 表格输出本次运行的汇总。"""
        if not self.results:
            logger.warning('[指挥喵-评分] 本次没有产生任何评分结果')
            return
        table = Table(show_lines=True)
        table.add_column('来源', style='cyan', no_wrap=True)
        table.add_column('指挥喵')
        table.add_column('口径')
        table.add_column('档位')
        table.add_column('参考分', justify='right')
        for name, result in self.results:
            key = result.primary[0] if result.primary else None
            rubric = result.rubrics.get(key)
            table.add_row(
                name,
                result.cat or '未知',
                rubric.label if rubric else '-',
                rubric.tier if rubric else '-',
                f'{rubric.score100}/100' if rubric else '-',
            )
        logger.hr('评分汇总', level=1)
        logger.print(table, justify='center')

    def _save_report(self):
        """把评分卡写成 Markdown + 自包含 HTML 报告（HTML 用于好看地查看/分享）。"""
        path = self._cfg('ReportPath', './log/meowfficer_score.md')
        if not path or not self.results:
            return
        stamp = f'{datetime.now():%Y-%m-%d %H:%M:%S}'
        lines = ['# 指挥喵天赋评分报告', '',
                 f'生成时间：{stamp}',
                 f'共 {len(self.results)} 只', '',
                 '> 评分口径来自公开攻略（28法则执行篇 / 详细上手攻略），不是游戏官方数值。', '']
        for name, result in self.results:
            lines.append(f'## {result.cat or "未知"}（{name}）')
            lines.append('')
            lines.append('```')
            lines.append(render_text(result))
            lines.append('```')
            lines.append('')

        html_path = os.path.splitext(path)[0] + '.html'
        json_path = os.path.splitext(path)[0] + '.json'
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            logger.info(f'[指挥喵-评分] Markdown 报告已写入 {path}')
        except OSError as e:
            logger.warning(f'[指挥喵-评分] Markdown 报告写入失败：{e}')

        try:
            from module.meowfficer.score_report import render_html, to_payload
            payload = to_payload(self.results, generated_at=stamp)
            os.makedirs(os.path.dirname(os.path.abspath(html_path)), exist_ok=True)
            with open(html_path, 'w', encoding='utf-8') as f:
                f.write(render_html(self.results, generated_at=stamp))
            logger.info(f'[指挥喵-评分] HTML 报告已写入 {html_path}'
                        '（可直接双击用浏览器打开）')
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            logger.info(f'[指挥喵-评分] JSON 报告已写入 {json_path}（供前端面板读取）')
        except Exception as e:
            logger.warning(f'[指挥喵-评分] HTML/JSON 报告写入失败：{e}')

    def _run_scan(self):
        """``scan`` 模式：自动遍历猫窝，逐只读取天赋并评分。

        页面操作全部交给 :class:`~module.meowfficer.scan.MeowfficerScanner`，
        这里只把每只猫的天赋送去评分并记录结果。
        """
        if self.device is None:
            raise RequestHumanTakeover(
                'scan 模式需要连接设备：请确认模拟器已启动且 ADB 可连，'
                '或把「评分来源」改成「本地截图」')

        from module.meowfficer.scan import MeowfficerScanner

        limit = max(0, int(self._cfg('ScanLimit', 0) or 0))
        passes = max(1, int(self._cfg('ScanPasses', 12) or 12))

        scanner = MeowfficerScanner(self.config, self.device)
        scanned = scanner.scan_all(limit=limit, passes=passes)
        if not scanned:
            logger.warning('[指挥喵-评分] 扫描没有拿到任何指挥喵，'
                           '请确认游戏停留在「指挥喵 - 猫窝」页面后重试')
            return

        for cat, talents in scanned:
            result = evaluate(talents, cat=cat)
            rubric = result.rubrics[result.primary[0]] if result.primary else None
            logger.attr(f'{cat} 猫名', result.cat or '未知')
            if rubric is not None:
                logger.attr(f'{cat} 评分', f'{rubric.label} {rubric.tier} {rubric.score100}/100')
            self.results.append((cat, result))

    def run(self):
        """任务入口。

        Pages:
            in: any
            out: any
        """
        logger.hr('指挥喵天赋评分', level=1)
        source = self._cfg('Source', 'screenshot')
        logger.attr('评分来源', source)

        if source == 'device':
            self._run_device()
        elif source == 'scan':
            self._run_scan()
        else:
            self._run_screenshots()

        self._log_summary()
        self._save_report()

        if not self.results:
            logger.error('[指挥喵-评分] 本次没有产生任何评分结果。请检查：'
                         '① 截图目录里是否有天赋面板截图（或设备是否停在指挥喵天赋页）；'
                         '② 日志里是否有 OCR 相关警告（首次运行需要联网下载模型）。')


def run_meowfficer_score(config, device=None):
    """工具任务包装函数，异常处理与 ``run_ocr_benchmark`` 保持一致。

    Args:
        config (AzurLaneConfig): 配置实例。
        device (Device): 设备实例；``device`` 模式必须传入，否则会请求人工接管。

    Returns:
        bool: 成功为 ``True``，需要人工接管为 ``False``。
    """
    try:
        MeowfficerScore(config, device=device, task='MeowfficerScore').run()
        return True
    except RequestHumanTakeover:
        logger.critical('[指挥喵-评分] 错误 请求人类接管')
        return False
