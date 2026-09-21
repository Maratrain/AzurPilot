"""指挥喵天赋评分引擎（纯逻辑，无设备与 OCR 依赖，便于单测）。

评分口径来自公开攻略，**不是游戏官方数值**：

- 《照做就行——28法则下的碧蓝航线攻略执行篇》(坐看云起) 指挥喵章
  —— 主流水面猫的 x+y 记点法、潜艇猫一档/二档、低耗猫天赋表
- 《指挥喵详细上手攻略》(智代智代)
  —— 潜艇/雷暴/低耗的天赋优先级、各彩天赋点评

两篇作者在「水雷魂算不算彩」等问题上存在分歧，因此这里按口径分别实现，
并在每个口径的 ``source`` 字段里标注依据，不做单一化处理。

术语：
    x —— 彩天赋点数（不能升级的特殊天赋，部分口径把个别普通天赋也算作彩）
    y —— 有用普通天赋点数，按天赋等级累加（2 级按 2 点计）
"""

import difflib
import re
from dataclasses import dataclass, field

from module.meowfficer.cat_data import CATS
from module.meowfficer.talent_data import SPECIAL_TALENTS, TALENT_LINES

# 重置天赋的物资消耗（已用点数 -> 物资），来源：WIKI 指挥喵页「重置天赋」表
RESET_COST = {1: 3000, 2: 5600, 3: 7800, 4: 9600, 5: 11000, 6: 12000}

# ---------------------------------------------------------------------------
# 名字归一化
# ---------------------------------------------------------------------------

# OCR / 手输常见错字
_CHAR_TRANS = str.maketrans({
    '：': ':', '（': '(', '）': ')',
    '土': '士',  # OCR 常把「士」认成「土」
    '巳': '己',
})
# 分隔符在匹配时直接丢弃：OCR 会把「·」读成 ．，、, 等各种形态
_SEP_RE = re.compile(r'[\s·．.・•‧﹒，、,]')


def normalize(text: str) -> str:
    """归一化天赋/猫名，用于匹配。

    Args:
        text: 原始文本（可能来自 OCR，含空格、错字、分隔符变体）。

    Returns:
        去掉空白与分隔符、修正常见错字后的字符串。
    """
    return _SEP_RE.sub('', (text or '').strip().translate(_CHAR_TRANS))


@dataclass
class TalentRef:
    """天赋库中的一条记录。"""

    name: str          # 规范名
    line: str          # 天赋线 id（普通天赋取 1 级名；特殊天赋取自身名）
    level: int         # 1/2/3；特殊天赋恒为 1
    kind: str          # 'normal' | 'special'
    effect: str = ''
    fixed_only: bool = False


@dataclass
class Talent:
    """识别结果中的一个天赋。"""

    name: str
    line: str
    level: int
    kind: str = 'normal'
    raw: str = ''
    inferred: bool = False   # 由效果文字推断而来，需人工核对


def _build_index() -> dict[str, TalentRef]:
    """构建「归一化名 -> TalentRef」索引。"""
    index: dict[str, TalentRef] = {}
    for line in TALENT_LINES:
        for i, (name, effect) in enumerate(line['levels']):
            key = normalize(name)
            if key and key not in index:
                index[key] = TalentRef(
                    name=name, line=line['id'], level=i + 1, kind='normal',
                    effect=effect, fixed_only=line['fixed_only'])
    for sp in SPECIAL_TALENTS:
        key = normalize(sp['name'])
        if key and key not in index:
            index[key] = TalentRef(
                name=sp['name'], line=sp['name'], level=1, kind='special',
                effect=sp['effect'], fixed_only=sp['fixed_only'])
    return index


TALENT_INDEX: dict[str, TalentRef] = _build_index()
# 长名优先，避免「新手观测士·主力」被更短的线误匹配
_ALL_NAMES: list[str] = sorted(TALENT_INDEX, key=len, reverse=True)
_CAT_NAMES: list[str] = sorted(CATS, key=len, reverse=True)


