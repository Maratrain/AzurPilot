"""平台环境检测常量。定义 IS_WINDOWS、IS_MACINTOSH、IS_LINUX 等
操作系统标识变量，供设备层各模块使用。"""

import sys

IS_WINDOWS = sys.platform == 'win32'
IS_MACINTOSH = sys.platform == 'darwin'
IS_LINUX = sys.platform == 'linux'
