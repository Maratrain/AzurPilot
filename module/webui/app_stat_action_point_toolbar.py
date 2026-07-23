"""WebUI 体力趋势图的视图切换工具栏。"""

from module.webui.app_dependencies import (
    put_button,
    put_buttons,
    put_html,
    put_row,
    t,
)


from module.webui.app_types import WebUIMixinBase


class ActionPointToolbarMixin(WebUIMixinBase):
    """WebUI 体力趋势图的视图切换工具栏。"""

    def _render_ap_chart_toolbar(self, current_view, chart_id):
        def _switch_view(v):
            self._ap_chart_view = v
            self._render_ap_chart()

        md3_colors = {
            "toolbar_border": "rgba(103, 80, 164, .18)",
            "toolbar_bg": "rgba(255, 251, 254, .96)",
            "toolbar_shadow": "0 1px 3px rgba(30, 27, 32, .10)",
            "segment_border": "rgba(121, 116, 126, .42)",
            "segment_divider": "rgba(121, 116, 126, .32)",
            "segment_outline": "rgba(121, 116, 126, .22)",
            "segment_bg": "#fffbfe",
            "text": "#49454f",
            "label": "#625b71",
            "hover": "rgba(103, 80, 164, .08)",
            "selected_bg": "#eaddff",
            "selected_text": "#21005d",
            "selected_outline": "rgba(103, 80, 164, .18)",
            "refresh_text": "#6750a4",
        }
        if self.theme == "dark":
            md3_colors.update(
                {
                    "toolbar_border": "rgba(122, 119, 187, .30)",
                    "toolbar_bg": "rgba(47, 49, 54, .96)",
                    "toolbar_shadow": "0 1px 3px rgba(0, 0, 0, .38)",
                    "segment_border": "rgba(147, 143, 153, .50)",
                    "segment_divider": "rgba(147, 143, 153, .34)",
                    "segment_outline": "rgba(147, 143, 153, .28)",
                    "segment_bg": "#2f3136",
                    "text": "#dfdcfb",
                    "label": "#c9d1d9",
                    "hover": "rgba(122, 119, 187, .18)",
                    "selected_bg": "#3e3b6a",
                    "selected_text": "#dfdcfb",
                    "selected_outline": "rgba(122, 119, 187, .46)",
                    "refresh_text": "#dfdcfb",
                }
            )
        elif self.theme == "advanced_material":
            md3_colors.update(
                {
                    "toolbar_border": "rgba(255, 255, 255, .46)",
                    "toolbar_bg": "rgba(255, 255, 255, .72)",
                    "toolbar_shadow": "0 2px 8px rgba(0, 0, 0, .06)",
                    "segment_border": "rgba(0, 0, 0, .14)",
                    "segment_divider": "rgba(0, 0, 0, .10)",
                    "segment_outline": "rgba(0, 0, 0, .08)",
                    "segment_bg": "rgba(255, 255, 255, .62)",
                    "text": "#1d1d1f",
                    "label": "#6e6e73",
                    "hover": "rgba(0, 122, 255, .08)",
                    "selected_bg": "rgba(0, 122, 255, .14)",
                    "selected_text": "#007aff",
                    "selected_outline": "rgba(0, 122, 255, .26)",
                    "refresh_text": "#007aff",
                }
            )
        elif self.theme == "dark_advanced_material":
            md3_colors.update(
                {
                    "toolbar_border": "rgba(96, 165, 250, .28)",
                    "toolbar_bg": "rgba(15, 23, 42, .78)",
                    "toolbar_shadow": "0 8px 20px rgba(0, 0, 0, .32)",
                    "segment_border": "rgba(148, 163, 184, .34)",
                    "segment_divider": "rgba(148, 163, 184, .24)",
                    "segment_outline": "rgba(148, 163, 184, .16)",
                    "segment_bg": "rgba(15, 23, 42, .72)",
                    "text": "#e5edf8",
                    "label": "#aab8cb",
                    "hover": "rgba(37, 99, 235, .18)",
                    "selected_bg": "rgba(30, 64, 175, .52)",
                    "selected_text": "#dbeafe",
                    "selected_outline": "rgba(96, 165, 250, .42)",
                    "refresh_text": "#93c5fd",
                }
            )
        put_html(f"""
        <style>
        [style*="--ap-chart-md3-toolbar-{chart_id}"] {{
            margin-top: 12px !important;
            padding: 10px 12px !important;
            border: 1px solid {md3_colors["toolbar_border"]} !important;
            border-radius: 16px !important;
            background: {md3_colors["toolbar_bg"]} !important;
            box-shadow: {md3_colors["toolbar_shadow"]} !important;
            align-items: center !important;
            column-gap: 10px !important;
        }}
        [style*="--ap-chart-md3-segment-{chart_id}"] {{
            display: inline-flex !important;
            width: auto !important;
            max-width: 100% !important;
            margin: 0 !important;
        }}
        [style*="--ap-chart-md3-segment-{chart_id}"] .btn-group {{
            display: inline-flex !important;
            flex-wrap: nowrap !important;
            width: auto !important;
            overflow: hidden !important;
            border: 1px solid {md3_colors["segment_border"]} !important;
            border-radius: 12px !important;
            background: {md3_colors["segment_bg"]} !important;
            box-shadow: none !important;
        }}
        [style*="--ap-chart-md3-segment-{chart_id}"] .btn {{
            min-width: 112px !important;
            margin: 0 !important;
            padding: 7px 16px !important;
            border: 0 !important;
            border-left: 1px solid {md3_colors["segment_divider"]} !important;
            border-radius: 0 !important;
            background: transparent !important;
            color: {md3_colors["text"]} !important;
            box-shadow: inset 0 0 0 1px {md3_colors["segment_outline"]} !important;
            font-size: 12px !important;
            font-weight: 600 !important;
            line-height: 20px !important;
            white-space: nowrap !important;
        }}
        [style*="--ap-chart-md3-segment-{chart_id}"] .btn:first-child {{
            border-left: 0 !important;
        }}
        [style*="--ap-chart-md3-segment-{chart_id}"] .btn:first-child {{
            border-top-left-radius: 11px !important;
            border-bottom-left-radius: 11px !important;
        }}
        [style*="--ap-chart-md3-segment-{chart_id}"] .btn:last-child {{
            border-top-right-radius: 11px !important;
            border-bottom-right-radius: 11px !important;
        }}
        [style*="--ap-chart-md3-segment-{chart_id}"] .btn:hover {{
            background: {md3_colors["hover"]} !important;
        }}
        [style*="--ap-chart-md3-segment-{chart_id}"] .btn-primary {{
            background: {md3_colors["selected_bg"]} !important;
            color: {md3_colors["selected_text"]} !important;
            box-shadow: inset 0 0 0 1px {md3_colors["selected_outline"]} !important;
        }}
        [style*="--ap-chart-md3-segment-{chart_id}"] .btn-secondary {{
            background: transparent !important;
            color: {md3_colors["text"]} !important;
            box-shadow: inset 0 0 0 1px {md3_colors["segment_outline"]} !important;
        }}
        [style*="--ap-chart-md3-refresh-{chart_id}"] {{
            margin: 0 !important;
            padding: 0 !important;
            border: 0 !important;
            background: transparent !important;
            box-shadow: none !important;
        }}
        [style*="--ap-chart-md3-refresh-{chart_id}"].btn,
        [style*="--ap-chart-md3-refresh-{chart_id}"] .btn {{
            margin: 0 !important;
            padding: 7px 16px !important;
            border: 1px solid {md3_colors["segment_border"]} !important;
            border-radius: 12px !important;
            background: {md3_colors["segment_bg"]} !important;
            color: {md3_colors["refresh_text"]} !important;
            box-shadow: none !important;
            font-size: 12px !important;
            font-weight: 600 !important;
            line-height: 20px !important;
            white-space: nowrap !important;
            transform: none !important;
        }}
        [style*="--ap-chart-md3-refresh-{chart_id}"].btn:hover,
        [style*="--ap-chart-md3-refresh-{chart_id}"] .btn:hover {{
            background: {md3_colors["hover"]} !important;
            transform: none !important;
        }}
        [style*="--ap-chart-md3-refresh-{chart_id}"].btn:active,
        [style*="--ap-chart-md3-refresh-{chart_id}"] .btn:active {{
            transform: none !important;
        }}
        @media (max-width: 720px) {{
            [style*="--ap-chart-md3-toolbar-{chart_id}"] {{
                grid-template-columns: 1fr auto !important;
                column-gap: 6px !important;
                padding: 6px 8px !important;
            }}
            [style*="--ap-chart-md3-toolbar-{chart_id}"] > :first-child {{
                display: none !important;
            }}
            [style*="--ap-chart-md3-segment-{chart_id}"] {{
                max-width: none !important;
                width: 100% !important;
                overflow-x: auto !important;
                -webkit-overflow-scrolling: touch !important;
            }}
            [style*="--ap-chart-md3-segment-{chart_id}"] .btn-group {{
                width: max-content !important;
                flex-wrap: nowrap !important;
                overflow: visible !important;
            }}
            [style*="--ap-chart-md3-segment-{chart_id}"] .btn {{
                flex: 0 0 auto !important;
                padding: 6px 12px !important;
                font-size: 12px !important;
                white-space: nowrap !important;
            }}
            [style*="--ap-chart-md3-segment-{chart_id}"]::-webkit-scrollbar {{
                height: 3px !important;
            }}
            [style*="--ap-chart-md3-segment-{chart_id}"]::-webkit-scrollbar-thumb {{
                background: {md3_colors["segment_border"]} !important;
                border-radius: 2px !important;
            }}
            [style*="--ap-chart-md3-refresh-{chart_id}"] .btn {{
                padding: 6px 12px !important;
                font-size: 12px !important;
                white-space: nowrap !important;
            }}
        }}
        </style>
        """)

        view_options = [
            (t("Gui.Stat.ViewLineButton"), "line"),
            (t("Gui.Stat.ViewDayButton"), "day"),
            (t("Gui.Stat.ViewMonthButton"), "month"),
            (t("Gui.Stat.ToggleDetailChart"), "detail"),
        ]
        view_buttons = [
            {
                "label": label,
                "value": value,
                "color": "primary" if current_view == value else "secondary",
            }
            for label, value in view_options
        ]
        put_row(
            [
                put_html(
                    f'<span style="display:inline-flex;align-items:center;gap:6px;'
                    f'font-size:12px;font-weight:600;color:{md3_colors["label"]};white-space:nowrap;">'
                    f"{t('Gui.Stat.ViewLabel')}</span>"
                ),
                put_buttons(
                    view_buttons, onclick=_switch_view, small=True, group=True
                ).style(f"--ap-chart-md3-segment-{chart_id}:1;"),
                put_button(
                    t("Gui.Stat.Refresh"),
                    onclick=self._render_ap_chart,
                    color="secondary",
                    small=True,
                    outline=True,
                ).style(f"--ap-chart-md3-refresh-{chart_id}:1; justify-self:end;"),
            ],
            size="auto auto 1fr",
        ).style(f"--ap-chart-md3-toolbar-{chart_id}:1;")