def match_talent(text: str, cutoff: float = 0.72) -> TalentRef | None:
    """把一段文本匹配到天赋库中的天赋。

    策略：精确归一化命中 -> 子串命中（长名优先）-> difflib 模糊匹配。
    子串命中是为了容忍 OCR 把等级标记、图标噪声粘在名字前后。

    Args:
        text: 待匹配文本。
        cutoff: 模糊匹配相似度下限。

    Returns:
        命中的 :class:`TalentRef`；无法匹配时返回 ``None``。
    """
    key = normalize(text)
    if len(key) < 2:
        return None
    if key in TALENT_INDEX:
        return TALENT_INDEX[key]
    best: str | None = None
    for name in _ALL_NAMES:
        if len(name) >= 4 and name in key:
            if best is None or len(name) > len(best):
                best = name
    if best:
        return TALENT_INDEX[best]
    close = difflib.get_close_matches(key, list(TALENT_INDEX), n=1, cutoff=cutoff)
    return TALENT_INDEX[close[0]] if close else None


def match_cat(text: str, cutoff: float = 0.75) -> str | None:
    """从一段文本里识别指挥喵名字，用于挑选评分口径。"""
    key = normalize(text)
    if not key:
        return None
    for name in _CAT_NAMES:
        if normalize(name) in key:
            return name
    close = difflib.get_close_matches(key, [normalize(n) for n in _CAT_NAMES],
                                      n=1, cutoff=cutoff)
    if close:
        for name in _CAT_NAMES:
            if normalize(name) == close[0]:
                return name
    return None


def resolve_talents(talents: list[str] | list[Talent]) -> list[Talent]:
    """把名字列表解析成去重后的 :class:`Talent` 列表（同线取最高等级）。"""
    out: dict[str, Talent] = {}
    for item in talents:
        if isinstance(item, Talent):
            ref, raw, inferred = TALENT_INDEX.get(normalize(item.name)), item.raw, item.inferred
            level = item.level
        else:
            ref, raw, inferred = match_talent(str(item)), str(item), False
            level = ref.level if ref else 1
        if ref is None:
            out.setdefault(str(item), Talent(name=str(item), line=str(item), level=level,
                                             kind='unknown', raw=raw, inferred=inferred))
            continue
        cur = out.get(ref.line)
        if cur is None or level > cur.level:
            out[ref.line] = Talent(name=ref.name, line=ref.line, level=level,
                                   kind=ref.kind, raw=raw, inferred=inferred)
    return list(out.values())


# ---------------------------------------------------------------------------
# 评分规则表（攻略口径）
# ---------------------------------------------------------------------------

# 主流水面猫：彩天赋（不能升级的特殊天赋）
SURFACE_X = {
    '侵略如火': 1, '不动如山': 1, '其徐如林': 1, '小小的奇迹': 1,
    '王牌机师': 1, '一发入魂': 1, '见敌必战': 1, '既定的命运': 1,
}
# 这些特殊天赋在 28 法则的水面口径下**不算彩**
SURFACE_NOT_X = ['水雷魂', '其疾如风', '被期待的新星', '最佳玩伴', '狼群之首']
# 主流水面猫：有用普通天赋，权重 × 等级
SURFACE_Y = {
    '新晋指挥官·战列': 1.0, '炮击新手·主力': 1.0,
    '新晋指挥官·空母': 1.0, '航空新兵·空母': 1.0,
    '新晋指挥官·巡洋': 1.0, '操舵手·中型舰': 1.0,
    '轮机手·巡洋': 0.75, '炮击新手·巡洋': 0.75,
    '装填新手·战列': 0.5, '新手整备士': 0.5,
    '新晋指挥官·铁血': 0.5, '新晋指挥官·皇家': 0.5,
    '新晋指挥官·重樱': 0.5, '新晋指挥官·白鹰': 0.5,
}

