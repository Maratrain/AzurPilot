"""掉落记录改本地的单测：档位语义与旧值迁移。

覆盖两件事：
1. 掉落记录档位（do_not / save / local / save_and_local）与
   AzurStats.new() 的本地解析判定一一对应，短猫相接的本地统计
   不再依赖早已废弃的"上传"档位。
2. 老配置里的 upload / save_and_upload 会被迁到新档位。
"""

import json
import unittest
from pathlib import Path

from module.config.config_updater import ConfigUpdater
from module.config.deep import deep_get
from module.config.redirect_utils.utils import (
    drop_record_local_redirect,
    drop_record_save_only_redirect,
)
from module.statistics.azurstats import AzurStats

ARGS_FILE = Path(__file__).resolve().parents[1] / 'module' / 'config' / 'argument' / 'args.json'


class TestDropRecordRedirect(unittest.TestCase):
    """迁移函数本身：upload 档位改成本地语义，其余取值原样返回。"""

    def test_opsi_upload_becomes_local(self):
        self.assertEqual('local', drop_record_local_redirect('upload'))

    def test_opsi_save_and_upload_becomes_save_and_local(self):
        self.assertEqual('save_and_local', drop_record_local_redirect('save_and_upload'))

    def test_opsi_other_values_unchanged(self):
        for value in ('do_not', 'save', 'local', 'save_and_local'):
            self.assertEqual(value, drop_record_local_redirect(value))

    def test_save_only_genres_fall_back(self):
        self.assertEqual('do_not', drop_record_save_only_redirect('upload'))
        self.assertEqual('save', drop_record_save_only_redirect('save_and_upload'))

    def test_save_only_other_values_unchanged(self):
        for value in ('do_not', 'save'):
            self.assertEqual(value, drop_record_save_only_redirect(value))


class TestDropRecordConfigRedirect(unittest.TestCase):
    """走真实的 config_redirect 表，确认旧配置会被改写。"""

    @staticmethod
    def _redirect(key, old_value):
        # 用 __new__ 绕开 __init__：实例化 ConfigUpdater 会读写配置文件。
        updater = ConfigUpdater.__new__(ConfigUpdater)
        arg = key.rsplit('.', 1)[-1]
        old = {'Alas': {'DropRecord': {arg: old_value}}}
        new = {'Alas': {'DropRecord': {arg: old_value}}}
        new = updater.config_redirect(old, new)
        return deep_get(new, keys=key)

    def test_opsi_record_values_migrated(self):
        self.assertEqual('local', self._redirect('Alas.DropRecord.OpsiRecord', 'upload'))
        self.assertEqual(
            'save_and_local',
            self._redirect('Alas.DropRecord.OpsiRecord', 'save_and_upload'),
        )

    def test_save_only_records_migrated(self):
        for key in (
            'Alas.DropRecord.ResearchRecord',
            'Alas.DropRecord.CommissionRecord',
            'Alas.DropRecord.OpsiShopRecord',
            'Alas.DropRecord.MeowfficerTalent',
        ):
            self.assertEqual('do_not', self._redirect(key, 'upload'), key)
            self.assertEqual('save', self._redirect(key, 'save_and_upload'), key)

    def test_already_migrated_value_untouched(self):
        self.assertEqual('save', self._redirect('Alas.DropRecord.OpsiRecord', 'save'))
        self.assertEqual('do_not', self._redirect('Alas.DropRecord.OpsiRecord', 'do_not'))


class TestAzurStatsRecordMethod(unittest.TestCase):
    """档位 → 保存截图 / 本地解析 的映射。"""

    @staticmethod
    def _stats():
        # AzurStats.__init__ 只保存 config，档位映射不依赖配置内容。
        return AzurStats(config=None)

    def _new(self, genre, method):
        return self._stats().new(genre, method=method)

    def test_opsi_local_method_parses_without_saving(self):
        drop = self._new('opsi_meowfficer_farming', 'local')
        self.assertTrue(drop.local)
        self.assertFalse(drop.save)

    def test_opsi_save_and_local_does_both(self):
        drop = self._new('opsi_meowfficer_farming', 'save_and_local')
        self.assertTrue(drop.local)
        self.assertTrue(drop.save)

    def test_opsi_save_only_does_not_parse(self):
        drop = self._new('opsi_meowfficer_farming', 'save')
        self.assertTrue(drop.save)
        self.assertFalse(drop.local)

    def test_opsi_do_not_does_nothing(self):
        drop = self._new('opsi_meowfficer_farming', 'do_not')
        self.assertFalse(drop.save)
        self.assertFalse(drop.local)
        self.assertFalse(bool(drop))

    def test_upload_value_no_longer_triggers_parsing(self):
        # 旧值不该再被认成"解析入库"，否则说明档位没切干净。
        drop = self._new('opsi_meowfficer_farming', 'upload')
        self.assertFalse(drop.local)
        self.assertFalse(drop.save)

    def test_genre_without_local_parser_never_parses(self):
        for method in ('local', 'save_and_local', 'save'):
            drop = self._new('research', method)
            self.assertFalse(drop.local, method)

    def test_genre_only_method_defaults_to_local_parse(self):
        self.assertTrue(self._new('opsi_meowfficer_farming', None).local)
        self.assertFalse(self._new('research', None).local)


class TestDropRecordOptionsMatchCode(unittest.TestCase):
    """args.json 里的档位必须与代码认识的档位一致。"""

    @classmethod
    def setUpClass(cls):
        cls.args = json.loads(ARGS_FILE.read_text(encoding='utf-8'))

    def test_opsi_record_options_cover_code_methods(self):
        options = self.args['Alas']['DropRecord']['OpsiRecord']['option']
        for method in AzurStats.SAVE_METHODS | AzurStats.LOCAL_METHODS:
            self.assertIn(method, options)
        self.assertEqual(['do_not', 'save', 'local', 'save_and_local'], options)

    def test_no_upload_option_left_in_drop_record(self):
        for arg, definition in self.args['Alas']['DropRecord'].items():
            options = definition.get('option') if isinstance(definition, dict) else None
            if options:
                self.assertNotIn('upload', options, arg)
                self.assertNotIn('save_and_upload', options, arg)

    def test_upload_only_arguments_removed(self):
        self.assertNotIn('AzurStatsID', self.args['Alas']['DropRecord'])
        self.assertNotIn('API', self.args['Alas']['DropRecord'])


if __name__ == '__main__':
    unittest.main()