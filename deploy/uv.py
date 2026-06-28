import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlparse


BOOTSTRAPPED_ENV = "AZURPILOT_UV_BOOTSTRAPPED"
BOOTSTRAP_UV_ENV = "AZURPILOT_BOOTSTRAP_UV"
NO_BOOTSTRAP_ENV = "AZURPILOT_NO_UV_BOOTSTRAP"
PYTHON_VERSION = "3.14.3"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def venv_path(root: Path = None) -> Path:
    root = root or project_root()
    return root / ".venv"


def venv_bin(root: Path = None) -> Path:
    venv = venv_path(root)
    if os.name == "nt":
        return venv / "Scripts"
    return venv / "bin"


def venv_python(root: Path = None) -> Path:
    executable = "python.exe" if os.name == "nt" else "python"
    return venv_bin(root) / executable


def venv_python_install_dir(root: Path = None) -> Path:
    return venv_path(root) / "python"


def venv_uv(root: Path = None) -> Path:
    executable = "uv.exe" if os.name == "nt" else "uv"
    return venv_bin(root) / executable


def venv_adb(root: Path = None) -> Path:
    executable = "adb.exe" if os.name == "nt" else "adb"
    return venv_bin(root) / executable


def venv_git(root: Path = None) -> Path:
    root = root or project_root()
    if os.name == "nt":
        return venv_path(root) / "Scripts" / "git" / "cmd" / "git.exe"
    return venv_bin(root) / "git"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def in_project_venv(root: Path = None) -> bool:
    root = root or project_root()
    executable = Path(sys.executable).resolve()
    python = venv_python(root)
    try:
        if python.exists() and executable.samefile(python):
            return True
    except OSError:
        pass

    prefix = Path(sys.prefix).resolve()
    return _is_relative_to(prefix, venv_path(root).resolve())


def _read_deploy_value(root: Path, key: str):
    deploy_config = root / "config" / "deploy.yaml"
    try:
        text = deploy_config.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        current_key, value = line.split(":", 1)
        if current_key.strip() != key:
            continue
        value = value.strip().strip("'\"")
        if not value or value.lower() == "null":
            return None
        return value
    return None


def _deploy_bool(root: Path, key: str, default: bool = True) -> bool:
    value = _read_deploy_value(root, key)
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "yes", "on"}


def _uv_index_args(root: Path):
    args = []
    mirror = _read_deploy_value(root, "PypiMirror")
    ssl_verify = _deploy_bool(root, "SSLVerify", default=True)

    if mirror:
        args += ["--default-index", mirror]
        hostname = urlparse(mirror).hostname
        if hostname and (mirror.startswith("http:") or not ssl_verify):
            args += ["--allow-insecure-host", hostname]
    elif not ssl_verify:
        args += ["--allow-insecure-host", "pypi.org"]
        args += ["--allow-insecure-host", "files.pythonhosted.org"]
    return args


PathLikeArg = Union[str, os.PathLike]


def _resolve_uv(root: Path, bootstrap_uv: Optional[PathLikeArg] = None) -> Path:
    candidates = []
    if bootstrap_uv:
        candidates.append(Path(bootstrap_uv))
    env_bootstrap = os.environ.get(BOOTSTRAP_UV_ENV)
    if env_bootstrap:
        candidates.append(Path(env_bootstrap))
    candidates.append(venv_uv(root))
    path_uv = shutil.which("uv")
    if path_uv:
        candidates.append(Path(path_uv))

    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
        if candidate and shutil.which(str(candidate)):
            return Path(str(candidate))

    raise RuntimeError(
        "uv is required to prepare AzurPilot's Python environment. "
        "Use the launcher package or install uv first."
    )


def _uv_python_env(root: Path):
    env = os.environ.copy()
    env["UV_PYTHON_INSTALL_DIR"] = str(venv_python_install_dir(root))
    env.setdefault("UV_NO_PROGRESS", "1")
    return env


def _managed_python_executable(root: Path) -> Optional[Path]:
    install_dir = venv_python_install_dir(root)
    for python_home in sorted(install_dir.glob(f"cpython-{PYTHON_VERSION}-*")):
        candidates = [
            python_home / "python.exe",
            python_home / "bin" / "python3.14",
            python_home / "bin" / "python",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
    return None


def _venv_python_works(root: Path) -> bool:
    python = venv_python(root)
    if not python.exists():
        return False
    try:
        subprocess.run(
            [
                str(python),
                "-c",
                "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 14) else 1)",
            ],
            cwd=str(root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=True,
        )
    except Exception:
        return False
    return True


def _remove_stale_venv_launcher(root: Path):
    """
    清理 uv venv 在 Windows 上重建可重定位环境时可能撞到的旧启动器。

    uv 使用 --allow-existing 时会复用 .venv，但 Windows 上已有的
    Scripts/python.exe 可能阻止它创建新的可执行文件链接，报 os error 80。
    """
    if os.name != "nt":
        return
    python = venv_python(root)
    if not python.exists():
        return
    try:
        python.unlink()
    except OSError:
        pass


def _run(command, root: Path, env=None):
    command = [str(part) for part in command]
    print("+ " + _join_command(command))
    # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
    subprocess.run(command, cwd=str(root), check=True, env=env)


def _run_output(command, root: Path, env=None) -> str:
    command = [str(part) for part in command]
    print("+ " + _join_command(command))
    # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
    return subprocess.check_output(command, cwd=str(root), text=True, env=env).strip()


def _join_command(command):
    if hasattr(shlex, "join"):
        return shlex.join(command)
    return " ".join(shlex.quote(part) for part in command)


def _ensure_self_contained_python(root: Path, uv: Path):
    if _venv_python_works(root) and _managed_python_executable(root):
        return

    env = _uv_python_env(root)
    managed_python = _managed_python_executable(root)
    if managed_python is None:
        _run(
            [
                uv,
                "python",
                "install",
                "--install-dir",
                venv_python_install_dir(root),
                "--no-bin",
                "--managed-python",
                PYTHON_VERSION,
            ],
            root,
            env=env,
        )
        managed_python = _managed_python_executable(root)
    if managed_python is None:
        managed_python = Path(
            _run_output(
                [
                    uv,
                    "python",
                    "find",
                    "--managed-python",
                    PYTHON_VERSION,
                ],
                root,
                env=env,
            )
        )

    _remove_stale_venv_launcher(root)
    _run(
        [
            uv,
            "venv",
            "--allow-existing",
            "--relocatable",
            "--python",
            managed_python,
            venv_path(root),
        ]
        + _uv_index_args(root),
        root,
        env=env,
    )


def sync_project_venv(root: Path = None, bootstrap_uv: Optional[PathLikeArg] = None):
    root = root or project_root()
    if not _deploy_bool(root, "InstallDependencies", default=True):
        print("InstallDependencies is disabled, skip uv sync")
        return

    uv = _resolve_uv(root, bootstrap_uv=bootstrap_uv)

    _ensure_self_contained_python(root, uv)

    _run(
        [
            uv,
            "sync",
            "--project",
            str(root),
            "--frozen",
            "--no-dev",
            "--no-install-project",
        ]
        + _uv_index_args(root),
        root,
        env=_uv_python_env(root),
    )


def ensure_uv_environment():
    if os.environ.get(NO_BOOTSTRAP_ENV):
        return
    if in_project_venv():
        return

    root = project_root()
    try:
        sync_project_venv(root=root)
    except Exception as exc:
        print(f"Failed to prepare uv environment: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    os.environ[BOOTSTRAPPED_ENV] = "1"
    os.execv(str(venv_python(root)), [str(venv_python(root)), *sys.argv])