# 潜艇猫
SUB_X = {'狼群之首': 1, '侵略如火': 1}
SUB_Y = {
    '新晋指挥官·潜艇': 1.0, '新人雷击士·潜艇': 1.0,
    '装填新手·潜艇': 1.0, '既定的命运': 1.0, '新晋指挥官·铁血': 1.0,
}
# 潜艇口径下不计分（不代表没用，只是潜艇不需要）
SUB_ZERO = ['不动如山', '其徐如林', '小小的奇迹', '新手观测士·潜艇',
            '轮机手·潜艇', '操舵手·小型舰']

# 低耗猫（前排驱逐 + 后排 100 级突击者/黑暗界）
LOWOIL_X = {'侵略如火': 1, '不动如山': 1, '其徐如林': 1, '小小的奇迹': 1,
            '既定的命运': 1, '新晋指挥官·白鹰': 1}
LOWOIL_Y = {
    '水雷魂': 1.0, '航空新兵·空母': 1.0, '新晋指挥官·驱逐': 1.0,
    '操舵手·小型舰': 1.0, '轮机手·驱逐': 0.75,
    '炮击新手·驱逐': 0.5, '新人雷击士·驱逐': 0.5, '轮机手·空母': 0.5,
    '新手整备士': 0.5, '对空炮手·主力': 0.5, '对空炮手·先锋': 0.5,
    '操舵手·中型舰': 0.5, '装填新手·驱逐': 0.5,
}
LOWOIL_DEAD = ['王牌机师', '新晋指挥官·空母', '一发入魂', '新晋指挥官·战列',
               '见敌必战', '其疾如风', '被期待的新星', '最佳玩伴', '狼群之首',
               '新手观测士·主力', '新手观测士·先锋', '声纳兵·主力', '声纳兵·先锋']

# 雷暴猫（详细篇排序：火 ≥ 水雷魂 > 其疾如风 > 重樱指挥 > 驱逐雷击 > 驱逐指挥 > 既定）
TORPEDO_W = {
    '侵略如火': 3.0, '水雷魂': 3.0, '其疾如风': 2.0, '新晋指挥官·重樱': 2.0,
    '新人雷击士·驱逐': 1.5, '新晋指挥官·驱逐': 1.0, '既定的命运': 1.0,
}

RUBRIC_META = {
    'surface': {
        'label': '主流水面猫（战列/航母/巡洋）',
        'source': '28法则执行篇·水面彩天赋/普通天赋；详细上手攻略·天赋收益略讲',
        'applicable': ('战列', '航母', '巡洋'),
    },
    'submarine': {
        'label': '潜艇猫',
        'source': '28法则执行篇·潜艇猫；详细上手攻略·潜艇喵',
        'applicable': ('潜艇',),
    },
    'lowoil': {
        'label': '低耗猫（前排驱逐 + 后排100级突击者/黑暗界）',
        'source': '28法则执行篇·低耗猫；详细上手攻略·纯低耗猫',
        'applicable': ('驱逐', '航母'),
    },
    'torpedo': {
        'label': '雷暴猫（驱逐/轻巡鱼雷特化）',
        'source': '详细上手攻略·雷暴猫（28法则不评价此线）',
        'applicable': ('驱逐', '巡洋'),
    },
}


# ---------------------------------------------------------------------------
# 评分实现
# ---------------------------------------------------------------------------

@dataclass
class RubricResult:
    """单个口径的评分结果。"""

    key: str
    label: str
    x: int
    y: float
    tier: str
    score100: int
    source: str
    x_hits: list[str] = field(default_factory=list)
    y_hits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class ScoreResult:
    """一次评分的完整结果。"""

    talents: list[Talent]
    cat: str | None = None
    cat_info: dict = field(default_factory=dict)
    rubrics: dict[str, RubricResult] = field(default_factory=dict)
    primary: list[str] = field(default_factory=list)
    maxed: bool = False
    points_spent: int | None = None


def _find(talents: list[Talent], line: str) -> Talent | None:
    for t in talents:
        if t.line == line:
            return t
    return None


