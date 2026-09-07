"""进程早期诊断：只依赖标准库，紧急 fd 与普通 logging/轮转独立。

Qt fatal 回调返回后仍由 Qt 终止进程。主动输出的是 Python 线程栈，
不是 native 栈；Windows fast-fail 不能靠 faulthandler 自动捕获保证留证。
"""

from __future__ import annotations

import faulthandler
import io
import os
import sys
import tempfile
import threading
import time
import traceback
import uuid
from datetime import datetime

_diagnostics: CrashDiagnostics | None = None
_fault_owner: CrashDiagnostics | None = None
_qt_owner: CrashDiagnostics | None = None  # 强引用，防止 Qt 的 Python 回调被回收。


def log_directories(preferred: str | None = None) -> list[str]:
    """统一候选顺序；可写性由调用方真正打开目标文件来判定。"""
    if preferred is None:
        app_dir = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
                   else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        preferred = os.path.join(app_dir, "logs")
    candidates = [preferred]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "ArknightsPassMaker", "logs"))
    try:
        temp_dir = tempfile.gettempdir()
    except OSError:
        temp_dir = os.environ.get("TEMP", ".")
    candidates.append(os.path.join(temp_dir, "ArknightsPassMaker_logs"))
    return list(dict.fromkeys(os.path.abspath(path) for path in candidates))


def _text(value: object) -> str:
    """Qt 已提供字符串/整数；未知对象不调用用户定义的 str/repr。"""
    if type(value) is str:
        return value
    if type(value) is bytes:
        return value.decode("utf-8", "replace")
    if type(value) is int:
        return str(value)
    return "?"


class _DiscardStream(io.TextIOBase):
    """所有目录拒写时的有界降级，不在内存累积日志。"""

    def write(self, text: str) -> int:
        return len(text)


