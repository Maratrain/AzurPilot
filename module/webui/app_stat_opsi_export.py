"""WebUI 耄耋相接收获统计视图。"""

from base64 import b64encode
from functools import lru_cache

from module.webui.app_dependencies import (
    Path,
    close_popup,
    current_time,
    logger,
    popup,
    put_buttons,
    put_html,
    put_row,
    t,
    toast,
    use_scope,
)

from module.webui.app_helpers import build_title_block


from module.webui.app_types import WebUIMixinBase


# (统计分类键, i18n 键后缀, 图标文件名, 主题色)
MEOW_LOOT_ITEMS = (
    ("Plate", "Plate", "meow_plate.png", "#BA7517"),
    ("GearDesignPlanT5", "GearDesign", "meow_gear_design.png", "#7F77DD"),
    ("OrdnanceTestingReportT4", "Ordnance", "meow_ordnance.png", "#C9A227"),
    ("CoordinateObscure", "Obscure", "meow_obscure.png", "#378ADD"),
    ("CoordinateAbyssal", "Abyssal", "meow_abyssal.png", "#E24B4A"),
    ("CatT3", "Cat", "meow_cat.png", "#D4537E"),
)
MEOW_LOOT_HAZARD_LEVELS = (3, 5)
_MEOW_LOOT_ICON_ROOT = Path(__file__).resolve().parents[2] / "assets" / "gui" / "icon"


@lru_cache(maxsize=32)
def _meow_loot_icon_data_uri(icon_name: str):
    """把物品图标读成 data URI；文件缺失时返回 None。

    必须内联而不是给相对路径：远控（P2P）下 `<img src="static/...">` 会静默 404
    （app_home.py 里记过同一个坑）。图标是 48×48 PNG，六张合计约 35KB。
    """
    try:
        raw = (_MEOW_LOOT_ICON_ROOT / icon_name).read_bytes()
    except OSError:
        return None
    return f"data:image/png;base64,{b64encode(raw).decode('ascii')}"


