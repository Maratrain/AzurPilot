"""部署设置（deploy.yaml）schema 测试。"""

import unittest

from module.webui.deploy_settings import DEPLOY_FIELDS, deploy_settings_schema


class TestDeploySettingsSchema(unittest.TestCase):
    def test_skip_repository_update_field_registered(self):
        """跳过仓库更新字段应注册为 Git 组布尔项。"""
        field = DEPLOY_FIELDS.get('SkipRepositoryUpdate')
        self.assertIsNotNone(field)
        self.assertEqual(field.kind, 'bool')

    def test_schema_git_group_contains_skip_repository_update(self):
        schema = deploy_settings_schema(lambda key: key)
        git_group = next(g for g in schema['groups'] if g['key'] == 'Git')
        keys = [field['key'] for field in git_group['fields']]
        self.assertIn('SkipRepositoryUpdate', keys)

    def test_skip_repository_update_default_false(self):
        """未显式配置时取模板默认值 false，不改变既有行为。"""
        schema = deploy_settings_schema(lambda key: key)
        git_group = next(g for g in schema['groups'] if g['key'] == 'Git')
        field = next(f for f in git_group['fields'] if f['key'] == 'SkipRepositoryUpdate')
        self.assertIs(field['value'], False)


if __name__ == '__main__':
    unittest.main()
