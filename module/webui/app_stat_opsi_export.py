"""WebUI 短猫收益刷新和大世界统计导出。"""

from module.webui.app_dependencies import (
    Path,
    current_time,
    datetime,
    logger,
    put_button,
    put_html,
    t,
    toast,
    use_scope,
)

from module.webui.app_helpers import (
    build_muted_notice,
    build_simple_table,
    build_title_block,
)


from module.webui.app_types import WebUIMixinBase


class OpsiExportMixin(WebUIMixinBase):
    """WebUI 短猫收益刷新和大世界统计导出。"""

    def _refresh_meowofficer_farming(self):
        from module.statistics.azurstats import AzurStats

        AzurStats.get_meowofficer_farming()
        self._render_meowofficer_farming()

    def _render_meowofficer_farming(self):
        from module.statistics.azurstats import AzurStats

        with use_scope("meow_loot_scope", clear=True):
            all_data = AzurStats.load_meowofficer_farming()
            meow_rows = []
            for row in all_data:
                if row[2] > 0:
                    meow_row = [
                        int(row[0]),
                        datetime.fromtimestamp(row[1]).strftime("%Y-%m-%d %H:%M:%S"),
                        int(row[2]),
                    ] + list(row[3:])

                    meow_rows.append(meow_row)

            put_html(
                build_title_block(
                    t("Gui.Stat.MeowLootTitle"),
                    margin_top=20,
                    margin_bottom=8,
                )
            )
            if meow_rows:
                put_html(
                    build_simple_table(AzurStats.meowofficer_farming_labels, meow_rows)
                )
            else:
                put_html(build_muted_notice(t("Gui.Stat.NoMeowDataNotice")))

            put_button(
                t("Gui.Stat.Refresh"),
                onclick=self._refresh_meowofficer_farming,
                color="off",
            )

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
