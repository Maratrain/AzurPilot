"""指挥喵评分报告渲染（自包含 HTML）。

为什么要 HTML：评分卡里有大量结构化信息（档位、x+y、天赋等级、命中明细、口径依据），
纯文本/Markdown 在日志里既不好读也不好看。这里生成**单文件自包含 HTML**：

- 样式内联，不依赖任何外部资源，双击即可打开，也可放进静态目录由 WebUI 展示
- 深色卡片式布局：环形分数 + 档位徽章 + 天赋标签（彩天赋描金、等级用罗马数字小徽章）
- 命中明细按 X/Y 分组渲染成标签，而不是一长串文字
- 多只猫时自动堆叠成卡片，并在顶部给出汇总表

只负责渲染，不写文件（文件写入由 :mod:`module.meowfficer.score_task` 决定）。
"""

import html

from module.meowfficer.score import RESET_COST

# 档位关键词 -> 颜色主题，越靠前越优先匹配
TIER_THEMES = (
    ('完美猫', 'gold'), ('毕业级', 'gold'),
    ('准毕业', 'good'), ('优秀', 'good'), ('雷暴优秀', 'good'), ('好用的低耗猫', 'good'),
    ('可用', 'ok'), ('过渡', 'warn'), ('勉强', 'warn'),
    ('零食', 'bad'), ('无彩', 'bad'), ('不适合', 'bad'), ('不建议', 'bad'),
)

LEVEL_MARKS = {1: 'Ⅰ', 2: 'Ⅱ', 3: 'Ⅲ'}

