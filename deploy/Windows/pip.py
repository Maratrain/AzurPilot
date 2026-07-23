from deploy.Windows.config import DeployConfig, ExecutionError
from deploy.Windows.logger import Progress, logger
from deploy.uv import command_output, log_command_output, sync_project_venv, venv_python
from deploy.Windows.utils import cached_property


class PipManager(DeployConfig):
    @cached_property
    def pip(self):
        return f'"{self.python}" -m pip'

    @cached_property
    def python_site_packages(self):
        return ""

    def pip_install(self):
        logger.hr('Update Dependencies', 0)
        if not self.InstallDependencies:
            logger.info('InstallDependencies is disabled, skip')
            Progress.UpdateDependency()
            return
        try:
            result = sync_project_venv(capture_output=True)
        except Exception as exc:
            logger.critical(f'uv sync failed: {exc}')
            log_command_output(logger, command_output(exc))
            raise ExecutionError from exc
        else:
            log_command_output(logger, result.output)
        Progress.UpdateDependency()
