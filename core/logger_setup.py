"""
统一日志管理模块

提供 GUI 和 CLI 两种模式的日志配置功能。
"""

import os
import sys
import logging
import traceback
from datetime import datetime
from typing import Optional, Callable

# 模块级变量，存储当前日志文件路径
_current_log_path: Optional[str] = None

# 默认配置
DEFAULT_LOG_DIR = "logs"
DEFAULT_LOG_PREFIX = "ep_assetmaker"


def _get_app_root() -> str:
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.abspath(sys.argv[0]))


def get_log_file_path() -> Optional[str]:
    """
    获取当前日志文件路径

    Returns:
        当前日志文件的完整路径，如果尚未设置则返回 None
    """
    return _current_log_path


def _create_log_file(log_dir: str, log_prefix: str) -> str:
    """
    创建日志文件并返回路径

    Args:
        log_dir: 日志目录，可以是相对路径或绝对路径
                 如果是相对路径，则相对于应用程序根目录
        log_prefix: 日志文件名前缀

    Returns:
        日志文件完整路径
    """
    global _current_log_path

    # 如果 log_dir 是相对路径，则相对于应用程序根目录
    if not os.path.isabs(log_dir):
        log_dir = os.path.join(_get_app_root(), log_dir)

    os.makedirs(log_dir, exist_ok=True)
    log_filename = f"{log_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_path = os.path.join(log_dir, log_filename)
    _current_log_path = log_path

    return log_path


def _get_formatter() -> logging.Formatter:
    """获取统一的日志格式化器"""
    return logging.Formatter(
        "%(asctime)s [%(name)s] [%(threadName)s] [%(levelname)s] %(message)s"
    )


def _cli_excepthook(exc_type, exc_value, exc_traceback, old_hook=sys.excepthook):
    """
    CLI 模式的异常钩子

    在发生未捕获异常时记录日志并等待用户确认
    """
    logging.error("程序发生了错误，以下为详细信息：")
    logging.error("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))
    logging.error("请按回车键继续....")
    input()
    sys.exit(0)


def _gui_excepthook(exc_type, exc_value, exc_traceback, old_hook=sys.excepthook):
    """
    GUI 模式的异常钩子

    在发生未捕获异常时记录日志并通过 QMessageBox 显示错误信息
    """
    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    logging.error("程序发生了错误，以下为详细信息：")
    logging.error(error_msg)

    try:
        from PyQt5.QtWidgets import QMessageBox, QApplication

        # 确保 QApplication 实例存在
        app = QApplication.instance()
        if app is not None:
            msg_box = QMessageBox()
            msg_box.setIcon(QMessageBox.Critical)
            msg_box.setWindowTitle("程序错误")
            msg_box.setText("程序发生了未处理的错误")
            msg_box.setDetailedText(error_msg)
            msg_box.exec_()
    except ImportError:
        # 如果 PyQt5 不可用，回退到控制台输出
        print(f"程序发生了错误：\n{error_msg}", file=sys.stderr)
    except Exception as e:
        # 其他错误时也回退到控制台
        print(f"程序发生了错误：\n{error_msg}", file=sys.stderr)
        print(f"显示错误对话框时出错：{e}", file=sys.stderr)


def setup_cli_logger(
    log_dir: str = DEFAULT_LOG_DIR,
    log_prefix: str = DEFAULT_LOG_PREFIX,
    log_level: int = logging.DEBUG
) -> str:
    """
    为 CLI 模式设置日志记录器

    Args:
        log_dir: 日志文件存放目录，默认为 "logs"
        log_prefix: 日志文件名前缀，默认为 "ep_assetmaker"
        log_level: 日志级别，默认为 DEBUG

    Returns:
        日志文件名（不含路径）
    """
    log_path = _create_log_file(log_dir, log_prefix)
    log_filename = os.path.basename(log_path)

    # 获取根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除现有的处理器，避免重复
    root_logger.handlers.clear()

    # 设置格式化器
    formatter = _get_formatter()

    # 添加控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 添加文件处理器
    file_handler = logging.FileHandler(log_path, encoding='utf-8')
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 设置 CLI 异常钩子
    sys.excepthook = _cli_excepthook

    return log_filename


def setup_gui_logger(
    log_dir: str = DEFAULT_LOG_DIR,
    log_prefix: str = DEFAULT_LOG_PREFIX,
    log_level: int = logging.DEBUG,
    enable_console: bool = True
) -> str:
    """
    为 GUI 模式设置日志记录器

    与 CLI 模式的区别：
    - 异常处理使用 QMessageBox 而不是 input() 阻塞
    - 可选择是否输出到控制台

    Args:
        log_dir: 日志文件存放目录，默认为 "logs"
        log_prefix: 日志文件名前缀，默认为 "ep_assetmaker"
        log_level: 日志级别，默认为 DEBUG
        enable_console: 是否启用控制台输出，默认为 True

    Returns:
        日志文件名（不含路径）
    """
    log_path = _create_log_file(log_dir, log_prefix)
    log_filename = os.path.basename(log_path)

    # 获取根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除现有的处理器，避免重复
    root_logger.handlers.clear()

    # 设置格式化器
    formatter = _get_formatter()

    # 添加控制台处理器（可选）
    if enable_console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    # 添加文件处理器
    file_handler = logging.FileHandler(log_path, encoding='utf-8')
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 设置 GUI 异常钩子（不使用 input() 阻塞）
    sys.excepthook = _gui_excepthook

    return log_filename


def add_custom_handler(handler: logging.Handler, use_default_formatter: bool = True) -> None:
    """
    添加自定义日志处理器

    用于将日志输出到自定义目标，例如 LogDialog

    Args:
        handler: 自定义的日志处理器
        use_default_formatter: 是否使用默认格式化器，默认为 True

    Example:
        # 创建自定义处理器用于 LogDialog
        class LogDialogHandler(logging.Handler):
            def __init__(self, log_dialog):
                super().__init__()
                self.log_dialog = log_dialog

            def emit(self, record):
                msg = self.format(record)
                self.log_dialog.append_log(msg)

        # 添加处理器
        handler = LogDialogHandler(my_log_dialog)
        add_custom_handler(handler)
    """
    if use_default_formatter:
        handler.setFormatter(_get_formatter())

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)


def remove_handler(handler: logging.Handler) -> None:
    """
    移除指定的日志处理器

    Args:
        handler: 要移除的日志处理器
    """
    root_logger = logging.getLogger()
    root_logger.removeHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志记录器

    Args:
        name: 日志记录器名称，通常使用 __name__

    Returns:
        日志记录器实例
    """
    return logging.getLogger(name)
