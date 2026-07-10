import os
import queue
import threading
import time
from pathlib import Path

import numpy as np
import cv2
from PIL import Image

from module.exception import RequestHumanTakeover
from module.logger import logger
from module.config.config import AzurLaneConfig
from module.config.utils import DEFAULT_CONFIG_NAME


def handle_ocr_error(e):
    logger.critical(f"Failed to load OCR dependencies: {e}")
    logger.critical(
        "[OCR] 无法加载 OCR 依赖，请安装微软 C++ 运行库 https://aka.ms/vs/17/release/vc_redist.x64.exe"
    )
    logger.critical("[OCR] 也有可能是 GPU 不支持加速引起，请尝试关闭 GPU 加速")
    logger.critical("[OCR] 如果上述方法都无法解决，请加群获取支持")
    raise RequestHumanTakeover


try:
    from rapidocr import RapidOCR, OCRVersion
    from rapidocr.utils.output import RapidOCROutput
    from rapidocr.ch_ppocr_rec import TextRecognizer
    from rapidocr.ch_ppocr_rec.typings import TextRecOutput
    from rapidocr.cal_rec_boxes import CalRecBoxes
    from rapidocr.ch_ppocr_det import TextDetector, TextDetOutput
    from rapidocr.utils.load_image import LoadImage
    from rapidocr.utils.process_img import get_rotate_crop_image
    from module.ocr.ncnn_ocr import NcnnRecOCR, supports_ncnn_model
except Exception as e:
    handle_ocr_error(e)


DET_DEBUG = False
REPO_ROOT = Path(__file__).resolve().parents[2]
PPOCRV6_EN_REC_KEYS_PATH = "bin/ocr_models/ppocr-v6/ppocrv6_en_dict.txt"
OCR_MODEL_VERSION_AUTO = 'auto'
ALAS_CTC_MODEL_VERSION = "alocr_en_900k"
ALAS_CTC_MODEL_PATH = "bin/ocr_models/azur_lane/alocr-en-us-900k-w768.dml.onnx"
ALAS_CTC_CHARSET = "0123456789:-/ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
ALAS_CTC_BLANK_ID = 0
ALAS_CTC_IMAGE_HEIGHT = 48
ALAS_CTC_MAX_WIDTH = 768
GENERIC_PPOCR_V6_PARAMS = (
    "bin/ocr_models/ppocr-v6/PP-OCRv6_small_rec.onnx",
    "bin/ocr_models/ppocr-v6/ppocrv6_dict.txt",
    OCRVersion.PPOCRV6,
)
AZUR_LANE_JP_V6_PARAMS = (
    "bin/ocr_models/azur_lane_jp/ap_azurlane_jp-v6_small_rec_nvidia.onnx",
    "bin/ocr_models/azur_lane_jp/ppocrv6_azurlane_jp_dict.txt",
    OCRVersion.PPOCRV6,
)


class RecOnlyOCR(RapidOCR):
    """只加载识别模型，跳过 det 和 cls 的 ONNX 模型加载。"""

    def _initialize(self, cfg):
        self.text_score = cfg.Global.text_score
        self.min_height = cfg.Global.min_height
        self.width_height_ratio = cfg.Global.width_height_ratio

        self.use_det = False
        self.text_det = None

        self.use_cls = False
        self.text_cls = None

        self.use_rec = cfg.Global.use_rec
        cfg.Rec.engine_cfg = cfg.EngineConfig[cfg.Rec.engine_type.value]
        cfg.Rec.font_path = cfg.Global.font_path
        cfg.Rec.model_root_dir = cfg.Global.get("model_root_dir", os.getcwd())
        self.text_rec = TextRecognizer(cfg.Rec)

        self.load_img = LoadImage()
        self.max_side_len = cfg.Global.max_side_len
        self.min_side_len = cfg.Global.min_side_len

        self.cal_rec_boxes = CalRecBoxes()
        self.return_word_box = cfg.Global.return_word_box
        self.return_single_char_box = cfg.Global.return_single_char_box
        self.cfg = cfg


