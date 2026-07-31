#!/usr/bin/env python3
"""
从 AzurLaneLuaScripts 仓库的 Lua 脚本中提取舰船数据，生成统一 JSON。

数据来源：
  - {SERVER}/sharecfgdata/ship_data_statistics.lua  → 属性、名称、面板数据
  - CN/sharecfgdata/ship_data_template.lua          → 模板、装备槽、改造信息
  - CN/sharecfg/ship_data_by_type.lua              → 类型中文名

输出 JSON 结构（以 ship_id 为 key）：
  {
    "100001": {
      "name": {"cn": "泛用型布里", "en": "Universal Bulin", ...},
      "english_name": "UNIV Universal Bulin",
      "nationality": 98,
      "nation_name": "布里",
      "type": 1,
      "type_name": "驱逐",
      "rarity": 4,
      "rarity_name": "SR",
      "star": 4,
      "star_max": 4,
      "group_type": 10000,
      "is_retrofit": false,
      "retrofit_base_id": null,
      "armor_type": 1,
      "attrs": { "durability": 100, "cannon": 10, "torpedo": 10, ... },
      "attrs_growth": { "durability": 1000, "cannon": 100, ... },
      "equip_slots": [[1], [6], [6], [10, 14], [10, 14]],
      "oil_at_start": 1,
      "oil_at_end": 3,
      "max_level": 100
    },
    ...
  }

使用方式：
  uv run python dev_tools/ship_data_extractor.py
  uv run python dev_tools/ship_data_extractor.py --lua-repo D:/AzurLaneLuaScripts
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 阵营 ID → 中文名（来源: CN/model/const/nation.lua Nation2Name）
# ---------------------------------------------------------------------------
NATION_NAME_MAP: dict[int, str] = {
    0:   "其他",    1:   "白鹰",    2:   "皇家",    3:   "重樱",
    4:   "铁血",    5:   "东煌",    6:   "撒丁帝国", 7:   "北方联合",
    8:   "自由鸢尾", 9:   "维希教廷", 10:  "鸢尾教国", 11:  "御金王国",
    12:  "镜幻联盟",
    96:  "MOT",      97:  "META",     98:  "布里",     99:  "塞壬",
    101: "海王星",   102: "哔哩哔哩", 103: "传颂之物", 104: "AI",
    105: "hololive", 106: "死或生",   107: "偶像大师", 108: "SSSS",
    109: "莱莎",    110: "闪乱神乐", 111: "To LOVE",  112: "黑岩射手",
    113: "尤米亚",   114: "地错",    115: "约会大作战", 117: "尼尔",
}

# ---------------------------------------------------------------------------
# 稀有度 ID → 简称（来源: CN/model/const/shiprarity.lua）
# ---------------------------------------------------------------------------
RARITY_NAME_MAP: dict[int, str] = {
    2:  "N", 3: "R", 4: "SR", 5: "SSR", 6: "UR", 18: "DR",
}

# ---------------------------------------------------------------------------
# attrs 数组索引 → 属性名
# 来源: CN/model/vo/ship.lua slot0.PROPERTIES
# ---------------------------------------------------------------------------
ATTRS_INDEX_NAMES = [
    "durability",     # 索引 1 — 耐久
    "cannon",         # 索引 2 — 炮击
    "torpedo",        # 索引 3 — 雷击
    "antiaircraft",   # 索引 4 — 防空
    "air",            # 索引 5 — 航空
    "reload",         # 索引 6 — 装填
    "armor",          # 索引 7 — 装甲
    "hit",            # 索引 8 — 命中
    "dodge",          # 索引 9 — 机动
    "speed",          # 索引 10 — 航速
    "luck",           # 索引 11 — 幸运
    "antisub",        # 索引 12 — 反潜
]

# ---------------------------------------------------------------------------
# 各服务器目录名
# ---------------------------------------------------------------------------
SERVER_DIRS = {
    "cn": "CN", "en": "EN", "jp": "JP", "tw": "TW", "kr": "KR",
}

# ---------------------------------------------------------------------------
# Lua 解析器
# ---------------------------------------------------------------------------

class LuaParser:
    """简单快速的行级 Lua 解析器。"""

    def __init__(self, text: str):
        self.text = text
        self.lines = text.splitlines()
        self.pos = 0

    def skip_to(self, pattern: re.Pattern) -> bool:
        """移动指针到下一个匹配行，返回是否找到。"""
        while self.pos < len(self.lines):
            if pattern.search(self.lines[self.pos]):
                return True
            self.pos += 1
        return False

    def parse_block(self) -> dict:
        """从当前位置解析一个 Lua table，返回 Python dict。

        支持嵌套表（转为 list/dict）、简单值。只处理前两层深度。
        """
        result: dict = {}
        line = self.lines[self.pos]
        # 统计本行的 {，算出起始深度
        clean = _remove_string_content(line)
        # 找到 = { 的位置
        if "{" not in line:
            return result
        # 直接跳到 = { 后的第一行
        self.pos += 1
        brace_depth = 1
        block_lines: list[str] = []

        while self.pos < len(self.lines):
            line = self.lines[self.pos]
            clean = _remove_string_content(line)
            depth_before = 0
            brace_depth += clean.count("{") - clean.count("}")
            if brace_depth <= 0:
                break
            block_lines.append(line)
            self.pos += 1

        # 解析收集到的行
        return self._parse_top_level(block_lines)

    def _parse_top_level(self, lines: list[str]) -> dict:
        """解析顶层 KV（brace_depth=0 相对于块内第一层）。"""
        result: dict = {}
        i = 0
        while i < len(lines):
            raw = lines[i]
            clean = _remove_string_content(raw)

            # 检测嵌套表开始
            # key = {  → 需要收集这个嵌套表
            kv_m = _KV_LINE_RE.match(raw)
            if kv_m:
                key = kv_m.group(1)
                val = kv_m.group(2).strip()
                # 检测嵌套表：值以 { 开头（多行嵌套表）
                if val.startswith("{"):
                    nested, i = self._collect_nested_table(lines, i, raw)
                    result[key] = nested
                else:
                    result[key] = _parse_value(val)
            elif "{" in raw:
                # 纯表行（无 key=），跳过
                pass
            i += 1
        return result

    def _collect_nested_table(self, lines: list[str], start_i: int, start_line: str) -> tuple[list | dict, int]:
        """收集嵌套表，返回 (parsed_value, new_index)。"""
        clean_start = _remove_string_content(start_line)
        brace_count = clean_start.count("{") - clean_start.count("}")

        if brace_count == 0:
            # 空表 {} 或 单行表 {1, 2, 3}（已完成闭合）
            open_idx = start_line.index("{")
            close_idx = start_line.rindex("}")
            content = start_line[open_idx:close_idx + 1]
            return _parse_lua_table(content), start_i

        # 多行嵌套表——只取 { 之后的部分
        open_idx = start_line.index("{")
        collected = [start_line[open_idx:]]
        i = start_i + 1
        while i < len(lines) and brace_count > 0:
            collected.append(lines[i])
            clean = _remove_string_content(lines[i])
            brace_count += clean.count("{") - clean.count("}")
            i += 1
        return _parse_lua_table("\n".join(collected)), i - 1


def _remove_string_content(line: str) -> str:
    """移除字符串字面量内容，避免括号计数干扰。"""
    return re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', '""', line)


# 顶层 KV 匹配
_KV_LINE_RE = re.compile(r'^\s*(\w+)\s*=\s*(.*)$')


def _parse_value(raw: str) -> int | float | bool | str | None:
    """将 Lua 字面量转换为 Python 值。"""
    raw = raw.strip().rstrip(",").strip().rstrip(",").strip()
    if raw == "" or raw == "nil":
        return None
    if raw.startswith('"') and raw.endswith('"'):
        inner = raw[1:-1]
        inner = inner.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
        return inner
    if raw == "true":
        return True
    if raw == "false":
        return False
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def _parse_lua_table(text: str) -> list | dict:
    """解析 Lua 表字符串为 Python list 或 dict。

    - 纯数字索引 → list
    - 混合或字符串 key → dict
    """
    text = text.strip()
    if not text.startswith("{"):
        return text

    # 去掉首尾花括号
    inner = _extract_brace_content(text)

    if not inner:
        return [] if "{" in text else {}

    parts = _split_table_parts(inner)

    # 判断是 list 还是 dict
    has_keys = any("=" in p for p in parts)
    if has_keys:
        result = {}
        for part in parts:
            if "=" in part:
                k, v = part.split("=", 1)
                k = k.strip()
                v = v.strip()
                # key 可能是 [n] 或 裸标识符
                if k.startswith("[") and k.endswith("]"):
                    k = k[1:-1].strip().strip('"').strip("'")
                result[k] = _parse_value(v)
            elif part.strip():
                # 纯值无 key，跳过（dict 中少见）
                pass
    else:
        result = [_parse_value(p) for p in parts if p.strip()]

    return result


def _extract_brace_content(text: str) -> str:
    """提取最外层 {...} 的内容。"""
    if not text.startswith("{"):
        return text
    depth = 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[1:i]
    return text[1:]  # 找不到闭合括号时返回去掉首括号的内容


def _split_table_parts(inner: str) -> list[str]:
    """按逗号拆分表内容，正确处理嵌套括号和引号。"""
    parts = []
    depth = 0
    in_string = False
    current: list[str] = []
    for ch in inner:
        if ch == '"' and not current:
            pass  # 简化处理
        if ch == '"':
            in_string = not in_string
        if not in_string:
            if ch in ("{", "["):
                depth += 1
            elif ch in ("}", "]"):
                depth -= 1
        if ch == "," and depth == 0 and not in_string:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current).strip())
    return parts


_SHIP_ENTRY_RE = re.compile(r'\[(\d+)\]\s*=\s*\{')


def parse_lua_ship_blocks(filepath: str) -> dict[int, dict]:
    """解析 ship_data_statistics 或 ship_data_template 的舰船块。

    每块格式: _G.pg.base.xxx[ship_id] = { ... }
    """
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    parser = LuaParser(text)
    results: dict[int, dict] = {}

    while parser.pos < len(parser.lines):
        line = parser.lines[parser.pos]
        if "= {" not in line:
            parser.pos += 1
            continue

        m = _SHIP_ENTRY_RE.search(line)
        if not m:
            parser.pos += 1
            continue

        ship_id = int(m.group(1))
        block = parser.parse_block()
        if block:
            results[ship_id] = block
        # parse_block 已经推进了 parser.pos

    return results


# ---------------------------------------------------------------------------
# 类型名映射
# ---------------------------------------------------------------------------

def parse_ship_data_by_type(filepath: str) -> dict[int, str]:
    """返回 {type_id: type_name}。"""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()
    type_blocks = re.findall(
        r'pg\.base\.ship_data_by_type\[(\d+)\]\s*=\s*\{(.*?)\n\s*\}',
        text, re.MULTILINE | re.DOTALL,
    )
    result: dict[int, str] = {}
    for type_id_str, block in type_blocks:
        name_m = re.search(r'type_name\s*=\s*"([^"]*)"', block)
        if name_m:
            result[int(type_id_str)] = name_m.group(1).strip()
    return result


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------

def _index_arr(arr: list, idx1: int) -> int | float | None:
    """从 1-based Lua 数组中取值。"""
    return arr[idx1 - 1] if arr and len(arr) >= idx1 else None


def extract_ship_data(lua_repo: str) -> dict:
    """主提取函数。"""
    repo = Path(lua_repo)

    # ---- 1) CN statistics（主数据） ----
    print("[1/6] 解析 CN 舰船统计数据...")
    cn_stats = parse_lua_ship_blocks(str(repo / "CN" / "sharecfgdata" / "ship_data_statistics.lua"))
    print(f"      解析到 {len(cn_stats)} 条记录")

    # ---- 2) CN template ----
    print("[2/6] 解析 CN 舰船模板数据...")
    cn_template = parse_lua_ship_blocks(str(repo / "CN" / "sharecfgdata" / "ship_data_template.lua"))
    print(f"      解析到 {len(cn_template)} 条记录")

    # ---- 3) 类型名 ----
    print("[3/6] 解析舰船类型名称...")
    type_name_map = parse_ship_data_by_type(str(repo / "CN" / "sharecfg" / "ship_data_by_type.lua"))
    print(f"      解析到 {len(type_name_map)} 种类型")

    # ---- 4) 各服务器名称 ----
    print("[4/6] 解析各服务器舰船名称...")
    server_names: dict[str, dict[int, str]] = {}
    for sk, sd in SERVER_DIRS.items():
        path = repo / sd / "sharecfgdata" / "ship_data_statistics.lua"
        if path.exists():
            ships = parse_lua_ship_blocks(str(path))
            server_names[sk] = {sid: d.get("name", "") for sid, d in ships.items()}
            print(f"      [{sk}] {len(ships)} 个名称")
        else:
            server_names[sk] = {}
            print(f"      [{sk}] 文件不存在")

    # ---- 5) 构建 group_type → ship_ids 映射（用于改造检测） ----
    print("[5/6] 构建舰船群组关系...")
    group_map: dict[int, list[int]] = {}
    for ship_id, tpl in cn_template.items():
        gt = tpl.get("group_type")
        if gt is not None:
            group_map.setdefault(gt, []).append(ship_id)

    # ---- 6) 构建 english_name → ship_id 映射（用于 II 型舰检测） ----
    en_name_index: dict[str, list[int]] = {}
    for ship_id, stats in cn_stats.items():
        en_name = stats.get("english_name", "")
        if en_name:
            en_name_index.setdefault(en_name, []).append(ship_id)

    # ---- 7) 合并为最终 JSON ----
    print("[7/7] 合并数据...")
    output: dict = {}
    skipped_types: set = set()
    skipped_nations: set = set()
    skipped_rarities: set = set()

    for ship_id, stats in cn_stats.items():
        ship_type = stats.get("type")
        nationality = stats.get("nationality")
        rarity = stats.get("rarity")

        if ship_type is not None and ship_type not in type_name_map:
            skipped_types.add(ship_type)
        if nationality is not None and nationality not in NATION_NAME_MAP:
            skipped_nations.add(nationality)
        if rarity is not None and rarity not in RARITY_NAME_MAP:
            skipped_rarities.add(rarity)

        tpl = cn_template.get(ship_id, {})

        # --- 面板属性 ---
        attrs_list = stats.get("attrs", [])
        attrs_growth_list = stats.get("attrs_growth", [])
        attrs = {}
        attrs_growth = {}
        if isinstance(attrs_list, list):
            for idx, name in enumerate(ATTRS_INDEX_NAMES):
                attrs[name] = _index_arr(attrs_list, idx + 1)
                attrs_growth[name] = _index_arr(attrs_growth_list, idx + 1)

        # --- 装备槽 ---
        equip_slots = []
        for ek in ("equip_1", "equip_2", "equip_3", "equip_4", "equip_5"):
            val = tpl.get(ek)
            equip_slots.append(val if isinstance(val, list) else [])

        # --- 改造检测 ---
        group_type = tpl.get("group_type")
        is_retrofit = False
        retrofit_base_id = None

        if group_type is not None and group_type in group_map:
            siblings = group_map[group_type]
            cn_name = stats.get("name", "")
            if "改" in cn_name:
                is_retrofit = True
                # 找同组非改造舰中 star 最高的
                non_retro = [
                    sid for sid in siblings
                    if sid in cn_stats
                    and "改" not in cn_stats[sid].get("name", "")
                ]
                if non_retro:
                    retrofit_base_id = max(non_retro, key=lambda sid: cn_stats[sid].get("star", 0))

        # --- II 型舰检测（如拉菲II、约克城II） ---
        is_type2 = False
        type2_base_id = None
        en_name = stats.get("english_name", "")
        if en_name and " II" in en_name and "布里" not in stats.get("name", ""):
            is_type2 = True
            base_en = en_name.replace(" II", "")
            base_candidates = [
                sid for sid in en_name_index.get(base_en, [])
                if sid != ship_id and " II" not in cn_stats[sid].get("english_name", "")
            ]
            if base_candidates:
                type2_base_id = max(base_candidates, key=lambda sid: cn_stats[sid].get("star", 0))

        entry: dict = {
            "name": {sk: server_names[sk].get(ship_id) for sk in SERVER_DIRS},
            "english_name": stats.get("english_name", ""),
            "nationality": nationality,
            "nation_name": NATION_NAME_MAP.get(nationality, f"未知阵营({nationality})"),
            "type": ship_type,
            "type_name": type_name_map.get(ship_type, f"未知类型({ship_type})"),
            "rarity": rarity,
            "rarity_name": RARITY_NAME_MAP.get(rarity, f"未知稀有度({rarity})"),
            "star": stats.get("star"),
            "star_max": tpl.get("star_max"),
            "group_type": group_type,
            "is_retrofit": is_retrofit,
            "retrofit_base_id": retrofit_base_id,
            "is_type2": is_type2,
            "type2_base_id": type2_base_id,
            "armor_type": stats.get("armor_type"),
            "attrs": attrs,
            "attrs_growth": attrs_growth,
            "equip_slots": equip_slots,
            "oil_at_start": tpl.get("oil_at_start"),
            "oil_at_end": tpl.get("oil_at_end"),
            "max_level": tpl.get("max_level"),
        }
        output[str(ship_id)] = entry

    if skipped_types:
        print(f"      注意: 未映射的类型 ID: {sorted(skipped_types)}")
    if skipped_nations:
        print(f"      注意: 未映射的阵营 ID: {sorted(skipped_nations)}")
    if skipped_rarities:
        print(f"      注意: 未映射的稀有度 ID: {sorted(skipped_rarities)}")

    print(f"      输出 {len(output)} 条舰船记录")
    return output


def main():
    parser = argparse.ArgumentParser(description="从 AzurLaneLuaScripts 提取舰船数据生成 JSON")
    parser.add_argument(
        "--lua-repo", type=str,
        default=r"C:\Users\AzurLane\Desktop\Projects\AzurLaneLuaScripts",
        help="AzurLaneLuaScripts 仓库路径",
    )
    parser.add_argument("-o", "--output", type=str, default=None, help="输出路径")
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else Path(__file__).resolve().parent / "ship_data.json"

    if not os.path.isdir(args.lua_repo):
        print(f"错误: Lua 脚本仓库不存在: {args.lua_repo}", file=sys.stderr)
        sys.exit(1)

    cn_stats = os.path.join(args.lua_repo, "CN", "sharecfgdata", "ship_data_statistics.lua")
    if not os.path.isfile(cn_stats):
        print(f"错误: CN 舰船数据文件不存在: {cn_stats}", file=sys.stderr)
        sys.exit(1)

    try:
        data = extract_ship_data(args.lua_repo)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    kb = output_path.stat().st_size / 1024
    print(f"\n完成! 输出: {output_path} ({kb:.1f} KB)")

    # 统计
    counts: dict[str, int] = {}
    for s in data.values():
        k = (s["nation_name"], s["rarity_name"], s["type_name"])
        counts[k] = counts.get(k, 0) + 1
    print(f"  阵营×稀有度×类型组合数: {len(counts)}")

    # 改造统计
    ret = [s for s in data.values() if s["is_retrofit"]]
    print(f"  改造舰: {len(ret)} 条")
    t2 = [s for s in data.values() if s.get("is_type2")]
    print(f"  II型舰: {len(t2)} 条")


if __name__ == "__main__":
    main()