STYLE = """
:root{
  --bg:#0d1219; --card:#151d27; --card2:#1b2531; --soft:#111823; --line:#27313e;
  --fg:#e9eff7; --muted:#8798ad; --accent:#5aa9e6; --gold:#f2c14e;
  --good:#4ade80; --ok:#60a5fa; --warn:#fbbf24; --bad:#f87171;
}
*{box-sizing:border-box}
body{margin:0;padding:32px 18px 64px;color:var(--fg);
  background:radial-gradient(1100px 520px at 12% -8%,#1a2a3d 0%,transparent 62%),var(--bg);
  font:14px/1.65 "Microsoft YaHei UI","Microsoft YaHei",system-ui,-apple-system,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1020px;margin:0 auto}
/* ---------- 页头 ---------- */
.page-head{margin-bottom:20px}
.page-head h1{margin:0 0 6px;font-size:25px;letter-spacing:.6px;font-weight:700}
.page-head .sub{color:var(--muted);font-size:12.5px}
.pills{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 22px}
.pills span{background:var(--card2);border:1px solid var(--line);border-radius:999px;
  padding:4px 13px;font-size:12px;color:var(--muted)}
.pills b{color:#cfdcec;font-weight:600}
/* ---------- 汇总表 ---------- */
table.mini{width:100%;border-collapse:separate;border-spacing:0;margin:0 0 24px;font-size:13px;
  background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
table.mini th,table.mini td{text-align:left;padding:10px 14px;border-bottom:1px solid var(--line)}
table.mini tr:last-child td{border-bottom:none}
table.mini th{color:var(--muted);font-weight:500;font-size:11.5px;letter-spacing:.4px;background:var(--soft)}
table.mini td.name{font-weight:600}
.bar{display:inline-block;width:92px;height:6px;border-radius:3px;background:#26313f;
  vertical-align:middle;overflow:hidden;margin-right:8px}
.bar i{display:block;height:100%;border-radius:3px;background:linear-gradient(90deg,#3f7fb5,var(--accent))}
/* ---------- 猫卡片 ---------- */
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:20px 22px 18px;margin-bottom:18px;box-shadow:0 8px 28px rgba(0,0,0,.30)}
.card-head{display:flex;align-items:center;gap:18px}
.ring{position:relative;width:80px;height:80px;border-radius:50%;flex:0 0 auto;display:grid;place-items:center;
  background:conic-gradient(from -90deg,var(--ring-color,var(--accent)) calc(var(--p,0)*1%),#253141 0)}
.ring::after{content:'';position:absolute;inset:8px;border-radius:50%;background:var(--card)}
.ring i{position:relative;z-index:1;font-style:normal;font-size:21px;font-weight:700;line-height:1.05;
  text-align:center}
.ring i small{display:block;font-size:9px;color:var(--muted);font-weight:400;letter-spacing:.5px}
.head-main{min-width:0}
.cat-name{font-size:19px;font-weight:700;letter-spacing:.4px}
.badges{display:flex;gap:6px;flex-wrap:wrap;margin-top:7px}
.badge{font-size:11px;padding:2px 9px;border-radius:6px;background:#212d3b;color:var(--muted);
  border:1px solid var(--line)}
.badge.fixed{background:#372c12;color:var(--gold);border-color:#54451c}
.badge.maxed{background:#372431;color:#eda6c4;border-color:#553045}
.head-note{color:var(--muted);font-size:12.5px;margin-top:7px}
/* ---------- 档位 ---------- */
.tier{font-size:13.5px;font-weight:700;padding:2px 11px;border-radius:7px;white-space:nowrap}
.tier.gold{background:linear-gradient(135deg,#443512,#634d1c);color:var(--gold);border:1px solid #74591f}
.tier.good{background:#11301e;color:var(--good);border:1px solid #1d5835}
.tier.ok{background:#11273c;color:var(--ok);border:1px solid #1c4364}
.tier.warn{background:#372c12;color:var(--warn);border:1px solid #5a461a}
.tier.bad{background:#371a1a;color:var(--bad);border:1px solid #572929}
/* ---------- 天赋标签 ---------- */
.section-title{font-size:12px;color:var(--muted);letter-spacing:.6px;margin:20px 0 9px;
  text-transform:uppercase}
.chips{display:flex;gap:7px;flex-wrap:wrap}
.chip{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;padding:4px 10px;border-radius:8px;
  background:var(--card2);border:1px solid var(--line);color:#d3dfee}
.chip.gold{border-color:#74591f;background:linear-gradient(135deg,#3a2e10,#473714);color:var(--gold);
  font-weight:600}
.chip.inferred{border-style:dashed;border-color:#5a461a;color:var(--warn)}
.chip .star{color:var(--gold)}
.lv{display:inline-grid;place-items:center;min-width:19px;height:17px;padding:0 4px;border-radius:5px;
  background:#0f1720;border:1px solid var(--line);color:#9fb3ca;font-size:10.5px;font-weight:600;
  line-height:1}
.chip.gold .lv{background:#241c09;border-color:#6a5220;color:#e6cf94}
/* ---------- 口径区块 ---------- */
.rubric{margin-top:20px;padding:16px 18px;border-radius:12px;background:var(--soft);
  border:1px solid var(--line)}
.rubric.primary{background:linear-gradient(180deg,#17222f,#141c26);border-color:#31506e;
  box-shadow:inset 3px 0 0 var(--accent)}
.rubric-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.rubric-head .label{font-size:15px;font-weight:600}
.rubric-head .star-mark{font-size:15px;color:var(--gold)}
.rubric-head .score-mini{margin-left:auto;color:var(--muted);font-size:12.5px}
.xy{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.xy .item{background:var(--card2);border:1px solid var(--line);border-radius:9px;padding:5px 13px;
  font-size:12.5px;color:var(--muted)}
.xy .item b{color:var(--fg);font-size:16px;font-weight:700;margin-left:6px}
.xy .item.gold{border-color:#54451c;background:#2a220f}
.xy .item.gold b{color:var(--gold)}
.group{margin:10px 0 0}
.group-label{display:block;font-size:11.5px;color:var(--muted);letter-spacing:.4px;margin-bottom:6px}
.notes{margin:14px 0 0;padding:0;list-style:none}
.notes li{position:relative;padding-left:15px;color:#c2d0e0;font-size:12.8px;margin:5px 0}
.notes li::before{content:'';position:absolute;left:3px;top:9px;width:5px;height:5px;border-radius:50%;
  background:var(--accent);opacity:.75}
.src{margin-top:12px;color:#6f8098;font-size:11.5px}
.empty{color:var(--muted);font-size:12.5px}
.foot{margin-top:26px;color:var(--muted);font-size:11.8px;line-height:1.9;
  border-top:1px solid var(--line);padding-top:16px}
.foot b{color:#c2d0e0;font-weight:600}
@media (max-width:640px){.card-head{flex-wrap:wrap}.rubric-head .score-mini{margin-left:0}}
@media print{body{background:#fff;color:#111}.card,.rubric{box-shadow:none}}
"""


def _e(text) -> str:
    """HTML 转义。"""
    return html.escape(str(text if text is not None else ''))


def _tier_theme(tier: str) -> str:
    """把档位文案映射到颜色主题。"""
    for keyword, theme in TIER_THEMES:
        if keyword in (tier or ''):
            return theme
    return 'ok'


