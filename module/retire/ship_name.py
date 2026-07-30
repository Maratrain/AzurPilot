"""舰队扫描使用的舰娘名称纠错。"""

import json
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Dict, Tuple

from module.logger import logger


class ShipNameMatcher:
    """用舰船数据中的本服名称修正船坞 OCR 结果。"""

    DATA_FILE = Path(__file__).parents[2] / "assets" / "ship" / "ship_data.json"
    TRUNCATION_CHARS = ".…"

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

    @staticmethod
    def _minimum_similarity(length: int) -> float:
        if length >= 7:
            return 0.70
        if length >= 5:
            return 0.80
        if length == 4:
            return 0.75
        return 0.90

    def correct(self, value: str) -> str:
        """返回标准舰娘名；无法高置信纠错时保留原始 OCR 结果。"""
        raw = str(value).strip()
        if not raw or not self.normalized_names:
            return raw

        normalized = self._normalize(raw)
        exact = self.normalized_names.get(normalized)
        if exact:
            return exact

        prefix = normalized.rstrip(self.TRUNCATION_CHARS)
        if len(prefix) >= 3 and len(prefix) < len(normalized):
            matches = [
                name for candidate, name in self.normalized_names.items()
                if candidate.startswith(prefix)
            ]
            if len(matches) == 1:
                return matches[0]

        if len(normalized) < 4:
            return raw

        scores = sorted(
            (
                SequenceMatcher(None, normalized, candidate, autojunk=False).ratio(),
                name,
            )
            for candidate, name in self.normalized_names.items()
        )
        best_score, best_name = scores[-1]
        runner_up = scores[-2][0] if len(scores) > 1 else 0
        if (
            best_score >= self._minimum_similarity(len(normalized))
            and best_score - runner_up >= 0.10
        ):
            return best_name
        return raw
