# 超 级 多 的 屎 山 ciallo~ 啊哈哈.....
# 我就是那个大笨蛋....啊哈哈哈....
# 呜呜呜....果然还是被抛弃了嘛....啊哈哈哈....
# 最喜欢弦音姐姐啦~
# （以上为自动生成的无意义文本，请忽略）
"""
主窗口 - 三栏布局
"""
from core.error_handler import ErrorHandler, show_error
from core.crash_recovery_service import CrashRecoveryService
from core.auto_save_service import AutoSaveService, AutoSaveConfig
from gui.widgets.json_preview import JsonPreviewWidget
from gui.widgets.timeline import TimelineWidget
from gui.widgets.transition_preview import TransitionPreviewWidget
from gui.widgets.video_preview import PreviewRenderContext, VideoPreviewWidget
from gui.widgets.vs_script_panel import VSScriptPanel
from gui.widgets.config_panel import ConfigPanel
from config.constants import (
    APP_NAME, APP_VERSION, APP_VERSION_LABEL, get_resolution_spec,
    SUPPORTED_VIDEO_FORMATS, SUPPORTED_IMAGE_FORMATS
)
from gui.widgets.drop_overlay import DropOverlayWidget
from gui.styles import COLOR_TEXT_PRIMARY, COLOR_BG_ELEVATED, COLOR_BORDER, hex_with_alpha
from config.epconfig import EPConfig, EditorTrackState, VSScriptState, CONFIG_FILENAME
from config.vs_runtime import (
    default_vs_runtime_user_path,
    load_vs_runtime,
    save_vs_runtime_override,
)
from core.vs_runtime.script_header import parse_script_header
from core.vs_runtime.session import (
    ScriptSelection,
    compute_script_bundle_hash,
    script_bundle_code_files,
)
from core.vs_runtime.trust import (
    ProjectTrustStore,
    ScriptReference,
    ScriptTrustError,
    resolve_script_reference,
)
from qfluentwidgets import (
    PushButton, PrimaryPushButton, ToolButton, TransparentToolButton,
    TabWidget, SegmentedWidget,
    SubtitleLabel, StrongBodyLabel, BodyLabel, CaptionLabel,
    CardWidget, HyperlinkButton,
    ComboBox, SpinBox,
    DoubleSpinBox, CheckBox, LineEdit,
    ScrollArea, FluentIcon,
    setCustomStyleSheet, isDarkTheme, setThemeColor, themeColor
)
from PyQt6.QtGui import QAction, QDesktopServices, QKeySequence, QIcon, QShortcut
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QMenuBar, QMenu, QStatusBar,
    QFileDialog, QMessageBox, QLabel, QScrollArea,
    QCheckBox, QComboBox, QDoubleSpinBox,
    QSpinBox, QLineEdit, QTabWidget, QDialog, QApplication
)
from PyQt6.QtCore import Qt, QSettings, QTimer, QUrl
import os
import sys
import logging
import tempfile
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self, parent=None):
        super().__init__(parent)

        from utils.file_utils import get_app_dir
        self._app_dir = get_app_dir()

        self._config: Optional[EPConfig] = None
        self._project_path: str = ""
        self._base_dir: str = ""
        self._is_modified: bool = False
        self._temp_dir: Optional[str] = None  # 临时项目目录路径，None 表示非临时项目
        self._initializing: bool = True  # 初始化期间防护标志
        self._script_ready = True
        self._script_block_reason = ""
        self._active_script_path = ""

        # 为每个视频存储独立的入点/出点
        self._loop_in_out: tuple[int, int] = (0, 0)   # 循环视频的(入点, 出点)
        self._intro_in_out: tuple[int, int] = (0, 0)  # 入场视频的(入点, 出点)
        # 时间轴当前连接的预览器
        self._timeline_preview: Optional['VideoPreviewWidget'] = None

        self._auto_save_service = AutoSaveService()
        self._crash_recovery_service = CrashRecoveryService()
        # Pass the app dir directly; CrashRecoveryService.initialize() appends the single
        # ".recovery" itself (passing an already-suffixed path produced .recovery\.recovery).
        self._crash_recovery_service.initialize(self._app_dir)
        # Register every autosave with crash-recovery. AutoSaveService writes backups to
        # <project>/.autosave (or the temp dir) while CrashRecoveryService only scans
        # <app>/.recovery — without this pointer nothing is recoverable after a crash.
        self._auto_save_service.saved.connect(self._on_autosave_saved)

        self._error_handler = ErrorHandler()
        self._error_handler.error_occurred.connect(self._on_error_occurred)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_runtime_resources)

        self._undo_stack = []
        self._redo_stack = []
        self._max_history = 50  # 最大历史记录数
        # Undo baseline = last committed config dict. Edits are coalesced into
        # one undo step per ~800ms burst via a debounce timer (avoids one step
        # per keystroke). Snapshots include the editor section (crop/rotation/
        # trim), so those are undoable too.
        self._undo_baseline: dict = {}
        self._undo_timer = QTimer(self)
        self._undo_timer.setSingleShot(True)
        self._undo_timer.timeout.connect(self._commit_undo_snapshot)
        self._applying_undo = False

        self._recent_files = []
        self._max_recent_files = 10  # 最多保留10个最近文件

        # 页面切换时记录正在播放的视频预览器，以便返回素材页时恢复
        self._videos_were_playing: list = []

        # 导出运行期间独占预览暂停状态。它不能与页面切换共用
        # _videos_were_playing：导出完成前服务及其 worker 必须继续存活。
        self._export_in_progress = False
        self._export_call_active = False
        self._export_paused_previews: list = []

        # 异步加载失败聚合(短窗口内多个预览失败只弹一个警告框)
        self._pending_load_failures: list = []
        self._load_failure_flush_scheduled = False

        # editor-state(裁剪/旋转/入出点)同步与恢复:
        # - 打开项目时加载过程会先 emit 默认裁剪框,期间挂起同步防污染/防误标脏;
        # - 加载完成后按打开前的拷贝恢复到预览/时间轴。
        self._editor_sync_suspended: set = set()
        self._pending_editor_restore: dict = {}
        self._restoring_editor_state = False

        self._setup_ui()
        self._setup_menu()
        self._setup_shortcuts()
        self._setup_icon()
        self._connect_signals()
        self._load_settings()
        self._load_user_settings()

        self._update_title()
        self._check_first_run()

        # 根据用户设置决定是否自动创建临时项目
        auto_create = True
        try:
            import json
            config_dir = os.path.join(self._app_dir, "config")
            config_file = os.path.join(config_dir, "user_settings.json")
            if os.path.exists(config_file):
                with open(config_file, "r", encoding="utf-8") as f:
                    user_settings = json.load(f)
                    auto_create = user_settings.get(
                        'auto_create_temp_project', True)
        except Exception:
            pass

        if self._config is None and auto_create:
            self._init_temp_project()

        QTimer.singleShot(2000, self._check_update_on_startup)
        QTimer.singleShot(3000, self._check_crash_recovery)

        logger.info("主窗口初始化完成")
        self._initializing = False  # 初始化完成

    def _setup_icon(self):
        """设置窗口图标"""
        icon_path = os.path.join(
            self._app_dir,
            'resources',
            'icons',
            'favicon.ico')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
            logger.debug(f"已加载窗口图标: {icon_path}")
        else:
            logger.warning(f"窗口图标文件不存在: {icon_path}")

    def _setup_ui(self):
        """设置UI"""
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION_LABEL}")
        self.setMinimumSize(1200, 900)  # 增大最小高度，确保内容完全显示
        self.menuBar().setVisible(False)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint)
        # 启用透明背景 — CSS border-radius 仅影响绘制不裁剪窗口形状，
        # 必须配合 WA_TranslucentBackground + paintEvent 实现真正的圆角裁剪
        # https://doc.qt.io/qt-6/qt.html#WidgetAttribute-enum
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._light_bg_color = "#f0f4f9"
        self._dark_bg_color = "#202020"
        self._bg_color = self._dark_bg_color if isDarkTheme() else self._light_bg_color
        self._bg_pixmap = None
        self._corner_radius = 16.0
        self._is_dragging = False
        self._drag_start_pos = None
        self._is_resizing = False
        self._resize_direction = None
        self._resize_start_pos = None
        self._resize_start_geometry = None
        self._resize_margin = 8

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # === 顶部标题栏 ===
        self.header_bar = QWidget()
        self.header_bar.setObjectName("header_bar")
        _header_default_qss = "#header_bar { background-color: rgba(40, 40, 40, 0.7); color: white; border-top-left-radius: 16px; border-top-right-radius: 16px; } #header_bar > QLabel { font-weight: bold; font-size: 16px; }"
        setCustomStyleSheet(
            self.header_bar, _header_default_qss, _header_default_qss)
        header_layout = QHBoxLayout(self.header_bar)
        header_layout.setContentsMargins(20, 8, 20, 8)
        header_layout.setSpacing(24)

        self.logo_label = QLabel("PRTS")
        self.logo_label.setObjectName("logo_label")
        _logo_qss = "#logo_label { background-color: white; color: #ff6b8b; border-radius: 16px; padding: 8px 12px; font-size: 14px; font-weight: bold; }"
        setCustomStyleSheet(self.logo_label, _logo_qss, _logo_qss)
        header_layout.addWidget(self.logo_label)

        title_label = QLabel(APP_NAME)
        setCustomStyleSheet(title_label, "font-size: 16px; font-weight: bold;",
                            "font-size: 16px; font-weight: bold;")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        control_layout = QHBoxLayout()
        control_layout.setSpacing(5)

        # 窗口控制按钮 — 文本 PushButton，始终在主题色 header 上
        _ctrl_btn_qss = ("PushButton { background-color: transparent; color: white; "
                         "border: none; border-radius: 18px; font-size: 20px; font-weight: bold; "
                         "padding: 0; margin: 0; } "
                         "PushButton:hover { background-color: rgba(255, 255, 255, 76); } "
                         "PushButton:pressed { background-color: rgba(255, 255, 255, 102); }")
        _max_btn_qss = ("PushButton { background-color: transparent; color: white; "
                        "border: none; border-radius: 18px; font-size: 16px; font-weight: bold; "
                        "padding: 0; margin: 0; } "
                        "PushButton:hover { background-color: rgba(255, 255, 255, 76); } "
                        "PushButton:pressed { background-color: rgba(255, 255, 255, 102); }")
        _close_btn_qss = ("PushButton { background-color: transparent; color: white; "
                          "border: none; border-radius: 18px; font-size: 20px; font-weight: bold; "
                          "padding: 0; margin: 0; } "
                          "PushButton:hover { background-color: rgba(255, 0, 0, 102); } "
                          "PushButton:pressed { background-color: rgba(255, 0, 0, 128); }")

        self.btn_minimize = PushButton("−")
        self.btn_minimize.setFixedSize(36, 36)
        setCustomStyleSheet(self.btn_minimize, _ctrl_btn_qss, _ctrl_btn_qss)
        self.btn_minimize.clicked.connect(self.showMinimized)
        control_layout.addWidget(self.btn_minimize)

        self.btn_maximize = PushButton("□")
        self.btn_maximize.setFixedSize(36, 36)
        setCustomStyleSheet(self.btn_maximize, _max_btn_qss, _max_btn_qss)
        self.btn_maximize.clicked.connect(self._on_maximize)
        control_layout.addWidget(self.btn_maximize)

        self.btn_close = PushButton("×")
        self.btn_close.setFixedSize(36, 36)
        setCustomStyleSheet(self.btn_close, _close_btn_qss, _close_btn_qss)
        self.btn_close.clicked.connect(self.close)
        control_layout.addWidget(self.btn_close)

        header_layout.addLayout(control_layout)

        self.header_bar.setMouseTracking(True)
        self.header_bar.mousePressEvent = self._on_header_mouse_press
        self.header_bar.mouseMoveEvent = self._on_header_mouse_move
        self.header_bar.mouseReleaseEvent = self._on_header_mouse_release

        main_layout.addWidget(self.header_bar)

        content_container = QWidget()
        content_layout = QHBoxLayout(content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # === 左侧: 侧边栏导航 ===
        self.sidebar = QWidget()
        self.sidebar.setObjectName("sidebar")
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)
        sidebar_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.btn_firmware = ToolButton(FluentIcon.ROBOT, self.sidebar)
        self.btn_firmware.setCheckable(True)
        self.btn_firmware.setToolTip("固件烧录")
        self.btn_firmware.setFixedSize(50, 50)

        self.btn_material = ToolButton(FluentIcon.PALETTE, self.sidebar)
        self.btn_material.setCheckable(True)
        self.btn_material.setChecked(True)
        self.btn_material.setToolTip("素材制作")
        self.btn_material.setFixedSize(50, 50)

        self.btn_forum = ToolButton(FluentIcon.PEOPLE if hasattr(
            FluentIcon, 'PEOPLE') else FluentIcon.CHAT, self.sidebar)
        self.btn_forum.setCheckable(True)
        self.btn_forum.setToolTip("素材论坛")
        self.btn_forum.setFixedSize(50, 50)

        self.btn_about = ToolButton(FluentIcon.INFO, self.sidebar)
        self.btn_about.setCheckable(True)
        self.btn_about.setToolTip("项目介绍")
        self.btn_about.setFixedSize(50, 50)

        self.btn_remote = ToolButton(FluentIcon.WIFI, self.sidebar)
        self.btn_remote.setCheckable(True)
        self.btn_remote.setToolTip("远程管理")
        self.btn_remote.setFixedSize(50, 50)

        self.btn_usbControl = ToolButton(FluentIcon.IOT, self.sidebar)
        self.btn_usbControl.setCheckable(True)
        self.btn_usbControl.setToolTip("远程管理")
        self.btn_usbControl.setFixedSize(50, 50)

        buttons_container = QWidget()
        buttons_layout = QVBoxLayout(buttons_container)
        buttons_layout.setContentsMargins(0, 20, 0, 0)
        buttons_layout.setSpacing(15)
        buttons_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        buttons_layout.addWidget(self.btn_firmware)
        buttons_layout.addWidget(self.btn_material)
        buttons_layout.addWidget(self.btn_forum)
        buttons_layout.addWidget(self.btn_about)
        buttons_layout.addWidget(self.btn_remote)
        buttons_layout.addWidget(self.btn_usbControl)

        sidebar_layout.addWidget(buttons_container)
        sidebar_layout.addStretch()

        self.btn_settings = ToolButton(FluentIcon.SETTING, self.sidebar)
        self.btn_settings.setCheckable(True)
        self.btn_settings.setToolTip("设置")
        self.btn_settings.setFixedSize(50, 50)
        sidebar_layout.addWidget(
            self.btn_settings,
            alignment=Qt.AlignmentFlag.AlignCenter)
        sidebar_layout.addSpacing(20)

        self.sidebar.setFixedWidth(80)
        content_layout.addWidget(self.sidebar)

        # === 右侧: 内容区域 ===
        self.content_stack = QWidget()
        self.content_stack.setObjectName("content_stack")
        self.content_layout = QVBoxLayout(self.content_stack)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # === 左侧: 配置面板 ===
        from gui.widgets.basic_config_panel import BasicConfigPanel

        self.config_container = QWidget()
        self.config_layout = QVBoxLayout(self.config_container)

        from qfluentwidgets import (
            ComboBox as FluentComboBox,
            DropDownPushButton, RoundMenu, Action
        )

        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(10, 10, 10, 4)
        toolbar_layout.setSpacing(8)

        self.btn_operations = DropDownPushButton(FluentIcon.MENU, "操作")
        self.btn_operations.setFixedHeight(34)

        operations_menu = RoundMenu(parent=self)
        # Ensure the menu is wide enough so long shortcut labels aren't truncated
        operations_menu.setFixedWidth(500)

        operations_menu.addAction(
            Action(
                FluentIcon.DOCUMENT,
                "新建项目",
                shortcut="Ctrl+N",
                triggered=self._on_new_project
            )
        )
        operations_menu.addAction(
            Action(
                FluentIcon.FOLDER,
                "打开项目",
                shortcut="Ctrl+O",
                triggered=self._on_open_project
            )
        )
        operations_menu.addAction(
            Action(
                FluentIcon.SAVE,
                "保存",
                shortcut="Ctrl+S",
                triggered=self._on_save_project
            )
        )
        operations_menu.addAction(
            Action(
                FluentIcon.SAVE_AS,
                "另存为",
                shortcut="Ctrl+Shift+S",
                triggered=self._on_save_as
            )
        )

        operations_menu.addSeparator()

        self.menu_action_undo = Action(
            FluentIcon.RETURN,
            "撤销",
            shortcut="Ctrl+Z",
            triggered=self._on_undo
        )
        self.menu_action_undo.setEnabled(False)
        operations_menu.addAction(self.menu_action_undo)

        self.menu_action_redo = Action(
            FluentIcon.RIGHT_ARROW,
            "重做",
            shortcut="Ctrl+Shift+Z",
            triggered=self._on_redo
        )
        self.menu_action_redo.setEnabled(False)
        operations_menu.addAction(self.menu_action_redo)
        operations_menu.addSeparator()

        operations_menu.addAction(
            Action(
                FluentIcon.HELP,
                "快捷键帮助",
                shortcut="F1",
                triggered=self._on_shortcuts
            )
        )
        operations_menu.addSeparator()

        operations_menu.addAction(
            Action(
                FluentIcon.POWER_BUTTON,
                "退出",
                shortcut="Ctrl+Q",
                triggered=self.close
            )
        )
        self.btn_operations.setMenu(operations_menu)
        toolbar_layout.addWidget(self.btn_operations)

        self.settings_mode_combo = FluentComboBox()
        self.settings_mode_combo.addItem("基础设置", userData="basic")
        self.settings_mode_combo.addItem("高级设置", userData="advanced")
        self.settings_mode_combo.setFixedHeight(34)
        self.settings_mode_combo.currentIndexChanged.connect(
            self._on_settings_mode_combo_changed)
        toolbar_layout.addWidget(self.settings_mode_combo)

        toolbar_layout.addStretch()
        self.config_layout.addLayout(toolbar_layout)

        self.advanced_config_panel = ConfigPanel()
        self.basic_config_panel = BasicConfigPanel()

        self.config_layout.addWidget(self.advanced_config_panel)
        self.config_layout.addWidget(self.basic_config_panel)
        self.vs_script_panel = VSScriptPanel()
        self.config_layout.addWidget(self.vs_script_panel)
        self.advanced_config_panel.setVisible(False)
        self.basic_config_panel.setVisible(True)

        # 基础模式下，只显示循环视频标签页
        self._show_loop_tab_only()

        self.splitter.addWidget(self.config_container)

        # === 中间: 视频预览标签页 + 时间轴（深色预览区，剪映风格）===
        self.preview_container = QWidget()
        preview_container = self.preview_container
        preview_container.setObjectName("preview_container")
        setCustomStyleSheet(
            preview_container,
            "QWidget#preview_container { background-color: #1a1a1a; border-radius: 8px; }",
            "QWidget#preview_container { background-color: #0a0a0a; border-radius: 8px; }"
        )
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(5, 5, 5, 5)
        preview_layout.setSpacing(5)

        self.preview_tabs = TabWidget()
        self.preview_tabs.setTabsClosable(False)  # 禁用关闭按钮
        self.preview_tabs.setMovable(False)  # 禁用标签移动
        self.preview_tabs.tabBar.setAddButtonVisible(False)
        # 标签页文字在深色预览背景上需要浅色
        setCustomStyleSheet(
            self.preview_tabs,
            "TabWidget > QTabBar::tab { color: #ccc; } TabWidget > QTabBar::tab:selected { color: #fff; }",
            "TabWidget > QTabBar::tab { color: #aaa; } TabWidget > QTabBar::tab:selected { color: #eee; }"
        )
        self.video_preview = VideoPreviewWidget()  # 循环视频预览
        self.intro_preview = VideoPreviewWidget()  # 入场视频预览
        self.transition_preview = TransitionPreviewWidget()  # 过渡图片预览

        frame_capture_widget = QWidget()
        frame_capture_layout = QVBoxLayout(frame_capture_widget)
        frame_capture_layout.setContentsMargins(0, 0, 0, 0)
        frame_capture_layout.setSpacing(5)
        self.frame_capture_preview = VideoPreviewWidget()
        # The saved icon is center-cropped to 256x256 by process_for_logo, so the
        # capture crop box must be square too — otherwise the user frames a tall
        # 360:640 region but a different centre square is what actually ships.
        self.frame_capture_preview.set_target_resolution(256, 256)
        frame_capture_layout.addWidget(self.frame_capture_preview, stretch=1)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_save_icon = PrimaryPushButton("保存为图标")
        btn_layout.addWidget(self.btn_save_icon)
        frame_capture_layout.addLayout(btn_layout)

        self.preview_tabs.addTab(self.intro_preview, "入场视频")         # Tab 0
        self.preview_tabs.addTab(frame_capture_widget, "截取帧编辑")     # Tab 1
        self.preview_tabs.addTab(self.transition_preview, "过渡图片")    # Tab 2
        self.preview_tabs.addTab(self.video_preview, "循环视频")         # Tab 3
        preview_layout.addWidget(self.preview_tabs, stretch=1)

        self._show_loop_tab_only()

        self.timeline = TimelineWidget()
        preview_layout.addWidget(self.timeline)

        self.splitter.addWidget(preview_container)

        # === 右侧: JSON预览 ===
        self.json_preview = JsonPreviewWidget()
        self.splitter.addWidget(self.json_preview)

        self.splitter.setSizes([350, 800, 300])
        self.splitter.setStretchFactor(0, 1)   # 左侧允许少量伸缩
        self.splitter.setStretchFactor(1, 20)  # 中间优先伸缩，权重更大
        self.splitter.setStretchFactor(2, 1)   # 右侧允许少量伸缩

        self.content_layout.addWidget(self.splitter)
        content_layout.addWidget(self.content_stack)

        main_layout.addWidget(content_container)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪")

        self._setup_drop_support()

    def _setup_menu(self):
        """设置菜单"""
        menubar = self.menuBar()

        file_menu = menubar.addMenu("文件(&F)")

        self.action_new = QAction("新建项目(&N)", self)
        file_menu.addAction(self.action_new)

        self.action_open = QAction("打开项目(&O)...", self)
        file_menu.addAction(self.action_open)

        self.recent_menu = file_menu.addMenu("最近打开(&R)")
        self._update_recent_menu()

        file_menu.addSeparator()

        self.action_save = QAction("保存(&S)", self)
        file_menu.addAction(self.action_save)

        self.action_save_as = QAction("另存为(&A)...", self)
        file_menu.addAction(self.action_save_as)

        file_menu.addSeparator()

        self.action_exit = QAction("退出(&X)", self)
        file_menu.addAction(self.action_exit)

        edit_menu = menubar.addMenu("编辑(&E)")

        self.action_undo = QAction("撤销(&U)", self)
        self.action_undo.setEnabled(False)
        edit_menu.addAction(self.action_undo)

        self.action_redo = QAction("重做(&R)", self)
        self.action_redo.setEnabled(False)
        edit_menu.addAction(self.action_redo)

        tools_menu = menubar.addMenu("工具(&T)")

        self.action_flasher = QAction("固件烧录(&R)...", self)
        tools_menu.addAction(self.action_flasher)

        help_menu = menubar.addMenu("帮助(&H)")

        self.action_shortcuts = QAction("快捷键帮助(&K)", self)
        help_menu.addAction(self.action_shortcuts)

        self.action_check_update = QAction("检查更新(&U)...", self)
        help_menu.addAction(self.action_check_update)

        help_menu.addSeparator()

        self.action_about = QAction("关于(&A)", self)
        help_menu.addAction(self.action_about)

    def _setup_shortcuts(self):
        """设置全局快捷键 - 统一注册到 MainWindow 上，不受子面板可见性影响"""
        QShortcut(QKeySequence.StandardKey.New,
                  self).activated.connect(self._on_new_project)
        QShortcut(QKeySequence.StandardKey.Open,
                  self).activated.connect(self._on_open_project)
        QShortcut(QKeySequence.StandardKey.Save,
                  self).activated.connect(self._on_save_project)
        QShortcut(QKeySequence("Ctrl+Shift+S"),
                  self).activated.connect(self._on_save_as)
        QShortcut(QKeySequence.StandardKey.Quit,
                  self).activated.connect(self.close)

        self._shortcut_undo = QShortcut(QKeySequence.StandardKey.Undo, self)
        self._shortcut_undo.setEnabled(False)
        self._shortcut_undo.activated.connect(self._on_undo)
        self._shortcut_redo = QShortcut(QKeySequence.StandardKey.Redo, self)
        self._shortcut_redo.setEnabled(False)
        self._shortcut_redo.activated.connect(self._on_redo)

        QShortcut(QKeySequence("Ctrl+T"),
                  self).activated.connect(self._on_validate)
        QShortcut(QKeySequence("Ctrl+E"),
                  self).activated.connect(self._on_export)

        QShortcut(QKeySequence("F1"), self).activated.connect(
            self._on_shortcuts)

    def _connect_signals(self):
        """连接信号"""
        self.vs_script_panel.source_requested.connect(
            self._on_script_source_requested
        )
        self.vs_script_panel.reload_requested.connect(self._on_script_reload)
        self.vs_script_panel.open_directory_requested.connect(
            self._on_script_open_directory
        )
        self.action_new.triggered.connect(self._on_new_project)
        self.action_open.triggered.connect(self._on_open_project)
        self.action_save.triggered.connect(self._on_save_project)
        self.action_save_as.triggered.connect(self._on_save_as)
        self.action_exit.triggered.connect(self.close)
        self.action_undo.triggered.connect(self._on_undo)
        self.action_redo.triggered.connect(self._on_redo)
        self.action_flasher.triggered.connect(self._on_flasher)
        self.action_shortcuts.triggered.connect(self._on_shortcuts)
        self.action_check_update.triggered.connect(self._on_check_update)
        self.action_about.triggered.connect(self._on_about)

        self.advanced_config_panel.config_changed.connect(
            self._on_config_changed)
        self.advanced_config_panel.video_file_selected.connect(
            self._on_video_file_selected)
        self.advanced_config_panel.intro_video_selected.connect(
            self._on_intro_video_selected)
        self.advanced_config_panel.loop_image_selected.connect(
            self._load_loop_image)
        self.advanced_config_panel.loop_mode_changed.connect(
            self._on_loop_mode_changed)
        self.advanced_config_panel.validate_requested.connect(
            self._on_validate)
        self.advanced_config_panel.export_requested.connect(self._on_export)
        self.advanced_config_panel.capture_frame_requested.connect(
            self._on_capture_frame)
        self.advanced_config_panel.transition_image_changed.connect(
            self._on_transition_image_changed)
        self.advanced_config_panel.remote_upload_requested.connect(
            self._on_remote_upload)

        self.basic_config_panel.config_changed.connect(self._on_config_changed)
        self.basic_config_panel.video_file_selected.connect(
            self._on_video_file_selected)
        self.basic_config_panel.validate_requested.connect(self._on_validate)
        self.basic_config_panel.export_requested.connect(self._on_export)
        self.basic_config_panel.remote_upload_requested.connect(
            self._on_remote_upload)
        self.btn_save_icon.clicked.connect(self._on_save_captured_icon)

        self.transition_preview.transition_crop_changed.connect(
            self._on_transition_crop_changed)

        self.preview_tabs.currentChanged.connect(self._on_preview_tab_changed)

        self.video_preview.video_loaded.connect(self._on_video_loaded)
        self.video_preview.frame_changed.connect(self._on_frame_changed)
        self.video_preview.playback_state_changed.connect(
            self._on_playback_changed)
        self.video_preview.rotation_changed.connect(
            self._on_loop_rotation_changed)
        self.video_preview.load_failed.connect(
            lambda msg: self._on_preview_load_failed(
                "循环视频", msg, self.video_preview))
        # 裁剪框变化写入 config.editor(此前从未连接:关闭即静默丢失)
        self.video_preview.cropbox_changed.connect(
            lambda *a: self._on_editor_state_changed(self.video_preview))

        self.btn_firmware.clicked.connect(self._on_sidebar_firmware)
        self.btn_material.clicked.connect(self._on_sidebar_material)
        self.btn_forum.clicked.connect(self._on_sidebar_forum)
        self.btn_about.clicked.connect(self._on_sidebar_about)
        self.btn_remote.clicked.connect(self._on_sidebar_remote)
        self.btn_usbControl.clicked.connect(self._on_sidebar_usbControl)
        self.btn_settings.clicked.connect(self._on_sidebar_settings)

        self.intro_preview.video_loaded.connect(self._on_intro_video_loaded)
        self.intro_preview.frame_changed.connect(self._on_intro_frame_changed)
        self.intro_preview.playback_state_changed.connect(
            self._on_intro_playback_changed)
        self.intro_preview.rotation_changed.connect(
            self._on_intro_rotation_changed)
        self.intro_preview.load_failed.connect(
            lambda msg: self._on_preview_load_failed(
                "入场视频", msg, self.intro_preview))
        self.intro_preview.cropbox_changed.connect(
            lambda *a: self._on_editor_state_changed(self.intro_preview))

        self._connect_timeline_to_preview(self.intro_preview)

        self.timeline.simulator_requested.connect(self._on_simulator)

        self.timeline.set_in_point_clicked.connect(self._on_set_in_point)
        self.timeline.set_out_point_clicked.connect(self._on_set_out_point)

        from qfluentwidgets.common.config import qconfig
        qconfig.themeChanged.connect(self._on_system_theme_changed)

    def _on_system_theme_changed(self):
        """系统亮/暗主题切换时，刷新窗口背景和自定义样式"""
        self._bg_color = self._dark_bg_color if isDarkTheme() else self._light_bg_color

        # 重新应用当前主题色到自定义控件（header_bar、sidebar 等）
        settings = self._read_user_settings()
        theme_color = settings.get('theme_color', '#ff6b8b')
        self._apply_theme_color(theme_color)

        # 如果当前有背景图片，重新应用图片模式样式（content_bg 等值依赖 isDarkTheme）
        if self._bg_pixmap is not None:
            self._apply_image_mode_styles()

        self.update()

    def _on_remote_upload(self):
        """Export and upload through EPass RNDIS HTTP."""
        try:
            result = self._on_export()
            if not result:
                return
            export_dialog, dir_path = result

            if not getattr(export_dialog, 'was_successful', False):
                logger.warning("Export failed, cancelling RNDIS HTTP upload")
                return

            if not os.path.exists(dir_path):
                logger.warning("Export directory does not exist: %s", dir_path)
                return

            settings = self._read_user_settings()
            enable_restart = settings.get(
                'remote_auto_restart_program',
                settings.get('ssh_auto_restart_program', True),
            )

            from core.rndis_device_service import DEFAULT_BASE_URL
            from gui.dialogs.remote_upload_progress_dialog import (
                RemoteUploadProgressDialog,
            )
            from gui.workers.rndis_http_workers import HttpUploadAssetWorker

            self._remote_upload_worker = HttpUploadAssetWorker(
                DEFAULT_BASE_URL, dir_path, enable_restart, parent=self
            )
            self._remote_upload_dialog = RemoteUploadProgressDialog(self)

            self._remote_upload_worker.progress_updated.connect(
                self._remote_upload_dialog.update_progress
            )
            self._remote_upload_worker.upload_completed.connect(
                lambda msg: self._on_remote_upload_completed(True, msg)
            )
            self._remote_upload_worker.upload_failed.connect(
                lambda msg: self._on_remote_upload_completed(False, msg)
            )
            self._remote_upload_dialog.cancel_requested.connect(
                self._remote_upload_worker.cancel
            )

            self._remote_upload_worker.start()

            self._remote_upload_dialog.exec()

            # 如果用户取消了对话框，让后台线程尽快结束
            if self._remote_upload_worker.isRunning():
                self._remote_upload_worker.cancel()
                self._remote_upload_worker.wait(2000)

        except Exception as e:
            logger.exception("RNDIS HTTP upload failed")
            return
        return

    def _load_settings(self):
        """加载设置"""
        settings = QSettings("ArknightsPassMaker", "MainWindow")
        geometry = settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
            logger.debug("已恢复窗口几何设置")

    def _load_user_settings(self):
        """加载用户设置（启动时调用）"""
        try:
            import json
            config_dir = os.path.join(self._app_dir, "config")
            config_file = os.path.join(config_dir, "user_settings.json")

            if os.path.exists(config_file):
                with open(config_file, "r", encoding="utf-8") as f:
                    settings = json.load(f)

                theme_name = settings.get('theme', '默认')
                self._apply_theme_change(theme_name)

                hw_accel = settings.get('hardware_acceleration', True)
                if not hw_accel:
                    self._apply_instant_settings(
                        'hardware_acceleration', False)

                auto_save = settings.get('auto_save', True)
                if not auto_save:
                    self._auto_save_service.config.enabled = False

                logger.info("已加载用户设置")
        except Exception as e:
            logger.error(f"加载用户设置失败: {e}")

    def _read_user_settings(self) -> dict:
        """读取 user_settings.json 并返回 dict"""
        try:
            import json
            config_dir = os.path.join(self._app_dir, "config")
            config_file = os.path.join(config_dir, "user_settings.json")
            if os.path.exists(config_file):
                with open(config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"读取用户设置失败: {e}")
            return {}

    def _load_settings_to_page(self):
        """将 user_settings.json 的值加载到设置页面"""
        try:
            settings = self._read_user_settings()
            self._settings_page.load_settings(settings)
        except Exception as e:
            logger.error(f"加载设置到页面失败: {e}")

    def _check_first_run(self):
        """检查是否首次运行"""
        settings = QSettings("ArknightsPassMaker", "MainWindow")
        if not settings.value("first_run_completed", False, type=bool):
            show_welcome = True
            try:
                import json
                config_dir = os.path.join(self._app_dir, "config")
                config_file = os.path.join(config_dir, "user_settings.json")
                if os.path.exists(config_file):
                    with open(config_file, "r", encoding="utf-8") as f:
                        user_settings = json.load(f)
                        show_welcome = user_settings.get(
                            'show_welcome_dialog', True)
            except Exception:
                pass

            if show_welcome:
                self._show_splash_announcement()
                settings.setValue("first_run_completed", True)
        else:
            # 每次启动都显示开屏公告（可选择不再显示）
            self._show_splash_announcement()

    def _show_splash_announcement(self):
        """显示开屏公告"""
        settings = QSettings("ArknightsPassMaker", "MainWindow")
        if not settings.value("show_announcement", True, type=bool):
            return

        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextBrowser, QCheckBox
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QIcon

        dialog = QDialog(self)
        dialog.setWindowTitle("软件使用指南")
        dialog.setMinimumSize(800, 600)
        dialog.setWindowIcon(
            QIcon(
                os.path.join(
                    self._app_dir,
                    'resources',
                    'icons',
                    'favicon.ico')))

        main_layout = QVBoxLayout(dialog)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)

        title_label = QLabel("欢迎使用明日方舟通行证素材制作器 v2.0")
        _tc = themeColor().name()
        setCustomStyleSheet(
            title_label,
            f"font-size: 20px; font-weight: bold; color: {_tc}; text-align: center;",
            f"font-size: 20px; font-weight: bold; color: {_tc}; text-align: center;"
        )
        main_layout.addWidget(title_label)

        content_browser = QTextBrowser()
        setCustomStyleSheet(
            content_browser,
            "font-size: 14px; line-height: 1.5;",
            "font-size: 14px; line-height: 1.5;"
        )

        announcement_content = """
        <h2>软件使用指南</h2>

        <h3>一、软件简介</h3>
        <p>明日方舟通行证素材制作器是一款专门用于创建和编辑明日方舟电子通行证素材的工具，支持视频、图片等多种素材类型的处理和导出。</p>

        <h3>二、主要模块</h3>

        <h4>1. 固件烧录</h4>
        <p>用于为迷你Linux手持开发板烧录固件，支持FEL模式和DFU模式。</p>
        <ul>
            <li><strong>自动检测设备</strong>：软件会自动检测连接的设备类型</li>
            <li><strong>多版本选择</strong>：可选择不同版本的固件进行烧录</li>
            <li><strong>驱动安装</strong>：内置驱动安装功能，确保设备正常识别</li>
        </ul>

        <h4>2. 素材制作</h4>
        <p>软件的核心功能，用于创建和编辑通行证素材。</p>
        <ul>
            <li><strong>基础设置</strong>：简化的界面，适合快速创建素材</li>
            <li><strong>高级设置</strong>：完整的功能界面，支持详细的参数调整</li>
            <li><strong>视频预览</strong>：实时预览视频效果</li>
            <li><strong>过渡效果</strong>：支持自定义过渡图片</li>
            <li><strong>时间轴编辑</strong>：精确控制视频片段</li>
            <li><strong>JSON预览</strong>：实时查看生成的配置文件</li>
        </ul>

        <h4>3. 素材论坛</h4>
        <p>内置素材论坛客户端，提供完整的素材浏览和管理功能。</p>
        <ul>
            <li><strong>素材浏览</strong>：搜索、筛选和排序素材资源</li>
            <li><strong>下载管理</strong>：多任务下载，支持暂停和续传</li>
            <li><strong>素材库</strong>：管理已下载的素材文件</li>
            <li><strong>USB 传输</strong>：直接将素材传输到设备</li>
        </ul>

        <h4>4. 项目介绍</h4>
        <p>查看项目的详细介绍和最新动态。</p>
        <ul>
            <li><strong>官方网站</strong>：直接访问项目官网获取最新信息</li>
            <li><strong>项目特性</strong>：了解开发板的主要功能和规格</li>
        </ul>

        <h4>5. 设置</h4>
        <p>自定义软件的各项设置。</p>
        <ul>
            <li><strong>主题设置</strong>：可选择默认主题或自定义主题图片</li>
            <li><strong>界面设置</strong>：调整字体大小、界面缩放等</li>
            <li><strong>视频设置</strong>：设置硬件加速</li>
            <li><strong>导出设置</strong>：调整导出线程数</li>
            <li><strong>网络设置</strong>：配置GitHub加速等网络选项</li>
        </ul>

        <h3>三、使用流程</h3>
        <ol>
            <li><strong>准备素材</strong>：收集需要的视频、图片等素材文件</li>
            <li><strong>创建项目</strong>：点击"文件"菜单选择"新建项目"</li>
            <li><strong>编辑素材</strong>：在素材制作模块中调整各项参数</li>
            <li><strong>预览效果</strong>：使用预览功能查看效果</li>
            <li><strong>导出素材</strong>：点击"导出"按钮生成最终素材</li>
            <li><strong>烧录固件</strong>：使用固件烧录模块将素材烧录到设备</li>
        </ol>

        <h3>四、注意事项</h3>
        <ul>
            <li>确保使用兼容的视频格式（建议使用MP4格式）</li>
            <li>视频分辨率建议与设备屏幕分辨率匹配（360×640）</li>
            <li>使用高质量素材以获得最佳显示效果</li>
            <li>定期检查更新以获取最新功能和 bug 修复</li>
            <li>如遇到问题，请参考帮助文档或联系开发者</li>
        </ul>

        <h3>五、快捷键</h3>
        <ul>
            <li><strong>Ctrl+N</strong>：新建项目</li>
            <li><strong>Ctrl+O</strong>：打开项目</li>
            <li><strong>Ctrl+S</strong>：保存项目</li>
            <li><strong>F1</strong>：查看快捷键帮助</li>
        </ul>

        <h3>六、常见问题</h3>
        <h4>Q: 软件启动时提示缺少模块？</h4>
        <p>A: 请确保已安装所有必要的依赖包，可使用 pip 安装缺少的模块。</p>

        <h4>Q: 固件烧录失败？</h4>
        <p>A: 请检查设备连接是否正常，驱动是否安装正确，尝试更换USB端口或线缆。</p>

        <h4>Q: 导出的素材在设备上显示异常？</h4>
        <p>A: 请检查素材格式是否正确，分辨率是否匹配设备屏幕。</p>

        <h3>七、联系我们</h3>
        <p>如果您在使用过程中遇到任何问题，或有任何建议和反馈，欢迎联系我们。</p>
        <p>项目地址：<a href="https://github.com/rhodesepass/neo-assetmaker">https://github.com/rhodesepass/neo-assetmaker</a></p>
        <p>官方网站：<a href="https://ep.iccmc.cc">https://ep.iccmc.cc</a></p>

        <p style="text-align: center; color: #666; margin-top: 30px;">
            祝您使用愉快！
        </p>
        """

        content_browser.setHtml(announcement_content)
        main_layout.addWidget(content_browser)

        bottom_layout = QHBoxLayout()

        self.show_announcement_check = QCheckBox("下次启动时不再显示")
        bottom_layout.addWidget(self.show_announcement_check)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        ok_button = PrimaryPushButton("我知道了")
        ok_button.clicked.connect(dialog.accept)

        button_layout.addWidget(ok_button)
        bottom_layout.addLayout(button_layout)

        main_layout.addLayout(bottom_layout)

        dialog.exec()

        if self.show_announcement_check.isChecked():
            settings = QSettings("ArknightsPassMaker", "MainWindow")
            settings.setValue("show_announcement", False)

    def _on_autosave_saved(self, backup_path: str):
        """Write a crash-recovery pointer for each autosave so it can be found later."""
        try:
            project_path = self._project_path or None
            self._crash_recovery_service.save_recovery_info(
                backup_path,
                project_path=project_path,
                is_temp=not project_path,
            )
        except Exception as exc:
            logger.error(f"注册崩溃恢复信息失败: {exc}")

    def _init_temp_project(self):
        """创建临时项目，用户可立即开始编辑"""
        temp_dir = tempfile.mkdtemp(prefix="neo_assetmaker_")
        self._temp_dir = temp_dir

        self._config = EPConfig()
        self._base_dir = temp_dir
        self._project_path = ""  # 留空，首次保存时触发"另存为"
        self._is_modified = False

        self.advanced_config_panel.set_config(self._config, self._base_dir)
        self.basic_config_panel.set_config(self._config, self._base_dir)
        self.json_preview.set_config(self._config, self._base_dir)
        self.video_preview.set_epconfig(self._config)
        self._configure_preview_render_contexts()
        self._update_title()
        self.status_bar.showMessage("已创建临时项目，可以开始编辑")
        logger.info(f"已初始化临时项目: {temp_dir}")

        self._auto_save_service.start(
            self._config, self._project_path, self._base_dir)
        self._reset_undo_history()

    def _cleanup_temp_dir(self):
        """清理临时项目目录"""
        if self._temp_dir and os.path.exists(self._temp_dir):
            try:
                shutil.rmtree(self._temp_dir)
                logger.info(f"已清理临时目录: {self._temp_dir}")
            except Exception as e:
                logger.warning(f"清理临时目录失败: {e}")
        self._temp_dir = None

    def _migrate_temp_to_permanent(self, dest_dir: str):
        """将临时项目中的工作文件迁移到永久目录"""
        if not self._temp_dir or not os.path.exists(self._temp_dir):
            return

        try:
            for filename in os.listdir(self._temp_dir):
                src = os.path.join(self._temp_dir, filename)
                dst = os.path.join(dest_dir, filename)
                if not os.path.isfile(src):
                    continue
                if os.path.exists(dst):
                    # Don't bind the project to a pre-existing foreign file when
                    # the temp source is the intended one: overwrite only when
                    # the temp file is newer, otherwise log the skip so a stale
                    # destination doesn't silently win.
                    if os.path.getmtime(src) <= os.path.getmtime(dst):
                        logger.warning(
                            "迁移跳过(目标已存在且不更旧): %s", filename)
                        continue
                shutil.copy2(src, dst)
                logger.debug(f"已迁移文件: {filename}")

            self._cleanup_temp_dir()
            logger.info(f"已将临时项目迁移到: {dest_dir}")
        except Exception as e:
            logger.warning(f"迁移临时项目失败: {e}")
            # 迁移失败时保留临时目录作为备份

    def _on_shortcuts(self):
        """显示快捷键帮助"""
        from gui.dialogs.shortcuts_dialog import ShortcutsDialog
        dialog = ShortcutsDialog(self)
        dialog.exec()

    def _save_settings(self):
        """保存设置"""
        settings = QSettings("ArknightsPassMaker", "MainWindow")
        settings.setValue("geometry", self.saveGeometry())
        logger.debug("已保存窗口几何设置")

    def _update_title(self):
        """更新窗口标题"""
        title = f"{APP_NAME} v{APP_VERSION_LABEL}"
        if self._project_path:
            title = f"{os.path.basename(self._project_path)} - {title}"
        elif self._temp_dir:
            title = f"临时项目 - {title}"
        if self._is_modified:
            title = f"* {title}"
        self.setWindowTitle(title)

    def _on_new_project(self):
        """新建项目"""
        if not self._check_save():
            return

        dir_path = QFileDialog.getExistingDirectory(
            self, "选择项目目录", ""
        )
        if not dir_path:
            return

        self._cleanup_temp_dir()

        self._config = EPConfig()
        self._base_dir = dir_path
        self._project_path = os.path.join(dir_path, CONFIG_FILENAME)
        self._is_modified = True

        self.video_preview.clear()
        self.intro_preview.clear()
        self.frame_capture_preview.clear()
        self.transition_preview.clear_image("in")
        self.transition_preview.clear_image("loop")
        self._loop_image_path = None
        self.timeline.set_total_frames(0)
        self._loop_in_out = (0, 0)
        self._intro_in_out = (0, 0)

        self.advanced_config_panel.set_config(self._config, self._base_dir)
        self.basic_config_panel.set_config(self._config, self._base_dir)
        self.json_preview.set_config(self._config, self._base_dir)
        self.video_preview.set_epconfig(self._config)
        # 重新指向自动保存:服务在 start() 时缓存 config 对象与项目路径
        # (auto_save_service.py),不重启会继续把旧项目的配置备份到旧位置,
        # 崩溃恢复也会指向错误的项目。
        self._auto_save_service.start(
            self._config, self._project_path, self._base_dir)
        self._reset_undo_history()
        self._update_title()
        self.status_bar.showMessage(f"新建项目: {dir_path}")

    def _on_open_project(self):
        """打开项目"""
        if not self._check_save():
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "打开配置文件", "",
            "JSON文件 (*.json);;所有文件 (*.*)"
        )
        if not path:
            return

        self._cleanup_temp_dir()
        self.ReadProjectFromJson(path)

    def ReadProjectFromJson(self, path: str):
        try:
            self._config = EPConfig.load_from_file(path)
            self._project_path = path
            self._base_dir = os.path.dirname(path)
            self._is_modified = False
            self._apply_project_config()
            self._add_recent_file(path)
        except Exception as e:
            show_error(e, "打开文件", self)

    def _apply_project_config(self):
        """应用项目配置到UI（清除预览 → 设置面板 → 加载视频）

        前置条件：self._config, self._project_path, self._base_dir 已设置。
        """
        self.video_preview.clear()
        self.intro_preview.clear()
        self.frame_capture_preview.clear()
        self.transition_preview.clear_image("in")
        self.transition_preview.clear_image("loop")
        self._loop_image_path = None
        self.timeline.set_total_frames(0)
        self._loop_in_out = (0, 0)
        self._intro_in_out = (0, 0)
        self._configure_preview_render_contexts()

        self.advanced_config_panel.set_config(self._config, self._base_dir)
        self.basic_config_panel.set_config(self._config, self._base_dir)
        self.json_preview.set_config(self._config, self._base_dir)
        self.video_preview.set_epconfig(self._config)

        target_w, target_h = self._get_target_resolution()
        self.video_preview.set_target_resolution(target_w, target_h)
        self.intro_preview.set_target_resolution(target_w, target_h)
        # 过渡图预览此前从不接收目标分辨率(TransitionPreviewWidget.
        # set_target_resolution 有定义但全仓零调用),裁剪框永远锁在默认
        # 360x640 比例 —— 720x1080 工程里过渡图会被拉伸约 15.6%。
        self.transition_preview.set_target_resolution(target_w, target_h)

        load_failures: list[str] = []
        if self._config.loop.file:
            file_path = self._config.loop.file
            if not os.path.isabs(file_path):
                file_path = os.path.join(self._base_dir, file_path)

            if os.path.exists(file_path):
                # 打开项目:记录 editor 状态拷贝待加载完成后恢复;加载期挂起
                # 同步,防止 _init_cropbox 的默认框先把保存值覆盖掉/误标脏。
                self._begin_editor_restore(
                    self.video_preview, self._config.editor.loop)
                if self._config.loop.is_image:
                    logger.info(f"尝试加载循环图片: {file_path}")
                    self._load_loop_image(file_path)
                else:
                    logger.info(f"尝试加载循环视频: {file_path}")
                    if not self.video_preview.load_video(file_path):
                        load_failures.append("循环视频")
                        self._cancel_editor_restore(self.video_preview)
            else:
                logger.warning(f"循环素材文件不存在: {file_path}")

        if self._config.intro.enabled and self._config.intro.file:
            intro_path = self._config.intro.file
            if not os.path.isabs(intro_path):
                intro_path = os.path.join(self._base_dir, intro_path)
            if os.path.exists(intro_path):
                logger.info(f"尝试加载入场视频: {intro_path}")
                self._begin_editor_restore(
                    self.intro_preview, self._config.editor.intro)
                if not self.intro_preview.load_video(intro_path):
                    load_failures.append("入场视频")
                    self._cancel_editor_restore(self.intro_preview)

        # load_video 现在是异步受理:返回 False 仅代表同步拒绝(文件不存在),
        # 元数据探测失败会经 load_failed 信号进入 _on_preview_load_failed 聚合弹窗。
        if load_failures:
            QMessageBox.warning(
                self, "部分素材加载失败",
                "以下素材无法开始加载（请确认文件可被 VapourSynth/lsmas 解码）：\n"
                + "、".join(load_failures),
            )

        self._update_title()
        self.status_bar.showMessage(f"已打开: {self._project_path}")
        self._auto_save_service.start(
            self._config, self._project_path, self._base_dir)
        self._reset_undo_history()

    def _load_project(self, path: str):
        """加载指定路径的项目文件（供最近打开和崩溃恢复调用）"""
        if not os.path.exists(path):
            QMessageBox.warning(self, "文件不存在", f"文件不存在:\n{path}")
            return

        if not self._check_save():
            return

        self._cleanup_temp_dir()

        try:
            self._config = EPConfig.load_from_file(path)
            self._project_path = path
            self._base_dir = os.path.dirname(path)
            self._is_modified = False
            self._apply_project_config()
            self._add_recent_file(path)
        except Exception as e:
            show_error(e, "打开文件", self)

    def _on_save_project(self):
        """保存项目"""
        if not self._config:
            return

        if not self._project_path:
            self._on_save_as()
            return

        try:
            self._config.save_to_file(self._project_path)
            self._is_modified = False
            self._update_title()
            self.status_bar.showMessage(f"已保存: {self._project_path}")
        except Exception as e:
            show_error(e, "保存项目", self)

    def _on_save_as(self):
        """另存为"""
        if not self._config:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "保存配置文件",
            self._project_path or CONFIG_FILENAME,
            "JSON文件 (*.json)"
        )
        if not path:
            return

        # 校验文件名，防止用户误改
        if os.path.basename(path) != CONFIG_FILENAME:
            corrected = os.path.join(os.path.dirname(path), CONFIG_FILENAME)
            ret = QMessageBox.question(
                self, "文件名修正",
                f"配置文件名应为\u201c{CONFIG_FILENAME}\u201d，否则模拟器等功能将无法正常工作。\n\n"
                f"是否将文件名修正为\u201c{CONFIG_FILENAME}\u201d？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if ret == QMessageBox.StandardButton.Yes:
                path = corrected

        try:
            new_base_dir = os.path.dirname(path)

            if self._temp_dir and self._base_dir == self._temp_dir:
                self._migrate_temp_to_permanent(new_base_dir)

            self._config.save_to_file(path)
            self._project_path = path
            self._base_dir = new_base_dir
            self._is_modified = False

            self.advanced_config_panel.set_config(self._config, self._base_dir)
            self.basic_config_panel.set_config(self._config, self._base_dir)
            self.json_preview.set_config(self._config, self._base_dir)
            self._configure_preview_render_contexts()

            # 重新指向自动保存(路径已切换;服务缓存的是 start() 时的值)。
            self._auto_save_service.start(
                self._config, self._project_path, self._base_dir)

            self._update_title()
            self.status_bar.showMessage(f"已保存: {path}")
        except Exception as e:
            show_error(e, "另存为", self)

    def _on_validate(self):
        """验证配置"""
        if not self._config:
            QMessageBox.information(self, "提示", "请先创建或打开项目")
            return

        from core.validator import EPConfigValidator

        validator = EPConfigValidator(self._base_dir)
        results = validator.validate_config(self._config)

        if not validator.has_errors():
            QMessageBox.information(self, "验证通过", validator.get_summary())
        else:
            errors = validator.get_errors()
            warnings = validator.get_warnings()

            msg = f"{validator.get_summary()}\n\n"
            if errors:
                msg += "错误:\n"
                for r in errors:
                    msg += f"  - {r}\n"
            if warnings:
                msg += "\n警告:\n"
                for r in warnings:
                    msg += f"  - {r}\n"

            QMessageBox.warning(self, "验证结果", msg)

    def _on_export(self):
        """导出素材"""
        if self._export_in_progress:
            QMessageBox.information(self, "导出进行中", "当前导出尚未完成")
            return
        if not self._config:
            QMessageBox.information(self, "提示", "请先创建或打开项目")
            return
        if not self._script_ready:
            QMessageBox.warning(self, "脚本未就绪", self._script_block_reason)
            return

        from core.validator import EPConfigValidator
        validator = EPConfigValidator(self._base_dir)
        validator.validate_config(self._config)

        if validator.has_errors():
            errors = validator.get_errors()
            msg = "配置验证失败，无法导出:\n\n"
            for r in errors:
                msg += f"  - {r}\n"
            QMessageBox.critical(self, "验证失败", msg)
            return

        has_loop_video = self.video_preview.video_path
        has_loop_image = self._config.loop.is_image and hasattr(
            self, '_loop_image_path') and self._loop_image_path

        if not has_loop_video and not has_loop_image:
            QMessageBox.warning(
                self, "警告",
                "请先加载循环素材\n\n"
                "在配置面板的'视频配置'选项卡中选择循环视频或图片文件"
            )
            return

        dir_path = QFileDialog.getExistingDirectory(
            self, "选择导出目录", self._base_dir
        )
        if not dir_path:
            return

        # 暂停信号、准备阶段的错误弹窗也可能重入，须先建立本次导出的边界。
        self._export_in_progress = True
        self._export_call_active = True
        self._export_service = None
        self._export_dialog = None
        self._export_paused_previews = []
        try:
            self._export_paused_previews = self._pause_export_previews()
            try:
                export_data = self._collect_export_data()
            except Exception as e:
                logger.error(f"收集导出数据失败: {e}")
                show_error(e, "收集导出数据", self)
                return

            # 辅助图片在 GUI 线程收集；写盘仍由 export_all 的事务任务完成。
            aux_images: list = []
            try:
                aux_images.extend(self._collect_arknights_custom_images())
                aux_images.extend(self._collect_image_overlay())
            except Exception as e:
                logger.error(f"处理自定义图片失败: {e}")
                show_error(e, "处理自定义图片", self)
                return

            from core.export_service import ExportService
            from gui.dialogs.export_progress_dialog import ExportProgressDialog

            service = self._export_service = ExportService(self)
            dialog = self._export_dialog = ExportProgressDialog(self)
            service.progress_updated.connect(dialog.update_progress)
            service.export_completed.connect(
                lambda msg: self._on_export_completed(True, msg, service=service)
            )
            service.export_failed.connect(
                lambda msg: self._on_export_completed(False, msg, service=service)
            )
            dialog.cancel_requested.connect(service.cancel)

            service.export_all(
                output_dir=dir_path,
                epconfig=self._config,
                logo_mat=export_data.get('logo_mat'),
                overlay_mat=export_data.get('overlay_mat'),
                loop_render_session=export_data.get('loop_render_session'),
                intro_render_session=export_data.get('intro_render_session'),
                aux_images=aux_images,
            )

            dialog.exec()
            return dialog, dir_path
        finally:
            # 覆盖收集、构造、连接、启动及 exec 的所有退出路径；持有 worker
            # 时继续保留暂停和 guard，最终由服务清空引用后的回调收尾。
            self._export_call_active = False
            self._finish_export_preview_lifecycle()

    def _on_simulator(self):
        """打开模拟器预览"""
        import subprocess
        from core.simulator_launcher import (
            MediaLaunchState,
            SimulatorLauncher,
            SimulatorLaunchRequest,
        )

        if not self._config:
            QMessageBox.information(self, "提示", "请先创建或打开项目")
            return
        if not self._script_ready:
            QMessageBox.warning(self, "脚本未就绪", self._script_block_reason)
            return

        if not self._config.loop.file:
            QMessageBox.warning(
                self, "警告",
                "请先配置循环视频文件\n\n"
                "在配置面板的'视频配置'选项卡中选择循环视频文件"
            )
            return

        simulator_launcher = SimulatorLauncher(self._app_dir)
        simulator_path_obj = simulator_launcher.find_simulator()
        simulator_path = str(simulator_path_obj) if simulator_path_obj else os.path.join(
            self._app_dir,
            "simulator", "target", "release", "arknights_pass_simulator.exe",
        )

        if simulator_path_obj is None:
            QMessageBox.information(
                self, "提示",
                f"模拟器未找到\n\n"
                f"模拟器功能需要先编译 Rust 模拟器:\n"
                f"cd simulator && cargo build --release\n\n"
                f"路径: {simulator_path}\n\n"
                f"如果您不需要使用模拟器预览功能，可以忽略此提示。"
            )
            return

        try:
            # 启动模拟器前自动保存，确保磁盘配置与 GUI 状态一致
            # 模拟器从磁盘读取 epconfig.json（不共享 GUI 内存），
            # 而导出直接使用 video_preview.video_path（内存中的当前视频）。
            # 如果用户修改了视频但未保存，模拟器会打开旧视频 → 画面完全不同。
            self._snapshot_active_timeline_state()
            if self._is_modified:
                if self._project_path:
                    try:
                        self._config.save_to_file(self._project_path)
                        self._is_modified = False
                        self._update_title()
                        logger.info(
                            f"模拟器启动前自动保存: {self._project_path}")
                    except Exception as e:
                        logger.warning(f"自动保存失败: {e}")
                        QMessageBox.warning(
                            self, "警告",
                            f"自动保存失败，模拟器预览可能不准确\n\n{e}"
                        )
                        return
                else:
                    QMessageBox.warning(
                        self, "警告",
                        "请先保存项目配置\n\n"
                        "文件 → 保存项目"
                    )
                    return

            config_path = self._project_path

            if not config_path or not os.path.exists(config_path):
                QMessageBox.warning(
                    self, "警告",
                    "请先保存项目配置\n\n"
                    "文件 → 保存项目"
                )
                return

            # 预验证：模拟 Rust 端路径解析，确认视频文件可达
            loop_file = self._config.loop.file
            if loop_file:
                if os.path.isabs(loop_file):
                    resolved_video_path = loop_file
                else:
                    resolved_video_path = os.path.join(
                        self._base_dir, loop_file)

                if not os.path.exists(resolved_video_path):
                    QMessageBox.warning(
                        self, "视频文件不存在",
                        f"模拟器预览需要的视频文件未找到：\n\n"
                        f"配置路径: {loop_file}\n"
                        f"解析路径: {resolved_video_path}\n"
                        f"基础目录: {self._base_dir}\n\n"
                        f"请确认视频文件存在或重新选择视频"
                    )
                    return

            # 图片模式：生成临时视频供模拟器使用
            config_for_simulator = config_path
            if False and self._config.loop.is_image and loop_file:
                try:
                    legacy_media_writer = None
                    import cv2
                    import numpy as np
                    temp_video = os.path.join(
                        self._base_dir, "_sim_temp.mp4")
                    img_data = np.fromfile(
                        resolved_video_path, dtype=np.uint8)
                    frame_bgr = cv2.imdecode(img_data, cv2.IMREAD_COLOR)
                    if frame_bgr is None:
                        raise RuntimeError(
                            f"无法读取图片: {resolved_video_path}")
                    frame_bgr = cv2.resize(frame_bgr, (360, 640))
                    container = legacy_media_writer.open(temp_video, mode='w')
                    stream = container.add_stream('h264', rate=30)
                    stream.width, stream.height = 360, 640
                    stream.pix_fmt = 'yuv420p'
                    for _ in range(30):  # 1 秒循环
                        av_frame = legacy_media_writer.frame_from_ndarray(
                            cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB),
                            format='rgb24')
                        for packet in stream.encode(av_frame):
                            container.mux(packet)
                    for packet in stream.encode():
                        container.mux(packet)
                    container.close()
                    # 写入临时配置，替换图片为临时视频
                    temp_config = self._config.copy()
                    temp_config.loop.file = "_sim_temp.mp4"
                    temp_config.loop.is_image = False
                    temp_config_path = os.path.join(
                        self._base_dir, "_sim_temp_config.json")
                    temp_config.save_to_file(temp_config_path)
                    config_for_simulator = temp_config_path
                    logger.info(f"图片转临时视频: {temp_video}")
                except Exception as e:
                    logger.warning(f"图片转临时视频失败: {e}")
                    QMessageBox.warning(
                        self, "提示",
                        f"图片模式暂不支持模拟器预览\n\n"
                        f"原因：图片转换失败：{e}\n"
                        f"建议先导出素材后再使用模拟器预览")
                    return

            cropbox = self.video_preview.get_cropbox_in_rotated_space()
            rotation = self.video_preview.get_rotation()

            logger.info(
                f"启动模拟器: cropbox={cropbox}, rotation={rotation}, "
                f"config_video={self._config.loop.file}, "
                f"gui_video={self.video_preview.video_path}")

            loop_state = self._collect_preview_media_state(
                self.video_preview,
                self._config.loop.file,
                default_to_full=True,
                is_image=self._config.loop.is_image,
            )
            if loop_state is None:
                resolved_video_path = self._resolve_media_path(
                    self._config.loop.file)
                QMessageBox.warning(
                    self, "视频文件不存在",
                    f"模拟器预览需要的视频文件未找到：\n\n"
                    f"配置路径: {self._config.loop.file}\n"
                    f"解析路径: {resolved_video_path}\n"
                    f"基础目录: {self._base_dir}\n\n"
                    f"请确认视频文件存在或重新选择视频"
                )
                return

            intro_state = None
            if self._config.intro.enabled and self._config.intro.file:
                intro_state = self._collect_preview_media_state(
                    self.intro_preview,
                    self._config.intro.file,
                    default_to_full=True,
                )
                if intro_state is None:
                    resolved_intro_path = self._resolve_media_path(
                        self._config.intro.file
                    )
                    QMessageBox.warning(
                        self, "入场视频文件不存在",
                        f"模拟器预览需要的入场视频文件未找到：\n\n"
                        f"配置路径: {self._config.intro.file}\n"
                        f"解析路径: {resolved_intro_path}\n"
                        f"基础目录: {self._base_dir}\n\n"
                        f"请确认入场视频文件存在或重新选择视频"
                    )
                    return

            config_for_simulator = config_path
            if self._config.loop.is_image:
                try:
                    config_for_simulator, loop_state = (
                        self._bake_loop_image_for_simulator(loop_state)
                    )
                    logger.info(
                        "循环图片已烘焙为临时视频: %s", loop_state["path"]
                    )
                except Exception as e:
                    logger.warning(f"图片转临时视频失败: {e}")
                    QMessageBox.warning(
                        self, "提示",
                        f"图片模式暂不支持模拟器预览\n\n"
                        f"原因：图片转换失败：{e}\n"
                        f"建议先导出素材后再使用模拟器预览"
                    )
                    return

            cropbox = loop_state["cropbox"]
            rotation = loop_state["rotation"]

            logger.info(
                "启动模拟器: "
                f"loop={loop_state}, intro={intro_state}, "
                f"config_video={self._config.loop.file}, "
                f"gui_video={self.video_preview.video_path}"
            )

            # Detect current theme to pass to simulator
            from qfluentwidgets import isDarkTheme
            theme = "dark" if isDarkTheme() else "light"

            launch_request = SimulatorLaunchRequest(
                config_path=config_for_simulator,
                base_dir=self._base_dir,
                app_dir=self._app_dir,
                theme=theme,
                loop=MediaLaunchState(
                    cropbox=cropbox,
                    rotation=rotation,
                    start_frame=loop_state["start_frame"],
                    end_frame=loop_state["end_frame"],
                ),
            )
            if intro_state is not None:
                launch_request = SimulatorLaunchRequest(
                    config_path=config_for_simulator,
                    base_dir=self._base_dir,
                    app_dir=self._app_dir,
                    theme=theme,
                    loop=launch_request.loop,
                    intro=MediaLaunchState(
                        cropbox=intro_state["cropbox"],
                        rotation=intro_state["rotation"],
                        start_frame=intro_state["start_frame"],
                        end_frame=intro_state["end_frame"],
                    ),
                )

            proc = simulator_launcher.launch_cli(
                launch_request,
                simulator_path=simulator_path,
            )

            logger.info(f"模拟器已启动: {simulator_path}")

            # 定期检查进程状态（最多检查5次，覆盖启动后10秒内的崩溃）
            self._simulator_proc = proc
            self._simulator_check_count = 0

            def _check_simulator_periodic():
                self._simulator_check_count += 1
                if self._simulator_proc is None:
                    return
                retcode = self._simulator_proc.poll()
                if retcode is not None and retcode != 0:
                    stderr_output = ""
                    try:
                        stderr_output = self._simulator_proc.stderr.read(
                        ).decode('utf-8', errors='replace')
                    except Exception:
                        pass
                    QMessageBox.warning(
                        self, "模拟器错误",
                        f"模拟器异常退出（返回码: {retcode}）\n\n"
                        f"可能原因：\n"
                        f"• Media toolchain missing or version mismatch\n"
                        f"• 视频文件损坏或格式不支持\n"
                        f"• 配置文件格式错误\n\n"
                        f"路径: {simulator_path}"
                        + (f"\n\n日志输出:\n{stderr_output[:500]}"
                           if stderr_output else "")
                    )
                    self._simulator_proc = None
                    return
                elif retcode is not None:
                    # 正常退出，关闭 stderr pipe
                    try:
                        self._simulator_proc.stderr.close()
                    except Exception:
                        pass
                    self._simulator_proc = None
                    return
                if self._simulator_check_count < 5:
                    QTimer.singleShot(2000, _check_simulator_periodic)
                else:
                    self._simulator_proc = None
            QTimer.singleShot(1000, _check_simulator_periodic)

        except Exception as e:
            logger.error(f"启动模拟器失败: {e}")
            show_error(e, "启动模拟器", self)

    def _on_flasher(self):
        """启动固件烧录工具"""
        if sys.platform != 'win32':
            QMessageBox.warning(self, "不支持", "烧录工具目前仅支持 Windows")
            return

        try:
            from gui.dialogs.flasher_dialog import FlasherDialog
            dialog = FlasherDialog(self)
            dialog.exec()
            self.status_bar.showMessage("烧录工具已启动")
            logger.info("固件烧录对话框已启动")
        except Exception as e:
            logger.error(f"启动烧录工具失败: {e}")
            show_error(e, "启动烧录工具", self)

    def _on_about(self):
        """关于"""
        QMessageBox.about(
            self, f"关于 {APP_NAME}",
            f"<h3>{APP_NAME}</h3>"
            f"<p>版本: {APP_VERSION_LABEL}</p>"
            f"<p>明日方舟通行证素材制作器</p>"
            f"<p>作者: Rafael_ban & 初微弦音 & 涙不在为你而流</p>"
        )

    def _update_recent_menu(self):
        """更新最近打开的文件菜单"""
        self.recent_menu.clear()

        if not self._recent_files:
            action = QAction("无最近文件", self)
            action.setEnabled(False)
            self.recent_menu.addAction(action)
            return

        for i, file_path in enumerate(self._recent_files):
            action = QAction(f"{i + 1}. {file_path}", self)
            action.setData(file_path)
            action.triggered.connect(
                lambda checked,
                path=file_path: self._on_open_recent_file(path))
            self.recent_menu.addAction(action)

        self.recent_menu.addSeparator()

        clear_action = QAction("清空最近文件", self)
        clear_action.triggered.connect(self._clear_recent_files)
        self.recent_menu.addAction(clear_action)

    def _on_open_recent_file(self, file_path: str):
        """打开最近文件"""
        if os.path.exists(file_path):
            self._load_project(file_path)
        else:
            QMessageBox.warning(
                self,
                "文件不存在",
                f"文件不存在:\n{file_path}\n\n将从最近文件列表中移除。"
            )
            self._recent_files.remove(file_path)
            self._update_recent_menu()

    def _clear_recent_files(self):
        """清空最近文件列表"""
        self._recent_files.clear()
        self._update_recent_menu()

    def _add_recent_file(self, file_path: str):
        """添加文件到最近打开列表"""
        if file_path in self._recent_files:
            self._recent_files.remove(file_path)

        self._recent_files.insert(0, file_path)

        if len(self._recent_files) > self._max_recent_files:
            self._recent_files.pop()

        self._update_recent_menu()

    def _reset_undo_history(self):
        """Reset the undo baseline to the current config (project open/new)."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._undo_baseline = self._config.to_dict() if self._config else {}
        self._undo_timer.stop()
        self._set_undo_redo_enabled()

    def _mark_undo_change(self):
        """An edit happened — (re)start the debounce that coalesces the burst."""
        if self._applying_undo or not self._config:
            return
        self._undo_timer.start(800)

    def _commit_undo_snapshot(self):
        """Debounce fired: push the pre-burst baseline as one undo step."""
        if not self._config:
            return
        current = self._config.to_dict()
        if current == self._undo_baseline:
            return  # nothing actually changed
        self._undo_stack.append(self._undo_baseline)
        if len(self._undo_stack) > self._max_history:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._undo_baseline = current
        self._set_undo_redo_enabled()

    def _on_undo(self):
        """撤销操作"""
        # Flush a pending debounce so an in-flight burst becomes undoable first.
        if self._undo_timer.isActive():
            self._undo_timer.stop()
            self._commit_undo_snapshot()
        if not self._undo_stack:
            return
        prev_state = self._undo_stack.pop()
        self._redo_stack.append(self._config.to_dict() if self._config else {})
        self._apply_undo_state(prev_state)
        self._undo_baseline = prev_state
        self._set_undo_redo_enabled()
        self.status_bar.showMessage("已撤销", 2000)

    def _on_redo(self):
        """重做操作"""
        if not self._redo_stack:
            return
        next_state = self._redo_stack.pop()
        self._undo_stack.append(self._config.to_dict() if self._config else {})
        self._apply_undo_state(next_state)
        self._undo_baseline = next_state
        self._set_undo_redo_enabled()
        self.status_bar.showMessage("已重做", 2000)

    def _apply_undo_state(self, state: dict):
        """Restore a config snapshot to the model, panels and previews.

        A media file is reloaded only when its path actually changed between
        snapshots; otherwise the still-loaded preview just has its editor state
        (crop/rotation/trim) re-applied.
        """
        if not state:
            return
        old_loop = self._config.loop.file if self._config else ""
        old_intro = self._config.intro.file if self._config else ""
        old_loop_is_image = self._config.loop.is_image if self._config else False

        self._applying_undo = True
        try:
            self._config = EPConfig.from_dict(state)
            self._update_ui_from_config()  # panels + json + set_epconfig

            self._apply_undo_track(
                self.video_preview, self._config.editor.loop,
                old_loop, self._config.loop.file,
                is_image=self._config.loop.is_image,
                image_changed=old_loop_is_image != self._config.loop.is_image,
            )
            self._apply_undo_track(
                self.intro_preview, self._config.editor.intro,
                old_intro, self._config.intro.file if self._config.intro.enabled else "",
                is_image=False, image_changed=False,
            )
        finally:
            self._applying_undo = False

    def _apply_undo_track(self, preview, track, old_file, new_file, *,
                          is_image, image_changed):
        resolved_new = self._resolve_media_path(new_file) if new_file else ""
        if (old_file != new_file or image_changed) and resolved_new and os.path.exists(resolved_new):
            # File changed: reload; _finish_editor_restore re-applies the crop
            # after the (async) load via the pending-restore machinery.
            self._begin_editor_restore(preview, track)
            if is_image:
                self._load_loop_image(resolved_new)
            elif not preview.load_video(resolved_new):
                self._cancel_editor_restore(preview)
        elif new_file and self._preview_has_loaded_media(preview):
            # Same file still loaded: re-apply the editor state directly.
            self._restoring_editor_state = True
            try:
                if track.rotation != preview.get_rotation():
                    preview.set_rotation(track.rotation)
                if track.crop:
                    preview.set_cropbox(*track.crop)
                    if list(preview.get_cropbox()) != [int(v) for v in track.crop]:
                        self.status_bar.showMessage(
                            "裁剪框已按当前屏幕比例修正", 5000)
                if 0 <= track.in_frame <= track.out_frame:
                    total = max(1, int(getattr(preview, "total_frames", 1)))
                    in_f, out_f = min(track.in_frame, total - 1), min(track.out_frame, total - 1)
                    if preview is self.video_preview:
                        self._loop_in_out = (in_f, out_f)
                    else:
                        self._intro_in_out = (in_f, out_f)
                    if self._is_timeline_bound_to(preview):
                        self.timeline.set_in_point(in_f)
                        self.timeline.set_out_point(out_f)
                    start, end = self._get_trim_bounds(preview)
                    preview.set_timeline_range(start, end)
            finally:
                self._restoring_editor_state = False

    def _set_undo_redo_enabled(self):
        has_undo = len(self._undo_stack) > 0
        has_redo = len(self._redo_stack) > 0
        for attr, enabled in (
            ("action_undo", has_undo), ("action_redo", has_redo),
            ("menu_action_undo", has_undo), ("menu_action_redo", has_redo),
            ("_shortcut_undo", has_undo), ("_shortcut_redo", has_redo),
        ):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setEnabled(enabled)

    def _update_ui_from_config(self):
        """从配置更新UI"""
        if not self._config:
            return

        self.advanced_config_panel.set_config(self._config, self._base_dir)
        self.basic_config_panel.set_config(self._config, self._base_dir)
        self.json_preview.set_config(self._config, self._base_dir)
        self.video_preview.set_epconfig(self._config)

        self._is_modified = True
        self._update_title()

    def _pause_all_videos(self):
        """暂停所有正在播放的视频预览，记录播放状态以便返回时恢复"""
        self._videos_were_playing = []
        all_previews = [
            self.video_preview,
            self.intro_preview,
            self.frame_capture_preview,
            self.transition_preview.preview_in,
            self.transition_preview.preview_loop,
        ]
        for p in all_previews:
            if p.is_playing:
                self._videos_were_playing.append(p)
                p.pause()

    @staticmethod
    def _preview_media_identity(preview):
        """返回恢复播放前必须保持一致的预览媒体身份。"""
        try:
            path = getattr(preview, "video_path", "")
        except RuntimeError:
            return None
        if not path:
            return ""
        return str(Path(path).resolve())

    def _pause_export_previews(self):
        """在冻结导出 job 前暂停并记录原本播放中的同一批预览。"""
        page_paused_previews = self._videos_were_playing
        # pause 会同步发 playback_state_changed；身份必须在这些槽运行前冻结。
        paused_previews = [
            (preview, self._preview_media_identity(preview))
            for preview in (
                self.video_preview,
                self.intro_preview,
                self.frame_capture_preview,
                self.transition_preview.preview_in,
                self.transition_preview.preview_loop,
            )
            if preview.is_playing
        ]
        try:
            self._pause_all_videos()
        except Exception:
            self._restore_export_previews(paused_previews)
            raise
        finally:
            self._videos_were_playing = page_paused_previews
        return paused_previews

    def _restore_export_previews(self, paused_previews):
        """仅恢复导出开始前播放且媒体没有更换的预览。"""
        for preview, media_identity in paused_previews:
            try:
                if (
                    not preview.is_playing
                    and self._preview_media_identity(preview) == media_identity
                ):
                    preview.play()
            except RuntimeError:
                logger.debug("export preview was deleted before playback restore")

    def _export_service_has_pending_worker(self):
        """返回导出服务是否仍持有待处理 terminal 的 worker。"""
        service = getattr(self, "_export_service", None)
        worker = getattr(service, "_worker", None)
        # QThread.run() 返回到 queued terminal 回调清掉 service._worker 之间，
        # isRunning() 已是 False，但旧回调仍可能投递到当前窗口。该窗口必须
        # 继续保留导出 guard、dialog 和暂停状态，直到 ExportService 清空引用。
        return worker is not None

    def _finish_export_preview_lifecycle(self):
        """入口已退出且服务没有待收尾 worker 时，恢复本次暂停的预览。"""
        if self._export_call_active:
            return False
        if self._export_service_has_pending_worker():
            logger.debug("导出服务仍持有 worker，等待 finished 收尾后恢复预览")
            return False
        paused_previews = self._export_paused_previews
        self._export_paused_previews = []
        self._export_in_progress = False
        self._restore_export_previews(paused_previews)
        return True

    def _resume_videos(self):
        """恢复之前暂停的视频播放"""
        for p in self._videos_were_playing:
            p.play()
        self._videos_were_playing = []

    def _on_sidebar_firmware(self):
        """侧边栏：固件烧录"""
        self.btn_firmware.setChecked(True)
        self.btn_material.setChecked(False)
        self.btn_forum.setChecked(False)
        self.btn_about.setChecked(False)
        self.btn_remote.setChecked(False)
        self.btn_usbControl.setChecked(True)
        self.btn_usbControl.setChecked(False)
        self.btn_settings.setChecked(False)

        self._pause_all_videos()

        self.splitter.setVisible(False)
        if hasattr(self, '_forum_widget'):
            self._forum_widget.setVisible(False)
        if hasattr(self, '_settings_page'):
            self._settings_page.setVisible(False)
        if hasattr(self, '_about_widget'):
            self._about_widget.setVisible(False)
        if hasattr(self, '_remote_page'):
            self._remote_page.setVisible(False)
        if hasattr(self, '_usbControl_page'):
            self._usbControl_page.setVisible(False)

        if not hasattr(self, '_flasher_widget'):
            from gui.dialogs.flasher_dialog import FlasherDialog

            self._flasher_widget = QWidget()
            self._flasher_widget_layout = QVBoxLayout(self._flasher_widget)

            self._flasher_dialog = FlasherDialog(self)
            self._flasher_dialog.setWindowFlags(Qt.WindowType.Widget)
            self._flasher_widget_layout.addWidget(self._flasher_dialog)
            self.content_layout.addWidget(self._flasher_widget)

        self._flasher_widget.setVisible(True)
        self.status_bar.showMessage("固件烧录模式")

    def _on_sidebar_material(self):
        """侧边栏：素材制作"""
        self.btn_firmware.setChecked(False)
        self.btn_material.setChecked(True)
        self.btn_forum.setChecked(False)
        self.btn_about.setChecked(False)
        self.btn_remote.setChecked(False)
        self.btn_usbControl.setChecked(True)
        self.btn_usbControl.setChecked(False)
        self.btn_settings.setChecked(False)

        if hasattr(self, '_forum_widget'):
            self._forum_widget.setVisible(False)
        if hasattr(self, '_settings_page'):
            self._settings_page.setVisible(False)
        if hasattr(self, '_about_widget'):
            self._about_widget.setVisible(False)
        if hasattr(self, '_flasher_widget'):
            self._flasher_widget.setVisible(False)
        if hasattr(self, '_remote_page'):
            self._remote_page.setVisible(False)
        if hasattr(self, '_usbControl_page'):
            self._usbControl_page.setVisible(False)
        self.splitter.setVisible(True)
        self.status_bar.showMessage("素材制作模式")

        self._resume_videos()

    def _on_sidebar_forum(self):
        """侧边栏：素材论坛"""
        self.btn_firmware.setChecked(False)
        self.btn_material.setChecked(False)
        self.btn_forum.setChecked(True)
        self.btn_about.setChecked(False)
        self.btn_remote.setChecked(False)
        self.btn_usbControl.setChecked(True)
        self.btn_usbControl.setChecked(False)
        self.btn_settings.setChecked(False)

        self._pause_all_videos()

        self.splitter.setVisible(False)
        if hasattr(self, '_settings_page'):
            self._settings_page.setVisible(False)
        if hasattr(self, '_about_widget'):
            self._about_widget.setVisible(False)
        if hasattr(self, '_flasher_widget'):
            self._flasher_widget.setVisible(False)
        if hasattr(self, '_remote_page'):
            self._remote_page.setVisible(False)
        if hasattr(self, '_usbControl_page'):
            self._usbControl_page.setVisible(False)
        if not hasattr(self, '_forum_widget'):
            try:
                from gui.plugin_host import (
                    create_material_forum_plugin,
                )
                self._forum_plugin_handle = create_material_forum_plugin(
                    parent=self,
                    context=self._create_forum_plugin_context(),
                )
                self._forum_widget = self._forum_plugin_handle.widget
                self.content_layout.addWidget(self._forum_widget)
            except ImportError as exc:
                logger.error("素材论坛模块加载失败，缺少必要依赖", exc_info=True)
                missing_pkg = getattr(exc, 'name', None) or str(exc)
                QMessageBox.warning(
                    self, "模块加载失败",
                    f"素材论坛所需的依赖库缺失，请检查安装是否完整。\n\n"
                    f"缺少的包: {missing_pkg}\n\n"
                    "可能需要: httpx, keyring, platformdirs, fido2 等。")
                self._on_sidebar_material()
                return
            except Exception as exc:
                logger.error("素材论坛初始化失败: %s", exc, exc_info=True)
                QMessageBox.warning(
                    self, "初始化失败",
                    f"素材论坛初始化时发生错误:\n{type(exc).__name__}: {exc}\n\n"
                    "请查看日志文件获取详细信息。")
                self._on_sidebar_material()
                return

        self._forum_widget.setVisible(True)
        self.status_bar.showMessage("素材论坛模式")

    def _create_forum_plugin_context(self):
        """Create plugin context from the current user settings."""
        from gui.plugin_host import default_plugin_context

        return default_plugin_context(self._read_user_settings())

    def _on_sidebar_about(self):
        """侧边栏：项目介绍"""
        self.btn_firmware.setChecked(False)
        self.btn_material.setChecked(False)
        self.btn_forum.setChecked(False)
        self.btn_about.setChecked(True)
        self.btn_remote.setChecked(False)
        self.btn_usbControl.setChecked(True)
        self.btn_usbControl.setChecked(False)
        self.btn_settings.setChecked(False)

        self._pause_all_videos()

        self.splitter.setVisible(False)
        if hasattr(self, '_forum_widget'):
            self._forum_widget.setVisible(False)
        if hasattr(self, '_settings_page'):
            self._settings_page.setVisible(False)
        if hasattr(self, '_flasher_widget'):
            self._flasher_widget.setVisible(False)
        if hasattr(self, '_remote_page'):
            self._remote_page.setVisible(False)
        if hasattr(self, '_usbControl_page'):
            self._usbControl_page.setVisible(False)
        if not hasattr(self, '_about_widget'):
            self._about_widget = QWidget()
            self._about_widget.setVisible(False)

            about_layout = QVBoxLayout(self._about_widget)
            about_layout.setContentsMargins(20, 10, 20, 10)
            about_layout.setSpacing(15)

            title_label = SubtitleLabel("项目介绍")
            about_layout.addWidget(title_label)

            # 滚动区域
            scroll_area = ScrollArea()
            scroll_area.setWidgetResizable(True)
            scroll_area.enableTransparentBackground()

            scroll_content = QWidget()
            scroll_layout = QVBoxLayout(scroll_content)
            scroll_layout.setContentsMargins(0, 0, 10, 0)
            scroll_layout.setSpacing(12)

            # 卡片 1 — 项目概述
            card_overview = CardWidget()
            card_overview_layout = QVBoxLayout(card_overview)
            card_overview_layout.setContentsMargins(20, 16, 20, 16)
            card_overview_layout.setSpacing(8)
            card_overview_layout.addWidget(StrongBodyLabel("项目概述"))
            card_overview_layout.addWidget(BodyLabel(
                "一款基于 F1C200S 的迷你 Linux 手持开发板，"
                "面向折腾与二次开发的开源硬件项目。"
            ))
            link_btn = HyperlinkButton("https://ep.iccmc.cc", "访问项目官网")
            card_overview_layout.addWidget(link_btn)
            scroll_layout.addWidget(card_overview)

            # 卡片 2 — 硬件规格
            card_hw = CardWidget()
            card_hw_layout = QVBoxLayout(card_hw)
            card_hw_layout.setContentsMargins(20, 16, 20, 16)
            card_hw_layout.setSpacing(6)
            card_hw_layout.addWidget(StrongBodyLabel("硬件规格"))
            hw_specs = [
                ("主控", "F1C200S (ARM926EJ-S)，默认 408MHz，可超频至 720MHz，内置 64MB RAM"),
                ("存储", "TF 卡 / SPI Flash"),
                ("屏幕", "3.0 英寸 360×640 竖屏，ST7701S 驱动，支持 H.264 硬件解码"),
                ("电池", "1500mAh 锂电池，TP4056 充电管理"),
                ("接口", "I²C、UART×2、SPI、GPIO×3、ADC"),
            ]
            for key, value in hw_specs:
                row = QHBoxLayout()
                row.setSpacing(8)
                key_label = CaptionLabel(key)
                key_label.setFixedWidth(40)
                row.addWidget(key_label)
                row.addWidget(BodyLabel(value), 1)
                card_hw_layout.addLayout(row)
            scroll_layout.addWidget(card_hw)

            # 卡片 3 — 软件平台
            card_sw = CardWidget()
            card_sw_layout = QVBoxLayout(card_sw)
            card_sw_layout.setContentsMargins(20, 16, 20, 16)
            card_sw_layout.setSpacing(6)
            card_sw_layout.addWidget(StrongBodyLabel("软件平台"))
            sw_specs = [
                ("系统", "Buildroot 构建，Linux 主线 5.4.77 内核"),
                ("协议", "完全开源（硬件 / 软件资料）"),
                ("版本", f"素材制作器 v{APP_VERSION_LABEL}"),
            ]
            for key, value in sw_specs:
                row = QHBoxLayout()
                row.setSpacing(8)
                key_label = CaptionLabel(key)
                key_label.setFixedWidth(40)
                row.addWidget(key_label)
                row.addWidget(BodyLabel(value), 1)
                card_sw_layout.addLayout(row)
            scroll_layout.addWidget(card_sw)

            # 卡片 4 — 主要特性
            card_features = CardWidget()
            card_features_layout = QVBoxLayout(card_features)
            card_features_layout.setContentsMargins(20, 16, 20, 16)
            card_features_layout.setSpacing(6)
            card_features_layout.addWidget(StrongBodyLabel("主要特性"))
            features = [
                "高性能主控 — ARM926EJ-S 核心，支持超频至 720MHz",
                "高清竖屏显示 — 3.0 英寸 360×640，H.264 硬件解码",
                "完善供电方案 — 1500mAh 锂电池 + TP4056 充电管理",
                "丰富扩展接口 — I²C / UART / SPI / GPIO / ADC",
                "主线 Linux 支持 — Buildroot + Linux 5.4.77 内核",
                "完全开源 — 硬件与软件资料全部开源，欢迎社区共建",
            ]
            for feat in features:
                card_features_layout.addWidget(BodyLabel(f"• {feat}"))
            scroll_layout.addWidget(card_features)

            scroll_layout.addStretch()
            scroll_area.setWidget(scroll_content)
            about_layout.addWidget(scroll_area)

            self.content_layout.addWidget(self._about_widget)

        self._about_widget.setVisible(True)

        self.status_bar.showMessage("项目介绍")

    def _on_settings_mode_combo_changed(self, index: int):
        """下拉框切换设置模式"""
        mode = self.settings_mode_combo.currentData()
        self._on_settings_mode_changed(mode)

    def _show_loop_tab_only(self):
        """基础模式：仅显示循环视频标签页"""
        if not hasattr(self, 'preview_tabs'):
            return

        tab_bar = self.preview_tabs.tabBar
        # 阻塞 tabBar 信号，防止 setTabVisible 内部
        # 发射虚假 currentChanged 导致 stackedWidget 索引被污染
        tab_bar.blockSignals(True)
        try:
            if 3 < self.preview_tabs.count():
                self.preview_tabs.setTabVisible(3, True)
            for i in [0, 1, 2]:
                if i < self.preview_tabs.count():
                    self.preview_tabs.setTabVisible(i, False)
        finally:
            tab_bar.blockSignals(False)

        # 手动设置正确状态
        self._fix_tab_selected_state(3)
        self.preview_tabs.stackedWidget.setCurrentIndex(3)
        if hasattr(self, 'timeline'):
            self._on_preview_tab_changed(3)

    def _show_all_tabs(self):
        """高级模式：显示所有标签页"""
        if not hasattr(self, 'preview_tabs'):
            return

        tab_bar = self.preview_tabs.tabBar
        current = tab_bar._currentIndex

        tab_bar.blockSignals(True)
        try:
            for i in range(self.preview_tabs.count()):
                self.preview_tabs.setTabVisible(i, True)
        finally:
            tab_bar.blockSignals(False)

        self._fix_tab_selected_state(current)
        self.preview_tabs.stackedWidget.setCurrentIndex(current)
        if hasattr(self, 'timeline'):
            self._on_preview_tab_changed(current)

    def _fix_tab_selected_state(self, active_index: int):
        """强制清理 TabBar 所有 item 的 isSelected，仅保留指定索引"""
        tab_bar = self.preview_tabs.tabBar
        for idx, item in enumerate(tab_bar.items):
            item.setSelected(idx == active_index)
        tab_bar._currentIndex = active_index

    def _on_settings_mode_changed(self, mode):
        """设置模式切换"""
        try:
            if mode == "basic":
                # 切换前先同步，避免丢失高级面板的修改
                if self.advanced_config_panel.isVisible():
                    self.advanced_config_panel.update_config_from_ui()

                self.advanced_config_panel.setVisible(False)
                self.basic_config_panel.setVisible(True)

                if self._config:
                    self.basic_config_panel.set_config(
                        self._config, self._base_dir)

                self.status_bar.showMessage("基础设置模式 - 简化界面")
                self._show_loop_tab_only()
            elif mode == "advanced":
                # 切换前先同步，避免丢失基础面板的修改
                if self.basic_config_panel.isVisible():
                    self.basic_config_panel.update_config_from_ui()

                self.advanced_config_panel.setVisible(True)
                self.basic_config_panel.setVisible(False)

                if self._config:
                    self.advanced_config_panel.set_config(
                        self._config, self._base_dir)

                self.status_bar.showMessage("高级设置模式 - 完整界面")
                self._show_all_tabs()
        except Exception as e:
            logger.error(f"设置模式切换错误: {e}")

    def _on_sidebar_remote(self):
        """侧边栏：远程管理"""
        self.btn_firmware.setChecked(False)
        self.btn_material.setChecked(False)
        self.btn_forum.setChecked(False)
        self.btn_about.setChecked(False)
        self.btn_remote.setChecked(True)
        self.btn_usbControl.setChecked(False)
        self.btn_settings.setChecked(False)

        self._pause_all_videos()

        self.splitter.setVisible(False)
        if hasattr(self, '_forum_widget'):
            self._forum_widget.setVisible(False)
        if hasattr(self, '_settings_page'):
            self._settings_page.setVisible(False)
        if hasattr(self, '_about_widget'):
            self._about_widget.setVisible(False)
        if hasattr(self, '_flasher_widget'):
            self._flasher_widget.setVisible(False)
        if hasattr(self, '_usbControl_page'):
            self._usbControl_page.setVisible(False)
        if not hasattr(self, '_remote_page'):
            from gui.widgets.remote_page import RemotePage
            self._remote_page = RemotePage(parent=self)
            self.content_layout.addWidget(self._remote_page)

            try:
                import json
                config_file = os.path.join(self._app_dir, "config",
                                           "user_settings.json")
                if os.path.exists(config_file):
                    with open(config_file, "r", encoding="utf-8") as f:
                        settings = json.load(f)
                    self._remote_page.load_settings(settings)
            except Exception:
                pass

        self._remote_page.setVisible(True)
        self.status_bar.showMessage("远程管理模式")

    def _on_sidebar_usbControl(self):
        """侧边栏：USB控制"""
        self.btn_firmware.setChecked(False)
        self.btn_material.setChecked(False)
        self.btn_forum.setChecked(False)
        self.btn_about.setChecked(False)
        self.btn_remote.setChecked(False)
        self.btn_usbControl.setChecked(True)
        self.btn_settings.setChecked(False)
        self._pause_all_videos()
        if hasattr(self, '_forum_widget'):
            self._forum_widget.setVisible(False)
        self.splitter.setVisible(False)
        if hasattr(self, '_about_widget'):
            self._about_widget.setVisible(False)
        if hasattr(self, '_flasher_widget'):
            self._flasher_widget.setVisible(False)
        if hasattr(self, '_remote_page'):
            self._remote_page.setVisible(False)
        if hasattr(self, '_settings_page'):
            self._settings_page.setVisible(False)
        if not hasattr(self, '_usbControl_page'):
            from gui.widgets.usb_control_page import UsbControlPage
            self._usbControl_page = UsbControlPage(parent=self)
            self.content_layout.addWidget(self._usbControl_page)
            try:
                import json
                config_file = os.path.join(self._app_dir, "config",
                                           "user_settings.json")
                if os.path.exists(config_file):
                    with open(config_file, "r", encoding="utf-8") as f:
                        settings = json.load(f)
                    self._usbControl_page.load_settings(settings)
            except Exception:
                pass

        self._usbControl_page.setVisible(True)
        self.status_bar.showMessage("USB管理器模式")

    def _on_sidebar_settings(self):
        """侧边栏：设置"""
        self.btn_firmware.setChecked(False)
        self.btn_material.setChecked(False)
        self.btn_forum.setChecked(False)
        self.btn_about.setChecked(False)
        self.btn_remote.setChecked(False)
        self.btn_usbControl.setChecked(False)
        self.btn_settings.setChecked(True)

        self._pause_all_videos()

        if hasattr(self, '_forum_widget'):
            self._forum_widget.setVisible(False)
        self.splitter.setVisible(False)
        if hasattr(self, '_about_widget'):
            self._about_widget.setVisible(False)
        if hasattr(self, '_flasher_widget'):
            self._flasher_widget.setVisible(False)
        if hasattr(self, '_remote_page'):
            self._remote_page.setVisible(False)
        if hasattr(self, '_usbControl_page'):
            self._usbControl_page.setVisible(False)
        if not hasattr(self, '_settings_page'):
            from gui.widgets.settings_page import SettingsPage
            self._settings_page = SettingsPage(parent=self)
            self._settings_page.setting_changed.connect(
                self._on_setting_changed)
            self._settings_page.check_update_requested.connect(
                self._on_check_update)
            self._settings_page.show_shortcuts_requested.connect(
                self._on_shortcuts)
            self.content_layout.addWidget(self._settings_page)

        self._load_settings_to_page()
        self._settings_page.setVisible(True)

        self.status_bar.showMessage("设置模式")

    def _on_nav_file(self):
        """顶部导航：文件"""
        from PyQt6.QtWidgets import QMenu, QMessageBox
        from PyQt6.QtGui import QAction

        try:
            file_menu = QMenu(self)

            new_action = QAction("新建项目", self)
            new_action.triggered.connect(self._on_new_project)
            file_menu.addAction(new_action)

            open_action = QAction("打开项目", self)
            open_action.triggered.connect(self._on_open_project)
            file_menu.addAction(open_action)

            save_action = QAction("保存项目", self)
            save_action.triggered.connect(self._on_save_project)
            file_menu.addAction(save_action)

            save_as_action = QAction("另存为", self)
            save_as_action.triggered.connect(self._on_save_as)
            file_menu.addAction(save_as_action)

            pos = self.btn_nav_file.mapToGlobal(
                self.btn_nav_file.rect().bottomLeft())
            file_menu.exec(pos)
        except Exception as e:
            logger.error(f"文件菜单错误: {e}")
            show_error(e, "文件菜单", self)

    def _on_nav_basic(self):
        """顶部导航：基础设置"""
        try:
            self._on_sidebar_material()

            if hasattr(
                    self,
                    'advanced_config_panel') and hasattr(
                    self,
                    'basic_config_panel'):
                self.advanced_config_panel.setVisible(False)
                self.basic_config_panel.setVisible(True)
                self.status_bar.showMessage("基础设置模式 - 简化界面")

            self._show_loop_tab_only()
        except Exception as e:
            logger.error(f"基础设置切换错误: {e}")

    def _on_nav_advanced(self):
        """顶部导航：高级设置"""
        try:
            self._on_sidebar_material()

            if hasattr(
                    self,
                    'advanced_config_panel') and hasattr(
                    self,
                    'basic_config_panel'):
                self.advanced_config_panel.setVisible(True)
                self.basic_config_panel.setVisible(False)
                self.status_bar.showMessage("高级设置模式 - 完整界面")

            self._show_all_tabs()
        except Exception as e:
            logger.error(f"高级设置切换错误: {e}")

    def _on_nav_help(self):
        """顶部导航：帮助"""
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QAction

        try:
            help_menu = QMenu(self)

            shortcuts_action = QAction("快捷键帮助", self)
            shortcuts_action.triggered.connect(self._on_shortcuts)
            help_menu.addAction(shortcuts_action)

            update_action = QAction("检查更新", self)
            update_action.triggered.connect(self._on_check_update)
            help_menu.addAction(update_action)

            about_action = QAction("关于", self)
            about_action.triggered.connect(self._on_about)
            help_menu.addAction(about_action)

            pos = self.btn_nav_help.mapToGlobal(
                self.btn_nav_help.rect().bottomLeft())
            help_menu.exec(pos)
        except Exception as e:
            logger.error(f"帮助菜单错误: {e}")
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "错误", f"帮助菜单加载失败: {str(e)}")

    def _on_check_update(self):
        """手动检查更新"""
        from gui.dialogs.update_dialog import UpdateDialog
        dialog = UpdateDialog(self, auto_check=True)
        dialog.exec()

    def _check_update_on_startup(self):
        """启动时后台检查更新"""
        try:
            from datetime import datetime, timedelta
            from config.constants import UPDATE_CHECK_INTERVAL_HOURS

            settings = QSettings("ArknightsPassMaker", "MainWindow")

            auto_check_enabled = settings.value(
                "auto_check_updates", True, type=bool)

            try:
                import json
                config_dir = os.path.join(self._app_dir, "config")
                config_file = os.path.join(config_dir, "user_settings.json")
                if os.path.exists(config_file):
                    with open(config_file, "r", encoding="utf-8") as f:
                        user_settings = json.load(f)
                        auto_check_enabled = user_settings.get(
                            'auto_update', True)
            except Exception:
                pass

            if not auto_check_enabled:
                return

            # 检查上次检查时间（避免频繁检查）
            last_check = settings.value("last_update_check", "")
            if last_check:
                try:
                    last_check_time = datetime.fromisoformat(last_check)
                    if datetime.now() - last_check_time < timedelta(
                            hours=UPDATE_CHECK_INTERVAL_HOURS):
                        logger.debug("跳过更新检查（24小时内已检查）")
                        return
                except ValueError:
                    pass

            from core.update_service import UpdateService

            self._startup_update_service = UpdateService(APP_VERSION, self)
            self._startup_update_service.check_completed.connect(
                self._on_startup_update_check_completed)
            self._startup_update_service.check_failed.connect(
                self._on_startup_update_check_failed)
            self._startup_update_service.check_for_updates()

            settings.setValue(
                "last_update_check", datetime.now().isoformat())

        except Exception as e:
            logger.error(f"启动时更新检查失败: {e}", exc_info=True)

    def _check_crash_recovery(self):
        """启动时检查崩溃恢复"""
        try:
            recovery_list = self._crash_recovery_service.check_crash_recovery()

            if not recovery_list:
                logger.info("没有发现可恢复的项目")
                return

            from gui.dialogs.crash_recovery_dialog import CrashRecoveryDialog

            dialog = CrashRecoveryDialog(self._crash_recovery_service, self)
            dialog.recovery_requested.connect(self._on_recovery_requested)

            result = dialog.exec()

            if result == QDialog.DialogCode.Accepted:
                logger.info("崩溃恢复对话框已关闭")

        except Exception as e:
            logger.error(f"检查崩溃恢复失败: {e}")

    def _on_recovery_requested(self, recovery_info, target_path):
        """恢复项目请求"""
        try:
            self._load_project(target_path)
            self._crash_recovery_service.cleanup_old_recoveries(
                max_age_hours=24)

            logger.info(f"项目已恢复: {target_path}")

        except Exception as e:
            logger.error(f"恢复项目失败: {e}")
            show_error(e, "恢复项目", self)

    def _on_error_occurred(self, error_info):
        """错误发生时的处理"""
        self.status_bar.showMessage(f"错误: {error_info.user_message}", 5000)

    def _on_startup_update_check_completed(self, release_info):
        """启动时更新检查完成"""
        if release_info:
            result = QMessageBox.information(
                self, "发现新版本",
                f"发现新版本 v{release_info.version}\n\n"
                f"当前版本: v{APP_VERSION_LABEL}\n\n"
                f"是否立即查看更新详情？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if result == QMessageBox.StandardButton.Yes:
                self._on_check_update()

        if hasattr(self, '_startup_update_service'):
            self._startup_update_service.deleteLater()
            del self._startup_update_service

    def _on_startup_update_check_failed(self, error_msg: str):
        """启动时更新检查失败（静默失败）"""
        logger.debug(f"启动时更新检查失败: {error_msg}")
        if hasattr(self, '_startup_update_service'):
            self._startup_update_service.deleteLater()
            del self._startup_update_service

    def _on_config_changed(self):
        """配置变更"""
        self._is_modified = True
        self._update_title()
        self._mark_undo_change()

        if self._config:
            self.json_preview.set_config(self._config, self._base_dir)
            self.video_preview.set_epconfig(self._config)
            target_w, target_h = self._get_target_resolution()
            self.video_preview.set_target_resolution(target_w, target_h)
            self.intro_preview.set_target_resolution(target_w, target_h)
            # 分辨率改变时过渡图裁剪框也要跟着换比例(见 _apply_project_config)。
            self.transition_preview.set_target_resolution(target_w, target_h)

    def _on_video_file_selected(self, path: str):
        """视频文件被选择"""
        logger.info(f"视频文件被选择: {path}")

        import os
        path_exists = os.path.exists(path)
        logger.info(f"路径存在检查: {path_exists}")

        try:
            path_exists_raw = os.path.exists(path)
            logger.info(f"原始路径检查: {path_exists_raw}")

            if isinstance(path, str):
                path_exists_unicode = os.path.exists(path)
                logger.info(f"Unicode 路径检查: {path_exists_unicode}")
        except Exception as e:
            logger.error(f"路径检查出错: {e}")

        if path:
            logger.info("尝试加载文件...")
            try:
                ext = os.path.splitext(path)[1].lower()
                image_extensions = [".jpg", ".jpeg", ".png", ".bmp", ".gif"]

                if ext in image_extensions:
                    logger.info("加载图片文件...")
                    self.video_preview.load_static_image_from_file(path)
                else:
                    logger.info("加载视频文件...")
                    # 同步拒绝(文件不存在)即时弹窗;探测失败会经
                    # load_failed 信号弹窗,预览区同时显示错误文案。
                    if not self.video_preview.load_video(path):
                        QMessageBox.warning(
                            self, "加载失败",
                            "无法开始加载视频，请确认文件可被 VapourSynth/lsmas 解码。")
                        return
                    if self._config:
                        # 换了新素材:旧文件的取景状态不再适用。
                        self._config.editor.loop = EditorTrackState()

                logger.info("将时间轴连接到video_preview")
                self._connect_timeline_to_preview(self.video_preview)

                if hasattr(
                        self,
                        'basic_config_panel') and self.basic_config_panel.isVisible():
                    logger.info("基础模式下，不自动切换标签页")
                else:
                    self.preview_tabs.setCurrentIndex(3)
            except Exception as e:
                logger.error(f"加载文件出错: {e}")
        else:
            logger.warning(f"视频文件路径为空")

    def _on_intro_video_selected(self, path: str):
        """入场视频文件被选择"""
        logger.info(f"入场视频文件被选择: {path}")
        if path and os.path.exists(path):
            if self.intro_preview.load_video(path):
                if self._config:
                    # 换了新素材:旧文件的取景状态不再适用。
                    self._config.editor.intro = EditorTrackState()
                self.preview_tabs.setCurrentIndex(0)
        else:
            logger.warning(f"入场视频文件不存在: {path}")

    def _on_setting_changed(self, setting_name: str, value):
        """SettingsPage 发射的统一设置变更处理器"""
        logger.info(f"应用设置: {setting_name} = {value}")

        try:
            import json
            config_dir = os.path.join(
                self._app_dir, "config")
            config_file = os.path.join(config_dir, "user_settings.json")

            settings = {}
            if os.path.exists(config_file):
                with open(config_file, "r", encoding="utf-8") as f:
                    settings = json.load(f)

            settings[setting_name] = value

            if setting_name == 'theme_image' and value:
                settings['theme'] = '自定义图片'

            os.makedirs(config_dir, exist_ok=True)
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)

            self._apply_instant_settings(setting_name, value)

            self.status_bar.showMessage(f"设置已应用: {setting_name}")

        except Exception as e:
            logger.error(f"应用设置失败: {e}")
            self.status_bar.showMessage(f"应用设置失败: {str(e)}")

    def _apply_instant_settings(self, setting_name, value):
        """应用即时生效的设置"""
        if setting_name == 'show_status_bar':
            self.statusBar().setVisible(value)

        elif setting_name == 'theme':
            self._apply_theme_change(value)

        elif setting_name == 'theme_color':
            self._apply_theme_color(value)
            # 如果当前有自定义背景图片，刷新图片模式的 QMainWindow 级 QSS（使用新主题色）
            if self._bg_pixmap is not None:
                self._apply_image_mode_styles()

        elif setting_name == 'theme_image':
            if value:
                self._apply_theme_image(value)

        elif setting_name == 'hardware_acceleration':
            # No-op: preview decodes on the CPU through VapourSynth/lsmas;
            # the retired in-process OpenGL renderer had this toggle.
            pass

        elif setting_name == 'scale':
            logger.info(f"界面缩放已设置为: {value}")

        elif setting_name in {'remote_auto_restart_program'} or \
                setting_name.startswith('ssh_'):
            # Keep RemotePage's in-memory settings synchronized.
            if hasattr(self, '_remote_page'):
                self._remote_page._settings[setting_name] = value

        elif setting_name.startswith('usb_controler_'):
            # Keep RemotePage's in-memory settings synchronized.
            if hasattr(self, '_usbControl_page'):
                self._usbControl_page._settings[setting_name] = value

        elif setting_name == 'auto_save':
            if value:
                self._auto_save_service.config.enabled = True
                # 如果有项目打开，重启定时器
                if self._config and self._project_path:
                    self._auto_save_service.start(
                        self._config, self._project_path, self._base_dir)
            else:
                self._auto_save_service.config.enabled = False
                self._auto_save_service.stop()

    def _apply_theme_change(self, theme_name):
        """应用主题变化"""
        logger.info(f"应用主题: {theme_name}")

        try:
            import json
            config_dir = os.path.join(
                self._app_dir, "config")
            config_file = os.path.join(config_dir, "user_settings.json")

            settings = {}
            if os.path.exists(config_file):
                with open(config_file, "r", encoding="utf-8") as f:
                    settings = json.load(f)

            if theme_name == '默认':
                self._bg_pixmap = None
                self.setStyleSheet("")
                self._apply_default_theme()
            elif theme_name == '自定义图片':
                theme_color = settings.get('theme_color', '#ff6b8b')
                self._apply_theme_color(theme_color)
                theme_image = settings.get('theme_image', '')
                if theme_image:
                    self._apply_theme_image(theme_image)

        except Exception as e:
            logger.error(f"应用主题失败: {e}")

    def _apply_default_theme(self):
        """应用默认主题"""
        self._apply_theme_color('#ff6b8b')

    def _apply_theme_color(self, color_hex):
        """应用主题颜色到界面"""
        # 同步 QFluentWidgets 全局主题色（驱动 PrimaryPushButton 等内置控件）
        setThemeColor(color_hex, lazy=True)

        # 仅在当前没有自定义背景图片时，才重置为纯色背景
        if self._bg_pixmap is None:
            self._bg_color = self._dark_bg_color if isDarkTheme() else self._light_bg_color
            self.setStyleSheet("")
            self.update()

        r = 0 if self.isMaximized() else int(self._corner_radius)

        if hasattr(self, 'header_bar'):
            header_qss = f"#header_bar {{ background-color: {color_hex}; color: white; border-top-left-radius: {r}px; border-top-right-radius: {r}px; }} #header_bar > QLabel {{ font-weight: bold; font-size: 16px; }}"
            setCustomStyleSheet(self.header_bar, header_qss, header_qss)

        if hasattr(self, 'sidebar'):
            sidebar_qss = f"#sidebar {{ background-color: {color_hex}; border-bottom-right-radius: {r}px; }}"
            setCustomStyleSheet(self.sidebar, sidebar_qss, sidebar_qss)

        if hasattr(self, 'status_bar'):
            is_dark = isDarkTheme()
            sb_bg = "rgba(30, 30, 30, 0.95)" if is_dark else "rgba(248, 249, 250, 0.95)"
            sb_color = "#eeeeee" if is_dark else "#333333"
            sb_border = "rgba(255, 255, 255, 0.1)" if is_dark else "rgba(0, 0, 0, 0.1)"
            sb_qss = (f"QStatusBar {{ background-color: {sb_bg}; color: {sb_color}; "
                      f"border-top: 1px solid {sb_border}; "
                      f"border-bottom-left-radius: {r}px; "
                      f"border-bottom-right-radius: {r}px; }}"
                      " QStatusBar::item { border: none; }")
            self.status_bar.setStyleSheet(sb_qss)

        if hasattr(self, 'content_stack'):
            content_light = "#content_stack { background-color: rgba(255, 255, 255, 0.95); }"
            content_dark = "#content_stack { background-color: rgba(30, 30, 30, 0.95); }"
            setCustomStyleSheet(self.content_stack,
                                content_light, content_dark)

        nav_buttons = [
            'btn_nav_file',
            'btn_nav_basic',
            'btn_nav_advanced',
            'btn_nav_help']
        for btn_name in nav_buttons:
            if hasattr(self, btn_name):
                btn = getattr(self, btn_name)
                nav_qss = ("PushButton { background-color: transparent; color: white; "
                           "border: none; padding: 10px 20px; font-size: 14px; border-radius: 6px; } "
                           "PushButton:hover { background-color: rgba(255, 255, 255, 0.3); } "
                           "PushButton:pressed, PushButton:checked { background-color: rgba(255, 255, 255, 0.4); }")
                setCustomStyleSheet(btn, nav_qss, nav_qss)

        for btn in [
                self.btn_firmware,
                self.btn_material,
                self.btn_forum,
                self.btn_about,
                self.btn_usbControl,
                self.btn_remote,
                self.btn_settings]:
            light_qss = (
                f"ToolButton {{ background-color: {COLOR_BG_ELEVATED[0]}; color: {COLOR_TEXT_PRIMARY[0]}; "
                f"border: 1px solid #e9ecef; border-radius: 10px; padding: 14px 20px; "
                f"text-align: left; font-size: 15px; margin: 8px; }} "
                f"ToolButton:hover {{ background-color: {hex_with_alpha(color_hex, 64)}; border-color: {color_hex}; }} "
                f"ToolButton:pressed, ToolButton:checked {{ background-color: {color_hex}; color: white; border-color: {color_hex}; }}"
            )
            dark_qss = (
                f"ToolButton {{ background-color: {COLOR_BG_ELEVATED[1]}; color: {COLOR_TEXT_PRIMARY[1]}; "
                f"border: 1px solid {COLOR_BORDER[1]}; border-radius: 10px; padding: 14px 20px; "
                f"text-align: left; font-size: 15px; margin: 8px; }} "
                f"ToolButton:hover {{ background-color: {hex_with_alpha(color_hex, 80)}; border-color: {color_hex}; }} "
                f"ToolButton:pressed, ToolButton:checked {{ background-color: {color_hex}; color: white; border-color: {color_hex}; }}"
            )
            setCustomStyleSheet(btn, light_qss, dark_qss)

        # 更新 logo 颜色
        if hasattr(self, 'logo_label'):
            logo_qss = f"#logo_label {{ background-color: white; color: {color_hex}; border-radius: 16px; padding: 8px 12px; font-size: 14px; font-weight: bold; }}"
            setCustomStyleSheet(self.logo_label, logo_qss, logo_qss)

        if hasattr(self, '_forum_plugin_handle'):
            self._forum_plugin_handle.apply_theme()

        logger.info(f"应用主题颜色: {color_hex}")

    def _apply_theme_image(self, image_path):
        """应用主题图片到界面（带有毛玻璃效果）"""
        logger.info(f"应用主题图片: {image_path}")
        from PyQt6.QtGui import QPixmap
        pixmap = QPixmap(image_path)
        if not pixmap.isNull():
            self._bg_pixmap = pixmap
        else:
            logger.warning(f"主题图片加载失败: {image_path}")
            self._bg_pixmap = None
        self._apply_image_mode_styles()

    def _apply_image_mode_styles(self):
        """应用图片模式下的子 widget 半透明样式"""
        theme_color = themeColor().name()
        try:
            is_dark = isDarkTheme()

            if is_dark:
                content_bg = "rgba(30, 30, 30, 0.7)"
                status_bg = "rgba(30, 30, 30, 0.7)"
                status_color = "white"
                status_border = "rgba(255, 255, 255, 0.1)"
            else:
                content_bg = "rgba(255, 255, 255, 0.7)"
                status_bg = "rgba(248, 249, 250, 0.7)"
                status_color = "#333"
                status_border = "rgba(0, 0, 0, 0.1)"

            style = """
                QWidget#content_stack {
                    background-color: %s;
                }

                QWidget#header_bar {
                    background-color: %s;
                    border-top-left-radius: 16px;
                    border-top-right-radius: 16px;
                }

                QWidget#sidebar {
                    background-color: %s;
                    border-top-right-radius: 0px;
                    border-bottom-left-radius: 0px;
                    border-bottom-right-radius: 16px;
                }

                QStatusBar {
                    background-color: %s;
                    color: %s;
                    border-top: 1px solid %s;
                    border-bottom-left-radius: 16px;
                    border-bottom-right-radius: 16px;
                }

                QStatusBar::item {
                    border: none;
                }
            """

            self.setStyleSheet(style % (
                content_bg, theme_color, theme_color, status_bg, status_color, status_border))
            self.update()

            logger.info("图片模式样式已应用")
        except Exception as e:
            logger.error(f"应用图片模式样式失败: {e}")

    def _connect_timeline_to_preview(self, preview: VideoPreviewWidget):
        """将时间轴连接到指定预览器"""
        # 断开旧连接（忽略错误，因为可能没有连接）
        try:
            self.timeline.play_pause_clicked.disconnect()
        except TypeError:
            pass
        try:
            self.timeline.seek_requested.disconnect()
        except TypeError:
            pass
        try:
            self.timeline.prev_frame_clicked.disconnect()
        except TypeError:
            pass
        try:
            self.timeline.next_frame_clicked.disconnect()
        except TypeError:
            pass
        try:
            self.timeline.goto_start_clicked.disconnect()
        except TypeError:
            pass
        try:
            self.timeline.goto_end_clicked.disconnect()
        except TypeError:
            pass
        try:
            self.timeline.rotation_value_changed.disconnect()
        except TypeError:
            pass

        self.timeline.play_pause_clicked.connect(preview.toggle_play)
        self.timeline.seek_requested.connect(preview.seek_to_frame)
        self.timeline.prev_frame_clicked.connect(preview.prev_frame)
        self.timeline.next_frame_clicked.connect(preview.next_frame)
        self.timeline.goto_start_clicked.connect(
            lambda: preview.seek_to_frame(0))
        self.timeline.goto_end_clicked.connect(
            lambda: preview.seek_to_frame(preview.total_frames - 1)
        )
        self.timeline.rotation_value_changed.connect(preview.set_rotation)

        self._timeline_preview = preview

        if hasattr(preview, 'total_frames') and preview.total_frames > 0:
            self.timeline.set_total_frames(preview.total_frames)
            if hasattr(preview, 'video_fps'):
                self.timeline.set_fps(preview.video_fps)
            if hasattr(preview, 'current_frame_index'):
                self.timeline.set_current_frame(preview.current_frame_index)
            self.timeline.set_rotation(preview.get_rotation())
            if hasattr(preview, 'is_playing'):
                self.timeline.set_playing(preview.is_playing)

        try:
            preview.frame_changed.disconnect(self._on_video_frame_changed)
        except TypeError:
            pass
        preview.frame_changed.connect(self._on_video_frame_changed)
        self._apply_timeline_capability_controls(preview)

    def _is_timeline_bound_to(self, preview: VideoPreviewWidget) -> bool:
        return self._timeline_preview is preview

    def _snapshot_active_timeline_state(self):
        if self._timeline_preview is self.intro_preview:
            if not self._preview_supports(self.intro_preview, "trim"):
                return
            self._intro_in_out = (
                self.timeline.get_in_point(),
                self.timeline.get_out_point(),
            )
            start, end = self._get_trim_bounds(self.intro_preview)
            self.intro_preview.set_timeline_range(start, end)
        elif self._timeline_preview is self.video_preview:
            if not self._preview_supports(self.video_preview, "trim"):
                return
            self._loop_in_out = (
                self.timeline.get_in_point(),
                self.timeline.get_out_point(),
            )
            start, end = self._get_trim_bounds(self.video_preview)
            self.video_preview.set_timeline_range(start, end)

    def _configure_preview_render_contexts(self) -> None:
        """在加载任何媒体前解析、核验并冻结本次脚本选择。"""
        if not self._base_dir:
            return
        project_root = str(os.path.abspath(self._base_dir))
        cache_root = os.path.join(project_root, ".assetmaker-vs", "preview")
        reference = self._script_reference()
        try:
            runtime = load_vs_runtime()
            script = resolve_script_reference(
                reference,
                project_root=project_root,
                app_dir=self._app_dir,
                global_script_path=runtime.scripts.global_script_path,
            )
            bundle_hash = compute_script_bundle_hash(script)
            header = parse_script_header(script)
            trusted = reference.source != "project"
            if reference.source == "project":
                store = ProjectTrustStore()
                trusted = store.is_trusted(script.parent, bundle_hash)
                if not trusted:
                    from gui.dialogs.vs_script_trust_dialog import (
                        VSScriptTrustDialog,
                    )

                    dialog = VSScriptTrustDialog(
                        canonical_root=str(script.parent),
                        main_script=str(script),
                        code_files=script_bundle_code_files(script),
                        bundle_hash=bundle_hash,
                        parent=self,
                    )
                    if dialog.exec() == QDialog.DialogCode.Accepted:
                        store.trust(script.parent, bundle_hash)
                        trusted = True
                    else:
                        raise ScriptTrustError("脚本未获信任")
            selection = ScriptSelection.from_header(script, header, bundle_hash)
        except (OSError, RuntimeError, ScriptTrustError, ValueError) as exc:
            self._block_script_execution(reference, str(exc))
            return

        self._script_ready = True
        self._script_block_reason = ""
        self._active_script_path = str(script)
        self.video_preview.set_render_context(
            PreviewRenderContext(
                project_root=project_root,
                track="loop",
                selection=selection,
                cache_dir=os.path.join(cache_root, "loop"),
                header=header,
            )
        )
        self.intro_preview.set_render_context(
            PreviewRenderContext(
                project_root=project_root,
                track="intro",
                selection=selection,
                cache_dir=os.path.join(cache_root, "intro"),
                header=header,
            )
        )
        self.vs_script_panel.set_script_info(
            reference=reference,
            canonical_root=str(script.parent),
            main_script=str(script),
            header=header,
            bundle_hash=bundle_hash,
            trusted=trusted,
        )
        self._set_script_export_enabled(True)
        self._apply_timeline_capability_controls(self._timeline_preview)

    def _script_reference(self) -> ScriptReference:
        if self._config is None:
            return ScriptReference()
        state = self._config.editor.vs_script
        return ScriptReference(state.source, state.path)

    def _block_script_execution(
        self, reference: ScriptReference, reason: str
    ) -> None:
        self._script_ready = False
        self._script_block_reason = reason
        self._active_script_path = ""
        for preview in (self.video_preview, self.intro_preview):
            preview.set_execution_blocked(reason)
        self.vs_script_panel.set_error(reference, reason)
        self._set_script_export_enabled(False)
        self._apply_timeline_capability_controls(self._timeline_preview)
        self.status_bar.showMessage(reason)

    def _set_script_export_enabled(self, enabled: bool) -> None:
        for panel in (self.advanced_config_panel, self.basic_config_panel):
            button = getattr(panel, "btn_export", None)
            if button is not None:
                button.setEnabled(enabled)

    @staticmethod
    def _preview_supports(preview, capability: str) -> bool:
        checker = getattr(preview, "supports_editor_capability", None)
        return bool(checker(capability)) if callable(checker) else True

    def _apply_timeline_capability_controls(self, preview) -> None:
        if preview is None:
            return
        controls = (
            (
                "trim",
                ("btn_set_in", "btn_set_out"),
                "当前脚本未声明 trim capability",
            ),
            (
                "rotation",
                ("btn_rotate_ccw", "spin_rotation", "btn_rotate_cw"),
                "当前脚本未声明 rotation capability",
            ),
        )
        for capability, names, tooltip in controls:
            enabled = self._preview_supports(preview, capability)
            for name in names:
                control = getattr(self.timeline, name, None)
                if control is not None:
                    control.setEnabled(enabled)
                    if not enabled:
                        control.setToolTip(tooltip)
        label = getattr(preview, "video_label", None)
        if label is not None and not self._preview_supports(preview, "crop"):
            label.setToolTip("当前脚本未声明 crop capability")

    def _on_script_source_requested(self, source: str) -> None:
        if self._config is None or not self._base_dir:
            return
        if source == "builtin":
            state = VSScriptState("builtin", "")
        else:
            title = "选择全局 VPY 脚本" if source == "global" else "选择项目 VPY 脚本"
            path, _ = QFileDialog.getOpenFileName(self, title, self._base_dir, "VPY 脚本 (*.vpy)")
            if not path:
                self.vs_script_panel.set_error(
                    self._script_reference(), "未选择脚本；保留当前来源"
                )
                return
            if source == "global":
                try:
                    save_vs_runtime_override(
                        default_vs_runtime_user_path(),
                        {"scripts": {"global_script_path": str(Path(path).resolve())}},
                    )
                except Exception as exc:
                    QMessageBox.warning(self, "全局脚本", str(exc))
                    return
                state = VSScriptState("global", "")
            else:
                try:
                    root = Path(self._base_dir).resolve(strict=True)
                    relative = Path(path).resolve(strict=True).relative_to(root).as_posix()
                    ScriptReference("project", relative)
                except (OSError, RuntimeError, ValueError) as exc:
                    QMessageBox.warning(self, "项目脚本", f"脚本必须位于项目目录内：{exc}")
                    return
                state = VSScriptState("project", relative)
        self._config.editor.vs_script = state
        self._is_modified = True
        self._update_title()
        self._apply_project_config()

    def _on_script_reload(self) -> None:
        if self._config is not None:
            self._apply_project_config()

    def _on_script_open_directory(self) -> None:
        if self._active_script_path:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(Path(self._active_script_path).parent))
            )

    def _get_cached_in_out(
        self, preview: VideoPreviewWidget
    ) -> tuple[int, int]:
        if preview is self.intro_preview:
            return self._intro_in_out
        return self._loop_in_out

    def _get_trim_bounds(
        self,
        preview: VideoPreviewWidget,
        cached_in_out: Optional[tuple[int, int]] = None,
        *,
        total_frames: Optional[int] = None,
        default_to_full: bool = False,
    ) -> tuple[int, int]:
        resolved_total = total_frames
        if resolved_total is None:
            resolved_total = getattr(preview, "total_frames", 0)
        resolved_total = max(1, resolved_total or 0)

        in_point, out_point = cached_in_out or self._get_cached_in_out(preview)
        if default_to_full and (in_point, out_point) == (0, 0) and resolved_total > 1:
            in_point, out_point = (0, resolved_total - 1)

        in_point = max(0, min(in_point, resolved_total - 1))
        out_point = max(in_point, min(out_point, resolved_total - 1))
        return in_point, min(resolved_total, out_point + 1)

    def _resolve_media_path(self, file_path: str) -> str:
        if not file_path:
            return ""
        if os.path.isabs(file_path):
            return file_path
        return os.path.join(self._base_dir, file_path)

    def _probe_video_metadata(
        self, video_path: str
    ) -> tuple[int, int, int, float]:
        # 刻意保持同步:仅在导出时对"已配置但预览未加载"的媒体兜底探测
        # (_collect_preview_media_state 优先复用预览缓存),导出本就是阻塞
        # 模态流程;预览路径的探测已改为 MetadataProbeWorker 异步执行。
        from core.video_processor import VideoProcessor

        info = VideoProcessor().get_video_info(video_path)
        if info is None:
            raise RuntimeError(f"Unable to probe video metadata: {video_path}")
        return info.width, info.height, max(1, info.total_frames), info.fps or 30.0

    def _probe_image_size(self, image_path: str) -> tuple[int, int]:
        import cv2
        import numpy as np

        img_data = np.fromfile(image_path, dtype=np.uint8)
        frame = cv2.imdecode(img_data, cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError(f"无法读取图片: {image_path}")
        return frame.shape[1], frame.shape[0]

    def _preview_has_loaded_media(self, preview: VideoPreviewWidget) -> bool:
        return bool(
            getattr(preview, "video_path", "")
            and getattr(preview, "total_frames", 0) > 0
        )

    def _collect_preview_media_state(
        self,
        preview: VideoPreviewWidget,
        configured_path: str,
        *,
        default_to_full: bool = False,
        is_image: bool = False,
    ) -> Optional[dict]:
        preview_path = getattr(preview, "video_path", "")
        if (
            self._preview_has_loaded_media(preview)
            and preview_path
            and os.path.exists(preview_path)
        ):
            total_frames = max(1, int(preview.total_frames or 0))
            start_frame, end_frame = self._get_trim_bounds(
                preview,
                total_frames=total_frames,
            )
            return {
                "path": preview_path,
                "cropbox": preview.get_cropbox_in_rotated_space(),
                "rotation": preview.get_rotation(),
                "start_frame": start_frame,
                "end_frame": end_frame,
                "fps": float(preview.video_fps or 30.0),
                "total_frames": total_frames,
                "width": getattr(preview, "video_width", 0),
                "height": getattr(preview, "video_height", 0),
            }

        resolved_path = self._resolve_media_path(configured_path)
        if not resolved_path or not os.path.exists(resolved_path):
            return None

        if is_image:
            width, height = self._probe_image_size(resolved_path)
            total_frames = max(
                1, int(getattr(preview, "total_frames", 0) or 150))
            fps = float(getattr(preview, "video_fps", 0) or 30.0)
        else:
            width, height, total_frames, fps = self._probe_video_metadata(
                resolved_path)

        start_frame, end_frame = self._get_trim_bounds(
            preview,
            total_frames=total_frames,
            default_to_full=default_to_full,
        )
        return {
            "path": resolved_path,
            "cropbox": (0, 0, width, height),
            "rotation": 0,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "fps": fps,
            "total_frames": total_frames,
            "width": width,
            "height": height,
        }

    def _bake_loop_image_for_simulator(self, loop_state: dict) -> tuple[str, dict]:
        from core.export_service import ExportWorker
        from core.vs_runtime.job import load_render_job

        # 模拟器不能再用 cv2 另行裁剪、旋转和缩放图片。直接冻结循环预览的
        # RenderSession，令同一 runner/script/job 生成它将播放的 loop.mp4。
        session = self.video_preview.flush_render_job()
        job = load_render_job(session.job_path)
        temp_video = os.path.join(self._base_dir, "_sim_temp.mp4")
        worker = ExportWorker()
        worker._staging_dir = self._base_dir
        try:
            worker._export_video(temp_video, session, 0)
        finally:
            worker._media_encoder = None

        temp_config = self._config.copy()
        temp_config.loop.file = os.path.basename(temp_video)
        temp_config.loop.is_image = False
        temp_config_path = os.path.join(
            self._base_dir, "_sim_temp_config.json")
        temp_config.save_to_file(temp_config_path)

        baked_state = dict(loop_state)
        baked_state.update(
            path=temp_video,
            cropbox=(0, 0, job.output.display_width, job.output.display_height),
            rotation=0,
            width=job.output.display_width,
            height=job.output.display_height,
            fps=job.timeline.fps.numerator / job.timeline.fps.denominator,
            total_frames=job.timeline.end_frame - job.timeline.start_frame,
        )
        return temp_config_path, baked_state

    def _on_video_frame_changed(self, frame):
        """视频帧变更时更新截取帧编辑页面"""
        if self.preview_tabs.currentIndex() == 1 and hasattr(self,
                                                             '_current_video_preview'):
            source_preview = self._current_video_preview
            frame = source_preview.current_frame
            if frame is not None:
                frame = frame.copy()
                self.frame_capture_preview.update_static_frame(frame)
                logger.info(
                    f"更新截取帧编辑页面，帧: {source_preview.current_frame_index}")

    def _on_preview_tab_changed(self, index: int):
        """预览标签页切换"""
        # 保存当前 in/out 到正确的位置（基于当前连接的预览器）
        self._snapshot_active_timeline_state()

        if index == 0:
            self._connect_timeline_to_preview(self.intro_preview)
            self.timeline.set_in_point(self._intro_in_out[0])
            self.timeline.set_out_point(self._intro_in_out[1])
            self.timeline.show()
            logger.debug("切换到入场视频预览")
        elif index == 1:
            if hasattr(
                    self,
                    '_current_video_preview') and self._current_video_preview:
                logger.debug("连接时间轴到保存的视频预览器")
                source_preview = self._current_video_preview
            else:
                logger.debug("连接时间轴到默认视频预览器")
                source_preview = self.video_preview
            self._connect_timeline_to_preview(source_preview)
            cached_in, cached_out = self._get_cached_in_out(source_preview)
            self.timeline.set_in_point(cached_in)
            self.timeline.set_out_point(cached_out)
            self.timeline.show()
            logger.debug("切换到截取帧编辑")
        elif index == 2:
            self.timeline.hide()
            logger.debug("切换到过渡图片预览")
        elif index == 3:
            self._connect_timeline_to_preview(self.video_preview)
            self.timeline.set_in_point(self._loop_in_out[0])
            self.timeline.set_out_point(self._loop_in_out[1])
            self.timeline.show()
            logger.debug("切换到循环视频预览")

        if hasattr(self, '_drop_overlay'):
            self._update_drop_context()

    def _on_intro_video_loaded(self, total_frames: int, fps: float):
        """入场视频加载完成"""
        if self._is_timeline_bound_to(self.intro_preview):
            self.timeline.set_total_frames(total_frames)
            self.timeline.set_fps(fps)
            self.timeline.set_in_point(0)
            self.timeline.set_out_point(total_frames - 1)
        self._intro_in_out = (0, total_frames - 1)
        self._finish_editor_restore(self.intro_preview)
        start, end = self._get_trim_bounds(self.intro_preview)
        self.intro_preview.set_timeline_range(start, end)
        self.status_bar.showMessage(
            f"入场视频已加载: {total_frames} 帧, {fps:.1f} FPS")

    def _on_intro_frame_changed(self, frame: int):
        """入场视频帧变更"""
        if self._is_timeline_bound_to(self.intro_preview):
            self.timeline.set_current_frame(frame)

    def _on_intro_playback_changed(self, is_playing: bool):
        """入场视频播放状态变更"""
        if self._is_timeline_bound_to(self.intro_preview):
            self.timeline.set_playing(is_playing)

    def _on_intro_rotation_changed(self, rotation: int):
        """入场视频旋转变更"""
        if self._is_timeline_bound_to(self.intro_preview):
            self.timeline.set_rotation(rotation)
        self._on_editor_state_changed(self.intro_preview)

    def _on_loop_rotation_changed(self, rotation: int):
        """循环视频旋转变更"""
        if self._is_timeline_bound_to(self.video_preview):
            self.timeline.set_rotation(rotation)
        self._on_editor_state_changed(self.video_preview)

    def _on_set_in_point(self):
        """设置入点为当前帧"""
        index = self.preview_tabs.currentIndex()
        if index == 0:
            preview = self.intro_preview
        elif index == 3:
            preview = self.video_preview
        else:
            return  # 截取帧/过渡图片标签页无入点操作
        if not self._preview_supports(preview, "trim"):
            return

        current_frame = preview.current_frame_index
        self.timeline.set_in_point(current_frame)
        logger.debug(f"设置入点: {current_frame}")
        self._on_editor_state_changed(preview)  # 入出点属于项目状态,需标脏并持久化

    def _on_set_out_point(self):
        """设置出点为当前帧"""
        index = self.preview_tabs.currentIndex()
        if index == 0:
            preview = self.intro_preview
        elif index == 3:
            preview = self.video_preview
        else:
            return  # 截取帧/过渡图片标签页无出点操作
        if not self._preview_supports(preview, "trim"):
            return

        current_frame = preview.current_frame_index
        self.timeline.set_out_point(current_frame)
        logger.debug(f"设置出点: {current_frame}")
        self._on_editor_state_changed(preview)

    def _load_loop_image(self, path: str):
        """加载循环图片到预览器（以循环视频方式预览）"""
        self._loop_image_path = path
        logger.info(f"加载循环图片: {path}")

        if self.video_preview.load_image_as_loop(path):
            self.status_bar.showMessage(
                f"图片已加载为循环视频: "
                f"{self.video_preview.video_width}x"
                f"{self.video_preview.video_height}"
            )
            self._connect_timeline_to_preview(self.video_preview)
        else:
            logger.error(f"无法加载图片: {path}")
            self.video_preview.video_label.setText(f"无法加载图片: {path}")

    def _on_loop_mode_changed(self, is_image: bool):
        """循环模式切换"""
        if self._initializing:
            return

        self.video_preview.clear()
        self._loop_image_path = None

        self.timeline.set_total_frames(0)
        self._loop_in_out = (0, 0)

        logger.info(f"循环模式切换为: {'图片' if is_image else '视频'}")

        if hasattr(self, '_drop_overlay'):
            self._update_drop_context()

    def _get_active_config_panel(self):
        """获取当前活动的配置面板（基础或高级）"""
        if hasattr(self, 'basic_config_panel') and \
                self.basic_config_panel.isVisible():
            return self.basic_config_panel
        return self.advanced_config_panel

    # ---- 拖放支持 ----

    def _setup_drop_support(self):
        """初始化拖放支持"""
        self._drop_overlay = DropOverlayWidget(self.preview_container)
        self._drop_overlay.file_dropped.connect(self._on_file_dropped)
        self._update_drop_context()

        self.json_preview.json_file_dropped.connect(self._on_json_file_dropped)

    def _update_drop_context(self):
        """根据当前标签页更新拖放接受的文件类型和提示文字"""
        tab_index = self.preview_tabs.currentIndex()

        if tab_index == 3:  # 循环视频/图片 — 始终接受两种格式
            self._drop_overlay.set_context(
                SUPPORTED_VIDEO_FORMATS + SUPPORTED_IMAGE_FORMATS,
                "释放以导入循环素材"
            )
        elif tab_index == 0:  # 入场视频
            self._drop_overlay.set_context(
                SUPPORTED_VIDEO_FORMATS,
                "释放以导入入场视频"
            )
        elif tab_index == 2:  # 过渡图片
            self._drop_overlay.set_context(
                SUPPORTED_IMAGE_FORMATS,
                "释放以导入过渡图片"
            )
        else:  # Tab 1 截取帧编辑等
            self._drop_overlay.set_context(
                SUPPORTED_VIDEO_FORMATS + SUPPORTED_IMAGE_FORMATS,
                "释放以导入文件"
            )

    def _on_json_file_dropped(self, file_path: str):
        """处理拖放到JSON预览面板的配置文件"""
        logger.info(f"JSON配置文件拖放导入: {file_path}")
        self._load_project(file_path)

    def _on_file_dropped(self, file_path: str, drop_pos):
        """处理拖放文件 — 根据上下文分发到对应处理逻辑"""
        tab_index = self.preview_tabs.currentIndex()
        logger.info(f"文件拖放: {file_path}, 标签页: {tab_index}")

        if tab_index == 3:  # 循环视频/图片
            self._handle_drop_loop(file_path)
        elif tab_index == 0:  # 入场视频
            self._handle_drop_intro(file_path)
        elif tab_index == 2:  # 过渡图片
            self._handle_drop_transition(file_path, drop_pos)
        else:
            self._handle_drop_loop(file_path)

    def _handle_drop_loop(self, file_path: str):
        """处理拖放到循环视频/图片标签页"""
        ext = os.path.splitext(file_path)[1].lower()
        is_image = ext in SUPPORTED_IMAGE_FORMATS

        config_panel = self._get_active_config_panel()

        # 自动切换循环模式以匹配拖放文件类型
        if hasattr(config_panel, 'radio_loop_image'):
            if is_image and not config_panel.radio_loop_image.isChecked():
                config_panel.radio_loop_image.setChecked(True)
            elif not is_image and hasattr(
                    config_panel, 'radio_loop_video'
            ) and not config_panel.radio_loop_video.isChecked():
                config_panel.radio_loop_video.setChecked(True)

        rel_path = config_panel._copy_to_project_dir(file_path, "loop")
        config_panel.edit_loop_file.setText(rel_path or file_path)

        if is_image and hasattr(config_panel, 'loop_image_selected'):
            config_panel.loop_image_selected.emit(file_path)
        else:
            # 基础模式无 loop_image_selected，图片也走 video_file_selected
            config_panel.video_file_selected.emit(file_path)

    def _handle_drop_intro(self, file_path: str):
        """处理拖放到入场视频标签页"""
        config_panel = self.advanced_config_panel
        rel_path = config_panel._copy_to_project_dir(file_path, "intro")
        config_panel.edit_intro_file.setText(rel_path or file_path)
        config_panel.intro_video_selected.emit(file_path)

    def _handle_drop_transition(self, file_path: str, drop_pos):
        """处理拖放到过渡图片标签页"""
        # 根据鼠标释放位置判断是进入过渡(左半)还是循环过渡(右半)
        mapped_pos = self.transition_preview.mapFrom(
            self.preview_container, drop_pos)
        mid_x = self.transition_preview.width() // 2
        trans_type = "in" if mapped_pos.x() < mid_x else "loop"
        logger.info(f"过渡图片拖放: type={trans_type}, pos={mapped_pos.x()}")
        self.advanced_config_panel._process_transition_image(
            file_path, trans_type)

    def _on_transition_image_changed(self, trans_type: str, abs_path: str):
        """过渡图片变更"""
        self.transition_preview.load_image(trans_type, abs_path)
        self.preview_tabs.setCurrentIndex(2)

    def _on_transition_crop_changed(self, trans_type: str):
        """过渡图片 cropbox 变化 → 裁切原始图片并保存"""
        if not self._base_dir:
            return

        import cv2
        import glob

        pattern = os.path.join(self._base_dir, f"trans_{trans_type}_src.*")
        matches = glob.glob(pattern)
        if not matches:
            return

        src_path = matches[0]
        original = cv2.imread(src_path, cv2.IMREAD_UNCHANGED)
        if original is None:
            return

        x, y, w, h = self.transition_preview.get_cropbox(trans_type)

        img_h, img_w = original.shape[:2]
        x = max(0, min(x, img_w - 1))
        y = max(0, min(y, img_h - 1))
        w = min(w, img_w - x)
        h = min(h, img_h - y)

        if w <= 0 or h <= 0:
            return

        cropped = original[y:y + h, x:x + w]

        target_w, target_h = self._get_target_resolution()
        resized = cv2.resize(cropped, (target_w, target_h),
                             interpolation=cv2.INTER_AREA)

        out_path = os.path.join(
            self._base_dir,
            f"trans_{trans_type}_image.png")
        success, encoded = cv2.imencode('.png', resized)
        if success:
            with open(out_path, 'wb') as f:
                f.write(encoded.tobytes())
            # The on-disk transition asset changed — reflect it in the dirty flag
            # (this edit was previously invisible to the unsaved-changes prompt).
            if not self._is_modified:
                self._is_modified = True
                self._update_title()

    def _get_target_resolution(self):
        """获取当前选择的目标分辨率"""
        if self._config:
            spec = get_resolution_spec(self._config.screen.value)
            if spec:
                return spec['width'], spec['height']
        return 360, 640

    def _on_video_loaded(self, total_frames: int, fps: float):
        """视频加载完成"""
        if self._is_timeline_bound_to(self.video_preview):
            self.timeline.set_total_frames(total_frames)
            self.timeline.set_fps(fps)
            self.timeline.set_in_point(0)
            self.timeline.set_out_point(total_frames - 1)
        self._loop_in_out = (0, total_frames - 1)
        self._finish_editor_restore(self.video_preview)
        start, end = self._get_trim_bounds(self.video_preview)
        self.video_preview.set_timeline_range(start, end)
        self.status_bar.showMessage(f"视频已加载: {total_frames} 帧, {fps:.1f} FPS")

    def _on_editor_state_changed(self, preview):
        """预览裁剪/旋转/入出点变化 → 写入 config.editor 并标记项目已修改。

        此前这些编辑结果只活在控件内存里:不持久化、不标脏,关闭或换项目
        即静默丢失,自动保存/崩溃恢复也无从恢复。
        """
        if self._restoring_editor_state or preview in self._editor_sync_suspended:
            return
        if not self._config or not self._preview_has_loaded_media(preview):
            return
        track = (
            self._config.editor.loop
            if preview is self.video_preview
            else self._config.editor.intro
        )
        changed = False
        if self._preview_supports(preview, "crop"):
            track.crop = list(preview.get_cropbox_in_rotated_space())
            changed = True
        if self._preview_supports(preview, "rotation"):
            track.rotation = preview.get_rotation()
            changed = True
        if self._is_timeline_bound_to(preview) and self._preview_supports(preview, "trim"):
            self._snapshot_active_timeline_state()
        if self._preview_supports(preview, "trim"):
            in_out = self._get_cached_in_out(preview)
            track.in_frame, track.out_frame = int(in_out[0]), int(in_out[1])
            changed = True
        if not changed:
            return
        if not self._is_modified:
            self._is_modified = True
            self._update_title()
        self._mark_undo_change()

    def _begin_editor_restore(self, preview, track):
        """打开项目:记录待恢复拷贝并挂起该预览的 editor 同步。"""
        self._editor_sync_suspended.add(preview)
        self._pending_editor_restore[preview] = EditorTrackState.from_dict(
            track.to_dict()
        )

    def _cancel_editor_restore(self, preview):
        self._editor_sync_suspended.discard(preview)
        self._pending_editor_restore.pop(preview, None)

    def _finish_editor_restore(self, preview):
        """加载完成:恢复打开前保存的裁剪/旋转/入出点,并解除同步挂起。"""
        self._editor_sync_suspended.discard(preview)
        track = self._pending_editor_restore.pop(preview, None)
        if track is None or track.is_default() or not self._config:
            return
        self._restoring_editor_state = True
        try:
            if track.rotation and self._preview_supports(preview, "rotation"):
                # 先旋转再设裁剪框:旋转会按目标比例重新适配裁剪框。
                preview.set_rotation(track.rotation)
            if track.crop and self._preview_supports(preview, "crop"):
                preview.set_cropbox(*track.crop)
                # set_cropbox 会把框规整到当前屏幕比例。若被改动(旧工程存的
                # 框比例不符、或来自其它分辨率),明确告知用户而非静默变更。
                if list(preview.get_cropbox()) != [int(v) for v in track.crop]:
                    self.status_bar.showMessage(
                        "裁剪框已按当前屏幕比例修正", 5000)
            if (
                self._preview_supports(preview, "trim")
                and 0 <= track.in_frame <= track.out_frame
            ):
                total = max(1, int(getattr(preview, "total_frames", 1)))
                in_f = min(track.in_frame, total - 1)
                out_f = min(track.out_frame, total - 1)
                if preview is self.video_preview:
                    self._loop_in_out = (in_f, out_f)
                else:
                    self._intro_in_out = (in_f, out_f)
                if self._is_timeline_bound_to(preview):
                    self.timeline.set_in_point(in_f)
                    self.timeline.set_out_point(out_f)
        finally:
            self._restoring_editor_state = False
        # 以打开前的拷贝写回,修正加载期间默认值对 config.editor 的任何覆盖。
        if preview is self.video_preview:
            self._config.editor.loop = track
        else:
            self._config.editor.intro = track

    def _on_preview_load_failed(self, which: str, message: str, preview=None):
        """异步视频加载失败(元数据探测/启动失败信号)——聚合后弹一次警告。"""
        if preview is not None:
            self._cancel_editor_restore(preview)
        logger.warning("%s加载失败: %s", which, message)
        if which not in self._pending_load_failures:
            self._pending_load_failures.append(which)
        self.status_bar.showMessage(f"{which}加载失败: {message}")
        if not self._load_failure_flush_scheduled:
            self._load_failure_flush_scheduled = True
            QTimer.singleShot(600, self._flush_load_failures)

    def _flush_load_failures(self):
        self._load_failure_flush_scheduled = False
        failures, self._pending_load_failures = self._pending_load_failures, []
        if failures:
            QMessageBox.warning(
                self, "素材加载失败",
                "以下素材无法获取视频元数据（请确认文件可被 VapourSynth/lsmas 解码）：\n"
                + "、".join(failures),
            )

    def _on_frame_changed(self, frame: int):
        """帧变更"""
        if self._is_timeline_bound_to(self.video_preview):
            self.timeline.set_current_frame(frame)

    def _on_playback_changed(self, is_playing: bool):
        """播放状态变更"""
        if self._is_timeline_bound_to(self.video_preview):
            self.timeline.set_playing(is_playing)

    def _on_capture_frame(self):
        """截取当前视频帧 → 加载到截取帧编辑标签页"""
        logger.info("开始截取视频帧")

        if not self._base_dir:
            logger.warning("_base_dir 不存在，显示警告")
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return

        current_tab = self.preview_tabs.currentIndex()
        if current_tab == 3:
            source_preview = self.video_preview
        else:
            source_preview = self.intro_preview

        if not getattr(source_preview, "_has_video", False):
            other = self.video_preview if source_preview is self.intro_preview else self.intro_preview
            if getattr(other, "_has_video", False):
                source_preview = other

        if not getattr(source_preview, "_has_video", False):
            logger.warning("所有预览器的当前帧都为 None，显示警告")
            QMessageBox.warning(self, "警告", "请先加载视频")
            return

        # 帧由 VapourSynth 图直接给出（异步:首帧可能仍在解码），拿到后再继续。
        def _deliver(frame):
            if frame is None:
                logger.warning("截取视频帧失败：未能从预览图取回当前帧")
                QMessageBox.warning(self, "警告", "截取视频帧失败，请稍后重试")
                return
            self._finish_capture_frame(source_preview, frame)

        source_preview.capture_frame_async(_deliver)

    def _finish_capture_frame(self, source_preview, frame):
        """当前 worker surface 的 BGR 帧就绪后载入静态截图编辑器。"""
        frame = frame.copy()

        logger.info(f"加载到截取帧编辑预览，帧尺寸: {frame.shape}")
        self.frame_capture_preview.load_static_image_from_array(frame)

        self._current_video_preview = source_preview
        self._connect_timeline_to_preview(source_preview)
        self.preview_tabs.setCurrentIndex(1)

        logger.info("截取视频帧完成")
        self.status_bar.showMessage("已截取视频帧，请调整裁切框后点击\"保存为图标\"")

    def _on_save_captured_icon(self):
        """从截取帧编辑的 cropbox 保存图标"""
        logger.info("开始保存图标")

        if not self._base_dir:
            logger.warning("_base_dir 不存在，显示警告")
            QMessageBox.warning(self, "警告", "请先创建或打开项目")
            return

        frame = self.frame_capture_preview.current_frame
        logger.info(f"当前帧: {frame}")

        if frame is None:
            logger.warning("当前帧为 None，显示警告")
            QMessageBox.warning(self, "警告", "请先截取视频帧")
            return

        try:
            import cv2

            cropbox = self.frame_capture_preview.get_cropbox()
            logger.info(f"裁剪框: {cropbox}")

            if len(cropbox) != 4:
                logger.error(f"裁剪框格式错误: {cropbox}")
                QMessageBox.warning(self, "错误", "裁剪框格式错误")
                return

            x, y, w, h = cropbox

            frame_h, frame_w = frame.shape[:2]
            logger.info(f"帧尺寸: {frame_w}x{frame_h}")

            x = max(0, min(x, frame_w - 1))
            y = max(0, min(y, frame_h - 1))
            w = min(w, frame_w - x)
            h = min(h, frame_h - y)

            logger.info(f"调整后的裁剪框: x={x}, y={y}, w={w}, h={h}")

            if w <= 0 or h <= 0:
                logger.warning("裁切区域无效")
                QMessageBox.warning(self, "错误", "裁切区域无效")
                return

            logger.info("开始裁剪帧")
            cropped = frame[y:y + h, x:x + w]
            logger.info(f"裁剪后的尺寸: {cropped.shape}")

            icon_path = os.path.join(self._base_dir, "icon.png")
            logger.info(f"保存图标到: {icon_path}")

            if os.path.exists(icon_path):
                if QMessageBox.question(
                    self, "覆盖确认",
                    "icon.png 已存在，是否覆盖？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                ) != QMessageBox.StandardButton.Yes:
                    self.status_bar.showMessage("已取消保存图标")
                    return

            success, encoded = cv2.imencode('.png', cropped)
            if success:
                with open(icon_path, 'wb') as f:
                    f.write(encoded.tobytes())
                self.advanced_config_panel.edit_icon.setText("icon.png")
                self.status_bar.showMessage("已保存图标")
                logger.info("图标保存成功")
            else:
                logger.error("保存图标失败")
                QMessageBox.warning(self, "错误", "保存图标失败")

        except Exception as e:
            logger.error(f"保存图标时发生错误: {e}", exc_info=True)
            QMessageBox.critical(self, "错误", f"保存图标时发生错误: {str(e)}")

    def _collect_export_data(self) -> dict:
        """收集导出所需的数据"""
        from core.image_processor import ImageProcessor

        self._snapshot_active_timeline_state()
        data = {}

        # 导出先冻结预览已解析的 job。后续 worker/VSPipe 必须消费同一份
        # RenderSession，不能在后台从可变 UI/config 重建另一条滤镜链。
        sessions = self._flush_export_render_jobs(
            include_loop=bool(self._config.loop.file),
            include_intro=bool(self._config.intro.enabled and self._config.intro.file),
        )
        data.update({key: value for key, value in sessions.items() if value is not None})

        icon_path = self._config.icon
        if icon_path:
            if not os.path.isabs(icon_path):
                icon_path = os.path.join(self._base_dir, icon_path)
            if os.path.exists(icon_path):
                logo_img = ImageProcessor.load_image(icon_path)
                if logo_img is not None:
                    data['logo_mat'] = ImageProcessor.process_for_logo(
                        logo_img)

        if self._config.loop.is_image:
            loop_image_path = getattr(self, '_loop_image_path', "") or \
                self._resolve_media_path(self._config.loop.file)
            if loop_image_path and os.path.exists(loop_image_path):
                data['is_loop_image'] = True
            else:
                # The loop asset is required. Fail loudly (caught by _on_export's
                # try/except -> show_error + return) instead of silently dropping it and
                # exporting an epconfig.json that references a loop.mp4 that never gets made.
                raise FileNotFoundError(
                    f"循环素材(图片)缺失或不存在: {loop_image_path or self._config.loop.file!r}"
                )
        if self._config.intro.enabled and self._config.intro.file:
            intro_state = self._collect_preview_media_state(
                self.intro_preview,
                self._config.intro.file,
                default_to_full=True,
            )
            if intro_state:
                # 把 intro.duration(µs)校准到实际编码时长:trim 与用户手填的
                # duration 各自独立,设备按 duration 计时,不一致会导致入场
                # 卡顿/截断。以修剪后的真实帧数为准写回。
                fps = float(intro_state['fps'] or 30.0)
                frames = max(1, int(intro_state['end_frame']) - int(intro_state['start_frame']))
                if fps > 0:
                    computed_us = round(frames / fps * 1_000_000)
                    frame_us = round(1_000_000 / fps)
                    if abs(self._config.intro.duration - computed_us) > frame_us:
                        self._config.intro.duration = computed_us
                        self.status_bar.showMessage(
                            f"入场时长已按修剪长度校准为 {computed_us/1_000_000:.2f}s")
                        if not self._is_modified:
                            self._is_modified = True
                            self._update_title()

        from config.epconfig import OverlayType
        if self._config.overlay.type == OverlayType.IMAGE:
            if self._config.overlay.image_options and self._config.overlay.image_options.image:
                img_path = self._config.overlay.image_options.image
                if not os.path.isabs(img_path):
                    img_path = os.path.join(self._base_dir, img_path)
                if os.path.exists(img_path):
                    overlay_img = ImageProcessor.load_image(img_path)
                    if overlay_img is not None:
                        spec = get_resolution_spec(self._config.screen.value)
                        target_size = (spec['width'], spec['height'])

                        import cv2
                        overlay_img = cv2.resize(overlay_img, target_size)
                        data['overlay_mat'] = overlay_img

        return data

    def _flush_export_render_jobs(
        self,
        *,
        include_loop: bool = True,
        include_intro: bool = True,
    ) -> dict:
        """在 UI 线程冻结导出轨道的 preview RenderSession。"""
        # 部分纯单元测试以 ``MainWindow.__new__`` 构造最小替身；避免 Qt
        # 基类的缺失属性访问触发运行时错误。真实窗口在 __init__ 中总会设置
        # 此标志，只有显式 False 才能越过 trust gate。
        if not self.__dict__.get("_script_ready", True):
            raise RuntimeError(
                self.__dict__.get("_script_block_reason") or "脚本未获信任"
            )
        sessions = {"loop_render_session": None, "intro_render_session": None}
        if include_loop:
            sessions["loop_render_session"] = self.video_preview.flush_render_job()
        if include_intro:
            sessions["intro_render_session"] = self.intro_preview.flush_render_job()
        return sessions

    def _collect_arknights_custom_images(self) -> list:
        """收集 arknights 叠加的自定义图片(职业图标 / logo),缩放后返回。

        返回 [(标准化文件名, 缩放后的图像 mat)];实际写盘由导出的 AUX_IMAGE
        任务在暂存目录完成(见 ExportService),失败即回滚整个包。
        """
        from config.epconfig import OverlayType
        from config.constants import ARK_CLASS_ICON_SIZE, ARK_LOGO_SIZE
        from core.image_processor import ImageProcessor
        import cv2

        result: list = []
        if not self._config or self._config.overlay.type != OverlayType.ARKNIGHTS:
            return result
        ark_opts = self._config.overlay.arknights_options
        if not ark_opts:
            return result

        for src, size, dst_filename in (
            (ark_opts.operator_class_icon, ARK_CLASS_ICON_SIZE, "class_icon.png"),
            (ark_opts.logo, ARK_LOGO_SIZE, "ark_logo.png"),
        ):
            if not src:
                continue
            src_path = src if os.path.isabs(src) else os.path.join(self._base_dir, src)
            if not os.path.exists(src_path):
                continue
            img = ImageProcessor.load_image(src_path)
            if img is not None:
                result.append((dst_filename, cv2.resize(img, size)))
        return result

    def _collect_image_overlay(self) -> list:
        """收集 ImageOverlay 的叠加图片(overlay.png),返回 [(文件名, mat)]。"""
        from config.epconfig import OverlayType
        from core.image_processor import ImageProcessor

        result: list = []
        if not self._config or self._config.overlay.type != OverlayType.IMAGE:
            return result
        opts = self._config.overlay.image_options
        if not (opts and opts.image):
            return result
        src_path = opts.image if os.path.isabs(opts.image) else os.path.join(
            self._base_dir, opts.image)
        if not os.path.exists(src_path):
            return result
        img = ImageProcessor.load_image(src_path)
        if img is not None:
            result.append(("overlay.png", img))
        return result

    def _on_export_completed(self, success: bool, message: str, *, service=None):
        """导出完成回调"""
        if service is not None and service is not self._export_service:
            return
        if self._export_service_has_pending_worker():
            return
        if hasattr(self, '_export_dialog') and self._export_dialog:
            self._export_dialog.set_completed(success, message)

        self._finish_export_preview_lifecycle()

        if success:
            self.status_bar.showMessage(message)
            logger.info(f"导出成功: {message}")
        else:
            self.status_bar.showMessage("导出失败")
            logger.error(f"导出失败: {message}")

    def _on_remote_upload_completed(self, success: bool, message: str):
        """Remote HTTP upload completion callback."""
        if hasattr(self, '_remote_upload_dialog') and self._remote_upload_dialog:
            self._remote_upload_dialog.set_completed(success, message)

        if success:
            self.status_bar.showMessage(message)
            logger.info(f"RNDIS HTTP upload succeeded: {message}")
        else:
            self.status_bar.showMessage("远程上传失败")
            logger.error(f"RNDIS HTTP upload failed: {message}")

    def _check_save(self) -> bool:
        """检查是否需要保存"""
        if not self._is_modified:
            return True

        result = QMessageBox.question(
            self, "保存更改",
            "当前项目有未保存的更改，是否保存?",
            QMessageBox.StandardButton.Save |
            QMessageBox.StandardButton.Discard |
            QMessageBox.StandardButton.Cancel
        )

        if result == QMessageBox.StandardButton.Save:
            self._on_save_project()
            return not self._is_modified
        elif result == QMessageBox.StandardButton.Discard:
            return True
        else:
            return False

    def paintEvent(self, event):
        """绘制圆角窗口背景

        CSS border-radius 仅影响绘制不裁剪窗口形状（Qt 文档：
        "Stylesheets only affect painting. They do not change the widget's
        geometry, mask, or hit-testing."）。
        必须配合 WA_TranslucentBackground + QPainterPath 实现真正裁剪。
        """
        from PyQt6.QtGui import QPainter, QPainterPath, QColor
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 最大化时不圆角
        radius = 0.0 if self.isMaximized() else self._corner_radius
        rect = self.rect().toRectF()

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path)

        if self._bg_pixmap:
            scaled = self._bg_pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            x = (scaled.width() - self.width()) // 2
            y = (scaled.height() - self.height()) // 2
            painter.drawPixmap(0, 0, scaled, x, y,
                               self.width(), self.height())
        else:
            painter.fillRect(rect, QColor(self._bg_color))

        painter.end()

    def showEvent(self, event):
        """窗口显示时设置 DWM 圆角（Windows 11）"""
        super().showEvent(event)
        if sys.platform == 'win32' and not getattr(self, '_dwm_corner_set', False):
            self._dwm_corner_set = True
            try:
                ver = sys.getwindowsversion()
                if ver.build >= 22000:  # Windows 11
                    from ctypes import windll, byref, c_int
                    DWMWA_WINDOW_CORNER_PREFERENCE = 33
                    DWMWCP_ROUND = 2
                    windll.dwmapi.DwmSetWindowAttribute(
                        int(self.winId()),
                        DWMWA_WINDOW_CORNER_PREFERENCE,
                        byref(c_int(DWMWCP_ROUND)), 4
                    )
            except Exception:
                pass

    def closeEvent(self, event):
        """关闭事件"""
        if self._check_save():
            self._save_settings()
            self._cleanup_temp_dir()

            self._shutdown_runtime_resources()

            event.accept()
        else:
            event.ignore()

    def _shutdown_runtime_resources(self):
        """Stop media previews and auxiliary pages during close or app quit."""
        try:
            self._auto_save_service.stop()
        except Exception as exc:
            logger.debug("auto-save shutdown failed: %s", exc)

        for preview in (
            getattr(self, "video_preview", None),
            getattr(self, "intro_preview", None),
            getattr(self, "frame_capture_preview", None),
        ):
            if preview is None:
                continue
            try:
                # App-quit path: the event loop is stopping, async kill-escalation
                # timers would never fire — use the bounded blocking teardown.
                preview.clear(sync_shutdown=True)
            except Exception as exc:
                logger.debug("preview shutdown failed: %s", exc)

        try:
            if hasattr(self, '_forum_plugin_handle'):
                self._forum_plugin_handle.shutdown()
            elif hasattr(self, '_forum_widget'):
                self._forum_widget.shutdown()
        except Exception as exc:
            logger.debug("forum shutdown failed: %s", exc)

        try:
            if hasattr(self, '_remote_page'):
                self._remote_page.shutdown()
        except Exception as exc:
            logger.debug("remote page shutdown failed: %s", exc)

    def _on_maximize(self):
        """最大化/还原窗口"""
        if self.isMaximized():
            self.showNormal()
            self.btn_maximize.setText("□")
        else:
            self.showMaximized()
            self.btn_maximize.setText("❐")
        # 同步子 widget 圆角
        self._apply_theme_color(themeColor().name())

    def _on_header_mouse_press(self, event):
        """鼠标按下事件，开始拖动窗口"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = True
            self._drag_start_pos = event.globalPosition().toPoint() - \
                self.frameGeometry().topLeft()

    def _on_header_mouse_move(self, event):
        """鼠标移动事件，执行窗口拖动"""
        if self._is_dragging and not self.isMaximized():
            self.move(event.globalPosition().toPoint() - self._drag_start_pos)

    def _on_header_mouse_release(self, event):
        """鼠标释放事件，结束窗口拖动"""
        self._is_dragging = False

    def cursorAtPosition(self, pos):
        """根据鼠标位置返回对应的光标类型和调整方向"""
        rect = self.rect()
        margin = self._resize_margin

        if pos.x() < margin and pos.y() < margin:
            return Qt.CursorShape.SizeFDiagCursor, 'top-left'
        elif pos.x() > rect.width() - margin and pos.y() < margin:
            return Qt.CursorShape.SizeBDiagCursor, 'top-right'
        elif pos.x() < margin and pos.y() > rect.height() - margin:
            return Qt.CursorShape.SizeBDiagCursor, 'bottom-left'
        elif pos.x() > rect.width() - margin and pos.y() > rect.height() - margin:
            return Qt.CursorShape.SizeFDiagCursor, 'bottom-right'
        elif pos.x() < margin:
            return Qt.CursorShape.SizeHorCursor, 'left'
        elif pos.x() > rect.width() - margin:
            return Qt.CursorShape.SizeHorCursor, 'right'
        elif pos.y() < margin:
            return Qt.CursorShape.SizeVerCursor, 'top'
        elif pos.y() > rect.height() - margin:
            return Qt.CursorShape.SizeVerCursor, 'bottom'
        else:
            return Qt.CursorShape.ArrowCursor, None

    def mousePressEvent(self, event):
        """鼠标按下事件，开始调整窗口大小"""
        if event.button() == Qt.MouseButton.LeftButton:
            cursor, direction = self.cursorAtPosition(event.pos())
            if direction:
                self._is_resizing = True
                self._resize_direction = direction
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_geometry = self.geometry()

    def mouseMoveEvent(self, event):
        """鼠标移动事件，执行窗口大小调整或更新光标"""
        if self._is_resizing:
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            geometry = self._resize_start_geometry

            if self._resize_direction == 'top-left':
                new_width = geometry.width() - delta.x()
                new_height = geometry.height() - delta.y()
                new_x = geometry.x() + delta.x()
                new_y = geometry.y() + delta.y()
                if new_width >= self.minimumWidth() and new_height >= self.minimumHeight():
                    self.setGeometry(new_x, new_y, new_width, new_height)
            elif self._resize_direction == 'top-right':
                new_width = geometry.width() + delta.x()
                new_height = geometry.height() - delta.y()
                new_y = geometry.y() + delta.y()
                if new_width >= self.minimumWidth() and new_height >= self.minimumHeight():
                    self.setGeometry(
                        geometry.x(), new_y, new_width, new_height)
            elif self._resize_direction == 'bottom-left':
                new_width = geometry.width() - delta.x()
                new_height = geometry.height() + delta.y()
                new_x = geometry.x() + delta.x()
                if new_width >= self.minimumWidth() and new_height >= self.minimumHeight():
                    self.setGeometry(
                        new_x, geometry.y(), new_width, new_height)
            elif self._resize_direction == 'bottom-right':
                new_width = geometry.width() + delta.x()
                new_height = geometry.height() + delta.y()
                if new_width >= self.minimumWidth() and new_height >= self.minimumHeight():
                    self.setGeometry(
                        geometry.x(), geometry.y(), new_width, new_height)
            elif self._resize_direction == 'left':
                new_width = geometry.width() - delta.x()
                new_x = geometry.x() + delta.x()
                if new_width >= self.minimumWidth():
                    self.setGeometry(
                        new_x, geometry.y(), new_width, geometry.height())
            elif self._resize_direction == 'right':
                new_width = geometry.width() + delta.x()
                if new_width >= self.minimumWidth():
                    self.setGeometry(
                        geometry.x(),
                        geometry.y(),
                        new_width,
                        geometry.height())
            elif self._resize_direction == 'top':
                new_height = geometry.height() - delta.y()
                new_y = geometry.y() + delta.y()
                if new_height >= self.minimumHeight():
                    self.setGeometry(
                        geometry.x(), new_y, geometry.width(), new_height)
            elif self._resize_direction == 'bottom':
                new_height = geometry.height() + delta.y()
                if new_height >= self.minimumHeight():
                    self.setGeometry(
                        geometry.x(),
                        geometry.y(),
                        geometry.width(),
                        new_height)
        else:
            cursor, _ = self.cursorAtPosition(event.pos())
            if self.cursor().shape() != cursor:
                self.setCursor(cursor)

    def mouseReleaseEvent(self, event):
        """鼠标释放事件，结束窗口大小调整"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_resizing = False
            self._resize_direction = None