class AlOcrCtcRecOCR:
    """900k 参数 CNN-CTC 英文识别模型，直接使用 ONNXRuntime 推理。"""

    def __init__(self, model_path, device="cpu"):
        try:
            import onnxruntime as ort
        except Exception as exc:
            handle_ocr_error(exc)

        self.model_path = self._resolve_path(model_path)
        if not self.model_path.is_file():
            raise FileNotFoundError(f"OCR model not found: {self.model_path}")

        self.device = device
        self.charset = ALAS_CTC_CHARSET
        self.blank_id = ALAS_CTC_BLANK_ID
        self.image_height = ALAS_CTC_IMAGE_HEIGHT
        self.max_width = ALAS_CTC_MAX_WIDTH
        self.load_image = LoadImage()

        providers = self._select_providers(ort)
        self.session = ort.InferenceSession(str(self.model_path), providers=providers)
        self.input_names = [item.name for item in self.session.get_inputs()]
        logger.info(
            f"Loaded OCR model '{ALAS_CTC_MODEL_VERSION}' on "
            f"{', '.join(self.session.get_providers())}"
        )

    @staticmethod
    def _resolve_path(model_path):
        path = Path(model_path)
        if path.is_absolute():
            return path
        return REPO_ROOT / path

    def _select_providers(self, ort):
        available = ort.get_available_providers()
        providers = []
        if os.name == 'nt' and self.device == 'gpu':
            if "DmlExecutionProvider" in available:
                providers.append("DmlExecutionProvider")
            else:
                logger.warning(
                    "DmlExecutionProvider is not available, falling back to CPU"
                )
        providers.append("CPUExecutionProvider")
        return providers

    def close(self):
        self.session = None

    def __call__(self, image_or_path):
        if self.session is None:
            raise RuntimeError("OCR model has been closed")

        start_time = time.perf_counter()
        image, width, original = self._preprocess(image_or_path)
        scores, lengths = self.session.run(
            None,
            {
                self.input_names[0]: image,
                self.input_names[1]: np.array([width], dtype=np.int64),
            },
        )
        text, score = self._decode(scores, lengths)
        return TextRecOutput(
            imgs=[original],
            txts=(text,),
            scores=(score,),
            word_results=(),
            elapse=time.perf_counter() - start_time,
        )

    def _preprocess(self, image_or_path):
        img = self.load_image(image_or_path)
        gray = self._to_gray(img)

        height, width = gray.shape[:2]
        if height <= 0 or width <= 0:
            raise ValueError(f"Invalid OCR image shape: {gray.shape}")

        scaled_width = max(1, int(round(width * (self.image_height / height))))
        scaled_width = min(scaled_width, self.max_width)
        resized = cv2.resize(gray, (scaled_width, self.image_height))

        canvas = np.full(
            (self.image_height, self.max_width),
            255,
            dtype=np.float32,
        )
        canvas[:, :scaled_width] = resized.astype(np.float32)
        array = canvas / 255.0
        array = (array - 0.5) / 0.5
        array = array[np.newaxis, np.newaxis, :, :].astype(np.float32)
        return array, scaled_width, img

    @staticmethod
    def _to_gray(img):
        arr = np.asarray(img)
        if arr.ndim == 2:
            gray = arr
        elif arr.ndim == 3 and arr.shape[2] == 1:
            gray = arr[:, :, 0]
        elif arr.ndim == 3 and arr.shape[2] == 4:
            gray = cv2.cvtColor(arr, cv2.COLOR_BGRA2GRAY)
        elif arr.ndim == 3:
            gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
        else:
            raise ValueError(f"Unsupported OCR image shape: {arr.shape}")

        if gray.dtype != np.uint8:
            gray = gray.astype(np.float32)
            if gray.size and gray.max() <= 1.0:
                gray *= 255.0
            gray = np.clip(gray, 0, 255).astype(np.uint8)
        return gray

    def _decode(self, scores, lengths):
        logits = np.asarray(scores, dtype=np.float32)[0]
        length = int(np.asarray(lengths).reshape(-1)[0])
        length = max(0, min(length, logits.shape[0]))
        logits = logits[:length]
        if logits.size == 0:
            return "", 0.0

        best = logits.argmax(axis=1)
        shifted = logits - logits.max(axis=1, keepdims=True)
        exp = np.exp(shifted)
        probs = exp / exp.sum(axis=1, keepdims=True)

        chars = []
        char_scores = []
        prev = self.blank_id
        for pos, idx in enumerate(best):
            idx = int(idx)
            if idx != self.blank_id and idx != prev:
                char_index = idx - 1
                if 0 <= char_index < len(self.charset):
                    chars.append(self.charset[char_index])
                    char_scores.append(float(probs[pos, idx]))
            prev = idx

        score = float(np.mean(char_scores)) if char_scores else 0.0
        return "".join(chars), score


