import json
import shutil
import sqlite3

from datetime import datetime, timedelta
from pathlib import Path

from module.logger import logger


ROOT_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT_DIR / 'config'
BACKUP_ROOT = ROOT_DIR.parent / 'AzurPilot_Data_Backup'

BACKUP_KEEP_DAYS = 7

DATABASE_FILES = (
    'azurstats_local.db',
    'cl1_data.db',
)


def backup():
    """
    执行每日备份。

    包括：
        - 数据库
        - 用户配置
    """
    date = datetime.now().strftime('%Y-%m-%d')
    backup_dir = BACKUP_ROOT / date

    if backup_dir.exists():
        logger.info(f'今日备份已存在，跳过备份：{backup_dir}')
        return

    logger.info('开始执行每日备份')

    backup_dir.mkdir(parents=True, exist_ok=True)

    files = []

    files.extend(backup_database(backup_dir))
    files.extend(backup_config(backup_dir))

    create_backup_info(
        backup_dir=backup_dir,
        files=files,
    )

    clean_backup()

    logger.info(f'每日备份完成，共备份 {len(files)} 个文件')


def backup_database(backup_dir):
    """
    备份数据库。

    Args:
        backup_dir (Path): 备份目录。

    Returns:
        list: 备份文件信息。
    """
    logger.info('开始备份数据库')

    files = []

    for name in DATABASE_FILES:
        source = CONFIG_DIR / name

        if not source.exists():
            logger.warning(f'未找到数据库文件，跳过备份：{source}')
            continue

        target = backup_dir / name

        try:
            sqlite_backup(
                source=source,
                target=target,
            )

            files.append({
                'name': name,
                'size': target.stat().st_size,
            })

            logger.info(f'数据库备份成功：{name}')
        except Exception as e:
            logger.warning(f'数据库备份失败：{name}，{e}')

    return files


def backup_config(backup_dir):
    """
    备份用户配置。

    包括：
        - deploy.yaml
        - 用户配置 json（排除 template*.json）

    Args:
        backup_dir (Path): 备份目录。

    Returns:
        list: 备份文件信息。
    """
    logger.info('开始备份用户配置')

    files = []

    deploy = CONFIG_DIR / 'deploy.yaml'

    if deploy.exists():
        target = backup_dir / deploy.name

        shutil.copy2(deploy, target)

        files.append({
            'name': deploy.name,
            'size': target.stat().st_size,
        })

        logger.info('用户配置备份成功：deploy.yaml')

    for file in CONFIG_DIR.glob('*.json'):
        if file.stem.startswith('template'):
            continue

        target = backup_dir / file.name

        try:
            shutil.copy2(file, target)

            files.append({
                'name': file.name,
                'size': target.stat().st_size,
            })

            logger.info(f'用户配置备份成功：{file.name}')
        except Exception as e:
            logger.warning(f'用户配置备份失败：{file.name}，{e}')

    return files

def sqlite_backup(source, target):
    """
    使用 SQLite 原生 backup() 接口备份数据库。

    Args:
        source (Path): 原数据库路径。
        target (Path): 备份数据库路径。
    """
    source_conn = sqlite3.connect(source)
    target_conn = sqlite3.connect(target)

    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()


def create_backup_info(backup_dir, files):
    """
    创建备份信息文件。

    Args:
        backup_dir (Path): 备份目录。
        files (list): 已备份文件信息。
    """
    info = {
        'backup_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'file_count': len(files),
        'files': files,
    }

    with open(backup_dir / 'backup_info.json', 'w', encoding='utf-8') as f:
        json.dump(
            info,
            f,
            indent=4,
            ensure_ascii=False,
        )


def clean_backup():
    """
    清理超过保留天数的历史备份。
    """
    if not BACKUP_ROOT.exists():
        return

    expire_date = datetime.now().date() - timedelta(days=BACKUP_KEEP_DAYS)

    for folder in BACKUP_ROOT.iterdir():
        if not folder.is_dir():
            continue

        try:
            folder_date = datetime.strptime(folder.name, '%Y-%m-%d').date()
        except ValueError:
            continue

        if folder_date >= expire_date:
            continue

        try:
            shutil.rmtree(folder)
            logger.info(f'已删除过期备份：{folder.name}')
        except Exception as e:
            logger.warning(f'删除过期备份失败：{folder.name}，{e}')