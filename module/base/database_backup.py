import json
import shutil
import sqlite3

from datetime import datetime, timedelta
from pathlib import Path

from module.logger import logger


ROOT_DIR = Path(__file__).resolve().parents[2]

DATABASE_BACKUP_DIR = ROOT_DIR.parent / 'AzurPilot_Data_Backup' / 'database'

DATABASE_FILES = {
    'azurstats_local.db': ROOT_DIR / 'config' / 'azurstats_local.db',
    'cl1_data.db': ROOT_DIR / 'config' / 'cl1_data.db',
}

DATABASE_BACKUP_KEEP_DAYS = 7


def backup_database():
    """
    备份本地数据库文件。

    每天生成一次数据库备份，并自动清理过期备份。
    """
    date = datetime.now().strftime('%Y-%m-%d')
    backup_dir = DATABASE_BACKUP_DIR / date

    if backup_dir.exists():
        logger.info(f'数据库备份已存在，跳过本次备份: {backup_dir}')
        return

    backup_dir.mkdir(parents=True, exist_ok=True)

    files = []
    for name, source in DATABASE_FILES.items():
        if not source.exists():
            logger.warning(f'未找到数据库文件，跳过备份: {source}')
            continue

        target = backup_dir / name

        try:
            sqlite_backup(source, target)
            files.append({
                'name': name,
                'size': target.stat().st_size,
            })
            logger.info(f'数据库备份成功: {name}')
        except Exception as e:
            logger.warning(f'数据库备份失败: {name}, {e}')

    create_backup_info(backup_dir, files)
    clean_database_backup()


def sqlite_backup(source, target):
    """
    使用 SQLite 原生备份接口备份数据库。

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
    创建数据库备份校验信息。

    Args:
        backup_dir (Path): 备份目录。
        files (list): 备份文件信息。
    """
    info = {
        'backup_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'files': files,
    }

    with open(backup_dir / 'backup_info.json', 'w', encoding='utf-8') as f:
        json.dump(info, f, indent=4, ensure_ascii=False)


def clean_database_backup():
    """
    清理超过保留时间的数据库备份。
    """
    if not DATABASE_BACKUP_DIR.exists():
        return

    expire_date = datetime.now() - timedelta(days=DATABASE_BACKUP_KEEP_DAYS)

    for folder in DATABASE_BACKUP_DIR.iterdir():
        if not folder.is_dir():
            continue

        try:
            folder_date = datetime.strptime(folder.name, '%Y-%m-%d')
        except ValueError:
            continue

        if folder_date < expire_date:
            shutil.rmtree(folder)
            logger.info(f'已删除过期数据库备份: {folder}')