class OpsiExportMixin(WebUIMixinBase):
    """WebUI 耄耋相接收获统计视图（物品卡片 + 按侵蚀等级拆分的明细表）。

    版式全在 ``assets/gui/css/meow-loot-alas.css`` 里（class 前缀 ``meow-loot-``）：
    这里只拼结构、不写内联样式 —— 内联写不了媒体查询，也压不过主题的
    ``#pywebio-scope-opsi_stats th/td { … !important }``。
    """

    def _render_meowofficer_farming(self):
        with use_scope("meow_loot_scope", clear=True):
            self._render_monthly_meow_loot()

    def _render_monthly_meow_loot(self):
        """渲染「本月/历史耄耋相接收获」：物品卡片 + 侵蚀等级明细表。"""
        from module.statistics.azurstats import AzurStats

        view_month = getattr(self, "_meow_loot_month", None)
        now = current_time()
        if view_month is None:
            year, month = now.year, now.month
            title = t("Gui.Stat.MeowLootTitleCurrent")
        else:
            year, month = view_month
            title = t("Gui.Stat.MeowLootTitleHistory", month=f"{year:04d}-{month:02d}")

        loot_totals = AzurStats.get_meow_loot_monthly_totals(year=year, month=month)
        rounds = self._load_meow_loot_rounds(year, month)

        # 月份切换按钮紧跟在标题右侧（标题列自适应内容宽度，按钮列吃掉剩余空间，
        # 因此按钮不会被推到最右边）；已经在本月时不再显示「回到本月」。
        buttons = [
            {
                "label": t("Gui.Stat.MeowLootViewHistory"),
                "value": "history",
                "color": "secondary",
            }
        ]
        if view_month is not None:
            buttons.append(
                {
                    "label": t("Gui.Stat.MeowLootBackToCurrent"),
                    "value": "current",
                    "color": "primary",
                }
            )
        put_row(
            [
                put_html(
                    build_title_block(
                        title,
                        margin_top=0,
                        margin_bottom=0,
                    )
                ),
                put_buttons(
                    buttons,
                    onclick=self._on_meow_loot_month_click,
                    small=True,
                ),
            ],
            size="auto 1fr",
        ).style("align-items:center; gap:10px; margin-top:20px; margin-bottom:8px")
        put_html(self._build_meow_loot_summary_html(loot_totals))
        put_html(self._build_meow_loot_table_html(loot_totals, rounds))

    def _load_meow_loot_rounds(self, year, month):
        """读取各侵蚀等级当月的有效战斗轮次，失败时返回空表。"""
        from module.statistics.cl1_database import db as cl1_db

        instance_name = getattr(self, "alas_name", None)
        if not instance_name:
            from module.config.utils import alas_instance

            all_instances = alas_instance()
            instance_name = all_instances[0] if all_instances else "default"

        rounds = {}
        for hazard_level in MEOW_LOOT_HAZARD_LEVELS:
            try:
                meow_data = cl1_db.get_meow_stats(
                    instance_name, year, month, hazard_level=hazard_level
                )
                value = round(float(meow_data.get("effective_rounds", 0) or 0), 1)
                # 整数轮次不带小数点，0.0 显示成 0
                rounds[hazard_level] = (
                    int(value) if abs(value - int(value)) < 1e-6 else value
                )
            except Exception:
                rounds[hazard_level] = 0
        return rounds

    @staticmethod
    def _meow_loot_icon_html(icon_name, color, kind):
        """物品图标；文件缺失时退回同尺寸色点（尺寸见 CSS 的 --card / --row）。"""
        classes = f"meow-loot-icon meow-loot-icon--{kind}"
        data_uri = _meow_loot_icon_data_uri(icon_name) if icon_name else None
        if data_uri:
            inner = f'<img src="{data_uri}" alt="">'
        else:
            inner = f'<span class="meow-loot-dot" style="background: {color};"></span>'
        return f'<div class="{classes}" style="background: {color}1a;">{inner}</div>'

    def _build_meow_loot_summary_html(self, loot_totals):
        """物品卡片区：每张卡片是一个物品在所选月份的总数（跨侵蚀等级）。"""
        html = '<div class="meow-loot-grid">'
        for key, i18n_suffix, icon_name, color in MEOW_LOOT_ITEMS:
            total = sum(
                int((loot_totals.get(hazard_level) or {}).get(key, 0) or 0)
                for hazard_level in MEOW_LOOT_HAZARD_LEVELS
            )
            total_str = f"+{total:,}" if total > 0 else "0"
            html += (
                '<div class="meow-loot-card">'
                f"{self._meow_loot_icon_html(icon_name, color, 'card')}"
                '<div class="meow-loot-card-body">'
                f'<span class="meow-loot-card-name">'
                f'{t(f"Gui.Stat.MeowLootItem{i18n_suffix}")}</span>'
                f'<span class="meow-loot-card-value">{total_str}</span>'
                "</div></div>"
            )
        return html + "</div>"

    def _build_meow_loot_table_html(self, loot_totals, rounds):
        """明细表：行 = 物品，列 = 各侵蚀等级 + 合计，末行是战斗轮次。"""
        head_cells = (
            f'<th class="meow-loot-cell-name">'
            f'{t("Gui.Stat.MeowLootHeaderItem")}</th>'
            + "".join(
                f'<th class="meow-loot-cell-value">'
                f'{t("Gui.Stat.MeowLootHazardColumn", level=hazard_level)}</th>'
                for hazard_level in MEOW_LOOT_HAZARD_LEVELS
            )
            + f'<th class="meow-loot-cell-value">'
            f'{t("Gui.Stat.MeowLootHeaderTotal")}</th>'
        )
        html = (
            '<div class="meow-loot-table-wrap">'
            '<table class="meow-loot-table">'
            '<colgroup><col class="meow-loot-col-name">'
            '<col class="meow-loot-col-value"><col class="meow-loot-col-value">'
            '<col class="meow-loot-col-value"></colgroup>'
            f"<thead><tr>{head_cells}</tr></thead><tbody>"
        )
        for key, i18n_suffix, icon_name, color in MEOW_LOOT_ITEMS:
            values = [
                int((loot_totals.get(hazard_level) or {}).get(key, 0) or 0)
                for hazard_level in MEOW_LOOT_HAZARD_LEVELS
            ]
            value_cells = "".join(
                f'<td class="meow-loot-cell-value">{value:,}</td>' for value in values
            )
            html += (
                f'<tr><td class="meow-loot-cell-name">'
                f'<span class="meow-loot-name-cell">'
                f"{self._meow_loot_icon_html(icon_name, color, 'row')}"
                f'{t(f"Gui.Stat.MeowLootItem{i18n_suffix}")}'
                f"</span></td>{value_cells}"
                f'<td class="meow-loot-cell-value">{sum(values):,}</td></tr>'
            )

        # 战斗轮次不是掉落物，单独放在末行、用上边框与主数据分开
        round_values = [
            rounds.get(hazard_level, 0) for hazard_level in MEOW_LOOT_HAZARD_LEVELS
        ]
        round_cells = "".join(
            f'<td class="meow-loot-cell-value">{value}</td>' for value in round_values
        )
        html += (
            f'<tr class="meow-loot-rounds">'
            f'<td class="meow-loot-cell-name">{t("Gui.Stat.BattleRounds")}</td>'
            f"{round_cells}"
            f'<td class="meow-loot-cell-value">{sum(round_values)}</td></tr>'
        )
        return html + "</tbody></table></div>"

    def _on_meow_loot_month_click(self, value):
        """月份切换按钮回调：history 打开历史月份选择器，current 回到本月。"""
        if value == "history":
            self._show_meow_loot_month_picker()
        else:
            self._reset_meow_loot_month()

    def _show_meow_loot_month_picker(self):
        """弹出历史月份选择器。"""
        from module.statistics.azurstats import AzurStats

        now = current_time()
        months = AzurStats.get_meow_loot_available_months()
        buttons = [
            {
                "label": t(
                    "Gui.Stat.MeowLootCurrentMonthOption",
                    month=f"{now.year:04d}-{now.month:02d}",
                ),
                "value": None,
                "color": "primary",
            }
        ]
        buttons += [
            {"label": f"{y:04d}-{m:02d}", "value": (y, m), "color": "secondary"}
            for y, m in months
            if (y, m) != (now.year, now.month)
        ]
        if len(buttons) == 1:
            toast(t("Gui.Stat.MeowLootNoHistoryMonth"))
            return

        with popup(t("Gui.Stat.MeowLootPickMonthTitle")):
            put_buttons(buttons, onclick=lambda v: self._set_meow_loot_month(v))

    def _set_meow_loot_month(self, value):
        """设置要查看的月份并重绘收获区块。value 为 None 表示本月。"""
        close_popup()
        self._meow_loot_month = value
        self._render_meowofficer_farming()

    def _reset_meow_loot_month(self):
        """回到本月视图。"""
        self._meow_loot_month = None
        self._render_meowofficer_farming()
    def _export_opsi_csv(self, save_to_desktop: bool = True):
        import io

        try:
            from module.statistics.opsi_month import (
                get_opsi_stats,
                compute_monthly_cl1_akashi_ap,
            )
        except Exception as e:
            toast(t("Gui.Stat.ExportModuleLoadFailed", e=e), color="error")
            return

        instance_name_local: str | None = getattr(self, "alas_name", None)
        try:
            s_local = get_opsi_stats(instance_name=instance_name_local).summary() or {}
        except Exception:
            s_local = {}

        month_local = s_local.get("month") or current_time().strftime("%Y-%m")
        total_battles_local = int(s_local.get("total_battles") or 0)
        total_rounds_local = int(
            s_local.get("total_rounds") or ((total_battles_local + 1) // 2)
        )
        ap_spent_local = int(s_local.get("ap_spent") or (total_rounds_local * 5))
        akashi_count_local = int(
            s_local.get("akashi_encounters") or s_local.get("akashi_count") or 0
        )

        if "akashi_percent" in s_local:
            try:
                akashi_percent_local = float(s_local.get("akashi_percent") or 0)
            except Exception:
                akashi_percent_local = 0.0
        elif total_rounds_local > 0:
            akashi_percent_local = (akashi_count_local / total_rounds_local) * 100
        else:
            akashi_percent_local = 0.0

        try:
            purchased_local = (
                compute_monthly_cl1_akashi_ap(instance_name=instance_name_local) or 0
            )
        except Exception:
            purchased_local = 0

        if akashi_count_local > 0:
            try:
                avg_ap_local = int(float(purchased_local) / akashi_count_local + 0.5)
            except Exception:
                avg_ap_local = "-"
        else:
            avg_ap_local = "-"

        try:
            net_ap_local = int((purchased_local or 0) - ap_spent_local)
        except Exception:
            net_ap_local = "-"

        if isinstance(net_ap_local, (int, float)) and ap_spent_local:
            try:
                eff_local = (net_ap_local / ap_spent_local) * 100
            except Exception:
                eff_local = "-"
        else:
            eff_local = "-"

        labels_local = [
            t("Gui.Stat.Month"),
            t("Gui.Stat.BattleCount"),
            t("Gui.Stat.BattleRounds"),
            t("Gui.Stat.SortieCost"),
            t("Gui.Stat.AkashiEncounters"),
            t("Gui.Stat.CsvHeaderAkashiRate"),
            t("Gui.Stat.AverageAP"),
            t("Gui.Stat.NetAP"),
            t("Gui.Stat.CsvHeaderLoopEfficiency"),
            t("Gui.Stat.CsvHeaderMonthlyPurchasedAP"),
        ]
        values_local = [
            month_local,
            total_battles_local,
            total_rounds_local,
            ap_spent_local,
            akashi_count_local,
            f"{akashi_percent_local:.2f}"
            if isinstance(akashi_percent_local, (int, float))
            else akashi_percent_local,
            avg_ap_local,
            net_ap_local,
            f"{eff_local:.2f}" if isinstance(eff_local, (int, float)) else eff_local,
            purchased_local,
        ]

        output = io.StringIO()
        output.write(",".join(labels_local) + "\n")

        def _escape(cell):
            s = str(cell)
            if "," in s or '"' in s or "\n" in s:
                s = '"' + s.replace('"', '""') + '"'
            return s

        output.write(",".join([_escape(c) for c in values_local]) + "\n")
        csv_bytes = output.getvalue().encode("utf-8-sig")

        filename_local = t("Gui.Stat.CsvFilenameTemplate", month=month_local)

        if save_to_desktop:
            try:
                desktop_local = Path.home() / "Desktop"
                desktop_local.mkdir(parents=True, exist_ok=True)
                fpath = desktop_local / filename_local
                with open(fpath, "wb") as _f:
                    _f.write(csv_bytes)
                toast(
                    t("Gui.Stat.SavedToDesktop", path=fpath),
                    color="success",
                )
            except Exception as e:
                logger.exception(e)
                toast(t("Gui.Stat.SaveDesktopFailed", e=e), color="error")
