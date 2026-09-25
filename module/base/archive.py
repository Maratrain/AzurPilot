"""过期文件与目录的处理方式：删除 / 拷贝备份 / 压缩备份。

错误日志（``alas.py``）与掉落记录（``module/statistics/drop_cleanup.py``）
共用这里的实现，压缩格式与 ``module/logger.py`` 的日志轮转保持一致
（``ZIP_EXTENSIONS`` 直接复用 ``RichTimedRotatingHandler.ZIPMAP``）。

备份统一放在调用方指定的 ``bak`` 目录下：

- ``copy``：原样复制一份再删除原条目，重名时跳过复制（不覆盖已有备份）；
- ``zip``：一次调用产生的条目打成一个压缩包再删除原条目，
  命名为 ``<最早日期>~<最晚日期>_<来源标识>.<扩展名>``，重名时自动加序号。

删除不可逆，所以 ``bak`` 目录内的条目一律跳过；配置值异常时回落默认值
（过期天数回落 0，即什么都不做）。
"""

import os
import shutil
import tarfile
import time
import zipfile

from module.logger import RichTimedRotatingHandler, logger

# 与日志轮转共用同一份「压缩格式 → 扩展名」映射
ZIP_EXTENSIONS = RichTimedRotatingHandler.ZIPMAP

# 过期后的处理方式
VALID_METHODS = ('delete', 'zip', 'copy')

# 压缩后文件名的日期格式
DATE_FORMAT = '%Y-%m-%d'


