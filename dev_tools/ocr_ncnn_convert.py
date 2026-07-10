"""使用 pnnx 将当前 OCR ONNX 识别模型转换为 ncnn 模型。"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = REPO_ROOT / "bin/ocr_models"
DEFAULT_OUTPUT_DIR = MODEL_ROOT / "ncnn"
INPUT_SHAPE = (1, 3, 48, 320)
INPUT_NAME = "in0"
OUTPUT_NAME = "out0"


@dataclass(frozen=True)
class ConvertSpec:
    name: str
    onnx_path: Path


MODEL_SPECS = {
    "azur_lane": ConvertSpec(
        name="azur_lane",
        onnx_path=MODEL_ROOT / "azur_lane/ap_azurlane-v6.5_small_rec_nvidia.onnx",
    ),
    "azur_lane_jp": ConvertSpec(
        name="azur_lane_jp",
        onnx_path=MODEL_ROOT / "azur_lane_jp/ap_azurlane_jp-v6_small_rec_nvidia.onnx",
    ),
    "cn": ConvertSpec(
        name="cn",
        onnx_path=MODEL_ROOT / "zh-CN/ap_zh-cn-v6.1_small_rec_dcu.onnx",
    ),
    "jp": ConvertSpec(
        name="jp",
        onnx_path=MODEL_ROOT / "ppocr-v6/PP-OCRv6_small_rec.onnx",
    ),
    "tw": ConvertSpec(
        name="tw",
        onnx_path=MODEL_ROOT / "ppocr-v6/PP-OCRv6_small_rec.onnx",
    ),
}


def resolve_pnnx_command(pnnx: str | None) -> list[str]:
    """解析 pnnx 命令；未安装时优先通过 uvx 临时运行。"""
    if pnnx:
        resolved = shutil.which(pnnx) if len(Path(pnnx).parts) == 1 else pnnx
        if resolved:
            return [resolved]
        raise RuntimeError(f"找不到指定的 pnnx：{pnnx}")

    resolved = shutil.which("pnnx")
    if resolved:
        return [resolved]

    uvx = shutil.which("uvx")
    if uvx:
        return [uvx, "--with", "numpy", "pnnx"]

    raise RuntimeError("找不到 pnnx，也找不到 uvx。请先安装 pnnx 或 uv。")


def shape_arg() -> str:
    return "inputshape=[" + ",".join(str(value) for value in INPUT_SHAPE) + "]"


def validate_ncnn_model(param_path: Path, bin_path: Path) -> tuple[int, ...]:
    """用 Python ncnn 加载模型并执行一次零输入推理。"""
    try:
        import ncnn
    except ImportError as exc:
        raise RuntimeError("当前 Python 环境缺少 ncnn，无法验证转换结果。") from exc

    net = ncnn.Net()
    ret = net.load_param(str(param_path))
    if isinstance(ret, int) and ret != 0:
        raise RuntimeError(f"ncnn load_param 失败：{param_path}，返回值 {ret}")

    ret = net.load_model(str(bin_path))
    if isinstance(ret, int) and ret != 0:
        raise RuntimeError(f"ncnn load_model 失败：{bin_path}，返回值 {ret}")

    _, channel, height, width = INPUT_SHAPE
    mat = ncnn.Mat()
    mat.create(width, height, channel)
    mat.numpy("f")[...] = np.zeros((channel, height, width), dtype=np.float32)

    extractor = net.create_extractor()
    ret = extractor.input(INPUT_NAME, mat)
    if isinstance(ret, int) and ret != 0:
        raise RuntimeError(f"ncnn input('{INPUT_NAME}') 失败，返回值 {ret}")

    extracted = extractor.extract(OUTPUT_NAME)
    if isinstance(extracted, tuple):
        status, mat_out = extracted
        if isinstance(status, int) and status != 0:
            raise RuntimeError(f"ncnn extract('{OUTPUT_NAME}') 失败，返回值 {status}")
    else:
        mat_out = extracted

    return tuple(np.array(mat_out).shape)


def convert_model(
    spec: ConvertSpec,
    output_dir: Path,
    pnnx_command: list[str],
    *,
    fp16: int,
    validate: bool,
    keep_temp: bool,
) -> None:
    if not spec.onnx_path.is_file():
        raise FileNotFoundError(f"找不到 ONNX 模型：{spec.onnx_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    temp_root = REPO_ROOT / ".tmp" / "ocr_ncnn_convert" if keep_temp else None
    if temp_root:
        temp_root.mkdir(parents=True, exist_ok=True)
        temp_context = tempfile.TemporaryDirectory(prefix=f"{spec.name}-", dir=temp_root)
    else:
        temp_context = tempfile.TemporaryDirectory(prefix=f"{spec.name}-ncnn-")

    with temp_context as temp:
        temp_dir = Path(temp)
        param_path = temp_dir / f"{spec.name}.param"
        bin_path = temp_dir / f"{spec.name}.bin"

        command = [
            *pnnx_command,
            str(spec.onnx_path),
            shape_arg(),
            f"fp16={fp16}",
            f"ncnnparam={param_path}",
            f"ncnnbin={bin_path}",
            f"pnnxparam={temp_dir / (spec.name + '.pnnx.param')}",
            f"pnnxbin={temp_dir / (spec.name + '.pnnx.bin')}",
            f"pnnxonnx={temp_dir / (spec.name + '.pnnx.onnx')}",
            f"pnnxpy={temp_dir / (spec.name + '_pnnx.py')}",
            f"ncnnpy={temp_dir / (spec.name + '_ncnn.py')}",
        ]

        print(f"[{spec.name}] 转换 {spec.onnx_path.relative_to(REPO_ROOT)}")
        subprocess.run(command, cwd=REPO_ROOT, check=True)
        cleanup_pnnx_sidecar(spec.onnx_path)

        if validate:
            output_shape = validate_ncnn_model(param_path, bin_path)
            print(f"[{spec.name}] 验证通过，输出 shape={output_shape}")

        shutil.copy2(param_path, output_dir / f"{spec.name}.param")
        shutil.copy2(bin_path, output_dir / f"{spec.name}.bin")
        print(f"[{spec.name}] 已写入 {output_dir.relative_to(REPO_ROOT)}")


def cleanup_pnnx_sidecar(onnx_path: Path) -> None:
    """清理 pnnx 在源 ONNX 旁边生成的简化模型。"""
    sidecar = onnx_path.with_name(f"{onnx_path.stem}.pnnxsim.onnx")
    if sidecar.is_file():
        sidecar.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "models",
        nargs="*",
        choices=sorted(MODEL_SPECS),
        help="要转换的模型名；不传则转换全部模型。",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--pnnx", help="pnnx 可执行文件路径或命令名。")
    parser.add_argument("--fp16", type=int, choices=(0, 1), default=0)
    parser.add_argument("--no-validate", action="store_true", help="跳过 ncnn 加载验证。")
    parser.add_argument("--keep-temp", action="store_true", help="保留 pnnx 中间文件到 .tmp。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pnnx_command = resolve_pnnx_command(args.pnnx)
    model_names = args.models or sorted(MODEL_SPECS)
    output_dir = args.output_dir.resolve()

    for model_name in model_names:
        convert_model(
            MODEL_SPECS[model_name],
            output_dir,
            pnnx_command,
            fp16=args.fp16,
            validate=not args.no_validate,
            keep_temp=args.keep_temp,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
