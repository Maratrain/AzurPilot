#!/usr/bin/env python3
"""
从 AzurLaneLuaScripts 仓库的 Lua 脚本中提取舰船数据，生成统一 JSON。

数据来源：
  - {SERVER}/sharecfgdata/ship_data_statistics.lua  → 舰船属性（阵营、类型、稀有度、名称）
  - CN/sharecfg/ship_data_by_type.lua              → 类型中文名映射

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
      "rarity_name": "精锐",
      "star": 4
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
# 阵营 ID → 中文名
# 来源: CN/model/const/nation.lua Nation2Name
# ---------------------------------------------------------------------------
NATION_NAME_MAP: dict[int, str] = {
    0:   "其他",
    1:   "白鹰",
    2:   "皇家",
    3:   "重樱",
    4:   "铁血",
    5:   "东煌",
    6:   "撒丁帝国",
    7:   "北方联合",
    8:   "自由鸢尾",
    9:   "维希教廷",
    10:  "鸢尾教国",
    11:  "御金王国",
    12:  "镜幻联盟",
    # 特殊阵营
    96:  "MOT",
    97:  "META",
    98:  "布里",
    99:  "塞壬",
    # 联动阵营（100+）
    101: "海王星",
    102: "哔哩哔哩",
    103: "传颂之物",
    104: "AI",
    105: "hololive",
    106: "死或生",
    107: "偶像大师",
    108: "SSSS",
    109: "莱莎",
    110: "闪乱神乐",
    111: "To LOVE",
    112: "黑岩射手",
    113: "尤米亚",
    114: "地错",
    115: "约会大作战",
    117: "尼尔",
}

# ---------------------------------------------------------------------------
# 稀有度 ID → 中文名
# 来源: CN/model/const/shiprarity.lua
# ---------------------------------------------------------------------------
RARITY_NAME_MAP: dict[int, str] = {
    2:  "N",
    3:  "R",
    4:  "SR",
    5:  "SSR",
    6:  "UR",
    18: "DR",
}

# ---------------------------------------------------------------------------
# 各服务器目录名
# ---------------------------------------------------------------------------
SERVER_DIRS = {
    "cn": "CN",
    "en": "EN",
    "jp": "JP",
    "tw": "TW",
    "kr": "KR",
}

# ---------------------------------------------------------------------------
# Lua 解析辅助
# ---------------------------------------------------------------------------

# 匹配 _G.pg.base.ship_data_statistics[123456] = {
_SHIP_ENTRY_RE = re.compile(
    r'_G\.pg\.base\.ship_data_statistics\[(\d+)\]\s*=\s*\{'
)

# 匹配简单 key = value 行（支持数字、字符串、布尔值）
# 例如: nationality = 98, name = "泛用型布里", oxy_max = 0
_KV_LINE_RE = re.compile(
    r'^\s*(\w+)\s*=\s*'
    r'('
    r'"(?:[^"\\]|\\.)*"'       # 字符串
    r'|\d+(?:\.\d+)?'           # 数字
    r'|true|false'              # 布尔
    r')\s*,?\s*$'
)

# 匹配只包含 key 的行（用于检测嵌套表、空表等）
_BRACE_OPEN_RE = re.compile(r'\{')
_BRACE_CLOSE_RE = re.compile(r'\}')


def parse_ship_statistics(filepath: str) -> dict[int, dict]:
    """解析 ship_data_statistics.lua，返回 {ship_id: {key: value}, ...}。

    仅提取顶层简单字段（nationality, type, name, rarity, star,
    english_name 等）。嵌套表字段（attrs, equip_* 等）会被跳过。
    """
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    results: dict[int, dict] = {}
    lines = text.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i]
        m = _SHIP_ENTRY_RE.search(line)
        if not m:
            i += 1
            continue

        ship_id = int(m.group(1))
        ship_data: dict = {}

        # 从下一行开始解析，直到匹配的 }（入口行已有 {，故从 1 开始）
        i += 1
        brace_depth = 1
        while i < len(lines):
            cline = lines[i]
            # 统计本行的花括号（需要跳过字符串内的）
            clean = _remove_string_content(cline)
            brace_depth += clean.count("{") - clean.count("}")

            if brace_depth <= 0:
                # 结束了这个顶层 table
                break

            # 尝试匹配简单 KV
            # 先跳过已经深入嵌套的行（brace_depth > 1 时我们只关心这行的括号）
            if brace_depth == 1:
                kv = _KV_LINE_RE.match(cline)
                if kv:
                    key = kv.group(1)
                    val = _parse_value(kv.group(2))
                    ship_data[key] = val

            i += 1

        if ship_data:
            results[ship_id] = ship_data

        i += 1

    return results


def parse_ship_data_by_type(filepath: str) -> dict[int, str]:
    """解析 ship_data_by_type.lua，返回 {type_id: type_name}（中文类型名）。"""
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    # 匹配每个 type entry:
    # pg.base.ship_data_by_type[N] = { ... type_name = "驱逐" ... }
    type_blocks = re.findall(
        r'pg\.base\.ship_data_by_type\[(\d+)\]\s*=\s*\{'
        r'(.*?)'
        r'^\s*\}',
        text,
        re.MULTILINE | re.DOTALL,
    )

    result: dict[int, str] = {}
    for type_id_str, block in type_blocks:
        type_id = int(type_id_str)
        name_m = re.search(r'type_name\s*=\s*"([^"]*)"', block)
        if name_m:
            result[type_id] = name_m.group(1).strip()

    return result


def _remove_string_content(line: str) -> str:
    """移除行中的字符串字面量内容，保留引号结构以避免干扰括号计数。"""
    # 简单方法：用正则移除所有 "..." 内容
    return re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', '""', line)


def _parse_value(raw: str) -> int | float | bool | str:
    """将 Lua 字面量转换为 Python 值。"""
    raw = raw.strip().rstrip(",").strip()
    if raw.startswith('"') and raw.endswith('"'):
        # 去掉首尾引号，处理转义
        inner = raw[1:-1]
        inner = inner.replace('\\"', '"').replace('\\n', '\n').replace('\\\\', '\\')
        return inner
    if raw == "true":
        return True
    if raw == "false":
        return False
    if "." in raw:
        try:
            return float(raw)
        except ValueError:
            return raw
    try:
        return int(raw)
    except ValueError:
        return raw


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------

def extract_ship_data(lua_repo: str) -> dict:
    """主提取函数，返回按 ship_id 索引的完整舰船数据。"""
    repo = Path(lua_repo)
    if not repo.exists():
        raise FileNotFoundError(f"Lua 脚本仓库不存在: {repo}")

    # 1. 以 CN 为主数据源，提取所有舰船的属性
    print("[1/4] 解析 CN 舰船数据...")
    cn_stats_path = repo / "CN" / "sharecfgdata" / "ship_data_statistics.lua"
    if not cn_stats_path.exists():
        raise FileNotFoundError(f"CN 舰船数据文件不存在: {cn_stats_path}")
    cn_ships = parse_ship_statistics(str(cn_stats_path))
    print(f"      解析到 {len(cn_ships)} 条 CN 舰船记录")

    # 2. 解析 CN 的类型名映射
    print("[2/4] 解析舰船类型名称...")
    type_path = repo / "CN" / "sharecfg" / "ship_data_by_type.lua"
    type_name_map = parse_ship_data_by_type(str(type_path))
    print(f"      解析到 {len(type_name_map)} 种舰船类型: {dict(sorted(type_name_map.items()))}")

    # 3. 从各服务器提取多语言名称
    print("[3/4] 解析各服务器舰船名称...")
    server_names: dict[str, dict[int, str]] = {}
    for server_key, server_dir in SERVER_DIRS.items():
        stats_path = repo / server_dir / "sharecfgdata" / "ship_data_statistics.lua"
        if not stats_path.exists():
            print(f"      [{server_key}] 文件不存在，跳过: {stats_path}")
            server_names[server_key] = {}
            continue
        ships = parse_ship_statistics(str(stats_path))
        server_names[server_key] = {
            sid: data.get("name", "") for sid, data in ships.items()
        }
        print(f"      [{server_key}] 解析到 {len(ships)} 个舰船名称")

    # 4. 合并输出
    print("[4/4] 合并数据...")
    output: dict = {}
    skipped_types: set = set()
    skipped_nations: set = set()
    skipped_rarities: set = set()

    for ship_id, data in cn_ships.items():
        ship_type = data.get("type")
        nationality = data.get("nationality")
        rarity = data.get("rarity")

        # 收集未知的类型/阵营/稀有度
        if ship_type is not None and ship_type not in type_name_map:
            skipped_types.add(ship_type)
        if nationality is not None and nationality not in NATION_NAME_MAP:
            skipped_nations.add(nationality)
        if rarity is not None and rarity not in RARITY_NAME_MAP:
            skipped_rarities.add(rarity)

        entry: dict = {
            "name": {
                sk: server_names[sk].get(ship_id, None)
                for sk in SERVER_DIRS
            },
            "english_name": data.get("english_name", ""),
            "nationality": nationality,
            "nation_name": NATION_NAME_MAP.get(nationality, f"未知阵营({nationality})"),
            "type": ship_type,
            "type_name": type_name_map.get(ship_type, f"未知类型({ship_type})"),
            "rarity": rarity,
            "rarity_name": RARITY_NAME_MAP.get(rarity, f"未知稀有度({rarity})"),
            "star": data.get("star"),
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
    parser = argparse.ArgumentParser(
        description="从 AzurLaneLuaScripts 提取舰船数据生成 JSON"
    )
    parser.add_argument(
        "--lua-repo",
        type=str,
        default=r"C:\Users\AzurLane\Desktop\Projects\AzurLaneLuaScripts",
        help="AzurLaneLuaScripts 仓库路径 (默认: C:\\Users\\AzurLane\\Desktop\\Projects\\AzurLaneLuaScripts)",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help="输出 JSON 文件路径 (默认: dev_tools/ship_data.json)",
    )
    args = parser.parse_args()

    # 确定输出路径
    if args.output:
        output_path = Path(args.output)
    else:
        # 默认输出到本项目的 dev_tools 目录
        script_dir = Path(__file__).resolve().parent
        output_path = script_dir / "ship_data.json"

    # 验证输入
    if not os.path.isdir(args.lua_repo):
        print(f"错误: Lua 脚本仓库不存在: {args.lua_repo}", file=sys.stderr)
        sys.exit(1)

    # 检查必要文件
    cn_stats = os.path.join(args.lua_repo, "CN", "sharecfgdata", "ship_data_statistics.lua")
    if not os.path.isfile(cn_stats):
        print(f"错误: CN 舰船数据文件不存在: {cn_stats}", file=sys.stderr)
        sys.exit(1)

    try:
        data = extract_ship_data(args.lua_repo)
    except Exception as e:
        print(f"错误: 提取失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 输出
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    file_size_kb = output_path.stat().st_size / 1024
    print(f"\n完成! 输出文件: {output_path} ({file_size_kb:.1f} KB)")

    # 输出统计
    type_counts: dict[str, int] = {}
    nation_counts: dict[str, int] = {}
    for entry in data.values():
        tn = entry["type_name"]
        nn = entry["nation_name"]
        type_counts[tn] = type_counts.get(tn, 0) + 1
        nation_counts[nn] = nation_counts.get(nn, 0) + 1

    print(f"\n类型分布 (Top 10):")
    for name, count in sorted(type_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"  {name}: {count}")

    print(f"\n阵营分布:")
    for name, count in sorted(nation_counts.items(), key=lambda x: -x[1]):
        print(f"  {name}: {count}")


if __name__ == "__main__":
    main()