config_name = os.environ.get("ALAS_CONFIG_NAME") or DEFAULT_CONFIG_NAME
config = AzurLaneConfig(config_name)


class _OcrJob:
    def __init__(self, func, args, kwargs):
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.done = threading.Event()
        self.result = None
        self.exc_info = None

    def run(self):
        try:
            self.result = self.func(*self.args, **self.kwargs)
        except BaseException as e:
            self.exc_info = (e, e.__traceback__)
        finally:
            self.done.set()


_ocr_queue = queue.Queue()
_ocr_worker = None
_ocr_worker_lock = threading.Lock()
_ocr_worker_ident = None


def _ocr_worker_loop():
    global _ocr_worker_ident
    _ocr_worker_ident = threading.get_ident()
    while True:
        job = _ocr_queue.get()
        try:
            job.run()
        finally:
            _ocr_queue.task_done()


def _ensure_ocr_worker():
    global _ocr_worker
    with _ocr_worker_lock:
        if _ocr_worker is None or not _ocr_worker.is_alive():
            _ocr_worker = threading.Thread(
                target=_ocr_worker_loop,
                name='AlOcrQueue',
                daemon=True,
            )
            _ocr_worker.start()


def _run_ocr_queued(func, *args, **kwargs):
    if threading.get_ident() == _ocr_worker_ident:
        return func(*args, **kwargs)

    _ensure_ocr_worker()
    job = _OcrJob(func, args, kwargs)
    _ocr_queue.put(job)
    job.done.wait()

    if job.exc_info is not None:
        exc, traceback = job.exc_info
        raise exc.with_traceback(traceback)
    return job.result


ONNX_MODEL_PARAMS = {
    "azur_lane": {
        "azur_lane_v6_6": (
            "bin/ocr_models/azur_lane/ap_azurlane-v6.6_small_rec_dcu.onnx",
            "bin/ocr_models/azur_lane/ppocrv6_azurlane_dict.txt",
            OCRVersion.PPOCRV6,
        ),
        "azur_lane_v6_5": (
            "bin/ocr_models/azur_lane/ap_azurlane-v6.5_small_rec_nvidia.onnx",
            "bin/ocr_models/azur_lane/ppocrv6_azurlane_dict.txt",
            OCRVersion.PPOCRV6,
        ),
        "ppocr_v6": GENERIC_PPOCR_V6_PARAMS,
        "alocr_en_v2_6": (
            "bin/ocr_models/azur_lane/alocr-en-us-v2.6.nvc.onnx",
            "bin/ocr_models/azur_lane/en_dict.txt",
            OCRVersion.PPOCRV4,
        ),
        "alocr_en_v2_0": (
            "bin/ocr_models/azur_lane/alocr-en-us-v2.0.nvc.onnx",
            "bin/ocr_models/azur_lane/en_dict.txt",
            OCRVersion.PPOCRV4,
        ),
        "alocr_en_v1_0": (
            "bin/ocr_models/azur_lane/alocr-en-v1.0.onnx",
            "bin/ocr_models/azur_lane/en_dict.txt",
            OCRVersion.PPOCRV4,
        ),
    },
    "azur_lane_jp": {
        "azur_lane_jp_v6": AZUR_LANE_JP_V6_PARAMS,
        "ppocr_v6": GENERIC_PPOCR_V6_PARAMS,
    },
    "ppocr_v6": {
        "ppocr_v6": GENERIC_PPOCR_V6_PARAMS,
    },
    "cn": {
        "cn_v6_1": (
            "bin/ocr_models/zh-CN/ap_zh-cn-v6.1_small_rec_dcu.onnx",
            "bin/ocr_models/zh-CN/ppocrv6_cn_dict.txt",
            OCRVersion.PPOCRV6,
        ),
        "cn_v6": (
            "bin/ocr_models/zh-CN/ap_zh-cn-v6_small_rec_dcu.onnx",
            "bin/ocr_models/zh-CN/ppocrv6_cn_dict.txt",
            OCRVersion.PPOCRV6,
        ),
        "ppocr_v6": GENERIC_PPOCR_V6_PARAMS,
        "alocr_cn_v3": (
            "bin/ocr_models/zh-CN/alocr-zh-cn-v3.dtk.onnx",
            "bin/ocr_models/zh-CN/cn.txt",
            OCRVersion.PPOCRV5,
        ),
        "alocr_cn_v2_5": (
            "bin/ocr_models/zh-CN/alocr-zh-cn-v2.5.dtk.onnx",
            "bin/ocr_models/zh-CN/cn.txt",
            OCRVersion.PPOCRV5,
        ),
    },
    "jp": {
        "azur_lane_jp_v6": AZUR_LANE_JP_V6_PARAMS,
        "ppocr_v6": GENERIC_PPOCR_V6_PARAMS,
    },
    "tw": {
        "ppocr_v6": GENERIC_PPOCR_V6_PARAMS,
    },
}