def _sum_weight(talents: list[Talent], table: dict[str, float]) -> tuple[float, list[str]]:
    """按权重×等级汇总，返回 (总分, 命中明细)。"""
    total, hits = 0.0, []
    for line, weight in table.items():
        t = _find(talents, line)
        if t:
            total += weight * t.level
            hits.append(f'{line} Lv{t.level}')
    return total, hits


def _score_surface(talents: list[Talent]) -> RubricResult:
    """主流水面猫口径。"""
    x_hits = [t.name for t in talents if t.line in SURFACE_X]
    x = len(x_hits)
    y, y_hits = _sum_weight(talents, SURFACE_Y)
    is_cv = _find(talents, '王牌机师') is not None
    is_bb = _find(talents, '一发入魂') is not None

    if x >= 2 and y >= 3:
        tier = '毕业级'
    elif x >= 2 and y >= 2:
        tier = '准毕业（偏上）'
    elif x >= 1 and y >= 2:
        tier = '准毕业'
    elif x >= 3:
        tier = '准毕业（彩多但普通点少）'
    elif x >= 1:
        tier = '过渡可用'
    else:
        tier = '零食（建议喂掉）'

    notes = []
    if not x:
        notes.append('没有任何彩天赋：28法则口径下属于陪玩材料')
    if x and '侵略如火' not in x_hits:
        notes.append('缺「侵略如火」——它是唯一被单列为第一档的输出彩')
    if is_cv and not is_bb:
        notes.append('含「王牌机师」，按攻略直接定性为航母猫')
    elif is_bb and not is_cv:
        notes.append('含「一发入魂」，按攻略直接定性为战列猫')
    else:
        notes.append('无 ACE/一发：请用普通天赋点数判断偏航母还是偏战列（哪边 Y 多算哪边）')
    notes.append('不计点项：命中/反潜/驱逐/操舵手小型/大型等（28法则：不入榜的都算 0 点）')
    return RubricResult(
        key='surface', label=RUBRIC_META['surface']['label'], x=x, y=round(y, 2),
        tier=tier, score100=min(100, round(x * 28 + y * 13)),
        source=RUBRIC_META['surface']['source'],
        x_hits=x_hits, y_hits=y_hits, notes=notes)


def _score_submarine(talents: list[Talent]) -> RubricResult:
    """潜艇猫口径。"""
    x_hits = [n for n in SUB_X if _find(talents, n)]
    x = len(x_hits)
    y, y_hits = _sum_weight(talents, SUB_Y)
    has_cmd = _find(talents, '新晋指挥官·潜艇') is not None

    if x == 2 and has_cmd:
        tier = '完美猫'
    elif x == 2 or (x == 1 and has_cmd):
        tier = '优秀（够用）'
    elif x == 1 and y >= 2:
        tier = '准毕业'
    elif x == 1:
        tier = '过渡可用'
    else:
        tier = '无彩（建议喂掉）'

    notes = []
    miss = [n for n in ('侵略如火', '狼群之首') if not _find(talents, n)]
    if miss:
        notes.append('缺 ' + '、'.join(miss) + '：两者是潜艇口径里唯一的两个一档输出彩')
    else:
        notes.append('火 + 狼 双一档齐：潜艇输出核心完整')
    if has_cmd:
        notes.append('有「新晋指挥官·潜艇」：满级雷击+20/装填+6，收益甚至高于火'
                     '（但后天相对好出，初始含金量打折）')
    else:
        notes.append('缺「新晋指挥官·潜艇」：它是后天相对好出的那一项，可用洗猫补')
    if _find(talents, '新晋指挥官·铁血'):
        notes.append('「新晋指挥官·铁血」只对铁血潜艇生效，请确认潜艇队阵营')
    wasted = [n for n in SUB_ZERO if _find(talents, n)]
    if wasted:
        notes.append('本口径下不计分（≠没用，只是潜艇不需要）：' + '、'.join(wasted))
    return RubricResult(
        key='submarine', label=RUBRIC_META['submarine']['label'], x=x, y=round(y, 2),
        tier=tier, score100=min(100, round(x * 25 + y * 13)),
        source=RUBRIC_META['submarine']['source'],
        x_hits=x_hits, y_hits=y_hits, notes=notes)


