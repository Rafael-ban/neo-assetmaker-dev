"""
侧边栏面板 - OperatorPanel 和 DisplayPanel
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QGroupBox, QFileDialog, QFrame,
    QColorDialog
)
from PyQt6.QtCore import pyqtSignal, Qt, QRegularExpression
from PyQt6.QtGui import QColor, QRegularExpressionValidator, QPalette


class ColorPreviewWidget(QFrame):
    """颜色预览方块"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Plain)
        self._color = QColor(255, 255, 255, 255)
        self._update_color()

    def set_color(self, color: QColor):
        self._color = color
        self._update_color()

    def _update_color(self):
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, self._color)
        self.setPalette(palette)
        self.setAutoFillBackground(True)


class FileSelector(QWidget):
    """文件选择器"""

    file_selected = pyqtSignal(str)

    def __init__(self, file_filter: str = "All Files (*.*)", parent=None):
        super().__init__(parent)
        self._file_filter = file_filter

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._path_edit = QLineEdit()
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText("未选择文件")
        layout.addWidget(self._path_edit, 1)

        self._browse_btn = QPushButton("浏览...")
        self._browse_btn.setFixedWidth(80)
        self._browse_btn.clicked.connect(self._on_browse)
        layout.addWidget(self._browse_btn)

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", self._file_filter)
        if path:
            self._path_edit.setText(path)
            self.file_selected.emit(path)

    def get_path(self) -> str:
        return self._path_edit.text()

    def set_enabled(self, enabled: bool):
        self._path_edit.setEnabled(enabled)
        self._browse_btn.setEnabled(enabled)


class OptionalFileSelector(QWidget):
    """带复选框的可选文件选择器"""

    file_selected = pyqtSignal(str)

    def __init__(self, label: str, file_filter: str, parent=None):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._checkbox = QCheckBox(label)
        self._checkbox.setFixedWidth(150)
        self._checkbox.stateChanged.connect(self._on_checkbox_changed)
        layout.addWidget(self._checkbox)

        self._file_selector = FileSelector(file_filter)
        self._file_selector.set_enabled(False)
        self._file_selector.file_selected.connect(self.file_selected.emit)
        layout.addWidget(self._file_selector, 1)

    def _on_checkbox_changed(self, state):
        self._file_selector.set_enabled(state == Qt.CheckState.Checked.value)

    def is_enabled(self) -> bool:
        return self._checkbox.isChecked()

    def get_path(self) -> str:
        return self._file_selector.get_path() if self.is_enabled() else ""


