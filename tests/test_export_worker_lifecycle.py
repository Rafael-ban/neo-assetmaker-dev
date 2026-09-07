"""ExportService 仅在 QThread 完全退出后发布业务终态。"""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from PyQt6.QtCore import QCoreApplication, QThread, pyqtSignal

from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


class _LatchingExportWorker(QThread):
    """真实启动的 QThread；在 run 的 finally 中保持 running 状态。"""

    progress_updated = pyqtSignal(int, str)
    export_completed = pyqtSignal(str)
    export_failed = pyqtSignal(str)
    result_posted = pyqtSignal()

    def __init__(self, *, outcome: str, parent=None):
        super().__init__(parent)
        self.outcome = outcome
        self.entered_finally = threading.Event()
        self.release_finally = threading.Event()
        self.finally_timed_out = False
        self.delete_later_calls = 0
        self.delete_threads = []
        self.result_consumed = threading.Event()
        self.result_posted.connect(self.result_consumed.set)

    def setup(self, **_kwargs) -> None:
        pass

    def cancel(self) -> None:
        pass

    def deleteLater(self) -> None:
        # 安全截获旧服务在线程仍运行时发出的 deleteLater 调用；这里不会投递
        # 实际的 DeferredDelete，以免为了复现回归而触发 Qt 的致命析构路径。
        self.delete_later_calls += 1
        self.delete_threads.append(QThread.currentThread())

    def run(self) -> None:
        try:
            if self.outcome in ("success", "duplicate_success"):
                self.export_completed.emit("worker success")
                if self.outcome == "duplicate_success":
                    self.export_completed.emit("duplicate success")
                    self.export_failed.emit("duplicate failure")
            else:
                self.export_failed.emit("worker failure")
            self.result_posted.emit()
        finally:
            self.entered_finally.set()
            self.finally_timed_out = not self.release_finally.wait(timeout=3)


class ExportWorkerLifecycleTests(unittest.TestCase):
    def _start_service(self, outcome: str):
        from config.epconfig import EPConfig
        from core.export_service import ExportService

        service = ExportService()
        workers = []

        def make_worker(parent):
            worker = _LatchingExportWorker(outcome=outcome, parent=parent)
            # factory 返回前登记：即使 start 或随后的断言失败也能回收线程。
            self.addCleanup(self._release_worker, worker)
            workers.append(worker)
            return worker

        patcher = mock.patch("core.export_service.ExportWorker", side_effect=make_worker)
        patcher.start()
        self.addCleanup(patcher.stop)
        prepared = mock.Mock()
        with mock.patch.object(service, "_prepare_export_package", return_value=prepared):
            service.export_all(output_dir="unused", epconfig=EPConfig())
        self.assertEqual(len(workers), 1)
        worker = workers[0]
        self.assertTrue(worker.entered_finally.wait(timeout=2))
        return service, worker

    def _release_worker(self, worker) -> None:
        worker.release_finally.set()
        self.assertTrue(worker.wait(5000))
        self.assertFalse(worker.finally_timed_out)

    @staticmethod
    def _pump_until(predicate, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError("Qt event loop did not reach the expected lifecycle state")

    def _assert_terminal_waits_for_worker_exit(self, outcome: str):
        service, worker = self._start_service(outcome)
        completed, failed = [], []
        service.export_completed.connect(completed.append)
        service.export_failed.connect(failed.append)

        # Dispatch the worker's business result while its finally block holds
        # the native thread alive.  No externally visible terminal or deletion
        # is safe at this point.
        self._pump_until(worker.result_consumed.is_set)

        self.assertIs(service._worker, worker)
        self.assertTrue(worker.isRunning())
        self.assertEqual(worker.delete_later_calls, 0)
        self.assertEqual(completed, [])
        self.assertEqual(failed, [])

        worker.release_finally.set()
        self._pump_until(lambda: service._worker is None)

        self.assertFalse(worker.isRunning())
        self.assertFalse(worker.finally_timed_out)
        self.assertEqual(worker.delete_later_calls, 1)
        self.assertEqual(worker.delete_threads, [service.thread()])
        if outcome in ("success", "duplicate_success"):
            self.assertEqual(completed, ["worker success"])
            self.assertEqual(failed, [])
        else:
            self.assertEqual(completed, [])
            self.assertEqual(failed, ["worker failure"])

        self.assertEqual(len(completed) + len(failed), 1)

    def test_success_is_not_published_until_running_worker_exits(self):
        self._assert_terminal_waits_for_worker_exit("success")

    def test_failure_is_not_published_until_running_worker_exits(self):
        self._assert_terminal_waits_for_worker_exit("failure")

    def test_duplicate_business_results_publish_only_the_first_result(self):
        self._assert_terminal_waits_for_worker_exit("duplicate_success")

    def test_reentrant_export_ignores_all_late_events_from_previous_worker(self):
        """终态回调可开始下一轮；旧 worker 的进度和终态都不能污染它。"""
        from config.epconfig import EPConfig

        service, first = self._start_service("success")
        completed, failed, progress, terminal_states = [], [], [], []
        service.export_failed.connect(failed.append)
        service.progress_updated.connect(lambda *args: progress.append(args))

        def on_completed(message):
            terminal_states.append((service._worker, QThread.currentThread()))
            completed.append(message)
            if len(completed) == 1:
                with mock.patch.object(
                    service, "_prepare_export_package", return_value=mock.Mock()
                ):
                    service.export_all(output_dir="next", epconfig=EPConfig())

        service.export_completed.connect(on_completed)
        first.release_finally.set()
        self._pump_until(lambda: len(completed) == 1)
        second = service._worker
        self.assertIsNotNone(second)
        self.assertIsNot(second, first)
        self.assertTrue(second.entered_finally.wait(2))
        self._pump_until(second.result_consumed.is_set)

        first.progress_updated.emit(99, "stale progress")
        first.export_completed.emit("stale success")
        first.export_failed.emit("stale failure")
        first.finished.emit()
        self.assertIs(service._worker, second)
        self.assertEqual(progress, [])
        self.assertEqual(completed, ["worker success"])
        self.assertEqual(failed, [])
        self.assertEqual(first.delete_later_calls, 1)

        second.progress_updated.emit(12, "current progress")
        self.assertEqual(progress, [(12, "current progress")])
        second.release_finally.set()
        self._pump_until(lambda: len(completed) == 2)
        self.assertEqual(terminal_states, [(None, service.thread())] * 2)
        self.assertEqual(completed, ["worker success", "worker success"])
        self.assertEqual(failed, [])
        self.assertEqual(second.delete_threads, [service.thread()])


if __name__ == "__main__":
    unittest.main()
