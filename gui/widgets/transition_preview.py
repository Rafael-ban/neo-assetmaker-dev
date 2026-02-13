"""
过渡图片预览组件 - 左右并排显示进入过渡和循环过渡图片
"""
import logging
from typing import Optional

from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QSizePolicy
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap

logger = logging.getLogger(__name__)


class TransitionPreviewWidget(QWidget):
    """过渡图片预览组件，左右并排显示进入过渡和循环过渡图片"""

    def __init__(self, parent=None):
        super().__init__(parent)

        # 存储原始 QPixmap（用于 resizeEvent 时重新缩放）
        self._pixmap_in: Optional[QPixmap] = None
        self._pixmap_loop: Optional[QPixmap] = None

        self._setup_ui()

    def _setup_ui(self):
        """设置UI"""
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(10)

        # 左侧：进入过渡
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(5)

        self.label_in_title = QLabel("进入过渡")
        self.label_in_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_in_title.setStyleSheet("color: #ccc; font-size: 13px; font-weight: bold;")
        left_layout.addWidget(self.label_in_title)

        self.label_in_image = QLabel("未选择图片")
        self.label_in_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_in_image.setStyleSheet("background-color: #1a1a1a; border: 1px solid #333;")
        self.label_in_image.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        left_layout.addWidget(self.label_in_image, stretch=1)

        main_layout.addWidget(left_widget, stretch=1)

        # 右侧：循环过渡
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(5)

        self.label_loop_title = QLabel("循环过渡")
        self.label_loop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_loop_title.setStyleSheet("color: #ccc; font-size: 13px; font-weight: bold;")
        right_layout.addWidget(self.label_loop_title)

        self.label_loop_image = QLabel("未选择图片")
        self.label_loop_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_loop_image.setStyleSheet("background-color: #1a1a1a; border: 1px solid #333;")
        self.label_loop_image.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        right_layout.addWidget(self.label_loop_image, stretch=1)

        main_layout.addWidget(right_widget, stretch=1)

    def load_image(self, trans_type: str, image_path: str):
        """加载并显示过渡图片

        Args:
            trans_type: "in" 或 "loop"
            image_path: 图片文件的绝对路径
        """
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            logger.warning(f"无法加载过渡图片: {image_path}")
            return

        if trans_type == "in":
            self._pixmap_in = pixmap
            self._update_label(self.label_in_image, pixmap)
            logger.info(f"已加载进入过渡图片: {image_path}")
        else:
            self._pixmap_loop = pixmap
            self._update_label(self.label_loop_image, pixmap)
            logger.info(f"已加载循环过渡图片: {image_path}")

    def clear_image(self, trans_type: str):
        """清除过渡图片

        Args:
            trans_type: "in" 或 "loop"
        """
        if trans_type == "in":
            self._pixmap_in = None
            self.label_in_image.clear()
            self.label_in_image.setText("未选择图片")
        else:
            self._pixmap_loop = None
            self.label_loop_image.clear()
            self.label_loop_image.setText("未选择图片")

    def _update_label(self, label: QLabel, pixmap: QPixmap):
        """将 pixmap 等比缩放后设置到 label"""
        label_size = label.size()
        scaled = pixmap.scaled(
            label_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        label.setPixmap(scaled)

    def resizeEvent(self, event):
        """窗口大小变化时重新缩放图片"""
        super().resizeEvent(event)
        if self._pixmap_in is not None:
            self._update_label(self.label_in_image, self._pixmap_in)
        if self._pixmap_loop is not None:
            self._update_label(self.label_loop_image, self._pixmap_loop)
