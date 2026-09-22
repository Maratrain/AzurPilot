"""体力图表视图（分时/天视图/月视图）的装配回归测试。

覆盖 2026-09-20 恢复视图切换时定下的行为：
- 视图取值必须收敛到白名单，非法值回退分时曲线
- 已下线的"日变化表格 / 增减柱状图"不得被采纳（含历史配置里的残留值）
- 日/月聚合的 OHLC 口径
- 按钮组只渲染三个视图且水平居中
"""

import unittest
from datetime import datetime
from unittest.mock import patch

from module.webui.app_stat_action_point import ActionPointStatisticsMixin


def _points():
    def point(year, month, day, hour, minute, ap, source="-"):
        return {
            "dt": datetime(year, month, day, hour, minute),
            "ap": ap,
            "source": source,
        }

    return [
        point(2026, 9, 18, 23, 30, 100, "cl1"),
        point(2026, 9, 19, 8, 0, 120, "meow"),
        point(2026, 9, 19, 20, 0, 90, "-"),
        point(2026, 9, 20, 9, 0, 150, "cl1"),
        point(2026, 9, 20, 15, 0, 130, "meow"),
    ]


class _ChartHarness(ActionPointStatisticsMixin):
    """只保留图表装配所需状态的测试替身。"""

    def __init__(self, view="line"):
        self.alas_name = "alas"
        self._ap_chart_view = view
        self.rendered = []

    def _render_ap_chart(self):
        self.rendered.append(getattr(self, "_ap_chart_view", None))


class _ApChartTestCase(unittest.TestCase):
    def setUp(self):
        self.patches = (
            patch(
                "module.webui.app_stat_action_point.t",
                side_effect=lambda key, **kwargs: key,
            ),
            patch(
                "module.webui.app_stat_action_point.current_time",
                return_value=datetime(2026, 9, 20, 16, 0),
            ),
        )
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()

    def _series(self, view):
        return _ChartHarness(view)._build_ap_chart_series(_points())


class TestApChartViewWhitelist(_ApChartTestCase):
    def test_unknown_view_falls_back_to_line(self):
        data = self._series("nonsense")
        self.assertEqual("line", data["current_view"])
        self.assertEqual("Gui.Stat.ViewTitleLine", data["view_title"])
        self.assertEqual([], data["opens"])

    def test_all_documented_views_are_supported(self):
        self.assertEqual(
            ("line", "day", "month"),
            ActionPointStatisticsMixin.AP_CHART_VIEWS,
        )

    def test_switch_ignores_unknown_view_and_skips_same_view(self):
        gui = _ChartHarness("line")
        gui._switch_ap_chart_view("bogus")
        self.assertEqual([], gui.rendered)
        self.assertEqual("line", gui._ap_chart_view)

        gui._switch_ap_chart_view("line")
        self.assertEqual([], gui.rendered)

        gui._switch_ap_chart_view("day")
        self.assertEqual(["day"], gui.rendered)
        self.assertEqual("day", gui._ap_chart_view)

    def test_removed_views_are_rejected(self):
        """日变化表格与增减柱状图已下线，残留配置值不得被采纳。"""
        gui = _ChartHarness("table")
        gui._switch_ap_chart_view("bar")
        self.assertEqual([], gui.rendered)
        self.assertEqual("table", gui._ap_chart_view)

        data = gui._build_ap_chart_series(_points())
        self.assertEqual("line", data["current_view"])


class TestApChartAggregation(_ApChartTestCase):
    def test_month_view_aggregates_daily_ohlc(self):
        data = self._series("month")
        self.assertEqual(["09-18", "09-19", "09-20"], data["labels"])
        self.assertEqual([100, 120, 150], data["opens"])
        self.assertEqual([100, 90, 130], data["closes"])
        self.assertEqual([100, 120, 150], data["highs"])
        self.assertEqual([100, 90, 130], data["lows"])
        self.assertEqual([1, 2, 2], data["counts"])
        self.assertEqual("Gui.Stat.ViewTitleMonth", data["view_title"])

    def test_day_view_uses_latest_available_date(self):
        data = self._series("day")
        self.assertEqual(["09:00", "15:00"], data["labels"])
        self.assertEqual([150, 130], data["closes"])
        self.assertEqual("Gui.Stat.CandlesCount", data["data_points_text"])

    def test_line_view_keeps_raw_points(self):
        data = self._series("line")
        self.assertEqual([100, 120, 90, 150, 130], data["ap_list"])
        self.assertEqual(5, len(data["labels"]))


class _OutputStub:
    """记录 put_buttons 的入参与附加样式。"""

    def __init__(self, captured, buttons):
        self.captured = captured
        self.captured["buttons"] = buttons

    def style(self, css):
        self.captured["style"] = css
        return self


class TestApChartViewSwitcher(_ApChartTestCase):
    def test_switcher_renders_three_centered_buttons(self):
        captured = {}
        with patch(
            "module.webui.app_stat_action_point.put_buttons",
            side_effect=lambda buttons, onclick=None: _OutputStub(captured, buttons),
        ):
            _ChartHarness("month")._render_ap_chart_view_switcher("month")

        self.assertEqual(
            ["line", "day", "month"],
            [button["value"] for button in captured["buttons"]],
        )
        colors = {b["value"]: b["color"] for b in captured["buttons"]}
        self.assertEqual("primary", colors["month"])
        self.assertEqual("off", colors["line"])
        self.assertIn("justify-content:center", captured["style"])


if __name__ == "__main__":
    unittest.main()
