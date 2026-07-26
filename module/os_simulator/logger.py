"""大世界模拟器日志模块。

为大世界模拟器提供独立的日志系统，包括 tqdm 输出重定向器
（用于非控制台环境）和模拟器专用日志器（基于文件轮转），
确保模拟运行过程可追溯且不影响主程序日志。
"""
import logging
import os
from datetime import datetime

from module.logger import file_formatter

class TqdmToLogger:
    """
    A helper class to redirect tqdm output to a logger.
    Useful for non-console environments.
    """
    def __init__(self, logger):
        self.logger = logger

    def write(self, buf):
        msg = buf.strip('\r\n\t ')
        if msg:
            self.logger.info(msg)

    def flush(self):
        pass


class OSSLogger:
    def __init__(self):
        self.logger = logging.getLogger('alas.OSSimulator')
        self.logger.setLevel(logging.INFO)
        
        # 仅在未初始化 handler 时添加，防止重复
        if not self.logger.handlers:
            os.makedirs('./log/oss', exist_ok=True)
            self.logger_path = f'./log/oss/{datetime.now().strftime("%Y-%m-%d")}.log'
            fh = logging.FileHandler(self.logger_path, encoding='utf-8')
            fh.setFormatter(file_formatter)
            self.logger.addHandler(fh)
            # 通过 propagate 让日志显示在原有项目的控制台流中
            self.logger.propagate = True

    def __getattr__(self, name):
        return getattr(self.logger, name)