CUSTOM_CTC_MODEL_PARAMS = {
    "azur_lane": {
        ALAS_CTC_MODEL_VERSION: ALAS_CTC_MODEL_PATH,
    },
}

DEFAULT_ONNX_MODEL_VERSION = {
    "azur_lane": "alocr_en_v2_6",
    "azur_lane_jp": "azur_lane_jp_v6",
    "ppocr_v6": "ppocr_v6",
    "cn": "cn_v6_1",
    "jp": "ppocr_v6",
    "tw": "ppocr_v6",
}


def _resolve_onnx_model_version(name):
    specs = ONNX_MODEL_PARAMS.get(name)
    custom_specs = CUSTOM_CTC_MODEL_PARAMS.get(name, {})
    if specs is None and not custom_specs:
        raise ValueError(f"Unsupported OCR model: {name}")

    requested = config.ocr_model_version(name)
    if requested == OCR_MODEL_VERSION_AUTO:
        return DEFAULT_ONNX_MODEL_VERSION[name]
    if requested in specs or requested in custom_specs:
        return requested

    fallback = DEFAULT_ONNX_MODEL_VERSION[name]
    logger.warning(
        f"OCR model version '{requested}' is not available for '{name}', "
        f"using '{fallback}'"
    )
    return fallback


def _get_onnx_model_params(name):
    """
    按配置选择 ONNX 识别模型版本。

    Args:
        name: 模型名称，如 'azur_lane'、'azur_lane_jp'、'ppocr_v6'、'cn'、'jp'、'tw'。

    Returns:
        (model_path, rec_keys_path, ocr_version) 三元组。
    """
    version = _resolve_onnx_model_version(name)
    if version in CUSTOM_CTC_MODEL_PARAMS.get(name, {}):
        fallback = "azur_lane_v6_6" if name == "azur_lane" else DEFAULT_ONNX_MODEL_VERSION[name]
        logger.info(
            f"OCR model '{version}' is recognition-only, using '{fallback}' "
            f"for RapidOCR-compatible pipeline"
        )
        return ONNX_MODEL_PARAMS[name][fallback]
    return ONNX_MODEL_PARAMS[name][version]