def read_days(config, key, default=0):
    """读取过期天数配置。

    配置值缺失或不可解析时按 0（不清理）处理：删除不可逆，
    配置异常时宁可什么都不做。

    Args:
        config: 当前运行实例的 AzurLaneConfig（或测试用的假对象）。
        key (str): 配置键名，如 ``Error_SaveErrorRetentionDays``。
        default (int): 配置缺失时的返回值。

    Returns:
        int: 过期天数，0 表示不清理。
    """
    value = getattr(config, key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning(f'[清理] 过期天数配置无效: {key}={value!r}，本次跳过清理')
        return 0


def read_method(config, key, default):
    """读取过期后的处理方式，取值不在 VALID_METHODS 内时回落默认值。"""
    value = str(getattr(config, key, default)).lower()
    if value not in VALID_METHODS:
        logger.warning(f'[清理] 处理方式配置无效: {key}={value!r}，按 {default} 处理')
        return default
    return value


def read_zip_method(config, key, default):
    """读取压缩格式，取值不在 ZIP_EXTENSIONS 内时回落默认值。"""
    value = str(getattr(config, key, default)).lower()
    if value not in ZIP_EXTENSIONS:
        logger.warning(f'[清理] 压缩格式配置无效: {key}={value!r}，按 {default} 处理')
        return default
    return value


def _remove(path):
    """删除文件或目录，返回是否成功。"""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return True
    except OSError as e:
        logger.warning(f'[清理] 删除失败 {path}: {e}')
        return False


def _inside(path, folder):
    """判断 path 是否位于 folder 内部（用于保护备份目录）。"""
    path = os.path.abspath(path)
    folder = os.path.abspath(folder)
    return path == folder or path.startswith(folder + os.sep)


def _suffix(zip_method):
    """压缩格式 → 备份文件后缀，如 ``zip`` → ``.zip``、``bz2`` → ``.tar.bz2``。"""
    ext = ZIP_EXTENSIONS[zip_method]
    return '.zip' if ext == 'zip' else f'.tar.{ext}'


def _archive_path(bak_folder, name, suffix, times):
    """生成备份文件名：<最早日期>~<最晚日期>_<来源标识><后缀>，重名加序号。

    同一天或只有一条时只写一个日期。加序号而不是覆盖，
    避免同一时间范围内再次清理时丢掉上一轮备份。

    取名字时先独占创建占位文件：截图目录默认被多个实例共用，
    两个实例同时清理时若只判断「文件是否存在」，会双双选中同一个
    名字，后写入的把先写的备份截断。占位失败就换下一个序号。
    """
    fmt = DATE_FORMAT
    first = time.strftime(fmt, time.localtime(min(times)))
    last = time.strftime(fmt, time.localtime(max(times)))
    stem = first if first == last else f'{first}~{last}'

    index = 1
    while True:
        tail = '' if index == 1 else f'({index})'
        path = os.path.join(bak_folder, f'{stem}_{name}{tail}{suffix}')
        try:
            with open(path, 'x'):
                pass
            return path
        except FileExistsError:
            index += 1
        except OSError as e:
            # 连占位都建不了（权限/磁盘满），退回带序号的名字，
            # 由调用方的异常处理决定是否放弃
            logger.warning(f'[清理] 无法创建备份文件 {path}: {e}')
            return os.path.join(bak_folder, f'{stem}_{name}({index}){suffix}')


def _write_zip(archive, paths):
    """把文件或目录写进 zip，目录带上自身名字递归打包。"""
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for path in paths:
            if not os.path.isdir(path):
                zipf.write(path, arcname=os.path.basename(path))
                continue
            base = os.path.dirname(os.path.normpath(path))
            for folder, _, names in os.walk(path):
                for name in names:
                    file = os.path.join(folder, name)
                    zipf.write(file, arcname=os.path.relpath(file, base))


def _write_tar(archive, zip_method, paths):
    """把文件或目录写进 tar，目录递归打包。"""
    with tarfile.open(archive, 'w:' + ZIP_EXTENSIONS[zip_method]) as tar:
        for path in paths:
            tar.add(path, arcname=os.path.basename(os.path.normpath(path)))


def expire(paths, bak_folder, method, zip_method, name):
    """按 method 处理过期条目。

    Args:
        paths (list[str]): 过期文件或目录的路径。空列表时直接返回。
        bak_folder (str): 备份目录，method 为 copy / zip 时使用。
        method (str): delete / zip / copy，非法值按 delete 处理。
        zip_method (str): bz2 / gzip / xz / zip，非法值按 zip 处理。
        name (str): 备份命名用的来源标识，如实例名或分类目录名。

    Returns:
        int: 实际处理的条目数。
    """
    # 备份目录里的内容不再处理：拷贝备份保留原修改时间，
    # 只按时间判断会把备份当成过期内容反复处理甚至删掉
    paths = [path for path in paths if not _inside(path, bak_folder)]
    if not paths:
        return 0

    if method not in VALID_METHODS:
        logger.warning(f'[清理] 未知的处理方式 {method!r}，按 delete 处理')
        method = 'delete'
    if zip_method not in ZIP_EXTENSIONS:
        logger.warning(f'[清理] 未知的压缩格式 {zip_method!r}，按 zip 处理')
        zip_method = 'zip'

    if method == 'delete':
        return sum(1 for path in paths if _remove(path))

    try:
        os.makedirs(bak_folder, exist_ok=True)
    except OSError as e:
        logger.warning(f'[清理] 创建备份目录失败 {bak_folder}: {e}，保留原条目')
        return 0

    if method == 'copy':
        handled = 0
        for path in paths:
            dst = os.path.join(
                bak_folder, os.path.basename(os.path.normpath(path)))
            try:
                if not os.path.exists(dst):
                    if os.path.isdir(path):
                        shutil.copytree(path, dst)
                    else:
                        shutil.copy2(path, dst)
            except OSError as e:
                logger.warning(f'[清理] 备份失败 {path}: {e}，保留原条目')
                continue
            handled += 1 if _remove(path) else 0
        return handled

    # method == 'zip'
    times = []
    for path in paths:
        try:
            times.append(os.path.getmtime(path))
        except OSError:
            times.append(time.time())
    archive = _archive_path(bak_folder, name, _suffix(zip_method), times)

    try:
        if zip_method == 'zip':
            _write_zip(archive, paths)
        else:
            _write_tar(archive, zip_method, paths)
    except Exception as e:
        # 打包失败时保留原条目，下次清理再试；半成品压缩包一并删掉，
        # 否则下一轮会又生成一个 (2) 副本
        logger.warning(f'[清理] 压缩备份失败 {archive}: {e}，保留原条目')
        try:
            if os.path.exists(archive):
                os.remove(archive)
        except OSError:
            pass
        return 0

    return sum(1 for path in paths if _remove(path))
