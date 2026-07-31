"""舰队扫描使用的舰娘名称纠错。"""

import json
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Dict, Tuple

from module.logger import logger


class ShipNameMatcher:
    """用舰船数据中的本服名称匹配船坞 OCR 结果。"""

    DATA_FILE = Path(__file__).parents[2] / "assets" / "ship" / "ship_data.json"

    def __init__(self, language: str) -> None:
        self.names = self._load_names(language)
        self.normalized_names: Dict[str, str] = {}
        for name in self.names:
            self.normalized_names.setdefault(self._normalize(name), name)

    @staticmethod
    def _normalize(name: str) -> str:
        return "".join(unicodedata.normalize("NFKC", name).split()).casefold()

    @classmethod
    @lru_cache(maxsize=4)
    def _load_names(cls, language: str) -> Tuple[str, ...]:
        try:
            data = json.loads(cls.DATA_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"[舰队扫描-OCR] 无法读取舰船名称数据: {exc}")
            return ()

        names = {
            entry.get("name", {}).get(language) or entry.get("name", {}).get("cn")
            for entry in data.values()
            if isinstance(entry, dict)
        }
        return tuple(sorted(name for name in names if isinstance(name, str) and name.strip()))

    def correct(self, value: str) -> str:
        """返回与 OCR 结果相似度最高的本服标准舰娘名。"""
        raw = str(value).strip()
        if not raw or not self.normalized_names:
            return raw

        normalized = self._normalize(raw)
        exact = self.normalized_names.get(normalized)
        if exact:
            return exact

        _, best_name = max(
            (
                SequenceMatcher(None, normalized, candidate, autojunk=False).ratio(),
                name,
            )
            for candidate, name in self.normalized_names.items()
        )
        return best_name
