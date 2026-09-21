"""指挥喵静态资料（自动生成，请勿手工编辑）。

数据来源：碧蓝航线 WIKI《指挥喵》页面的初始天赋池 + 两篇公开攻略的人工校对层。
生成脚本：catscore/tools/gen_azurpilot_data.py

- types 用于挑选评分口径（潜艇/驱逐/航母/战列/巡洋）
- position/score/note 来自《指挥喵详细上手攻略》(智代智代)
- 阵营只填确定的；部分 SR/R 猫攻略未提及阵营，留空
"""

from typing import TypedDict


class CatInfo(TypedDict, total=False):
    """指挥喵资料。"""

    rarity: str                 # SSR / SR / R
    faction: str                # 白鹰 / 皇家 / 重樱 / 铁血 / 飓风
    types: tuple[str, ...]      # 舰种标签
    position: str               # 司令 / 参谋
    score: float                # 详细上手攻略的主观评分（满分 10）
    note: str                   # 攻略点评
    fixed: bool                 # 固定天赋指挥喵


CATS: dict[str, CatInfo] = {
    "乔治喵": {"rarity": "R", "types": ("战列",)},
    "伯克喵": {"rarity": "SSR", "faction": "白鹰", "types": ("驱逐",), "position": "司令", "score": 6.5, "note": "主线较强司令猫；技能偏功能(三驱移动力+1/15%鱼雷打击/减伏击)，周回与大世界无用"},
    "克雷喵": {"rarity": "SSR", "faction": "铁血", "types": ("潜艇",), "position": "司令", "note": "指定潜艇司令，狩猎范围+1；初始池小、最好毕业"},
    "埃弗喵": {"faction": "飓风", "types": ("巡洋",), "note": "固定天赋猫(风帆)；满级自带 侵略如火+既定的命运+碧海亲和性·精锐+飓风之眼", "fixed": True},
    "埃里喵": {"rarity": "R", "types": ("驱逐",)},
    "基德喵": {"types": ("驱逐",), "note": "固定天赋猫；满级自带 侵略如火+既定的命运+炮火覆盖·V+鹰眼·先锋", "fixed": True},
    "奥古喵": {"rarity": "SSR", "faction": "白鹰", "types": ("战列",), "position": "司令", "score": 9, "note": "目前最好的战列司令猫，亦可作参谋；技能不限司令参谋位"},
    "威廉喵": {"rarity": "SR", "types": ("巡洋",), "position": "参谋", "note": "唯一的巡洋参谋"},
    "小吉丸": {"rarity": "SR", "faction": "重樱", "types": ("驱逐",), "position": "参谋", "note": "最优秀的雷暴参谋，唯一重樱驱逐参谋"},
    "小竹丸": {"rarity": "SSR", "faction": "重樱", "types": ("巡洋",), "position": "司令", "score": 6, "note": "普通巡洋司令猫；初始池有见敌/既定/水雷魂，可做雷暴猫"},
    "小胜丸": {"rarity": "R", "types": ("战列",)},
    "帕特喵": {"rarity": "SR", "faction": "皇家", "types": ("战列",), "position": "参谋", "note": "普通战列参谋，一手降伏击；可做皇家猫"},
    "庞德喵": {"rarity": "SSR", "faction": "皇家", "types": ("战列",), "position": "司令", "score": 5, "note": "几乎只在皇家队生效；金猫里初始最差之一"},
    "弗里喵": {"rarity": "SR", "types": ("战列",), "position": "参谋", "note": "普通战列参谋，紫皮难上岗"},
    "德雷喵": {"types": ("驱逐",), "note": "固定天赋猫；满级自带 侵略如火+既定的命运+炮火覆盖·M+航海长·小型舰", "fixed": True},
    "朝丸": {"rarity": "R", "types": ("巡洋",)},
    "林德喵": {"rarity": "SSR", "faction": "铁血", "types": ("战列",), "position": "参谋", "note": "优秀道中战列参谋，初始池有火/山/一发"},
    "查理喵": {"rarity": "R", "types": ("巡洋",)},
    "次郎丸": {"rarity": "SR", "faction": "重樱", "types": ("航母",), "position": "参谋", "note": "常规航母参谋，特殊技能地图打击"},
    "毗沙丸": {"rarity": "SSR", "faction": "重樱", "types": ("航母",), "position": "司令", "note": "唯一能大幅影响阵容的猫；100级单航母后排时最强，且必带"},
    "汉克喵": {"rarity": "SR", "faction": "白鹰", "types": ("驱逐",), "position": "司令", "note": "最强紫猫、指定低耗猫；开幕弹幕推波用"},
    "海耶喵": {"rarity": "R", "types": ("巡洋",)},
    "约翰喵": {"rarity": "SSR", "faction": "皇家", "types": ("战列",), "position": "参谋", "score": 8, "note": "普通战列参谋，金猫底子好；初始池有一发/山/见敌"},
    "罗伯喵": {"faction": "飓风", "types": ("驱逐",), "note": "固定天赋猫(风帆)；满级自带 不动如山+其徐如林+既定的命运+航海长·小型舰", "fixed": True},
    "莫德喵": {"rarity": "SR", "faction": "皇家", "types": ("航母",), "position": "参谋", "note": "常规航母参谋；初始池混入驱逐技能，挑初始较难"},
    "莫赫喵": {"rarity": "SR", "types": ("潜艇",), "position": "参谋", "note": "指定潜艇参谋，狩猎范围+1，比赫尔好太多"},
    "莫里喵": {"rarity": "SR", "faction": "白鹰", "types": ("航母",), "position": "参谋", "note": "常规航母参谋；初始池有山，白鹰指挥对白鹰航母很舒服"},
    "蒂奇喵": {"faction": "飓风", "types": ("航母",), "note": "固定天赋猫(风帆)；满级自带 侵略如火+既定的命运+蓝天亲和性·精锐+飓风之眼", "fixed": True},
    "谢尔喵": {"rarity": "R", "types": ("航母",)},
    "贝尔喵": {"rarity": "R", "types": ("巡洋",)},
    "贝拉喵": {"faction": "飓风", "types": ("驱逐",), "note": "固定天赋猫(风帆)；满级自带 不动如山+小小的奇迹+轮机长·小型舰", "fixed": True},
    "赫尔喵": {"rarity": "SR", "types": ("潜艇",), "note": "凑数潜艇猫，没有特殊技能；只在天赋明显更好时用"},
    "邦尼喵": {"faction": "飓风", "types": ("驱逐",), "note": "固定天赋猫(风帆)；满级自带 不动如山+小小的奇迹+轮机长·先锋", "fixed": True},
    "鲁普喵": {"rarity": "SR", "types": ("驱逐",), "position": "参谋", "note": "较弱的驱逐参谋"},
}

