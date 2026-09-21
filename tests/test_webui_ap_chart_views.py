"""体力图表视图（分时/天视图/月视图/日变化表格/增减柱状图）的装配回归测试。

覆盖 2026-09-20 恢复视图切换 + 新增表格与柱状图时定下的行为：
- 视图取值必须收敛到白名单，非法值回退分时曲线
- 日/月聚合的 OHLC 与"净变化"基准（首日对开盘，之后对上一周期收盘）
- 日变化表格按最新日期倒序输出
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
            ("line", "day", "month", "table", "bar"),
            ActionPointStatisticsMixin.AP_CHART_VIEWS,
        )

    def test_switch_ignores_unknown_view_and_skips_same_view(self):
        gui = _ChartHarness("line")
        gui._switch_ap_chart_view("bogus")
        self.assertEqual([], gui.rendered)
        self.assertEqual("line", gui._ap_chart_view)

        gui._switch_ap_chart_view("line")
        self.assertEqual([], gui.rendered)

        gui._switch_ap_chart_view("bar")
        self.assertEqual(["bar"], gui.rendered)
        self.assertEqual("bar", gui._ap_chart_view)


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

    def test_bar_view_net_change_baseline(self):
        data = self._series("bar")
        # 首日对当日开盘，之后对上一周期收盘
        self.assertEqual([0, -10, 40], data["bars"])
        self.assertEqual("Gui.Stat.ViewTitleBar", data["view_title"])

    def test_table_view_rows_are_newest_first(self):
        data = self._series("table")
        self.assertEqual(["09-20", "09-19", "09-18"], [r["date"] for r in data["table_rows"]])
        self.assertEqual([40, -10, 0], [r["net"] for r in data["table_rows"]])
        self.assertEqual(2, data["table_rows"][0]["count"])

    def test_line_view_keeps_raw_points(self):
        data = self._series("line")
        self.assertEqual([100, 120, 90, 150, 130], data["ap_list"])
        self.assertEqual(5, len(data["labels"]))
        self.assertEqual([], data["bars"])


class TestApChartTableHtml(_ApChartTestCase):
    def test_table_html_marks_sign_and_headers(self):
        rows = [
            {"date": "09-20", "open": 150, "close": 130, "high": 150, "low": 130, "net": 40, "count": 2},
            {"date": "09-19", "open": 120, "close": 90, "high": 120, "low": 90, "net": -10, "count": 2},
        ]
        html = ActionPointStatisticsMixin._build_ap_table_html(rows)

        for key in (
            "Gui.Stat.TableHeaderDate",
            "Gui.Stat.TableHeaderOpen",
            "Gui.Stat.TableHeaderClose",
            "Gui.Stat.TableHeaderHigh",
            "Gui.Stat.TableHeaderLow",
            "Gui.Stat.TableHeaderNet",
            "Gui.Stat.TableHeaderPoints",
        ):
            self.assertIn(key, html)
        self.assertIn("#ef5350", html)
        self.assertIn("#26a69a", html)
        self.assertIn("+40", html)
        self.assertIn("-10", html)

    def test_empty_table_html_shows_notice(self):
        html = ActionPointStatisticsMixin._build_ap_table_html([])
        self.assertIn("Gui.Stat.NoDailyTableData", html)
        self.assertNotIn("<table", html)


if __name__ == "__main__":
    unittest.main()
