"""崩溃诊断启动器的隔离回归测试。"""

from __future__ import annotations

import logging
import logging.handlers
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from utils.crash_diagnostics import CrashDiagnostics


def run_child(script: str, directory: str) -> subprocess.CompletedProcess:
    """只在自建隐藏子进程触发 fatal；超时会 kill 并 wait，禁用 dump/弹窗。"""
    prelude = """
import os
if os.name == "nt":
    import ctypes
    ctypes.windll.kernel32.SetErrorMode(0x0003 | 0x8000)
    ctypes.CDLL("ucrtbase")._set_abort_behavior(0, 3)
else:
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
"""
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-c", prelude + textwrap.dedent(script)],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "CRASH_TEST_DIR": directory},
        capture_output=True, encoding="utf-8", errors="replace", timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


class CrashDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.log_dir = Path(self.temp_dir.name) / "logs"
        self.diagnostics = CrashDiagnostics(candidate_dirs=[str(self.log_dir)])
        self.addCleanup(self.diagnostics.close)
        self.assertTrue(self.diagnostics.initialize(app_version="test"))

    def _diagnostic_text(self) -> str:
        assert self.diagnostics.path is not None
        self.diagnostics.flush()
        return Path(self.diagnostics.path).read_text(encoding="utf-8")

    def test_sessions_use_unique_files_and_remain_writable_after_log_rotation(self) -> None:
        other = CrashDiagnostics(candidate_dirs=[str(self.log_dir)])
        self.addCleanup(other.close)
        self.assertTrue(other.initialize(app_version="test"))
        self.assertNotEqual(self.diagnostics.path, other.path)

        rotating = logging.handlers.RotatingFileHandler(
            self.log_dir / "ordinary.log", maxBytes=1, backupCount=1, encoding="utf-8"
        )
        rotating.emit(logging.makeLogRecord({"msg": "rotate", "levelno": 20, "levelname": "INFO"}))
        rotating.close()

        self.diagnostics.record_text("诊断文件仍可写标记")
        self.assertIn("诊断文件仍可写标记", self._diagnostic_text())

    def test_python_thread_and_unraisable_hooks_record_exception_chains(self) -> None:
        original_hooks = (sys.excepthook, threading.excepthook, sys.unraisablehook)
        received = []

        def observe_main(*args):
            received.append("sys")

        def observe_thread(args):
            received.append("threading")

        def observe_unraisable(args):
            received.append("unraisable")

        def restore_hooks():
            self.diagnostics.close()
            sys.excepthook, threading.excepthook, sys.unraisablehook = original_hooks

        self.addCleanup(restore_hooks)
        sys.excepthook = observe_main
        threading.excepthook = observe_thread
        sys.unraisablehook = observe_unraisable
        self.diagnostics.install_python_hooks()

        try:
            raise ValueError("主线程标记")
        except ValueError:
            sys.excepthook(*sys.exc_info())

        def raise_in_thread() -> None:
            raise RuntimeError("普通线程标记")

        thread = threading.Thread(target=raise_in_thread, name="诊断测试线程")
        thread.start()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

        try:
            raise LookupError("不可回收标记")
        except LookupError:
            exc_type, exc_value, exc_tb = sys.exc_info()
        sys.unraisablehook(
            types.SimpleNamespace(
                exc_type=exc_type,
                exc_value=exc_value,
                exc_traceback=exc_tb,
                err_msg="测试 unraisable",
                object=None,
            )
        )

        diagnostic_text = self._diagnostic_text()
        self.assertIn("主线程标记", diagnostic_text)
        self.assertIn("普通线程标记", diagnostic_text)
        self.assertIn("不可回收标记", diagnostic_text)
        self.assertEqual(["sys", "threading", "unraisable"], received)
        self.assertIn("source=sys.excepthook", diagnostic_text)
        self.assertIn("source=threading.excepthook", diagnostic_text)
        self.assertIn("source=sys.unraisablehook", diagnostic_text)

    def test_missing_context_and_failed_initialization_do_not_raise_from_callback(self) -> None:
        unavailable = CrashDiagnostics(candidate_dirs=[str(Path(self.temp_dir.name) / "not-a-dir")])
        with unittest.mock.patch("utils.crash_diagnostics.os.open", side_effect=OSError("denied")):
            self.assertFalse(unavailable.initialize())
        unavailable.record_qt_message(None, None, None)
        unavailable.close()

    def test_existing_directory_open_denied_tries_next_candidate(self) -> None:
        blocked = Path(self.temp_dir.name) / "blocked"
        fallback = Path(self.temp_dir.name) / "fallback"
        blocked.mkdir()
        real_open = os.open

        def open_except_blocked(path, *args, **kwargs):
            if Path(path).parent == blocked:
                raise PermissionError("file denied")
            return real_open(path, *args, **kwargs)

        other = CrashDiagnostics(candidate_dirs=[str(blocked), str(fallback)])
        self.addCleanup(other.close)
        with mock.patch("utils.crash_diagnostics.os.open", open_except_blocked):
            self.assertTrue(other.initialize())
        self.assertEqual(fallback, Path(other.path).parent)
        self.assertIn("PermissionError", Path(other.path).read_text(encoding="utf-8"))

    def test_callback_handles_missing_context_and_write_failure(self) -> None:
        self.diagnostics.record_qt_message(1, None, "无 context 消息")
        self.assertIn("无 context 消息", self._diagnostic_text())
        with mock.patch("utils.crash_diagnostics.os.write", side_effect=OSError):
            self.diagnostics.record_qt_message(3, None, "fatal write failure")
        self.assertIn("failed", self.diagnostics.status["write"])

    def test_fatal_callback_survives_context_and_flush_failures(self) -> None:
        class BrokenContext:
            @property
            def category(self):
                raise RuntimeError("missing context")

        with mock.patch("utils.crash_diagnostics.os.fsync", side_effect=ValueError):
            self.diagnostics.record_qt_message(3, BrokenContext(), "flush failed marker")
        self.assertIn("flush failed marker", self._diagnostic_text())

    def test_reentrant_message_is_bounded(self) -> None:
        real_write = os.write
        calls = 0

        def reentrant_write(fd, data):
            nonlocal calls
            calls += 1
            self.diagnostics.record_qt_message(1, None, "recursive")
            return real_write(fd, data)

        with mock.patch("utils.crash_diagnostics.os.write", reentrant_write):
            self.diagnostics.record_qt_message(1, None, "outer message")
        self.assertLessEqual(calls, 3)
        self.assertIn("outer message", self._diagnostic_text())

    def test_unwritable_streams_and_faulthandler_failure_do_not_block_start(self) -> None:
        other = CrashDiagnostics(candidate_dirs=[self.temp_dir.name])
        self.addCleanup(other.close)
        with (
            mock.patch("utils.crash_diagnostics.os.open", side_effect=PermissionError),
            mock.patch("builtins.open", side_effect=PermissionError),
            mock.patch("sys.stdout", None), mock.patch("sys.stderr", None),
        ):
            self.assertFalse(other.initialize())
            other.redirect_missing_streams()
            print("discarded safely")
            sys.stderr.write("discarded safely\n")
            self.assertEqual("failed: no writable directory", other.status["file"])
            self.assertIn("discarded", other.status["stdout"])
            self.assertIn("discarded", other.status["stderr"])
        working = CrashDiagnostics(candidate_dirs=[self.temp_dir.name])
        self.addCleanup(working.close)
        with mock.patch("utils.crash_diagnostics.faulthandler.enable",
                        side_effect=RuntimeError("enable failed")):
            self.assertTrue(working.initialize())
        self.assertIn("failed", working.status["faulthandler"])

    def test_logger_reuses_session_directory_but_falls_back_on_app_file_denial(self) -> None:
        from utils import logger as logger_module
        from utils import crash_diagnostics

        root = logging.getLogger()
        old_handlers, old_level = root.handlers[:], root.level
        root.handlers = []
        self.addCleanup(lambda: root.setLevel(old_level))

        def restore():
            for handler in root.handlers:
                handler.close()
            root.handlers = old_handlers

        self.addCleanup(restore)
        real_handler = logger_module.RotatingFileHandler
        fallback = Path(self.temp_dir.name) / "local"
        with (
            mock.patch.object(crash_diagnostics, "_diagnostics", self.diagnostics),
            mock.patch.object(logger_module, "_global_log_manager", None),
            mock.patch("sys.stderr", io.StringIO()),
            mock.patch.dict(os.environ, {"LOCALAPPDATA": str(fallback)}),
        ):
            logger_module.setup_logger()
            self.assertEqual(self.log_dir, Path(logger_module.get_log_manager().log_file).parent)

            def refuse_session(path, *args, **kwargs):
                if Path(path).parent == self.log_dir:
                    raise PermissionError("app file denied")
                return real_handler(path, *args, **kwargs)

            with mock.patch.object(logger_module, "RotatingFileHandler", refuse_session):
                logger_module.setup_logger()
            actual = logger_module.get_log_manager().log_file
            self.assertTrue(Path(actual).is_relative_to(fallback))
            self.assertIn(actual, self._diagnostic_text())