def _score_lowoil(talents: list[Talent]) -> RubricResult:
    """低耗猫口径。"""
    x_hits = [t.name for t in talents if t.line in LOWOIL_X]
    x = len(x_hits)
    y, y_hits = _sum_weight(talents, LOWOIL_Y)
    dead = [t.name for t in talents if t.line in LOWOIL_DEAD]

    if x >= 2 and y >= 2:
        tier = '好用的低耗猫'
    elif x >= 1 and y >= 1:
        tier = '可用'
    elif x >= 1 or y >= 2:
        tier = '过渡'
    else:
        tier = '不适合低耗'

    notes = ['仅在「前排驱逐 + 后排 100 级突击者 / 黑暗界」语境成立；换环境结论会变']
    if _find(talents, '新晋指挥官·白鹰'):
        notes.append('白鹰指挥在 100 级突击者后排时相当于彩；1 级后排/满破船环境只算普通')
    if dead:
        notes.append('低耗吃不到（本口径 0 分）：' + '、'.join(dead))
    return RubricResult(
        key='lowoil', label=RUBRIC_META['lowoil']['label'], x=x, y=round(y, 2),
        tier=tier, score100=min(100, round(x * 20 + y * 10)),
        source=RUBRIC_META['lowoil']['source'],
        x_hits=x_hits, y_hits=y_hits, notes=notes)


def _score_torpedo(talents: list[Talent]) -> RubricResult:
    """雷暴猫口径（加权点）。"""
    weight, hits = _sum_weight(talents, TORPEDO_W)
    if weight >= 5:
        tier = '雷暴优秀'
    elif weight >= 3:
        tier = '雷暴可用'
    elif weight > 0:
        tier = '勉强能玩'
    else:
        tier = '不适合雷暴'
    return RubricResult(
        key='torpedo', label=RUBRIC_META['torpedo']['label'],
        x=1 if _find(talents, '侵略如火') else 0, y=0,
        tier=tier, score100=min(100, round(weight * 11)),
        source=RUBRIC_META['torpedo']['source'],
        x_hits=[], y_hits=hits, notes=[
            '雷暴是小众玩法，本口径只反映「驱逐/轻巡贴脸雷」的方向性收益',
            '详细篇排序：火 ≥ 水雷魂 > 其疾如风 > 重樱指挥 > 驱逐雷击 > 驱逐指挥 > 既定',
        ])


_SCORERS = {
    'surface': _score_surface,
    'submarine': _score_submarine,
    'lowoil': _score_lowoil,
    'torpedo': _score_torpedo,
}


def evaluate(talents: list[str] | list[Talent], cat: str | None = None,
             points_spent: int | None = None) -> ScoreResult:
    """对一组天赋打分。

    Args:
        talents: 天赋名列表，或已解析的 :class:`Talent` 列表。
        cat: 指挥喵名字，用于挑选适用口径并带上攻略点评。
        points_spent: 已使用的天赋点数，给出洗猫成本参考。

    Returns:
        :class:`ScoreResult`，含四套口径的结果与「主口径」排序。
    """
    cat_name = cat
    if cat is not None and cat not in CATS:
        cat_name = match_cat(cat) or cat
    cat_info = dict(CATS.get(cat_name or '', {}))
    types = cat_info.get('types') or []

    resolved = resolve_talents(talents)
    rubrics = {key: fn(resolved) for key, fn in _SCORERS.items()}

    primary = [key for key, meta in RUBRIC_META.items()
               if set(meta['applicable']) & set(types)]
    if not primary:
        primary = sorted(rubrics, key=lambda k: -rubrics[k].score100)[:2]
    primary.sort(key=lambda k: -rubrics[k].score100)

    return ScoreResult(
        talents=resolved, cat=cat_name, cat_info=cat_info, rubrics=rubrics,
        primary=primary,
        maxed=any(t.level >= 3 for t in resolved),  # 初始天赋最高只有 2 级
        points_spent=points_spent)
