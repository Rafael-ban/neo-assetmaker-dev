"""
日志查看对话框 - 用于在GUI中显示操作日志
"""

import logging
from datetime import datetime
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QFileDialog, QWidget
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject
from PyQt6.QtGui import QTextCharFormat, QColor, QTextCursor


class LogSignalEmitter(QObject):
    """日志信号发射器，用于线程安全的日志传递"""
    log_received = pyqtSignal(str, str)  # (level, message)


class QLogHandler(logging.Handler):
    """
    自定义日志处理器，将logging输出重定向到LogDialog

    使用方法:
        log_dialog = LogDialog()
        handler = QLogHandler(log_dialog)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] [%(threadName)s] [%(levelname)s] %(message)s"))
        logging.getLogger().addHandler(handler)
    """

    def __init__(self, log_dialog: 'LogDialog'):
        super().__init__()
        self.log_dialog = log_dialog
        self.emitter = LogSignalEmitter()
        self.emitter.log_received.connect(self._on_log_received)

    def emit(self, record: logging.LogRecord):
        """处理日志记录"""
        try:
            msg = self.format(record)
            level = record.levelname
            # 使用信号确保线程安全
            self.emitter.log_received.emit(level, msg)
        except Exception:
            self.handleError(record)

    def _on_log_received(self, level: str, message: str):
        """在主线程中处理日志"""
        self.log_dialog.append_log(message, level)


class LogDialog(QDialog):
    """
    日志查看对话框

    用于在GUI中显示操作日志，支持实时追加、颜色区分、保存和清空功能。
    """

    # 日志级别对应的颜色
    LOG_COLORS = {
        'DEBUG': '#888888',     # 灰色
        'INFO': '#FFFFFF',      # 白色
        'WARNING': '#FFFF00',   # 黄色
        'WARN': '#FFFF00',      # 黄色 (兼容)
        'ERROR': '#FF4444',     # 红色
        'CRITICAL': '#FF0000',  # 深红色
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("日志查看器")
        self.resize(800, 600)
        self.setMinimumSize(400, 300)

        # 设置窗口标志，允许最大化和最小化
        self.setWindowFlags(
            Qt.WindowType.Dialog |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )

        self._setup_ui()
        self._apply_style()

    def _setup_ui(self):
        """设置UI布局"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 日志显示区域
        self.log_text_edit = QTextEdit()
        self.log_text_edit.setReadOnly(True)
        self.log_text_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self.log_text_edit)

        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)

        # 添加弹性空间，使按钮靠右
        button_layout.addStretch()

        # 清空按钮
        self.clear_button = QPushButton("清空日志")
        self.clear_button.setMinimumWidth(100)
        self.clear_button.clicked.connect(self.clear_log)
        button_layout.addWidget(self.clear_button)

        # 保存按钮
        self.save_button = QPushButton("保存日志")
        self.save_button.setMinimumWidth(100)
        self.save_button.clicked.connect(self.save_log)
        button_layout.addWidget(self.save_button)

        # 关闭按钮
        self.close_button = QPushButton("关闭")
        self.close_button.setMinimumWidth(80)
        self.close_button.clicked.connect(self.close)
        button_layout.addWidget(self.close_button)

        layout.addLayout(button_layout)

    def _apply_style(self):
        """应用深色主题样式"""
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
            }
            QTextEdit {
                background-color: #2d2d2d;
                color: #ffffff;
                border: 1px solid #555;
                font-family: Consolas, "Courier New", monospace;
                font-size: 12px;
                padding: 4px;
            }
            QPushButton {
                background-color: #3d3d3d;
                color: #ffffff;
                border: 1px solid #555;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #4d4d4d;
                border-color: #666;
            }
            QPushButton:pressed {
                background-color: #2d2d2d;
            }
        """)

    def append_log(self, message: str, level: str = 'INFO'):
        """
        追加日志消息

        Args:
            message: 日志消息文本
            level: 日志级别 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        """
        # 获取对应颜色
        color = self.LOG_COLORS.get(level.upper(), '#FFFFFF')

        # 创建文本格式
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))

        # 移动光标到末尾
        cursor = self.log_text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        # 插入带颜色的文本
        cursor.insertText(message + '\n', fmt)

        # 滚动到底部
        self.log_text_edit.setTextCursor(cursor)
        self.log_text_edit.ensureCursorVisible()

    def clear_log(self):
        """清空日志"""
        self.log_text_edit.clear()

    def save_log(self):
        """保存日志到文件"""
        # 生成默认文件名
        default_filename = f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存日志文件",
            default_filename,
            "文本文件 (*.txt);;日志文件 (*.log);;所有文件 (*.*)"
        )

        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(self.log_text_edit.toPlainText())
            except Exception as e:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.critical(
                    self,
                    "保存失败",
                    f"无法保存日志文件:\n{str(e)}"
                )

    def get_log_content(self) -> str:
        """获取日志内容"""
        return self.log_text_edit.toPlainText()

    def create_handler(self, formatter=None) -> QLogHandler:
        """
        创建并返回一个连接到此对话框的日志处理器

        Args:
            formatter: 可选的日志格式化器，如果不提供则使用默认格式

        Returns:
            QLogHandler: 配置好的日志处理器
        """
        handler = QLogHandler(self)
        if formatter is None:
            formatter = logging.Formatter(
                "%(asctime)s [%(name)s] [%(threadName)s] [%(levelname)s] %(message)s"
            )
        handler.setFormatter(formatter)
        return handler


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    dialog = LogDialog()
    dialog.show()

    # 测试日志输出
    dialog.append_log("2024-01-01 12:00:00 [root] [MainThread] [DEBUG] 这是调试信息", "DEBUG")
    dialog.append_log("2024-01-01 12:00:01 [root] [MainThread] [INFO] 这是普通信息", "INFO")
    dialog.append_log("2024-01-01 12:00:02 [root] [MainThread] [WARNING] 这是警告信息", "WARNING")
    dialog.append_log("2024-01-01 12:00:03 [root] [MainThread] [ERROR] 这是错误信息", "ERROR")
    dialog.append_log("2024-01-01 12:00:04 [root] [MainThread] [CRITICAL] 这是严重错误", "CRITICAL")

    # 测试与 logging 模块集成
    handler = dialog.create_handler()
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.DEBUG)

    logging.debug("通过 logging 模块输出的调试信息")
    logging.info("通过 logging 模块输出的普通信息")
    logging.warning("通过 logging 模块输出的警告信息")
    logging.error("通过 logging 模块输出的错误信息")

    sys.exit(app.exec())
