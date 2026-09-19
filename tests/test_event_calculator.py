"""活动计算器 Wiki 数据解析的回归测试。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from module.webui import event_calculator


DYNAMIC_SHOP_RAW = """
当前活动：[[测试活动]]
{{#vardefine:_shop_items|
{{道具2|测试道具}},150,500
{{小图标|测试舰船}},8000,1
}}
{|id="ECALCPt" class="wikitable"
!项目||单价||个数
{{#arraymap:{{#var:_shop_items}}|\\n|@line@|{{!}}@line@|\\n}}
|}
{|id="ECALCTime" class="wikitable"
!结束日期||剩余天数
|-
||2026/09/30||
|}
{|id="ECALCDaily" class="wikitable"
!日常任务||点数
|-
|建造3次||300
|}
{|id="ECALCExtra" class="wikitable"
!每日额外||点数
|-
|A1||90
|}
{|id="ECALC" class="wikitable"
!如果只打||每次拿
|-
|D3||180
|}
"""


class TestEventCalculator(unittest.TestCase):
    """验证动态商店数据不会阻断活动计算器。"""

    def test_parse_dynamic_shop_vardefine_when_table_has_no_static_rows(self):
        data = event_calculator.parse_event_calculator(DYNAMIC_SHOP_RAW)

        self.assertEqual("当前活动：测试活动", data["event_name"])
        self.assertEqual("2026-09-30", data["end_date"])
        self.assertEqual(
            [
                {
                    "name": "测试道具",
                    "price": 150,
                    "quantity": 500,
                    "filter": "",
                },
                {
                    "name": "测试舰船",
                    "price": 8000,
                    "quantity": 1,
                    "filter": "ShipSSR",
                },
            ],
            data["shop_items"],
        )
        self.assertEqual(83000, data["shop_total"])
        self.assertEqual([{"name": "D3", "points": 180}], data["stages"])

    def test_load_dynamic_shop_vardefine_without_calculator_error(self):
        response = Mock(text=DYNAMIC_SHOP_RAW)
        response.raise_for_status = Mock()

        with tempfile.TemporaryDirectory() as directory:
            cache_file = Path(directory) / "event_calculator.json"
            with (
                patch.object(event_calculator, "CACHE_FILE", str(cache_file)),
                patch.object(event_calculator.requests, "get", return_value=response),
            ):
                data = event_calculator.load_event_calculator(force_refresh=True)

        self.assertFalse(data["from_cache"])
        self.assertNotIn("error", data)
        self.assertEqual(2, len(data["shop_items"]))
        response.raise_for_status.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
