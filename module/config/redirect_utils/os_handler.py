"""大世界配置重定向工具。

包含 action_point_redirect 等函数，用于将旧版大世界处理器的布尔配置值
迁移转换为新版的数值格式。
"""


def action_point_redirect(value):
    """
    redirect attr about action point

    Args:
        value (bool):
          If Enable, return 5.
          If Disable, return 0.
    """
    if value is True:
        return 5
    else:
        return 0