class OperatorPanel(QWidget):
    """运营员素材制作面板"""

    name_changed = pyqtSignal(str)
    loop_video_selected = pyqtSignal(str)
    intro_video_selected = pyqtSignal(str)
    logo_image_selected = pyqtSignal(str)
    overlay_image_selected = pyqtSignal(str)
    background_color_changed = pyqtSignal(int)
    export_requested = pyqtSignal()
    view_log_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)

        # 运营员名称
        name_group = QGroupBox("运营员名称")
        name_layout = QFormLayout(name_group)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("只能包含英文和数字")
        self._name_edit.setValidator(QRegularExpressionValidator(QRegularExpression("^[A-Za-z0-9]*$")))
        self._name_edit.textChanged.connect(self.name_changed.emit)
        name_layout.addRow("名称:", self._name_edit)
        main_layout.addWidget(name_group)

        # 视频文件
        video_group = QGroupBox("视频文件")
        video_layout = QVBoxLayout(video_group)

        loop_layout = QHBoxLayout()
        loop_layout.addWidget(QLabel("循环待机视频:"))
        self._loop_video = FileSelector("视频文件 (*.mp4 *.mkv *.avi)")
        self._loop_video.file_selected.connect(self.loop_video_selected.emit)
        loop_layout.addWidget(self._loop_video, 1)
        video_layout.addLayout(loop_layout)

        self._intro_video = OptionalFileSelector("入场视频(可选)", "视频文件 (*.mp4 *.mkv *.avi)")
        self._intro_video.file_selected.connect(self.intro_video_selected.emit)
        video_layout.addWidget(self._intro_video)

        main_layout.addWidget(video_group)

        # 图片文件
        image_group = QGroupBox("图片文件")
        image_layout = QVBoxLayout(image_group)

        self._logo_image = OptionalFileSelector("Logo图片(可选)", "图片文件 (*.png *.jpg *.jpeg)")
        self._logo_image.file_selected.connect(self.logo_image_selected.emit)
        image_layout.addWidget(self._logo_image)

        self._overlay_image = OptionalFileSelector("信息/UI图片(可选)", "图片文件 (*.png *.jpg *.jpeg)")
        self._overlay_image.file_selected.connect(self.overlay_image_selected.emit)
        image_layout.addWidget(self._overlay_image)

        main_layout.addWidget(image_group)

        # 背景颜色
        color_group = QGroupBox("背景颜色")
        color_layout = QHBoxLayout(color_group)
        color_layout.addWidget(QLabel("入场背景色:"))

        self._current_color = QColor(255, 255, 255, 255)

        self._color_preview = ColorPreviewWidget()
        self._color_preview.setFixedSize(32, 32)
        self._color_preview.setCursor(Qt.CursorShape.PointingHandCursor)
        color_layout.addWidget(self._color_preview)

        self._color_btn = QPushButton("选择颜色...")
        self._color_btn.clicked.connect(self._on_pick_color)
        color_layout.addWidget(self._color_btn)

        self._color_label = QLabel("#FFFFFFFF")
        self._color_label.setStyleSheet("color: #888; font-family: monospace;")
        color_layout.addWidget(self._color_label)

        color_layout.addStretch()

        main_layout.addWidget(color_group)

        # 裁剪参数
        crop_group = QGroupBox("裁剪参数 (只读)")
        crop_layout = QGridLayout(crop_group)

        crop_layout.addWidget(QLabel("裁剪框:"), 0, 0)
        self._crop_x = QLineEdit("0")
        self._crop_x.setReadOnly(True)
        self._crop_x.setFixedWidth(50)
        crop_layout.addWidget(QLabel("X:"), 0, 1)
        crop_layout.addWidget(self._crop_x, 0, 2)

        self._crop_y = QLineEdit("0")
        self._crop_y.setReadOnly(True)
        self._crop_y.setFixedWidth(50)
        crop_layout.addWidget(QLabel("Y:"), 0, 3)
        crop_layout.addWidget(self._crop_y, 0, 4)

        self._crop_w = QLineEdit("0")
        self._crop_w.setReadOnly(True)
        self._crop_w.setFixedWidth(50)
        crop_layout.addWidget(QLabel("W:"), 0, 5)
        crop_layout.addWidget(self._crop_w, 0, 6)

        self._crop_h = QLineEdit("0")
        self._crop_h.setReadOnly(True)
        self._crop_h.setFixedWidth(50)
        crop_layout.addWidget(QLabel("H:"), 0, 7)
        crop_layout.addWidget(self._crop_h, 0, 8)

        crop_layout.addWidget(QLabel("时间段:"), 1, 0)
        self._start_frame = QLineEdit("0")
        self._start_frame.setReadOnly(True)
        self._start_frame.setFixedWidth(70)
        crop_layout.addWidget(QLabel("开始帧:"), 1, 1, 1, 2)
        crop_layout.addWidget(self._start_frame, 1, 3, 1, 2)

        self._end_frame = QLineEdit("0")
        self._end_frame.setReadOnly(True)
        self._end_frame.setFixedWidth(70)
        crop_layout.addWidget(QLabel("结束帧:"), 1, 5, 1, 2)
        crop_layout.addWidget(self._end_frame, 1, 7, 1, 2)

        main_layout.addWidget(crop_group)

        # 按钮
        button_layout = QHBoxLayout()

        self._export_btn = QPushButton("导出素材")
        self._export_btn.setStyleSheet("""
            QPushButton { background-color: #0078d4; color: white; padding: 8px 16px; font-weight: bold; border-radius: 4px; }
            QPushButton:hover { background-color: #106ebe; }
        """)
        self._export_btn.clicked.connect(self.export_requested.emit)
        button_layout.addWidget(self._export_btn)

        self._log_btn = QPushButton("查看日志")
        self._log_btn.clicked.connect(self.view_log_requested.emit)
        button_layout.addWidget(self._log_btn)

        button_layout.addStretch()
        main_layout.addLayout(button_layout)
        main_layout.addStretch()

    def _on_pick_color(self):
        """打开颜色选择器"""
        color = QColorDialog.getColor(
            self._current_color,
            self,
            "选择背景颜色",
            QColorDialog.ColorDialogOption.ShowAlphaChannel
        )
        if color.isValid():
            self._current_color = color
            self._color_preview.set_color(color)
            # 更新显示标签
            argb = (color.alpha() << 24) | (color.red() << 16) | (color.green() << 8) | color.blue()
            self._color_label.setText(f"#{argb:08X}")
            self.background_color_changed.emit(argb)

    def update_crop_info(self, x: int, y: int, w: int, h: int):
        self._crop_x.setText(str(x))
        self._crop_y.setText(str(y))
        self._crop_w.setText(str(w))
        self._crop_h.setText(str(h))

    def update_time_range(self, start: int, end: int):
        self._start_frame.setText(str(start))
        self._end_frame.setText(str(end))

    def get_operator_name(self) -> str:
        return self._name_edit.text()

    def get_background_color(self) -> int:
        """获取背景颜色 (ARGB格式)"""
        c = self._current_color
        return (c.alpha() << 24) | (c.red() << 16) | (c.green() << 8) | c.blue()

    def get_loop_video_path(self) -> str:
        """获取循环待机视频路径"""
        return self._loop_video.get_path()

    def get_intro_video_path(self) -> str:
        """获取入场视频路径 (可选)"""
        return self._intro_video.get_path()

    def get_logo_image_path(self) -> str:
        """获取Logo图片路径 (可选)"""
        return self._logo_image.get_path()

    def get_overlay_image_path(self) -> str:
        """获取Overlay图片路径 (可选)"""
        return self._overlay_image.get_path()


