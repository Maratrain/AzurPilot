import sys
from typing import Optional, Union

from deploy.config_transaction import DeployConfigTransaction
from deploy.logger import logger
from deploy.utils import *


GITHUB_REPOSITORY = 'https://github.com/wess09/AzurPilot'
MARATRAIN_REPOSITORY = 'https://github.com/Maratrain/AzurPilot'


class ExecutionError(Exception):
    pass


class ConfigModel:
    # Git 配置
    Repository: str = MARATRAIN_REPOSITORY
    Branch: str = "master"
    GitExecutable: str = "./.venv/Scripts/git/cmd/git.exe" if sys.platform == "win32" else "./.venv/bin/git"
    GitProxy: Optional[str] = None
    SSLVerify: bool = False
    # 启动器启动时跳过仓库更新（不实时拉取 GitHub）
    SkipRepositoryUpdate: bool = False

    # Python 配置
    PythonExecutable: str = "./.venv/Scripts/python.exe" if sys.platform == "win32" else "./.venv/bin/python"
    PypiMirror: Optional[str] = None
    InstallDependencies: bool = True

    # ADB 配置
    AdbExecutable: str = "./.venv/Scripts/adb.exe" if sys.platform == "win32" else "./.venv/bin/adb"
    ReplaceAdb: bool = True
    AutoConnect: bool = True
    InstallUiautomator2: bool = True

    # OCR 配置
    UseOcrServer: bool = False
    StartOcrServer: bool = False
    OcrServerPort: int = 22268
    OcrClientAddress: str = "127.0.0.1:22268"

    # 更新配置
    EnableReload: bool = True
    CheckUpdateInterval: int = 5
    AutoRestartTime: str = "03:50"
    HideUpdateNotice: bool = False
    HideAnnouncement: bool = True

    # 杂项
    DiscordRichPresence: bool = False

    # 远程访问
    EnableRemoteAccess: bool = False
    RemoteAccessMode: str = "auto"
    SSHUser: Optional[str] = None
    SSHServer: Optional[str] = None
    SSHExecutable: Optional[str] = None
    SignalingServer: Optional[str] = None
    StunServers: Optional[str] = '["stun:stun.l.google.com:19302"]'
    TurnServers: Optional[str] = None
    TurnCredentialMode: str = "static"

    # WebUI 配置
    WebuiHost: str = "0.0.0.0"
    WebuiPort: int = 25548
    WebuiSSLKey: Optional[str] = None
    WebuiSSLCert: Optional[str] = None
    Language: str = "en-US"
    Theme: str = "default"
    DpiScaling: bool = True
    Password: Optional[str] = None
    CDN: Union[str, bool] = False
    Run: Optional[str] = None


class DeployConfig(DeployConfigTransaction, ConfigModel):
    def __init__(self, file=DEPLOY_CONFIG):
        """初始化部署配置。

        Args:
            file (str): 用户部署配置文件路径。
        """
        self.file = file
        self.template_file = get_deploy_template()
        self.config = {}
        self.config_template = {}
        self.read()

        self.show_config()

    def show_config(self):
        logger.hr("Show deploy config", 1)
        for k, v in self.config.items():
            if k in ("Password", "SSHUser"):
                continue
            if self.config_template.get(k) == v:
                continue
            logger.info(f"{k}: {v}")

        logger.info(f"Rest of the configs are the same as default")

    def config_redirect(self):
        """部署配置重定向，处理旧配置到新配置的迁移。

        每次 `read()` 之后必须调用。
        """
        self.config.pop('AutoUpdate', None)
        if self.Repository in [
            'https://gitee.com/LmeSzinc/AzurLaneAutoScript',
            'https://gitee.com/lmeszinc/azur-lane-auto-script-mirror',
            'https://e.coding.net/llop18870/alas/AzurLaneAutoScript.git',
            'https://e.coding.net/saarcenter/alas/AzurLaneAutoScript.git',
            'https://git.saarcenter.com/LmeSzinc/AzurLaneAutoScript.git',
            'https://git.lyoko.io/AzurLaneAutoScript',
            'https://gitcode.com/ddl2/AzurLaneAutoScript',
            'https://gitcode.com/ZhangMusan/AzurLaneAutoScript',
            'https://gitcode.com/nerom/AzurLaneAutoScript',
            'https://gitee.com/wqeaxc/AzurLaneAutoScript1',
        ]:
            object.__setattr__(self, 'Repository', MARATRAIN_REPOSITORY)
            self.config['Repository'] = MARATRAIN_REPOSITORY
        if self.PypiMirror in [
            'https://pypi.tuna.tsinghua.edu.cn/simple'
        ]:
            object.__setattr__(self, 'PypiMirror', 'https://mirrors.aliyun.com/pypi/simple')
            self.config['PypiMirror'] = 'https://mirrors.aliyun.com/pypi/simple'

        if self.Repository in ['global', 'cn']:
            super().__setattr__('Repository', MARATRAIN_REPOSITORY)

    def filepath(self, key):
        """根据配置键获取绝对文件路径。

        Args:
            key (str): 配置键名。

        Returns:
            str: 绝对文件路径。
        """
        return (
            os.path.abspath(os.path.join(self.root_filepath, self.config[key]))
            .replace(r"\\", "/")
            .replace("\\", "/")
            .replace('"', '"')
        )

    @cached_property
    def root_filepath(self):
        return (
            os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))
            .replace(r"\\", "/")
            .replace("\\", "/")
            .replace('"', '"')
        )

    def execute(self, command, allow_failure=False, output=True):
        """执行系统命令。

        Args:
            command (str): 要执行的命令。
            allow_failure (bool): 是否允许失败。
            output (bool): 是否显示输出。

        Returns:
            bool: 是否成功。失败且不允许失败时终止安装流程。
        """
        command = command.replace(r"\\", "/").replace("\\", "/").replace('"', '"')
        if not output:
            command = command + ' >nul 2>nul'
        logger.info(command)
        error_code = os.system(command)
        if error_code:
            if allow_failure:
                logger.info(f"[ allowed failure ], error_code: {error_code}")
                return False
            else:
                logger.info(f"[ failure ], error_code: {error_code}")
                self.show_error(command)
                raise ExecutionError
        else:
            logger.info(f"[ success ]")
            return True

    def show_error(self, command=None):
        logger.hr("Update failed", 0)
        self.show_config()
        logger.info("")
        logger.info(f"Last command: {command}")
        logger.info(
            "Please check your deploy settings in config/deploy.yaml "
            "and re-open AzurPilot.exe"
        )
        logger.info("Take the screenshot of entire window if you need help")
