"""WebUI 全资源趋势图视图。"""

from module.webui.app_dependencies import (
    datetime,
    json,
    put_button,
    put_html,
    put_text,
    run_js,
    t,
    use_scope,
)

from module.webui.app_helpers import (
    build_muted_notice,
    read_webapp_template,
)


from module.webui.app_types import WebUIMixinBase


class ResourceStatisticsMixin(WebUIMixinBase):
    """WebUI 全资源趋势图视图。"""

    def _render_resource_chart(self):
        self.cleanup_client_resources("__resourceChartCleanups")
        try:
            timeline = self._load_resource_timeline()
        except Exception as e:
            self._show_resource_load_error(e)
            return

        if not timeline:
            self._show_resource_no_data()
            return

        labels, series_map = self._build_resource_series(timeline)
        if not labels:
            self._show_resource_no_valid_data()
            return

        html, js_code = self._build_resource_chart_content(labels, series_map)
        self._output_resource_chart(html, js_code)

    def _load_resource_timeline(self):
        from module.statistics.opsi_month import get_resource_timeline

        instance_name = getattr(self, "alas_name", None)
        if not instance_name:
            from module.config.utils import alas_instance

            all_instances = alas_instance()
            instance_name = all_instances[0] if all_instances else None

        return get_resource_timeline(instance_name=instance_name, limit=500)

    def _build_resource_series(self, timeline):
        labels = []
        series_map = {
            "Oil": {"name": t("Gui.Dashboard.Oil"), "color": "#ff8a65", "data": []},
            "Coin": {"name": t("Gui.Dashboard.Coin"), "color": "#ffd54f", "data": []},
            "Gem": {"name": t("Gui.Dashboard.Gem"), "color": "#ef5350", "data": []},
            "Pt": {"name": t("Gui.Dashboard.Pt"), "color": "#4fc3f7", "data": []},
            "Cube": {"name": t("Gui.Dashboard.Cube"), "color": "#4dd0e1", "data": []},
            "Core": {"name": t("Gui.Dashboard.Core"), "color": "#b0bec5", "data": []},
            "Medal": {"name": t("Gui.Dashboard.Medal"), "color": "#ffd740", "data": []},
            "Merit": {"name": t("Gui.Dashboard.Merit"), "color": "#ffab00", "data": []},
            "GuildCoin": {
                "name": t("Gui.Dashboard.GuildCoin"),
                "color": "#a1887f",
                "data": [],
            },
            "ActionPoint": {
                "name": t("Gui.Dashboard.ActionPoint"),
                "color": "#64b5f6",
                "data": [],
            },
            "YellowCoin": {
                "name": t("Gui.Dashboard.YellowCoin"),
                "color": "#ffa726",
                "data": [],
            },
            "PurpleCoin": {
                "name": t("Gui.Dashboard.PurpleCoin"),
                "color": "#ce93d8",
                "data": [],
            },
        }

        key_map = {
            "guildcoin": "guild_coin",
            "actionpoint": "action_point",
            "yellowcoin": "yellow_coin",
            "purplecoin": "purple_coin",
        }
        for pt in timeline:
            ts_raw = pt.get("ts", "")
            try:
                dt = datetime.fromisoformat(ts_raw)
            except Exception:
                continue
            labels.append(dt.strftime("%m-%d %H:%M"))
            for key in series_map:
                raw_val = pt.get(key.lower())
                if raw_val is None:
                    col = key_map.get(key.lower())
                    if col:
                        raw_val = pt.get(col)
                    else:
                        raw_val = pt.get(key)
                if raw_val is not None:
                    try:
                        series_map[key]["data"].append(int(raw_val))
                    except TypeError, ValueError:
                        series_map[key]["data"].append(None)
                else:
                    series_map[key]["data"].append(None)

        # 前向填充：用上一个有效值填补 None，避免折线图断点
        for key in series_map:
            prev = None
            filled = []
            for v in series_map[key]["data"]:
                if v is not None:
                    prev = v
                filled.append(prev)
            series_map[key]["data"] = filled

        return labels, series_map

    def _build_resource_chart_content(self, labels, series_map):
        legend_html = ""
        stats_html = ""
        series_data = []
        series_idx = 0
        for key, meta in series_map.items():
            valid_data = [v for v in meta["data"] if v is not None]
            if valid_data:
                cur = valid_data[-1]
                change = valid_data[-1] - valid_data[0] if len(valid_data) >= 2 else 0
                change_color = "#ef5350" if change >= 0 else "#26a69a"
                change_sign = "+" if change >= 0 else ""
                stats_html += (
                    f'<span style="white-space:nowrap;">{meta["name"]}: '
                    f'<b style="color:{meta["color"]}">{cur:,}</b> '
                    f'<span style="color:{change_color}">({change_sign}{change:,})</span></span>'
                )
            else:
                stats_html += f'<span style="white-space:nowrap;opacity:0.5;">{meta["name"]}: -</span>'
            legend_html += (
                f'<span class="rc-legend-item" data-series="{series_idx}" '
                f'style="display:flex;align-items:center;gap:4px;cursor:pointer;opacity:1;">'
                f'<span style="width:12px;height:3px;background:{meta["color"]};border-radius:1px;"></span>'
                f"{meta['name']}</span>"
            )
            series_data.append(
                {
                    "key": key,
                    "name": meta["name"],
                    "color": meta["color"],
                    "data": meta["data"],
                }
            )
            series_idx += 1

        chart_id = f"rc_{id(self)}"

        html_tpl = read_webapp_template("resource_chart.html")
        html = html_tpl.format(
            chart_id=chart_id,
            title=t("Gui.Stat.ResourceChartTitle"),
            stats_html=stats_html,
            legend_html=legend_html,
        )

        js_tpl = read_webapp_template("resource_chart.js")
        js_code = (
            js_tpl.replace("__LABELS__", json.dumps(labels, ensure_ascii=False))
            .replace("__SERIES_DATA__", json.dumps(series_data, ensure_ascii=False))
            .replace("__CHART_ID__", chart_id)
            .replace("__CHART_TITLE__", t("Gui.Stat.ResourceChartTitle"))
        )
        return html, js_code

    def _show_resource_load_error(self, error):
        with use_scope("resource_chart", clear=True):
            put_text(t("Gui.Stat.LoadResourceDataFailed", e=error))

    def _show_resource_no_data(self):
        with use_scope("resource_chart", clear=True):
            put_html(build_muted_notice(t("Gui.Stat.NoResourceData")))
            put_button(
                t("Gui.Stat.Refresh"),
                onclick=self._render_resource_chart,
                color="off",
            )

    def _show_resource_no_valid_data(self):
        with use_scope("resource_chart", clear=True):
            put_html(build_muted_notice(t("Gui.Stat.NoValidResourceData")))

    @staticmethod
    def _output_resource_chart(html, js_code):
        with use_scope("resource_chart", clear=True):
            put_html(html)
            run_js(js_code)