class DisplayPanel(QWidget):
    """待机屏图制作面板"""

    image_selected = pyqtSignal(str)
    export_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)

        # 待机屏名称
        name_group = QGroupBox("待机屏名称")
        name_layout = QFormLayout(name_group)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("只能包含英文和数字")
        self._name_edit.setValidator(QRegularExpressionValidator(QRegularExpression("^[A-Za-z0-9]*$")))
        name_layout.addRow("名称:", self._name_edit)
        main_layout.addWidget(name_group)

        # 图片文件
        image_group = QGroupBox("图片文件")
        image_layout = QVBoxLayout(image_group)

        file_layout = QHBoxLayout()
        file_layout.addWidget(QLabel("图片文件:"))

        self._image_selector = FileSelector("图片文件 (*.png *.jpg *.jpeg *.bmp)")
        self._image_selector.file_selected.connect(self._on_image_selected)
        file_layout.addWidget(self._image_selector, 1)
        image_layout.addLayout(file_layout)

        main_layout.addWidget(image_group)

        # 图片信息
        info_group = QGroupBox("图片信息")
        info_layout = QFormLayout(info_group)

        self._size_label = QLabel("未加载")
        info_layout.addRow("尺寸:", self._size_label)

        self._alpha_label = QLabel("未加载")
        info_layout.addRow("Alpha通道:", self._alpha_label)

        main_layout.addWidget(info_group)

        # 导出按钮
        button_layout = QHBoxLayout()

        self._export_btn = QPushButton("导出待机图")
        self._export_btn.setStyleSheet("""
            QPushButton { background-color: #0078d4; color: white; padding: 8px 16px; font-weight: bold; border-radius: 4px; }
            QPushButton:hover { background-color: #106ebe; }
        """)
        self._export_btn.clicked.connect(self.export_requested.emit)
        button_layout.addWidget(self._export_btn)

        button_layout.addStretch()
        main_layout.addLayout(button_layout)
        main_layout.addStretch()

    def _on_image_selected(self, path: str):
        self.image_selected.emit(path)
        self._load_image_info(path)

    def _load_image_info(self, path: str):
        try:
            from PyQt6.QtGui import QImage
            image = QImage(path)
            if not image.isNull():
                self._size_label.setText(f"{image.width()} x {image.height()}")
                self._alpha_label.setText("是" if image.hasAlphaChannel() else "否")
            else:
                self._size_label.setText("加载失败")
                self._alpha_label.setText("N/A")
        except Exception:
            self._size_label.setText("错误")
            self._alpha_label.setText("N/A")

    def get_display_name(self) -> str:
        """获取待机屏名称"""
        return self._name_edit.text()

    def get_image_path(self) -> str:
        return self._image_selector.get_path()

    def update_image_info(self, width: int, height: int, has_alpha: bool):
        self._size_label.setText(f"{width} x {height}")
        self._alpha_label.setText("是" if has_alpha else "否")