def _score_theme(score: int) -> str:
    """按分数取颜色。"""
    if score >= 80:
        return 'good'
    if score >= 60:
        return 'ok'
    if score >= 40:
        return 'warn'
    return 'bad'


def _ring(score: int) -> str:
    """环形分数。"""
    return (f'<div class="ring" style="--p:{int(score)};'
            f'--ring-color:var(--{_score_theme(score)})">'
            f'<i>{int(score)}<small>/100</small></i></div>')


def _talent_chips(talents) -> str:
    """天赋标签：彩天赋描金带星，推断/未匹配用虚线框，等级用小徽章。"""
    if not talents:
        return '<span class="empty">没有识别到天赋</span>'
    out = []
    for talent in talents:
        classes = ['chip']
        star = ''
        if talent.kind == 'special':
            classes.append('gold')
            star = '<span class="star">★</span>'
        if getattr(talent, 'inferred', False) or talent.kind == 'unknown':
            classes.append('inferred')
        level = ''
        if talent.kind != 'special':
            level = f'<span class="lv">{LEVEL_MARKS.get(talent.level, talent.level)}</span>'
        out.append(f'<span class="{" ".join(classes)}">{star}{_e(talent.name)}{level}</span>')
    return ''.join(out)


def _hit_chips(hits) -> str:
    """命中明细标签：把 ``名字 Lv3`` 拆成「等级徽章 + 名字」。"""
    if not hits:
        return '<span class="empty">无</span>'
    out = []
    for hit in hits:
        name, level = str(hit), ''
        if ' Lv' in name:
            name, level = name.rsplit(' Lv', 1)
        badge = f'<span class="lv">{LEVEL_MARKS.get(int(level), level)}</span>' if level else ''
        out.append(f'<span class="chip">{badge}{_e(name)}</span>')
    return ''.join(out)


def _rubric_block(rubric, is_primary: bool) -> str:
    """单个评分口径区块。"""
    theme = _tier_theme(rubric.tier)
    mark = '<span class="star-mark">★</span>' if is_primary else '<span class="star-mark">☆</span>'
    parts = [f'<div class="rubric{" primary" if is_primary else ""}">',
             '<div class="rubric-head">', mark,
             f'<span class="label">{_e(rubric.label)}</span>',
             f'<span class="tier {theme}">{_e(rubric.tier)}</span>',
             f'<span class="score-mini">参考分 {int(rubric.score100)}/100</span>',
             '</div>']

    if rubric.key == 'torpedo':
        parts.append(f'<div class="xy"><span class="item">加权点<b>{round(rubric.score100 / 11, 1)}</b></span></div>')
    else:
        x_class = 'item gold' if rubric.x_hits else 'item'
        parts.append('<div class="xy">'
                     f'<span class="{x_class}">x · 彩天赋<b>{rubric.x}</b></span>'
                     f'<span class="item">y · 有用普通<b>{rubric.y}</b></span>'
                     '</div>')

    if rubric.x_hits:
        parts.append('<div class="group"><span class="group-label">X · 彩天赋</span>'
                     f'<div class="chips">{_hit_chips(rubric.x_hits)}</div></div>')
    parts.append('<div class="group"><span class="group-label">Y · 有用普通</span>'
                 f'<div class="chips">{_hit_chips(rubric.y_hits)}</div></div>')
    if rubric.notes:
        parts.append('<ul class="notes">')
        parts.extend(f'<li>{_e(n)}</li>' for n in rubric.notes)
        parts.append('</ul>')
    parts.append(f'<div class="src">依据：{_e(rubric.source)}</div>')
    parts.append('</div>')
    return ''.join(parts)


def _cat_card(name: str, result) -> str:
    """单只猫的卡片。"""
    info = result.cat_info or {}
    badges = []
    for value in (info.get('rarity'), info.get('faction'),
                  '/'.join(info.get('types') or []) or None, info.get('position')):
        if value:
            badges.append(f'<span class="badge">{_e(value)}</span>')
    if info.get('fixed'):
        badges.append('<span class="badge fixed">固定天赋猫</span>')
    if result.maxed:
        badges.append('<span class="badge maxed">成品猫 · 已点过点</span>')

    primary_key = result.primary[0] if result.primary else None
    primary = result.rubrics.get(primary_key)
    score = primary.score100 if primary else 0

    parts = ['<div class="card">', '<div class="card-head">', _ring(score),
             '<div class="head-main">',
             f'<div class="cat-name">{_e(result.cat or name)}</div>',
             '<div class="badges">' + ''.join(badges) + '</div>']
    if info.get('note'):
        parts.append(f'<div class="head-note">{_e(info["note"])}</div>')
    parts.append('</div></div>')

    parts.append('<div class="section-title">天赋</div>'
                 f'<div class="chips">{_talent_chips(result.talents)}</div>')

    if primary:
        parts.append(_rubric_block(primary, True))
    for key in result.primary[1:]:
        parts.append(_rubric_block(result.rubrics[key], False))

    if result.points_spent:
        parts.append('<ul class="notes"><li>已使用天赋点 '
                     f'{int(result.points_spent)} 点'
                     '（重置只回到初始天赋，后天天赋会全部消失）</li></ul>')
    parts.append('</div>')
    return ''.join(parts)


