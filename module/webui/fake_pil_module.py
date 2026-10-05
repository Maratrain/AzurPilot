"""
伪造 PIL 模块。

在子进程启动时注入虚拟的 PIL 模块到 sys.modules，避免加载真实的
图像处理库。用于减少进程管理器等非图像处理场景的启动开销。
"""

import sys
from types import ModuleType

# 注入 fake 前的 PIL 模块快照；remove 时原样恢复。
# 只 pop 顶层模块会让残留的 PIL 子模块（如 PngImagePlugin）与
# 重新导入的 PIL.Image 错配，插件注册表为空，后续任何图片加载
# 都会报 UnidentifiedImageError。
_pil_modules_backup = None


def import_fake_pil_module():
    global _pil_modules_backup
    if _pil_modules_backup is not None:
        return
    _pil_modules_backup = {
        name: module for name, module in sys.modules.items()
        if name == 'PIL' or name.startswith('PIL.')
    }
    fake_pil_module = ModuleType('PIL')
    fake_pil_module.Image = ModuleType('PIL.Image')
    fake_pil_module.Image.Image = type('MockPILImage', (), dict(__init__=None))
    sys.modules['PIL'] = fake_pil_module
    sys.modules['PIL.Image'] = fake_pil_module.Image


def remove_fake_pil_module():
    global _pil_modules_backup
    if _pil_modules_backup is None:
        # 本进程从未注入过 fake，sys.modules 里的 PIL 就是真实模块，保持原样
        return
    for name in [name for name in sys.modules if name == 'PIL' or name.startswith('PIL.')]:
        del sys.modules[name]
    sys.modules.update(_pil_modules_backup)
    _pil_modules_backup = None
