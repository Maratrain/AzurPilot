import sys

from deploy.config import DeployConfig, ExecutionError
from deploy.logger import logger
from deploy.uv import command_output, log_command_output, sync_project_venv, venv_python
from deploy.utils import cached_property


class PipManager(DeployConfig):
    @cached_property
    def python(self) -> str:
        python = venv_python()
        if python.exists():
            return str(python).replace("\\", "/")
        return sys.executable.replace("\\", "/")

    def pip_install(self):
        logger.hr("Update Dependencies", 0)
        if not self.InstallDependencies:
            logger.info("InstallDependencies is disabled, skip")
            return

        try:
            result = sync_project_venv(capture_output=True)
        except Exception as exc:
            logger.critical(f"uv sync failed: {exc}")
            log_command_output(logger, command_output(exc))
            raise ExecutionError from exc
        else:
            log_command_output(logger, result.output)