class CrashDiagnosticsQtFatalProcessTests(unittest.TestCase):
    @unittest.skipUnless(
        __import__("importlib").util.find_spec("PyQt6") is not None,
        "需要 PyQt6 才能验证 qFatal 回调",
    )
    def test_qfatal_writes_marker_and_still_terminates_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            script = textwrap.dedent(
                """
                import ctypes
                import os
                from utils.crash_diagnostics import CrashDiagnostics
                ctypes.windll.kernel32.SetErrorMode(0x0003 | 0x8000) if os.name == "nt" else None
                d = CrashDiagnostics(candidate_dirs=[os.environ["CRASH_TEST_DIR"]])
                assert d.initialize(app_version="subprocess")
                from PyQt6.QtCore import qFatal
                d.install_qt_message_handler()
                qFatal("唯一中文 qFatal 标记".encode("utf-8"))
                """
            )
            result = run_child(script, temp_dir)

            self.assertNotEqual(0, result.returncode)
            self.assertNotEqual(1, result.returncode, result.stderr)
            files = list(Path(temp_dir).glob("diagnostic-*.log"))
            self.assertEqual(1, len(files))
            content = files[0].read_text(encoding="utf-8")
            self.assertIn("唯一中文 qFatal 标记", content)
            self.assertIn("Current thread", content)

    @unittest.skipUnless(
        __import__("importlib").util.find_spec("PyQt6") is not None,
        "需要 PyQt6 才能验证 qFatal 回调",
    )
    def test_qfatal_does_not_wait_for_another_logging_handler_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            script = textwrap.dedent(
                """
                import ctypes
                import logging
                import os
                import threading
                import time
                from utils.crash_diagnostics import CrashDiagnostics
                ctypes.windll.kernel32.SetErrorMode(0x0003 | 0x8000) if os.name == "nt" else None
                handler = logging.StreamHandler()
                logging.getLogger().addHandler(handler)
                locked = threading.Event()
                release = threading.Event()
                def hold_lock():
                    handler.acquire()
                    locked.set()
                    release.wait(30)
                threading.Thread(target=hold_lock, daemon=True).start()
                assert locked.wait(2)
                d = CrashDiagnostics(candidate_dirs=[os.environ["CRASH_TEST_DIR"]])
                assert d.initialize()
                from PyQt6.QtCore import qFatal
                d.install_qt_message_handler()
                qFatal("锁竞争 fatal 标记".encode("utf-8"))
                """
            )
            started = time.monotonic()
            result = run_child(script, temp_dir)
            elapsed = time.monotonic() - started

            self.assertNotEqual(0, result.returncode)
            self.assertNotEqual(1, result.returncode, result.stderr)
            self.assertLess(elapsed, 8)
            files = list(Path(temp_dir).glob("diagnostic-*.log"))
            self.assertEqual(1, len(files))
            content = files[0].read_text(encoding="utf-8")
            self.assertIn("锁竞争 fatal 标记", content)
            self.assertIn("hold_lock", content)
            self.assertIn("Current thread", content)

    def test_two_process_starts_preserve_diagnostics_and_line_buffered_streams(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            script = """
                import sys
                from utils.crash_diagnostics import CrashDiagnostics
                d = CrashDiagnostics(candidate_dirs=[os.environ["CRASH_TEST_DIR"]])
                assert d.initialize(app_version="two-starts")
                sys.stdout = sys.stderr = None
                d.redirect_missing_streams()
                print("stdout 会话标记")
                sys.stderr.write("stderr 会话标记\\n")
                d.record_text("两次启动诊断标记")
                os._exit(0)
            """
            first = run_child(script, temp_dir)
            self.assertEqual(0, first.returncode, first.stderr)
            saved = {p: p.read_bytes() for p in Path(temp_dir).glob("*.log")}
            self.assertEqual(3, len(saved))
            second = run_child(script, temp_dir)
            self.assertEqual(0, second.returncode, second.stderr)
            self.assertEqual(6, len(list(Path(temp_dir).glob("*.log"))))
            for path, content in saved.items():
                self.assertEqual(content, path.read_bytes())
            for prefix in ("stdout", "stderr"):
                for path in Path(temp_dir).glob(f"{prefix}-*.log"):
                    self.assertIn(f"{prefix} 会话标记", path.read_text(encoding="utf-8"))

    def test_missing_stream_file_denial_falls_back_independently_before_immediate_exit(self) -> None:
        for denied_name in ("stdout", "stderr"):
            with self.subTest(stream=denied_name), tempfile.TemporaryDirectory() as temp_dir:
                script = f"denied_name = {denied_name!r}\n" + textwrap.dedent("""
                    import builtins, sys
                    from pathlib import Path
                    from unittest import mock
                    from utils.crash_diagnostics import CrashDiagnostics
                    root = Path(os.environ["CRASH_TEST_DIR"])
                    primary, fallback = root / "primary", root / "fallback"
                    d = CrashDiagnostics(candidate_dirs=[str(primary), str(fallback)])
                    assert d.initialize()
                    assert Path(d.path).parent == primary
                    original_open = builtins.open
                    def selective_open(path, *args, **kwargs):
                        target = Path(path)
                        if (target.parent == primary
                                and target.name.startswith(denied_name + "-")):
                            raise PermissionError("only this stream file is denied")
                        return original_open(path, *args, **kwargs)
                    sys.stdout = sys.stderr = None
                    with mock.patch("builtins.open", selective_open):
                        d.redirect_missing_streams()
                    print("stdout independent fallback marker")
                    sys.stderr.write("stderr independent fallback marker\\n")
                    os._exit(0)
                """)
                result = run_child(script, temp_dir)
                self.assertEqual(0, result.returncode, result.stderr)
                root = Path(temp_dir)
                for stream_name in ("stdout", "stderr"):
                    directory = root / ("fallback" if stream_name == denied_name else "primary")
                    files = list(directory.glob(f"{stream_name}-*.log"))
                    self.assertEqual(1, len(files), f"{stream_name}: {directory}")
                    self.assertIn(
                        f"{stream_name} independent fallback marker",
                        files[0].read_text(encoding="utf-8"),
                    )
                diagnostic = next((root / "primary").glob("diagnostic-*.log"))
                content = diagnostic.read_text(encoding="utf-8")
                self.assertIn(f"{denied_name}=enabled: {root / 'fallback'}", content)

    def test_real_unhandled_hooks_keep_chains_and_system_exit_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_child("""
                import sys, threading
                from utils.crash_diagnostics import CrashDiagnostics
                d = CrashDiagnostics(candidate_dirs=[os.environ["CRASH_TEST_DIR"]])
                assert d.initialize()
                d.install_python_hooks()
                def thread_error():
                    raise RuntimeError("real thread marker")
                t = threading.Thread(target=thread_error)
                t.start(); t.join()
                t = threading.Thread(target=lambda: sys.exit(0))
                t.start(); t.join()
                class BadDestructor:
                    def __repr__(self):
                        raise AssertionError("repr must not be called")
                    def __del__(self):
                        raise LookupError("real unraisable marker")
                del_object = BadDestructor()
                del del_object
                try:
                    raise ValueError("real cause marker")
                except ValueError as exc:
                    raise RuntimeError("real main marker") from exc
            """, temp_dir)
            self.assertEqual(1, result.returncode, result.stderr)
            content = next(Path(temp_dir).glob("diagnostic-*.log")).read_text(encoding="utf-8")
            for marker in ("real thread marker", "real unraisable marker",
                           "real cause marker", "real main marker"):
                self.assertIn(marker, content)
                self.assertIn(marker, result.stderr)
            self.assertIn("direct cause", content)
            self.assertNotIn("SystemExit", content)
            self.assertNotIn("repr must not be called", content)

    def test_bootstrap_import_does_not_load_qt_or_vapoursynth(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_child("""
                import sys
                from utils.crash_diagnostics import initialize
                d = initialize(os.environ["CRASH_TEST_DIR"])
                assert d.path
                assert not any(n.startswith("PyQt6") for n in sys.modules)
                assert "vapoursynth" not in sys.modules
                d.close()
            """, temp_dir)
            self.assertEqual(0, result.returncode, result.stderr)

    @unittest.skipUnless(importlib.util.find_spec("PyQt6"), "需要 PyQt6")
    def test_no_writable_fd_preserves_qt_stderr_and_qfatal_termination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_child("""
                from pathlib import Path
                from utils.crash_diagnostics import CrashDiagnostics
                blocked = Path(os.environ["CRASH_TEST_DIR"]) / "not-directory"
                blocked.touch()
                d = CrashDiagnostics(candidate_dirs=[str(blocked)])
                assert not d.initialize()
                os.environ["QT_FORCE_STDERR_LOGGING"] = "1"
                installed = d.install_qt_message_handler()
                print(f"installed={installed} status={d.status['qt_handler']}", flush=True)
                from PyQt6.QtCore import qFatal
                qFatal(b"fatal without fd stderr marker")
            """, temp_dir)
            self.assertNotIn(result.returncode, (0, 1), result.stderr)
            self.assertIn("fatal without fd stderr marker", result.stderr)
            self.assertIn("installed=False", result.stdout)
            self.assertIn("degraded: no diagnostic fd; Qt handler unchanged", result.stdout)
            self.assertEqual([], list(Path(temp_dir).glob("diagnostic-*.log")))

    @unittest.skipUnless(importlib.util.find_spec("PyQt6"), "需要 PyQt6")
    def test_no_writable_fd_preserves_preexisting_qt_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_child("""
                from utils.crash_diagnostics import CrashDiagnostics
                from PyQt6.QtCore import qInstallMessageHandler, qWarning, qFatal
                def previous_handler(kind, context, message):
                    os.write(2, ("previous-handler: " + message + "\\n").encode("utf-8"))
                qInstallMessageHandler(previous_handler)
                d = CrashDiagnostics(candidate_dirs=[])
                assert not d.initialize()
                d.install_qt_message_handler()
                qWarning(b"original warning marker")
                qFatal(b"original fatal marker")
            """, temp_dir)
            self.assertNotIn(result.returncode, (0, 1), result.stderr)
            self.assertIn("previous-handler: original warning marker", result.stderr)
            self.assertIn("previous-handler: original fatal marker", result.stderr)

    @unittest.skipUnless(importlib.util.find_spec("PyQt6"), "需要 PyQt6")
    def test_main_hooks_coexist_and_qt_handler_precedes_application(self) -> None:
        for frozen in (False, True):
            with self.subTest(frozen=frozen), tempfile.TemporaryDirectory() as temp_dir:
                script = f"frozen_mode = {frozen!r}\n" + textwrap.dedent("""
                    import runpy, sys
                    from pathlib import Path
                    from unittest import mock
                    from utils import crash_diagnostics as cd
                    root = os.environ["CRASH_TEST_DIR"]
                    source = str(Path.cwd() / "main.py")
                    if frozen_mode:
                        sys.frozen = True
                        sys.executable = str(Path(root) / "ArknightsPassMaker.exe")
                        sys.stdout = sys.stderr = None
                    with mock.patch.object(cd, "log_directories", return_value=[root]):
                        entry = runpy.run_path(source, run_name="diagnostic_test_main")
                    assert not any(n.startswith("PyQt6") for n in sys.modules)
                    assert "vapoursynth" not in sys.modules
                    from PyQt6 import QtCore, QtWidgets
                    from utils import logger as log_module
                    class ApplicationBoundary:
                        @staticmethod
                        def setAttribute(*args):
                            pass
                        def __init__(self, args):
                            assert cd.get_diagnostics().status["qt_handler"] == "enabled"
                            try:
                                raise RuntimeError("main legacy hook marker")
                            except RuntimeError:
                                sys.excepthook(*sys.exc_info())
                            QtCore.qFatal("main boundary fatal 中文标记".encode("utf-8"))
                    with (
                        mock.patch.object(QtWidgets, "QApplication", ApplicationBoundary),
                        mock.patch.object(log_module, "cleanup_old_logs"),
                    ):
                        entry["_main_inner"]()
                """)
                result = run_child(script, temp_dir)
                self.assertNotIn(result.returncode, (0, 1), result.stderr)
                diagnostic = next(Path(temp_dir).glob("diagnostic-*.log"))
                content = diagnostic.read_text(encoding="utf-8")
                self.assertIn("source=sys.excepthook", content)
                self.assertIn("main legacy hook marker", content)
                self.assertIn("main boundary fatal 中文标记", content)
                self.assertIn("Current thread", content)
                self.assertIn(f"frozen={frozen}", content)
                normal = next(Path(temp_dir).glob("app_*.log"))
                self.assertIn("main legacy hook marker", normal.read_text(encoding="utf-8"))

    def test_main_top_level_failure_retains_diagnostic_and_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = run_child("""
                import builtins, runpy
                from unittest import mock
                from utils import crash_diagnostics as cd
                root = os.environ["CRASH_TEST_DIR"]
                with mock.patch.object(cd, "log_directories", return_value=[root]):
                    entry = runpy.run_path("main.py", run_name="diagnostic_test_main")
                def failed_startup():
                    raise RuntimeError("top level startup marker")
                entry["main"].__globals__["_main_inner"] = failed_startup
                original_import = builtins.__import__
                def no_ui(name, *args, **kwargs):
                    if name == "PyQt6.QtWidgets":
                        raise ImportError("no GUI in test")
                    return original_import(name, *args, **kwargs)
                with mock.patch("builtins.__import__", no_ui):
                    entry["main"]()
            """, temp_dir)
            self.assertEqual(1, result.returncode, result.stderr)
            content = next(Path(temp_dir).glob("diagnostic-*.log")).read_text(encoding="utf-8")
            self.assertIn("source=main", content)
            self.assertIn("top level startup marker", content)
