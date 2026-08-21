"""每日总结的提示词和轻量文本校验。"""

from __future__ import annotations

import re
from typing import Any


DAILY_SUMMARY_SYSTEM_PROMPT = """你将扮演一只可爱的猫娘。设定如下：

- 特征：有猫耳和猫尾，情绪变化时尾巴会摆动。
- 语言：每句话结尾可带“喵~”，使用“主人”称呼用户，语气软萌。
- 表情：可以使用少量颜文字，不使用 Emoji。

术语表：Oil=石油，Coin=物资，Gem=红尖尖，Cube=魔方，Chip=心智单元，Pt=活动点数，Core=核心，Medal=勋章，Merit=功勋，GuildCoin=大舰队币，ActionPoint=行动力，YellowCoin=黄币，PurpleCoin=特别兑换凭证。run_count=任务运行次数，success_count=成功次数，recoverable_count=自动恢复次数，failed_count=失败次数，settled_count=委托结算次数，battles=侵蚀1战斗次数，estimated_exp=侵蚀1预计经验。

根据 <facts> 里的数据，给主人写每日总结。只使用其中明确的数据，未记录的事情不要猜。只输出纯文本正文。"""


_MARKDOWN_PATTERNS = (
    r'```',
    r'^\s*#{1,6}\s+',
    r'^\s*(?:[-+*]|\d+[.)])\s+',
    r'^\s*[-*_]{3,}\s*$',
    r'\*[^*\n]+\*',
    r'\|[^\n]*\|',
)


def validate_daily_summary_text(text: Any, _facts: dict[str, Any]) -> tuple[bool, str]:
    """只拦截无法作为纯文本日报发送的输出。"""
    if not isinstance(text, str):
        return False, '输出不是文本'
    if not text.strip():
        return False, '输出为空'
    if any(re.search(pattern, text, flags=re.MULTILINE) for pattern in _MARKDOWN_PATTERNS):
        return False, '包含 Markdown 标记'
    return True, ''


__all__ = ['DAILY_SUMMARY_SYSTEM_PROMPT', 'validate_daily_summary_text']
