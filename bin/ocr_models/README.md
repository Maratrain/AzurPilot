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