class CrashDiagnostics:
    """一个会话持有一个非轮转 fd，直至进程结束或显式 close。"""

    def __init__(self, candidate_dirs: list[str] | None = None):
        self.candidate_dirs = (log_directories() if candidate_dirs is None
                               else candidate_dirs)
        self.path: str | None = None
        self.log_dir: str | None = None
        self.status: dict[str, str] = {}
        self._fd: int | None = None
        self._session = (
            f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}-"
            f"{time.time_ns()}-{uuid.uuid4().hex}"
        )
        self._qt_local = threading.local()
        self._qt_handler = None
        self._qt_core = None
        self._previous_qt_handler = None
        self._hooks: dict = {}
        self._streams: dict = {}

    def initialize(self, app_version: str = "unknown") -> bool:
        global _fault_owner
        if self._fd is not None:
            return True
        failures = []
        for directory in self.candidate_dirs:
            try:
                os.makedirs(directory, exist_ok=True)
                path = os.path.abspath(os.path.join(
                    directory, f"diagnostic-{self._session}.log"
                ))
                self._fd = os.open(
                    path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_APPEND
                    | getattr(os, "O_BINARY", 0), 0o600,
                )
                self.path, self.log_dir = path, os.path.dirname(path)
                break
            except OSError as exc:
                failures.append(f"file failed: {directory}: {type(exc).__name__}")
        self.status["file"] = ("enabled" if self._fd is not None
                               else "failed: no writable directory")
        self.status["faulthandler"] = "failed: no diagnostic fd"
        if self._fd is not None:
            try:
                faulthandler.enable(file=self._fd, all_threads=True)
                _fault_owner = self
                self.status["faulthandler"] = "enabled"
            except Exception as exc:
                self.status["faulthandler"] = f"failed: {type(exc).__name__}"
        self.record_text(
            f"session={self._session} app_version={app_version} "
            f"Python={sys.version.split()[0]} frozen={bool(getattr(sys, 'frozen', False))} "
            f"path={self.path} file={self.status['file']} "
            f"faulthandler={self.status['faulthandler']}"
        )
        for failure in failures:
            self.record_text(failure)
        if self._fd is None:
            try:
                if sys.stderr is not None:
                    sys.stderr.write("[诊断] file=failed: no writable directory\n")
            except Exception:
                pass
        return self._fd is not None

    def record_text(self, message: str) -> None:
        """直接 write，无 logging 锁；部分写入最多重试四次。"""
        if self._fd is None:
            return
        try:
            prefix = (f"{datetime.now().isoformat(timespec='milliseconds')} "
                      f"pid={os.getpid()} tid={threading.get_ident()} ")
            data = (prefix + message + "\n").encode("utf-8", "backslashreplace")
            for _ in range(4):
                written = os.write(self._fd, data)
                if written <= 0:
                    break
                data = data[written:]
                if not data:
                    self.status["write"] = "enabled"
                    return
            self.status["write"] = "failed: partial write"
        except BaseException:
            # 紧急回调的边界不能再传播异常（包括异常对象格式化失败）。
            self.status["write"] = "failed: write error"

    def flush(self) -> None:
        if self._fd is not None:
            try:
                os.fsync(self._fd)
            except BaseException:
                self.status["flush"] = "failed: fsync error"

    def redirect_missing_streams(self) -> None:
        preferred = [self.log_dir] if self.log_dir is not None else []
        directories = list(dict.fromkeys(
            os.path.abspath(directory)
            for directory in preferred + self.candidate_dirs
        ))
        for name in ("stdout", "stderr"):
            if getattr(sys, name) is not None:
                self.status[name] = "existing"
                continue
            stream = None
            self.status[name] = "failed: no writable stream file; discarded"
            # diagnostic 文件可写不代表同目录每个 stream 文件都可写。
            for directory in directories:
                path = os.path.join(directory, f"{name}-{self._session}.log")
                try:
                    os.makedirs(directory, exist_ok=True)
                    stream = open(path, "x", encoding="utf-8", buffering=1)
                    self.status[name] = f"enabled: {path}"
                    break
                except OSError as exc:
                    self.record_text(f"{name} file failed: {path}: {type(exc).__name__}")
            if stream is None:
                stream = _DiscardStream()
            setattr(sys, name, stream)
            self._streams[name] = stream
            self.record_text(f"{name}={self.status[name]}")

    def record_exception(self, source: str, exc_type, exc_value, exc_tb) -> None:
        if exc_type is not None and issubclass(exc_type, SystemExit):
            return
        try:
            # 不捕获 locals，也不保存 exc/tb 或 unraisable.object。
            message = "".join(traceback.format_exception(
                exc_type, exc_value, exc_tb, chain=True
            ))
            self.record_text(f"source={source}\n{message}")
            self.flush()
        except BaseException:
            self.record_text(f"source={source} exception formatting failed")

    def install_python_hooks(self) -> None:
        def wrap(source, previous):
            def hook(*args):
                if source == "sys.excepthook":
                    self.record_exception(source, *args)
                else:
                    event = args[0]
                    self.record_exception(source, event.exc_type, event.exc_value,
                                          event.exc_traceback)
                # 保持原 handler 的行为；不在本模块持有异常或事件对象。
                previous(*args)
            return hook

        for owner, name, source in (
            (sys, "excepthook", "sys.excepthook"),
            (threading, "excepthook", "threading.excepthook"),
            (sys, "unraisablehook", "sys.unraisablehook"),
        ):
            current = getattr(owner, name)
            installed = self._hooks.get(source)
            if installed and current is installed[3]:
                continue
            handler = wrap(source, current)
            self._hooks[source] = (owner, name, current, handler)
            setattr(owner, name, handler)
        self.status["python_hooks"] = "enabled"
        self.record_text("python_hooks=enabled")

    def install_qt_message_handler(self) -> bool:
        """调用时才导入 QtCore，必须在 QApplication 创建之前调用。"""
        global _qt_owner
        if self._fd is None:
            # 无落盘通道时不接管 Qt，保留默认 stderr/调试输出或已有 handler。
            self.status["qt_handler"] = "degraded: no diagnostic fd; Qt handler unchanged"
            return False
        if self._qt_handler is not None:
            return True
        try:
            from PyQt6 import QtCore
            self._qt_handler = self.record_qt_message
            self._previous_qt_handler = QtCore.qInstallMessageHandler(self._qt_handler)
            self._qt_core = QtCore
            _qt_owner = self
            self.status["qt_handler"] = "enabled"
            self.record_text(f"qt_handler=enabled Qt={QtCore.qVersion()} "
                             f"PyQt={QtCore.PYQT_VERSION_STR}")
            return True
        except Exception as exc:
            self._qt_handler = None
            self.status["qt_handler"] = f"failed: {type(exc).__name__}"
            self.record_text(f"qt_handler={self.status['qt_handler']}")
            return False

    def record_qt_message(self, msg_type, context, message) -> None:
        # 线程局部重入守卫不等待其它线程；Qt 并发消息允许直接追加。
        if getattr(self._qt_local, "active", False):
            return
        self._qt_local.active = True
        fatal = False
        try:
            value = getattr(msg_type, "value", msg_type)
            fatal = value == 3  # QtFatalMsg，固定枚举值；bootstrap 不导入 Qt。
            kind = _text(getattr(msg_type, "name", value))
            text = _text(message)
            # 回调工作量有界；仅异常巨大的 Qt 消息截断并显式标记。
            if len(text) > 262_144:
                text = text[:262_144] + "\n[Qt message truncated at 262144 chars]"
            self.record_text(f"Qt type={kind}: {text}")  # 原文优先于 context。
            if fatal:
                self.flush()
            category = _text(getattr(context, "category", None))
            filename = _text(getattr(context, "file", None))
            line = _text(getattr(context, "line", None))
            self.record_text(f"Qt context category={category} file={filename} line={line}")
        except BaseException:
            pass
        finally:
            if fatal and self._fd is not None:
                try:
                    faulthandler.dump_traceback(file=self._fd, all_threads=True)
                    self.status["python_stacks"] = "enabled"
                except BaseException:
                    self.record_text("python_stacks=failed")
                self.flush()
            self._qt_local.active = False
        # 绝不 exit/abort，也不调用可能阻塞的旧 Qt handler；Qt 自行终止。

    def close(self) -> None:
        """先卸载自己安装的钩子，再释放 fd；应用正常运行期间保持打开。"""
        global _fault_owner, _qt_owner
        if _qt_owner is self:
            self._qt_core.qInstallMessageHandler(self._previous_qt_handler)
            _qt_owner = None
        self._qt_handler = None
        for owner, name, previous, installed in self._hooks.values():
            if getattr(owner, name) is installed:
                setattr(owner, name, previous)
        self._hooks.clear()
        if _fault_owner is self:
            faulthandler.disable()
            _fault_owner = None
        for name, stream in self._streams.items():
            if getattr(sys, name) is stream:
                setattr(sys, name, None)
            stream.close()
        self._streams.clear()
        if self._fd is not None:
            self.flush()
            os.close(self._fd)
            self._fd = None


def initialize(app_dir: str, app_version: str = "unknown") -> CrashDiagnostics:
    """main 的进程级启动器；源码和 frozen 共用。"""
    global _diagnostics
    if _diagnostics is None:
        _diagnostics = CrashDiagnostics(log_directories(os.path.join(app_dir, "logs")))
        _diagnostics.initialize(app_version)
        _diagnostics.redirect_missing_streams()
        _diagnostics.install_python_hooks()
    return _diagnostics


def get_diagnostics() -> CrashDiagnostics | None:
    """普通日志仅查询，不在导入时初始化进程诊断。"""
    return _diagnostics