def _create_ocr(name):
    backend = config.ocr_backend
    if backend == 'ncnn':
        if not supports_ncnn_model(name):
            raise ValueError(f"Unsupported ncnn OCR model: {name}")
        logger.info("OCR backend is ncnn, using ncnn-specific recognition model")
        return NcnnRecOCR(name, device=config.ocr_device)
    else:
        ocr_device = config.ocr_device
        use_dml = os.name == 'nt' and ocr_device == 'gpu'
        use_coreml = ocr_device == 'ane'
        version = _resolve_onnx_model_version(name)
        custom_model_path = CUSTOM_CTC_MODEL_PARAMS.get(name, {}).get(version)
        if custom_model_path is not None:
            return AlOcrCtcRecOCR(custom_model_path, device=ocr_device)

        model_path, rec_keys_path, ocr_version = _get_onnx_model_params(name)
        params = {
            "Global.use_det": False,
            "Global.use_cls": False,
            "Det.model_path": None,
            "Cls.model_path": None,
            "Rec.ocr_version": ocr_version,
            "Rec.model_path": model_path,
            "Rec.rec_keys_path": rec_keys_path,
            "EngineConfig.onnxruntime.use_dml": use_dml,
            "EngineConfig.onnxruntime.use_coreml": use_coreml,
            "EngineConfig.onnxruntime.coreml_ep_cfg.MLComputeUnits": "CPUAndNeuralEngine",
        }
        return RecOnlyOCR(params=params)


# 懒加载：模块级不再创建模型，首次 init() 时才加载
_model_cache = {}


def _model_cache_key(name):
    return (
        name,
        config.ocr_backend,
        config.ocr_device,
        config.ocr_model_version(name),
    )


def _get_model(name):
    key = _model_cache_key(name)
    if key not in _model_cache:
        _model_cache[key] = _create_ocr(name)
    return _model_cache[key]


DET_MODEL_PATH = "bin/ocr_models/det/PP-OCRv6_tiny_det.onnx"

_det_model_cache = {}


class DetOnlyOCR(RapidOCR):
    """仅加载 RapidOCR 检测模型，识别部分由 ncnn 处理。"""

    def _initialize(self, cfg):
        self.text_score = cfg.Global.text_score
        self.min_height = cfg.Global.min_height
        self.width_height_ratio = cfg.Global.width_height_ratio

        self.use_det = True
        cfg.Det.engine_cfg = cfg.EngineConfig[cfg.Det.engine_type.value]
        cfg.Det.model_root_dir = cfg.Global.get("model_root_dir", os.getcwd())
        self.text_det = TextDetector(cfg.Det)

        self.use_cls = False
        self.text_cls = None

        self.use_rec = False
        self.text_rec = None

        self.load_img = LoadImage()
        self.max_side_len = cfg.Global.max_side_len
        self.min_side_len = cfg.Global.min_side_len
        self.return_word_box = False
        self.return_single_char_box = False
        self.cfg = cfg


def _create_det_ocr_for_onnx(name):
    """为 ONNX 后端创建完整的 RapidOCR 实例（检测 + 识别）。"""
    ocr_device = config.ocr_device
    use_dml = os.name == 'nt' and ocr_device == 'gpu'
    use_coreml = ocr_device == 'ane'
    model_path, rec_keys_path, ocr_version = _get_onnx_model_params(name)
    params = {
        "Global.use_det": True,
        "Global.use_cls": False,
        "Det.model_path": DET_MODEL_PATH,
        "Cls.model_path": None,
        "Rec.ocr_version": ocr_version,
        "Rec.model_path": model_path,
        "Rec.rec_keys_path": rec_keys_path,
        "EngineConfig.onnxruntime.use_dml": use_dml,
        "EngineConfig.onnxruntime.use_coreml": use_coreml,
        "EngineConfig.onnxruntime.coreml_ep_cfg.MLComputeUnits": "CPUAndNeuralEngine",
    }
    return RapidOCR(params=params)


def _create_det_ocr_for_ncnn():
    """为 ncnn 后端创建 DetOnlyOCR 实例。"""
    params = {
        "Global.use_det": True,
        "Global.use_cls": False,
        "Global.use_rec": False,
        "Det.model_path": DET_MODEL_PATH,
        "Cls.model_path": None,
        "Rec.model_path": None,
    }
    return DetOnlyOCR(params=params)


def _get_det_model(name):
    """
    获取检测模型。

    Args:
        name: 语言名称。ONNX 后端按语言缓存，ncnn 后端共享单一实例。
    """
    backend = config.ocr_backend
    if backend == 'ncnn':
        key = _model_cache_key("det")
        if key not in _det_model_cache:
            _det_model_cache[key] = _create_det_ocr_for_ncnn()
        return _det_model_cache[key]
    else:
        key = _model_cache_key(name)
        if key not in _det_model_cache:
            _det_model_cache[key] = _create_det_ocr_for_onnx(name)
        return _det_model_cache[key]


