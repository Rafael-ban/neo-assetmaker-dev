#!/usr/bin/env python3
"""
明日方舟通行证素材编辑器（？）
Arknights Pass Material Maker
"""
import sys
import os
import logging

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
    plugin_path = os.path.join(APP_DIR, 'lib', 'PyQt6', 'Qt6', 'plugins')
    if os.path.exists(plugin_path):
        os.environ['QT_PLUGIN_PATH'] = plugin_path

else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, APP_DIR)

from utils.crash_diagnostics import initialize

# 标准库 bootstrap，早于 Qt 导入；源码与 frozen 共用会话文件和 Python hooks。
_diagnostics = initialize(APP_DIR)

def check_dependencies():
    """检查必要的依赖是否已安装"""
    missing = []

    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        missing.append("PyQt6")

    try:
        import qfluentwidgets
    except ImportError:
        missing.append("QFluentWidgets")

    try:
        import cv2
    except ImportError:
        missing.append("opencv-python")

    try:
        from PIL import Image
    except ImportError:
        missing.append("Pillow")

    try:
        import numpy
    except ImportError:
        missing.append("numpy")

    if missing:
        print("缺少以下依赖:")
        for dep in missing:
            print(f"  - {dep}")
        print("\n请运行以下命令安装:")
        print(f"  pip install {' '.join(missing)}")
        sys.exit(1)


def _main_inner():
    """应用程序实际入口"""
    # 创建命名互斥量，配合 installer.iss 的 AppMutex 防止安装时应用正在运行
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(None, False, "ArknightsPassMakerMutex")

    # 禁用QFluentWidgets启动提示（必须在导入QFluentWidgets之前设置）
    os.environ["QFluentWidgets_SUPPRESS_TIPS"] = "1"

    from utils.logger import setup_logger, cleanup_old_logs
    setup_logger()
    cleanup_old_logs(days=30)

    logger = logging.getLogger(__name__)

    # 安装全局异常钩子 — 防止 PyQt6 slot 中未处理异常导致 abort
    # PyQt6 行为：slot/callback 中未处理异常调用 sys.excepthook，
    # 默认实现打印到 stderr 后 qFatal() → abort。
    # 自定义 hook 记录异常后正常返回，sip 不再调用 qFatal()。
    # https://docs.python.org/3/library/sys.html#sys.excepthook
    def _excepthook(exc_type, exc_value, exc_tb):
        import traceback
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.critical(f"未捕获异常:\n{msg}")
        try:
            sys.stderr.write(msg)
            sys.stderr.flush()
        except Exception:
            pass

    sys.excepthook = _excepthook
    # 先留独立诊断，再链到上述既有业务 hook；其它两个 Python hooks 不重装。
    _diagnostics.install_python_hooks()

    logger.info("=" * 50)
    logger.info("明日方舟通行证素材制作器 启动")
    logger.info("=" * 50)

    from PyQt6.QtCore import Qt
    _diagnostics.install_qt_message_handler()
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QFont, QIcon
    from config.constants import APP_VERSION
    _diagnostics.record_text(f"app_version={APP_VERSION}")

    # 多个 QOpenGLWidget 共享 GL 上下文时需要此设置
    # https://doc.qt.io/qt-6/qopenglwidget.html
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = QApplication(sys.argv)

    check_dependencies()

    from qfluentwidgets import setTheme, setThemeColor, Theme

    from gui.main_window import MainWindow
    app.setApplicationName("明日方舟通行证素材制作器")
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("ArknightsPassMaker")

    setTheme(Theme.AUTO)
    setThemeColor("#ff6b8b")

    icon_path = os.path.join(APP_DIR, 'resources', 'icons', 'favicon.ico')
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    if sys.platform == "win32":
        font = QFont("Microsoft YaHei", 9)
        app.setFont(font)

    logger.info("创建主窗口...")
    window = MainWindow()
    window.show()

    logger.info("应用程序启动完成")

    exit_code = app.exec()
    logger.info(f"应用程序退出，退出码: {exit_code}")
    logging.shutdown()
    sys.exit(exit_code)


def main():
    """应用程序入口 — 包裹全局异常处理"""
    try:
        _main_inner()
    except Exception:
        _diagnostics.record_exception("main", *sys.exc_info())
        import traceback
        error_text = traceback.format_exc()
        try:
            # 即使主流程在 Qt 导入前失败，也先安装消息桥再显示原有错误框。
            _diagnostics.install_qt_message_handler()
            from PyQt6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(None, "致命错误", error_text[:1000])
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
