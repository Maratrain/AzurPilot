# OCR 模型

统一使用通用 PP-OCRv6 识别模型，所有语言（azur_lane、cn、jp、tw 等逻辑名称）共用同一套识别模型与字典。各服务器的语言差异通过运行时服务器切换（server.py）处理，不再需要专用模型。

## 目录结构

| 目录 | 内容 |
|---|---|
| `ppocr-v6/` | ONNX 识别模型 `PP-OCRv6_small_rec.onnx` + 通用字典 `ppocrv6_dict.txt` |
| `det/` | 文本检测模型（medium/small/tiny 三档），仅 `.det()` 场景使用 |
| `ncnn/` | 从 `ppocr-v6/PP-OCRv6_small_rec.onnx` 转换的 ncnn 运行时模型（`ppocr_v6.param/bin`） |

## ncnn 模型

ncnn 模型通过 pnnx 从 ONNX 识别模型转换，固定输入 shape 为
`[1,3,48,320]`，运行时输入 blob 为 `in0`，输出 blob 为 `out0`。
所有逻辑模型共用这一份 `ppocr_v6.param/bin`。

重新生成：

```bash
uv run python -m dev_tools.ocr_ncnn_convert
```

## 检测模型

文本检测使用 PP-OCRv6 系列检测模型（`det/` 目录），检测 + 识别流水线在
ncnn 和 ONNX 后端有不同实现。

## 旧版 AlOCR 专用模型（可选）

从 git 历史恢复的旧版 AlOCR 专用识别模型，作为 `OcrModelVersionEnglish` /
`OcrModelVersionChinese` 的可选版本在 GUI 中提供：

| 目录 | 内容 |
|---|---|
| `azur_lane/alocr-en-us-v2.6.nvc.onnx` | 旧版 AlOCR v2.6 英文数字/字母识别模型（PP-OCRv4 结构，仅 ONNX 后端） |
| `azur_lane/en_dict.txt` | 上述英文模型的字典 |
| `zh-CN/alocr-zh-cn-v3.dtk.onnx` | 旧版 AlOCR v3 简体中文识别模型（PP-OCRv5 结构，仅 ONNX 后端） |
| `zh-CN/cn.txt` | 上述中文模型的字典 |

默认仍使用 PP-OCRv6（`standard`），需要时可在 GUI 中单独切换为旧版模型。
