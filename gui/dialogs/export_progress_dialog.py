"""
导出进度对话框
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel
)
from PyQt6.QtCore import Qt, pyqtSignal
from qfluentwidgets import (
    PushButton, SubtitleLabel, BodyLabel, ProgressBar
)


class ExportProgressDialog(QDialog):
    """导出进度对话框"""

    export_success_signal = pyqtSignal(bool)

    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_completed = False
        self._was_successful = False
        self._cancel_requested = False
        self._setup_ui()

    def _setup_ui(self):
        """设置UI"""
        self.setWindowTitle("导出素材")
        self.setMinimumSize(400, 150)
        self.setModal(True)
        # 禁用关闭按钮
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        self.label_status = SubtitleLabel("准备导出...")
        self.label_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label_status)

        self.progress_bar = ProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.label_detail = BodyLabel("")
        self.label_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label_detail)

        self.btn_action = PushButton("取消")
        self.btn_action.clicked.connect(self._on_action_clicked)
        layout.addWidget(self.btn_action)

    def update_progress(self, value: int, message: str):
        """更新进度"""
        self.progress_bar.setValue(value)
        self.label_detail.setText(message)

    def set_completed(self, success: bool, message: str):
        """设置完成状态"""
        self._is_completed = True
        self._was_successful = success
        self.progress_bar.setValue(100 if success else self.progress_bar.value())
        self.label_status.setText("导出完成!" if success else "导出失败")
        self.label_detail.setText(message)
        self.btn_action.setText("确定")
        self.btn_action.setEnabled(True)

        if success:
            self.label_status.setStyleSheet("color: green;")
            self.export_success_signal.emit(success)
        else:
            self.label_status.setStyleSheet("color: red;")

    @property
    def was_successful(self) -> bool:
        return self._was_successful

    def _on_action_clicked(self):
        """按钮点击"""
        if self._is_completed:
            self.accept()
            return
        self._request_cancel()

    def _request_cancel(self):
        """请求取消，但在服务报告终态前继续保持模态窗口。"""
        if self._cancel_requested:
            return
        self._cancel_requested = True
        self.label_status.setText("正在取消...")
        self.btn_action.setEnabled(False)
        # DirectConnection 槽可能同步调用 set_completed；emit 后不再覆盖终态。
        self.cancel_requested.emit()

    def reject(self):
        """Esc 只能请求取消，不能让仍在收尾的导出脱离生命周期。"""
        if self._is_completed:
            super().reject()
            return
        self._request_cancel()

    def closeEvent(self, event):
        """关闭事件"""
        if self._is_completed:
            event.accept()
        else:
            event.ignore()  # 导出过程中禁止关闭