def to_payload(results, generated_at: str = '') -> dict:
    """把评分结果转成机器可读的 dict（供 WebUI 面板/接口消费）。

    刻意不直接暴露 ``dataclass`` 结构，字段名用 camelCase 且固定，避免内部实现变化
    影响前端契约。

    Args:
        results: ``[(来源名, ScoreResult), ...]``。
        generated_at: 生成时间文本。

    Returns:
        dict: ``{'generatedAt', 'count', 'cats': [...]}``。
    """
    cats = []
    for name, result in results:
        info = result.cat_info or {}
        rubrics = []
        for key, rubric in result.rubrics.items():
            entry = {
                'key': rubric.key,
                'label': rubric.label,
                'tier': rubric.tier,
                'score': int(rubric.score100),
                'x': rubric.x,
                'y': rubric.y,
                'xHits': list(rubric.x_hits),
                'yHits': list(rubric.y_hits),
                'notes': list(rubric.notes),
                'source': rubric.source,
                'primary': bool(result.primary and result.primary[0] == key),
            }
            # 雷暴口径是加权点制，没有 x/y 两个维度：清掉数值并给出语义标签，
            # 否则前端会把「加权命中」按字面渲染成「Y（有用普通）」与 x+y 公式。
            if rubric.key == 'torpedo':
                entry.update({'x': None, 'y': None, 'xHits': [], 'yLabel': '加权命中'})
            rubrics.append(entry)
        rubrics.sort(key=lambda item: (not item['primary'], -item['score']))
        cats.append({
            'source': name,
            'cat': result.cat,
            'tags': [str(x) for x in (info.get('rarity'), info.get('faction'),
                                      '/'.join(info.get('types') or []) or None,
                                      info.get('position')) if x],
            'fixed': bool(info.get('fixed')),
            'note': info.get('note') or '',
            'maxed': bool(result.maxed),
            'pointsSpent': result.points_spent,
            'primary': result.primary[0] if result.primary else None,
            'talents': [{'name': t.name, 'level': t.level, 'kind': t.kind,
                         'inferred': bool(getattr(t, 'inferred', False))}
                        for t in result.talents],
            'rubrics': rubrics,
        })
    return {'generatedAt': generated_at, 'count': len(cats), 'cats': cats}


def render_summary(result) -> str:
    """把评分结果压成一行摘要（日志里用，避免每只猫刷二十多行）。

    Args:
        result: :func:`module.meowfficer.score.evaluate` 的结果。

    Returns:
        str: 形如 ``克雷喵（SSR·铁血·潜艇）→ 潜艇猫 准毕业 100/100 | x+y=1+6 | 彩:狼群之首``
    """
    info = result.cat_info
    tags = '·'.join(str(x) for x in (info.get('rarity'), info.get('faction'),
                                    '/'.join(info.get('types') or []) or None) if x)
    name = result.cat or '未知'
    key = result.primary[0] if result.primary else None
    rubric = result.rubrics.get(key)
    if rubric is None:
        return f'{name}（{tags}）→ 无评分'
    specials = [t.name for t in result.talents if t.kind == 'special']
    parts = [f'{name}（{tags}）→ {rubric.label} {rubric.tier} {rubric.score100}/100']
    if rubric.key != 'torpedo':
        parts.append(f'x+y={rubric.x}+{rubric.y}')
    if specials:
        parts.append('彩:' + '、'.join(specials))
    if result.maxed:
        parts.append('成品猫')
    return ' | '.join(parts)


