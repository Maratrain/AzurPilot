"""
伪造 PIL 模块。

在子进程启动时注入虚拟的 PIL 模块到 sys.modules，避免加载真实的
图像处理库。用于减少进程管理器等非图像处理场景的启动开销。
"""

import sys
from types import ModuleType


def import_fake_pil_module():
    fake_pil_module = ModuleType('PIL')
    fake_pil_module.Image = ModuleType('PIL.Image')
    fake_pil_module.Image.Image = type('MockPILImage', (), dict(__init__=None))
    sys.modules['PIL'] = fake_pil_module
    sys.modules['PIL.Image'] = fake_pil_module.Image


def remove_fake_pil_module():
    sys.modules.pop('PIL', None)
    sys.modules.pop('PIL.Image', None)
