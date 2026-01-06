"""
明日方舟通行证 资源转换器 - 主窗口框架
"""

import sys
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QTabWidget, QStatusBar, QLabel, QFrame, QFileDialog, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction

# 尺寸配置
SCREEN_WIDTH, SCREEN_HEIGHT = 360, 640
LOGO_WIDTH, LOGO_HEIGHT = 256, 256
VIDEO_WIDTH, VIDEO_HEIGHT = 384, 640
DISPLAY_WIDTH, DISPLAY_HEIGHT = 360, 640


class PreviewPlaceholder(QFrame):
    """预览区域占位组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Sunken)
        self.setStyleSheet("background-color: #2d2d2d; border: 2px solid #555;")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.placeholder_label = QLabel("预览区域")
        self.placeholder_label.setStyleSheet("color: #888; font-size: 18px;")
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.placeholder_label)

    def set_placeholder_text(self, text: str):
        self.placeholder_label.setText(text)


class MainWindow(QMainWindow):
    """主窗口类"""

    video_opened = pyqtSignal(str)
    image_opened = pyqtSignal(str)
    export_requested = pyqtSignal()
    mode_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("明日方舟通行证 资源转换器")
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)

        self.current_video_path = None
        self.current_image_path = None

        self._setup_ui()
        self._setup_menu()
        self._setup_statusbar()
        self._setup_connections()

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧面板
        self.left_panel = QWidget()
        self.left_panel.setMinimumWidth(350)
        self.left_panel.setMaximumWidth(500)
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #555; background-color: #2d2d2d; }
            QTabBar::tab { background-color: #3d3d3d; color: #aaa; padding: 10px 20px; }
            QTabBar::tab:selected { background-color: #2d2d2d; color: #fff; }
        """)

        # 占位标签页
        self.operator_tab = QWidget()
        self.standby_tab = QWidget()
        self.tab_widget.addTab(self.operator_tab, "运营员素材制作")
        self.tab_widget.addTab(self.standby_tab, "待机屏图制作")

        left_layout.addWidget(self.tab_widget)

        # 右侧预览区
        self.preview_container = QWidget()
        preview_layout = QVBoxLayout(self.preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)

        self.preview_placeholder = PreviewPlaceholder()
        preview_layout.addWidget(self.preview_placeholder)

        self.splitter.addWidget(self.left_panel)
        self.splitter.addWidget(self.preview_container)
        self.splitter.setSizes([400, 800])

        main_layout.addWidget(self.splitter)

    def _setup_menu(self):
        menubar = self.menuBar()
        menubar.setStyleSheet("QMenuBar { background-color: #2d2d2d; color: #fff; }")

        file_menu = menubar.addMenu("文件(&F)")

        self.action_open_video = QAction("打开视频(&V)...", self)
        self.action_open_video.setShortcut("Ctrl+O")
        file_menu.addAction(self.action_open_video)

        self.action_open_image = QAction("打开图片(&I)...", self)
        self.action_open_image.setShortcut("Ctrl+Shift+O")
        file_menu.addAction(self.action_open_image)

        file_menu.addSeparator()

        self.action_export = QAction("导出(&E)...", self)
        self.action_export.setShortcut("Ctrl+S")
        file_menu.addAction(self.action_export)

        file_menu.addSeparator()

        self.action_exit = QAction("退出(&X)", self)
        file_menu.addAction(self.action_exit)

        help_menu = menubar.addMenu("帮助(&H)")
        self.action_about = QAction("关于(&A)...", self)
        help_menu.addAction(self.action_about)

    def _setup_statusbar(self):
        self.statusbar = QStatusBar()
        self.statusbar.setStyleSheet("QStatusBar { background-color: #2d2d2d; color: #aaa; }")
        self.setStatusBar(self.statusbar)

        self.status_label = QLabel("就绪")
        self.statusbar.addWidget(self.status_label)

        self.mode_label = QLabel("当前模式: 运营员素材制作")
        self.statusbar.addPermanentWidget(self.mode_label)

    def _setup_connections(self):
        self.action_open_video.triggered.connect(self._on_open_video)
        self.action_open_image.triggered.connect(self._on_open_image)
        self.action_export.triggered.connect(self.export_requested.emit)
        self.action_exit.triggered.connect(self.close)
        self.action_about.triggered.connect(self._on_about)
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

    def _on_open_video(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "", "视频文件 (*.mp4 *.mkv *.avi *.mov);;所有文件 (*.*)"
        )
        if file_path:
            self.current_video_path = file_path
            self.video_opened.emit(file_path)
            self.status_label.setText(f"已打开视频: {file_path}")

    def _on_open_image(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择图片文件", "", "图片文件 (*.png *.jpg *.jpeg *.bmp);;所有文件 (*.*)"
        )
        if file_path:
            self.current_image_path = file_path
            self.image_opened.emit(file_path)
            self.status_label.setText(f"已打开图片: {file_path}")

    def _on_about(self):
        QMessageBox.about(self, "关于", "明日方舟通行证 资源转换器\n\nBy Rafael_ban")

    def _on_tab_changed(self, index: int):
        self.mode_changed.emit(index)
        modes = ["运营员素材制作", "待机屏图制作"]
        self.mode_label.setText(f"当前模式: {modes[index]}")

    def set_operator_panel(self, panel):
        """设置运营员面板"""
        if self.operator_tab.layout():
            QWidget().setLayout(self.operator_tab.layout())
        layout = QVBoxLayout(self.operator_tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(panel)

    def set_display_panel(self, panel):
        """设置待机屏图面板"""
        if self.standby_tab.layout():
            QWidget().setLayout(self.standby_tab.layout())
        layout = QVBoxLayout(self.standby_tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(panel)

    def set_preview_widget(self, widget):
        """设置预览组件"""
        layout = self.preview_container.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        layout.addWidget(widget)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