def render_text(result) -> str:
    """把评分结果渲染成纯文本评分卡（Markdown 报告与终端用）。"""
    info = result.cat_info
    meta = ' · '.join(str(x) for x in (
        info.get('rarity'), info.get('faction'),
        '/'.join(info.get('types') or []) or None,
        info.get('position'), '固定天赋猫' if info.get('fixed') else None,
    ) if x)
    lines = [f"指挥喵：{result.cat or '（未识别猫名）'}" + (f"（{meta}）" if meta else '')]
    if info.get('note'):
        lines.append(f"  攻略点评：{info['note']}")

    lines.append('天赋：')
    for t in result.talents:
        level = '' if t.kind == 'special' else f' Lv{t.level}'
        flag = '  ⚠️由效果文字推断' if t.inferred else ''
        warn = '' if t.kind != 'unknown' else '  ⚠️未匹配到'
        lines.append(f'  - {t.name}{level}{warn}{flag}')
    if result.maxed:
        lines.append('  ⚠️ 检测到 Lv3 天赋：这是已点过点的成品猫，'
                     'x+y 档位判定按初始猫口径，仅供参考')

    for key in result.primary:
        r = result.rubrics[key]
        star = '★' if key == result.primary[0] else '☆'
        lines.append(f'{star} {r.label} —— {r.tier}（参考分 {r.score100}/100）')
        if key == 'torpedo':
            lines.append(f'    加权点：{r.score100 / 11:.1f}（命中：{"、".join(r.y_hits) or "无"}）')
        else:
            lines.append(f'    x + y = {r.x} + {r.y}')
            lines.append(f'    X（彩）：{"、".join(r.x_hits) or "无"}')
            lines.append(f'    Y（有用普通）：{"、".join(r.y_hits) or "无"}')
        for note in r.notes:
            lines.append(f'    - {note}')
        lines.append(f'    依据：{r.source}')

    if result.points_spent in RESET_COST:
        lines.append(f'洗猫：已点 {result.points_spent} 点，重置约需 '
                     f'{RESET_COST[result.points_spent]} 物资'
                     '（重置只回到初始天赋，后天天赋会全部消失）')
    return '\n'.join(lines)


def render_html(results, title: str = '指挥喵天赋评分报告', generated_at: str = '') -> str:
    """把若干 (来源名, ScoreResult) 渲染成自包含 HTML。

    Args:
        results: ``[(来源名, ScoreResult), ...]``。
        title: 报告标题。
        generated_at: 生成时间文本。

    Returns:
        str: 完整 HTML 文档。
    """
    cards = ''.join(_cat_card(name, result) for name, result in results)

    rows = []
    for name, result in results:
        key = result.primary[0] if result.primary else None
        rubric = result.rubrics.get(key)
        if rubric is None:
            continue
        rows.append(
            f'<tr><td class="name">{_e(result.cat or "未知")}</td>'
            f'<td>{_e(name)}</td>'
            f'<td>{_e(rubric.label)}</td>'
            f'<td><span class="tier {_tier_theme(rubric.tier)}">{_e(rubric.tier)}</span></td>'
            f'<td><span class="bar"><i style="width:{int(rubric.score100)}%"></i></span>'
            f'{int(rubric.score100)}/100</td></tr>')

    summary = ''
    if rows:
        summary = ('<table class="mini"><thead><tr><th>指挥喵</th><th>来源</th>'
                   '<th>主口径</th><th>档位</th><th>参考分</th></tr></thead><tbody>'
                   + ''.join(rows) + '</tbody></table>')

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title>
<style>{STYLE}</style>
</head>
<body>
<div class="wrap">
  <div class="page-head">
    <h1>{_e(title)}</h1>
    <div class="sub">共 {len(results)} 只 · {_e(generated_at)}</div>
  </div>
  <div class="pills">
    <span>口径：<b>28法则执行篇 / 详细上手攻略</b></span>
    <span>x = 彩天赋点数 · y = 有用普通点数（2 级计 2 点）</span>
    <span>非游戏官方数值</span>
  </div>
  {summary}
  {cards}
  <div class="foot">
    评分口径来自公开攻略：《照做就行——28法则下的碧蓝航线攻略执行篇》(坐看云起)、
    《指挥喵详细上手攻略》(智代智代)；天赋数值与初始池来自碧蓝航线 WIKI《指挥喵》。
    两篇作者在「水雷魂算不算彩」等问题上存在分歧，报告中按口径分别标注，不代表唯一正确答案。<br>
    x+y 档位判定面向<b>初始天赋</b>；已点过点的成品猫请重点看彩天赋数量与各口径得分。
  </div>
</div>
</body>
</html>
"""
