"""
Web界面部署配置管理。

提供 DeployConfig 的 WebUI 子类，将配置变更实时写入部署文件。
通过 __setattr__ 拦截属性修改，自动同步到磁盘配置。
"""

from deploy.config import DeployConfig as _DeployConfig


class DeployConfig(_DeployConfig):
    def show_config(self):
        pass

    def __setattr__(self, key: str, value):
        """
        Catch __setattr__, copy to `self.config`, write deploy config.
        """
        super().__setattr__(key, value)
        if key[0].isupper() and key in self.config:
            if key in self.config:
                before = self.config[key]
                if before != value:
                    self.config[key] = value
                    self.write()
            else:
                self.config[key] = value
                self.write()