def reset_ocr_model():
    def _reset():
        logger.info("Resetting OCR models")
        for model in _model_cache.values():
            close = getattr(model, "close", None)
            if close is not None:
                close()
        _model_cache.clear()
        _det_model_cache.clear()

    return _run_ocr_queued(_reset)


class AlOcr:
    def __init__(self, **kwargs):
        self.model = None
        self.name = kwargs.get("name", "en")
        self.params = {}
        self._model_loaded = False
        self._det_model = None
        self._det_loaded = False
        logger.info(
            f"Created AlOcr instance: name='{self.name}', kwargs={kwargs}, PID={os.getpid()}"
        )

    def init(self):
        self.model = _get_model(self.name)
        self._model_loaded = True

    def _ensure_loaded(self):
        if not self._model_loaded:
            self.init()

    def _ensure_det_loaded(self):
        if not self._det_loaded:
            self._det_model = _get_det_model(self.name)
            self._det_loaded = True

    def _save_debug_image(self, img, result):
        folder = "ocr_debug"
        if not os.path.exists(folder):
            os.makedirs(folder)

        # 获取当前时间用于文件名唯一性和排序
        import time

        now = int(time.time() * 1000)
        # 清理结果文本用于文件名
        res_clean = str(result).replace("\n", " ").replace("\r", " ").strip()
        # 移除无效文件名字符，仅保留安全字符
        res_clean = "".join(
            [c for c in res_clean if c.isalnum() or c in (" ", "_", "-")]
        ).strip()
        if not res_clean:
            res_clean = "empty"

        filename = f"{self.name}_{res_clean}_{now}.png"
        filepath = os.path.join(folder, filename)

        try:
            if isinstance(img, np.ndarray):
                cv2.imwrite(filepath, img)
            elif isinstance(img, Image.Image):
                img.save(filepath)
            elif isinstance(img, str) and os.path.exists(img):
                import shutil

                shutil.copy(img, filepath)

            # 限制文件数量为 100
            files = [
                os.path.join(folder, f)
                for f in os.listdir(folder)
                if os.path.isfile(os.path.join(folder, f))
            ]
            if len(files) > 100:
                files.sort(key=os.path.getmtime)
                # 保留最新的 100 个文件
                for f in files[:-100]:
                    try:
                        os.remove(f)
                    except:
                        pass
        except Exception as e:
            # 不应因调试图片保存失败而崩溃主进程
            logger.warning(f"Failed to save OCR debug image: {e}")

    def _ocr_direct(self, img_fp):
        logger.debug(f"[VERBOSE] AlOcr.ocr: Ensure loaded...")
        self._ensure_loaded()

        try:
            res = self.model(img_fp)
            txt = ""
            if hasattr(res, "txts") and res.txts:
                txt = res.txts[0]

            self._save_debug_image(img_fp, txt)
            return txt
        except Exception as e:
            logger.error(f"AlOcr.ocr exception: {e}")
            raise

    def ocr(self, img_fp):
        return _run_ocr_queued(self._ocr_direct, img_fp)

    def _det_direct(self, img_fp):
        self._ensure_loaded()
        self._ensure_det_loaded()

        try:
            if config.ocr_backend == 'ncnn':
                det_res = self._det_model(img_fp, use_det=True, use_cls=False, use_rec=False)
                if not isinstance(det_res, TextDetOutput) or det_res.boxes is None:
                    return []

                img = self.model.load_image(img_fp)
                results = []
                for box in det_res.boxes:
                    crop = get_rotate_crop_image(img, np.asarray(box, dtype=np.float32))
                    rec_res = self.model(crop)
                    if not getattr(rec_res, "txts", None):
                        continue

                    txt = rec_res.txts[0]
                    if not txt.strip():
                        continue

                    score = rec_res.scores[0] if getattr(rec_res, "scores", None) else 1.0
                    results.append((txt, box.tolist(), float(score)))

                if DET_DEBUG:
                    self._save_det_debug(img_fp, results)

                return results
            else:
                # ONNX：完整 RapidOCR 流水线（检测 + 识别一次调用）
                res = self._det_model(img_fp, use_det=True, use_rec=True)
                if isinstance(res, RapidOCROutput) and res.boxes is not None:
                    results = []
                    txts = res.txts if res.txts is not None else ("",) * len(res.boxes)
                    scores = res.scores if res.scores is not None else (0.0,) * len(res.boxes)
                    for box, txt, score in zip(res.boxes, txts, scores):
                        results.append((txt, box.tolist(), float(score)))

                    if DET_DEBUG:
                        self._save_det_debug(img_fp, results)

                    return results
                return []
        except Exception as e:
            logger.error(f"AlOcr.det exception: {e}")
            raise

    def _save_det_debug(self, img, results):
        import cv2 as cv
        import time
        from PIL import Image as PILImage

        # 根据需要转换为 numpy 数组
        if isinstance(img, PILImage.Image):
            img = np.array(img.convert("RGB"))
            img = cv.cvtColor(img, cv.COLOR_RGB2BGR)
        elif isinstance(img, str):
            img = cv.imread(img)
            if img is None:
                return

        if not isinstance(img, np.ndarray):
            return

        draw = img.copy()
        for txt, box, score in results:
            pts = np.array(box, dtype=np.int32).reshape((-1, 1, 2))
            cv.polylines(draw, [pts], True, (0, 255, 0), 2)
            cx, cy = int(sum(p[0] for p in box) / len(box)), int(sum(p[1] for p in box) / len(box))
            label = f"{txt} {score:.2f}"
            cv.putText(draw, label, (cx - 20, cy - 10),
                       cv.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)

        folder = "ocr_debug"
        os.makedirs(folder, exist_ok=True)
        now = int(time.time() * 1000)
        filename = f"det_{self.name}_{now}.png"
        filepath = os.path.join(folder, filename)
        cv.imwrite(filepath, draw)

        # 限制文件数量为 100
        files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".png")]
        if len(files) > 100:
            files.sort(key=os.path.getmtime)
            for f in files[:-100]:
                try:
                    os.remove(f)
                except Exception:
                    pass

    def det(self, img_fp):
        """
        运行文本检测 + 识别，返回带位置坐标的结果。

        Args:
            img_fp: 图像输入（numpy 数组、PIL Image 或文件路径字符串）。

        Returns:
            (text, box, score) 元组列表：
                - text (str): 识别文本。
                - box (list): 4 个角点 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]。
                - score (float): 置信度分数 (0.0-1.0)。
            未检测到内容时返回空列表。
        """
        return _run_ocr_queued(self._det_direct, img_fp)

    def ocr_for_single_line(self, img_fp):
        return self.ocr(img_fp)

    def _ocr_for_single_lines_direct(self, img_list):
        self._ensure_loaded()
        results = []
        for i, img in enumerate(img_list):
            try:
                res = self.model(img)
                txt = ""
                if hasattr(res, "txts") and res.txts:
                    txt = res.txts[0]

                results.append(txt)
                self._save_debug_image(img, txt)
            except Exception as e:
                logger.error(f"AlOcr.ocr_for_single_lines exception on image {i}: {e}")
                raise
        return results

    def ocr_for_single_lines(self, img_list):
        return _run_ocr_queued(self._ocr_for_single_lines_direct, img_list)

    def set_cand_alphabet(self, cand_alphabet):
        pass

    def atomic_ocr(self, img_fp, cand_alphabet=None):
        res = self.ocr(img_fp)
        if cand_alphabet:
            res = "".join([c for c in res if c in cand_alphabet])
        return res

    def atomic_ocr_for_single_line(self, img_fp, cand_alphabet=None):
        res = self.ocr_for_single_line(img_fp)
        if cand_alphabet:
            res = "".join([c for c in res if c in cand_alphabet])
        return res

    def atomic_ocr_for_single_lines(self, img_list, cand_alphabet=None):
        results = self.ocr_for_single_lines(img_list)
        if cand_alphabet:
            results = [
                "".join([c for c in res if c in cand_alphabet]) for res in results
            ]
        return results
