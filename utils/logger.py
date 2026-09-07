"""
日志系统配置 - 支持日志轮转、搜索和导出
"""
import os
import logging
import re
import shutil
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import List, Optional, Tuple

from utils.crash_diagnostics import get_diagnostics, log_directories


_LOG_HEADER = re.compile(
    rb'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] (\S+): ([^\r\n]*)'
)


def _iter_records(stream):
    """按日志头分组，字节与原始换行保持不变；首条头前的文本也保留。"""
    header = None
    lines = []
    for line in stream:
        match = _LOG_HEADER.match(line)
        if match:
            if lines:
                yield header, b''.join(lines)
            header = match
            lines = []
        lines.append(line)
    if lines:
        yield header, b''.join(lines)


def _matches(header, level, start_time, end_time):
    timestamp = header.group(1).decode('ascii')
    log_level = header.group(2).decode('ascii')
    # 本协议固定为零填充 ISO 时间，字符串比较与日期排序一致。
    return (not level or level.upper() == log_level) and (
        not start_time or timestamp >= start_time
    ) and (not end_time or timestamp <= end_time)


def setup_logger(log_dir: Optional[str] = None) -> logging.Logger:
    """
    配置应用日志系统

    Args:
        log_dir: 日志目录，默认为应用程序目录下的 logs 文件夹

    Returns:
        配置好的根日志记录器
    """
    diagnostics = get_diagnostics()
    if log_dir is None and diagnostics is not None:
        log_dir = diagnostics.log_dir

    file_formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_formatter = logging.Formatter('[%(levelname)s] %(message)s')

    file_handler = None
    log_file = None
    failures = []
    for directory in log_directories(log_dir):
        candidate = os.path.join(directory, f'app_{datetime.now():%Y%m%d}.log')
        try:
            os.makedirs(directory, exist_ok=True)
            file_handler = RotatingFileHandler(
                candidate, maxBytes=5 * 1024 * 1024, backupCount=5,
                encoding='utf-8',
            )
            log_file = file_handler.baseFilename
            break
        except OSError as exc:
            failures.append(f"日志文件不可写: {candidate}: {type(exc).__name__}")
    if file_handler is not None:
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(file_formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    for h in root_logger.handlers[:]:
        h.close()
        root_logger.removeHandler(h)

    if file_handler:
        root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    if file_handler:
        root_logger.info(f"日志系统已初始化，日志文件: {log_file}")
    else:
        root_logger.warning("日志系统已初始化（仅控制台输出）")

    for failure in failures:
        root_logger.warning(failure)
    if diagnostics is not None:
        diagnostics.record_text(f"ordinary_log={log_file} "
                                f"status={'enabled' if file_handler else 'failed'}")
        root_logger.info("独立诊断文件: %s；故障反馈请一并提供；状态: %s",
                         diagnostics.path, diagnostics.status)

    # 初始化日志管理器（仅用于搜索/导出/统计，不创建 handler）
    global _global_log_manager
    if log_file is not None:
        _global_log_manager = LogManager(log_file)
        root_logger.info("日志管理器已初始化")
    else:
        _global_log_manager = None

    return root_logger


def cleanup_old_logs(log_dir: Optional[str] = None, days: int = 30):
    """
    清理超过指定天数的旧日志文件

    Args:
        log_dir: 日志目录
        days: 保留天数
    """
    import glob
    from datetime import timedelta

    if log_dir is None:
        from utils.file_utils import get_app_dir
        log_dir = os.path.join(get_app_dir(), 'logs')

    if not os.path.exists(log_dir):
        return

    cutoff_date = datetime.now() - timedelta(days=days)
    logger = logging.getLogger(__name__)

    for log_file in glob.glob(os.path.join(log_dir, 'app_*.log*')):
        try:
            filename = os.path.basename(log_file)
            if filename.startswith('app_') and len(filename) >= 12:
                date_str = filename[4:12]  # app_YYYYMMDD.log
                file_date = datetime.strptime(date_str, '%Y%m%d')

                if file_date < cutoff_date:
                    os.remove(log_file)
                    logger.info(f"已删除旧日志文件: {log_file}")
        except (ValueError, OSError) as e:
            logger.warning(f"清理日志文件时出错: {log_file}, {e}")


class LogManager:
    """日志文件管理工具 - 提供日志搜索、导出、统计功能

    不创建任何 handler，所有日志记录依赖 root logger 的 handler（通过 propagate=True）。
    """

    def __init__(self, log_file: str):
        self.log_file = log_file
        self.logger = logging.getLogger(__name__)

    def search_logs(
        self,
        keyword: str,
        level: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        max_results: int = 100
    ) -> List[Tuple[str, str, str, str]]:
        """
        搜索日志

        参数:
            keyword: 搜索关键词
            level: 日志级别过滤（DEBUG, INFO, WARNING, ERROR, CRITICAL）
            start_time: 开始时间（格式: YYYY-MM-DD HH:MM:SS）
            end_time: 结束时间（格式: YYYY-MM-DD HH:MM:SS）
            max_results: 最大结果数

        返回:
            List[Tuple[时间, 级别, 名称, 消息]]
        """
        results = []

        if max_results <= 0 or not os.path.exists(self.log_file):
            return results

        try:
            with open(self.log_file, 'rb') as f:
                for header, raw in _iter_records(f):
                    if header is None or not _matches(header, level, start_time, end_time):
                        continue
                    timestamp, log_level, name = (
                        field.decode('utf-8', 'replace') for field in header.groups()[:3]
                    )
                    message = raw[header.start(4):].decode('utf-8', 'replace')
                    if keyword and keyword.lower() not in message.lower():
                        continue
                    results.append((timestamp, log_level, name, message))

                    if len(results) >= max_results:
                        break

        except Exception as e:
            self.logger.error(f"搜索日志失败: {e}")

        return results

    def export_logs(
        self,
        output_file: str,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        level: Optional[str] = None
    ):
        """
        导出日志

        参数:
            output_file: 输出文件路径
            start_time: 开始时间（格式: YYYY-MM-DD HH:MM:SS）
            end_time: 结束时间（格式: YYYY-MM-DD HH:MM:SS）
            level: 日志级别过滤
        """
        try:
            if (os.path.normcase(os.path.abspath(output_file))
                    == os.path.normcase(os.path.abspath(self.log_file))
                    or (os.path.exists(output_file)
                        and os.path.samefile(self.log_file, output_file))):
                raise ValueError("导出目标不能是正在读取的日志文件")
            with open(self.log_file, 'rb') as source, open(output_file, 'wb') as target:
                if not (level or start_time or end_time):
                    shutil.copyfileobj(source, target)
                else:
                    for header, raw in _iter_records(source):
                        if header is not None and _matches(header, level, start_time, end_time):
                            target.write(raw)

            self.logger.info(f"日志已导出到: {output_file}")

        except Exception as e:
            self.logger.error(f"导出日志失败: {e}")
            raise

    def get_log_stats(self) -> dict:
        """获取日志统计信息"""
        stats = {
            'total_lines': 0,
            'by_level': {
                'DEBUG': 0,
                'INFO': 0,
                'WARNING': 0,
                'ERROR': 0,
                'CRITICAL': 0
            },
            'file_size': 0,
            'backup_files': []
        }

        try:
            if os.path.exists(self.log_file):
                stats['file_size'] = os.path.getsize(self.log_file)

                with open(self.log_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        stats['total_lines'] += 1

                        match = re.search(r'\[(\w+)\]', line)
                        if match:
                            level = match.group(1)
                            if level in stats['by_level']:
                                stats['by_level'][level] += 1

            log_dir = os.path.dirname(self.log_file)
            log_name = os.path.basename(self.log_file)

            for file in os.listdir(log_dir):
                if file.startswith(log_name) and file != log_name:
                    backup_path = os.path.join(log_dir, file)
                    stats['backup_files'].append({
                        'name': file,
                        'path': backup_path,
                        'size': os.path.getsize(backup_path)
                    })

        except Exception as e:
            self.logger.error(f"获取日志统计失败: {e}")

        return stats

    def clear_logs(self):
        """清空日志文件"""
        try:
            if os.path.exists(self.log_file):
                with open(self.log_file, 'w', encoding='utf-8') as f:
                    f.write("")
                self.logger.info("日志文件已清空")
        except Exception as e:
            self.logger.error(f"清空日志失败: {e}")
            raise

    def cleanup_old_backups(self, keep_count: int = 5):
        """清理旧的备份文件"""
        try:
            log_dir = os.path.dirname(self.log_file)
            log_name = os.path.basename(self.log_file)

            backup_files = []
            for file in os.listdir(log_dir):
                if file.startswith(log_name) and file != log_name:
                    backup_path = os.path.join(log_dir, file)
                    backup_files.append((backup_path, os.path.getmtime(backup_path)))

            backup_files.sort(key=lambda x: x[1])

            if len(backup_files) > keep_count:
                for backup_path, _ in backup_files[:-keep_count]:
                    os.remove(backup_path)
                    self.logger.info(f"已删除旧备份: {backup_path}")

        except Exception as e:
            self.logger.error(f"清理旧备份失败: {e}")


# 全局日志管理器实例
_global_log_manager: Optional[LogManager] = None


def get_log_manager(log_file: str = None) -> LogManager:
    """
    获取全局日志管理器实例

    参数:
        log_file: 日志文件路径（仅首次调用时使用）

    返回:
        LogManager 实例
    """
    global _global_log_manager

    if _global_log_manager is None:
        if log_file is None:
            raise ValueError("首次调用时必须提供 log_file 参数")

        _global_log_manager = LogManager(log_file=log_file)

    return _global_log_manager


def search_logs(
    keyword: str,
    level: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    max_results: int = 100
) -> List[Tuple[str, str, str, str]]:
    """搜索日志（便捷函数）"""
    manager = get_log_manager()
    return manager.search_logs(keyword, level, start_time, end_time, max_results)


def export_logs(
    output_file: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    level: Optional[str] = None
):
    """导出日志（便捷函数）"""
    manager = get_log_manager()
    manager.export_logs(output_file, start_time, end_time, level)


def get_log_stats() -> dict:
    """获取日志统计（便捷函数）"""
    manager = get_log_manager()
    return manager.get_log_stats()
