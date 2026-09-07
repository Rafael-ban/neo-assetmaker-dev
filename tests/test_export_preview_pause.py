import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from PyQt6.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog, QMainWindow

from gui.dialogs.export_progress_dialog import ExportProgressDialog
from gui.main_window import MainWindow
from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


class _CountingWorker:
    def __init__(self):
        self.requests = 0

    def request_frame(self, **_kwargs):
        self.requests += 1
        return None


class _IdlePreview:
    is_playing = False
    video_path = ""


class _ExportWindow:
    _pause_all_videos = MainWindow._pause_all_videos
    _preview_media_identity = staticmethod(MainWindow._preview_media_identity)
    _restore_export_previews = MainWindow._restore_export_previews


class _PlayablePreview:
    def __init__(self, path="fixture.mp4", is_playing=True):
        self.video_path = path
        self.is_playing = is_playing
        self.pause_calls = 0
        self.play_calls = 0

    def pause(self):
        self.pause_calls += 1
        self.is_playing = False

    def play(self):
        self.play_calls += 1
        self.is_playing = True


class _CountingPreview(_PlayablePreview):
    def __init__(self):
        super().__init__()
        self.worker = _CountingWorker()

    def request_frame(self):
        if self.is_playing:
            self.worker.request_frame()


class _Signal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _FakeDialog:
    def __init__(self, _parent):
        self.cancel_requested = _Signal()
        self.completed = []
        self.exec_calls = 0

    def set_completed(self, success, message):
        self.completed.append((success, message))

    def update_progress(self, *_args):
        pass

    def exec(self):
        self.exec_calls += 1


class _ValidValidator:
    def __init__(self, _base_dir):
        pass

    def validate_config(self, _config):
        pass

    def has_errors(self):
        return False


class _OnExportWindow(_ExportWindow):
    _pause_export_previews = MainWindow._pause_export_previews
    _restore_export_previews = MainWindow._restore_export_previews
    _on_export_completed = MainWindow._on_export_completed
    _on_export = MainWindow._on_export

    def _export_service_has_pending_worker(self):
        return MainWindow._export_service_has_pending_worker(self)

    def _finish_export_preview_lifecycle(self):
        return MainWindow._finish_export_preview_lifecycle(self)

    def __init__(self):
        self.video_preview = _PlayablePreview()
        self.intro_preview = _IdlePreview()
        self.frame_capture_preview = _IdlePreview()
        self.transition_preview = SimpleNamespace(
            preview_in=_IdlePreview(), preview_loop=_IdlePreview()
        )
        self._videos_were_playing = []
        self._export_paused_previews = []
        self._export_in_progress = False
        self._export_call_active = False
        self._config = SimpleNamespace(loop=SimpleNamespace(is_image=False))
        self._script_ready = True
        self._base_dir = "project"
        self.status_bar = SimpleNamespace(showMessage=lambda _message: None)
        self._collect_export_data = mock.Mock(
            return_value={"loop_render_session": object()}
        )
        self._collect_arknights_custom_images = mock.Mock(return_value=[])
        self._collect_image_overlay = mock.Mock(return_value=[])


class _CompletedExportService:
    def __init__(self, _parent):
        self._worker = None
        self.progress_updated = _Signal()
        self.export_completed = _Signal()
        self.export_failed = _Signal()

    def export_all(self, **_kwargs):
        self.export_completed.emit("完成")

    def cancel(self):
        pass


class _SynchronousFailingExportService(_CompletedExportService):
    def export_all(self, **_kwargs):
        raise RuntimeError("同步启动失败")


class _RunningWorker:
    def isRunning(self):
        return True


class _StoppedButUncollectedWorker:
    def isRunning(self):
        return False


class _RunningThenFailingExportService(_CompletedExportService):
    def __init__(self, parent):
        super().__init__(parent)
        self._worker = _RunningWorker()

    def export_all(self, **_kwargs):
        raise RuntimeError("worker 已启动后异常")


class _StoppedThenFailingExportService(_CompletedExportService):
    def __init__(self, parent):
        super().__init__(parent)
        self._worker = _StoppedButUncollectedWorker()

    def export_all(self, **_kwargs):
        raise RuntimeError("worker 已返回但尚未收尾")


class ExportPreviewPauseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ensure_app()

    def setUp(self):
        self.preview = _CountingPreview()
        self.worker = self.preview.worker
        self.preview._render_session = SimpleNamespace(epoch=7)

        self.window = _ExportWindow()
        self.window.video_preview = self.preview
        self.window.intro_preview = _IdlePreview()
        self.window.frame_capture_preview = _IdlePreview()
        self.window.transition_preview = SimpleNamespace(
            preview_in=_IdlePreview(), preview_loop=_IdlePreview()
        )
        self.window._videos_were_playing = []

    def test_export_modal_pauses_preview_requests_then_restores(self):
        """删除导出暂停边界时，模态窗口期间的计数 worker 会继续取帧。"""

        def run_modal_with_preview_ticks():
            dialog = QDialog()
            dialog.setModal(True)
            ticks = QTimer(dialog)
            ticks.timeout.connect(self.preview.request_frame)
            ticks.start(10)
            QTimer.singleShot(120, dialog.accept)
            dialog.exec()

        run_modal_with_preview_ticks()
        self.assertGreater(self.worker.requests, 0)

        paused = MainWindow._pause_export_previews(self.window)
        requests_before_modal = self.worker.requests

        run_modal_with_preview_ticks()

        self.assertEqual(self.worker.requests, requests_before_modal)
        self.assertFalse(self.preview.is_playing)

        MainWindow._restore_export_previews(self.window, paused)
        self.preview.request_frame()
        self.assertGreater(self.worker.requests, requests_before_modal)
        self.assertTrue(self.preview.is_playing)

    def test_export_restore_skips_preview_replaced_during_export(self):
        """删除媒体身份校验时，导出完成会错误恢复已换素材的预览。"""
        self.preview.play()
        paused = MainWindow._pause_export_previews(self.window)
        self.preview.video_path = "replacement.mp4"

        MainWindow._restore_export_previews(self.window, paused)

        self.assertFalse(self.preview.is_playing)

    def test_export_restore_allows_reloaded_session_for_same_media(self):
        """把媒体身份误绑到 session epoch 时，同一路径重新冻结后不会恢复播放。"""
        self.preview.play()
        paused = MainWindow._pause_export_previews(self.window)
        self.preview._render_session = SimpleNamespace(epoch=8)

        MainWindow._restore_export_previews(self.window, paused)

        self.assertTrue(self.preview.is_playing)

    def test_export_restore_does_not_start_preview_that_was_already_paused(self):
        """删除原播放状态记录时，导出完成会错误启动本来暂停的预览。"""
        self.preview.pause()
        paused = MainWindow._pause_export_previews(self.window)

        MainWindow._restore_export_previews(self.window, paused)

        self.assertFalse(self.preview.is_playing)

    def test_export_pause_preserves_page_navigation_state(self):
        """删除页面暂停状态保存时，导出会覆盖返回素材页所需的恢复列表。"""
        page_paused = [object()]
        self.window._videos_were_playing = page_paused
        self.preview.play()

        MainWindow._pause_export_previews(self.window)

        self.assertIs(self.window._videos_were_playing, page_paused)


class MainWindowExportLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.window = _OnExportWindow()
        self.dialog_patch = mock.patch(
            "gui.dialogs.export_progress_dialog.ExportProgressDialog", _FakeDialog
        )
        self.file_dialog_patch = mock.patch(
            "gui.main_window.QFileDialog.getExistingDirectory", return_value="output"
        )
        self.validator_patch = mock.patch(
            "core.validator.EPConfigValidator", _ValidValidator
        )
        self.error_patch = mock.patch("gui.main_window.show_error")
        self.dialog_patch.start()
        self.file_dialog_patch.start()
        self.validator_patch.start()
        self.error_patch.start()
        self.addCleanup(self.dialog_patch.stop)
        self.addCleanup(self.file_dialog_patch.stop)
        self.addCleanup(self.validator_patch.stop)
        self.addCleanup(self.error_patch.stop)

    def _run_export_with(self, service_type):
        with mock.patch("core.export_service.ExportService", service_type):
            return self.window._on_export()

    def test_collect_failure_restores_preview_without_creating_service(self):
        """删除准备异常恢复时，收集失败会让原先播放的预览永久停住。"""
        self.window._collect_export_data.side_effect = RuntimeError("收集失败")

        self._run_export_with(_CompletedExportService)

        self.assertTrue(self.window.video_preview.is_playing)
        self.assertIsNone(getattr(self.window, "_export_service", None))

    def test_auxiliary_image_failure_restores_preview_without_creating_service(self):
        """删除辅助图片异常恢复时，辅助资源失败会让原先播放的预览永久停住。"""
        self.window._collect_arknights_custom_images.side_effect = RuntimeError("图片失败")

        self._run_export_with(_CompletedExportService)

        self.assertTrue(self.window.video_preview.is_playing)
        self.assertIsNone(getattr(self.window, "_export_service", None))

    def test_synchronous_export_start_failure_restores_preview_and_clears_guard(self):
        """删除安全收尾时，尚未拥有 worker 的同步启动失败会遗留暂停和重入锁。"""
        with self.assertRaisesRegex(RuntimeError, "同步启动失败"):
            self._run_export_with(_SynchronousFailingExportService)

        self.assertTrue(self.window.video_preview.is_playing)
        self.assertFalse(self.window._export_in_progress)

    def test_running_worker_exception_defers_restore_and_keeps_guard(self):
        """异常分支无条件收尾时，仍运行 worker 会被提前恢复预览并允许重入。"""
        with self.assertRaisesRegex(RuntimeError, "worker 已启动后异常"):
            self._run_export_with(_RunningThenFailingExportService)

        self.assertFalse(self.window.video_preview.is_playing)
        self.assertTrue(self.window._export_in_progress)

    def test_stopped_but_uncollected_worker_still_defers_restore(self):
        """只检查 isRunning 时，run 返回与 queued terminal 间会提前允许重入。"""
        with self.assertRaisesRegex(RuntimeError, "worker 已返回但尚未收尾"):
            self._run_export_with(_StoppedThenFailingExportService)

        self.assertFalse(self.window.video_preview.is_playing)
        self.assertTrue(self.window._export_in_progress)

        self.window._export_service._worker = None
        self.window._on_export_completed(False, "已收尾")

        self.assertTrue(self.window.video_preview.is_playing)
        self.assertFalse(self.window._export_in_progress)

    def test_completed_export_restores_preview_after_terminal_callback(self):
        """删除终态恢复时，导出完成后原先播放的预览不会继续播放。"""
        result = self._run_export_with(_CompletedExportService)

        self.assertEqual(result[1], "output")
        self.assertTrue(self.window.video_preview.is_playing)
        self.assertFalse(self.window._export_in_progress)
        self.assertEqual(self.window._export_dialog.completed, [(True, "完成")])

    def test_second_export_is_blocked_while_previous_worker_is_running(self):
        """删除重入保护时，第二次导出会替换仍在运行 worker 的父对象引用。"""
        self.window._export_in_progress = True

        with mock.patch("gui.main_window.QMessageBox.information") as information:
            self.window._on_export()

        information.assert_called_once()
        self.window._collect_export_data.assert_not_called()

    def test_preparation_is_paused_and_blocks_reentrant_export(self):
        states = []

        def collect():
            states.append(self.window.video_preview.is_playing)
            if len(states) == 1:
                self.window._on_export()
            return {}

        self.window._collect_export_data.side_effect = collect
        with mock.patch("gui.main_window.QMessageBox.information"):
            self._run_export_with(_CompletedExportService)

        self.assertEqual(states, [False])
        self.assertTrue(self.window.video_preview.is_playing)

    def test_error_dialog_exception_still_restores_preview(self):
        self.window._collect_export_data.side_effect = ValueError("收集失败")
        with mock.patch("gui.main_window.show_error", side_effect=RuntimeError("弹窗失败")):
            with self.assertRaisesRegex(RuntimeError, "弹窗失败"):
                self._run_export_with(_CompletedExportService)

        self.assertTrue(self.window.video_preview.is_playing)
        self.assertFalse(self.window._export_in_progress)

    def test_service_and_dialog_construction_failures_restore_preview(self):
        for target in (
            "core.export_service.ExportService",
            "gui.dialogs.export_progress_dialog.ExportProgressDialog",
        ):
            with self.subTest(target=target):
                self.window = _OnExportWindow()
                with mock.patch(
                    "core.export_service.ExportService", _CompletedExportService
                ):
                    with mock.patch(target, side_effect=RuntimeError("构造失败")):
                        with self.assertRaisesRegex(RuntimeError, "构造失败"):
                            self.window._on_export()
                self.assertTrue(self.window.video_preview.is_playing)
                self.assertFalse(self.window._export_in_progress)

    def test_exec_exception_restores_only_after_service_releases_worker(self):
        for worker in (None, _RunningWorker(), _StoppedButUncollectedWorker()):
            with self.subTest(worker=type(worker).__name__):
                self.window = _OnExportWindow()
                service = _CompletedExportService(self.window)
                service._worker = worker
                service.export_all = lambda **kwargs: None
                with mock.patch(
                    "core.export_service.ExportService", return_value=service
                ):
                    with mock.patch.object(
                        _FakeDialog, "exec", side_effect=RuntimeError("exec失败")
                    ):
                        with self.assertRaisesRegex(RuntimeError, "exec失败"):
                            self.window._on_export()
                self.assertEqual(self.window.video_preview.is_playing, worker is None)
                self.assertEqual(self.window._export_in_progress, worker is not None)
                if worker is not None:
                    service._worker = None
                    service.export_failed.emit("已收尾")
                    self.assertTrue(self.window.video_preview.is_playing)
                    self.assertFalse(self.window._export_in_progress)

    def test_completed_dialog_keeps_guard_until_modal_call_returns(self):
        observations = []
        with mock.patch.object(_FakeDialog, "exec", lambda dialog: observations.append(
            (self.window._export_in_progress, self.window.video_preview.is_playing)
        )):
            self._run_export_with(_CompletedExportService)

        self.assertEqual(observations, [(True, False)])
        self.assertTrue(self.window.video_preview.is_playing)
        self.assertFalse(self.window._export_in_progress)

    def test_early_dialog_return_without_pending_worker_restores(self):
        service = _CompletedExportService(self.window)
        service.export_all = lambda **kwargs: None
        with mock.patch("core.export_service.ExportService", return_value=service):
            self.window._on_export()
        self.assertTrue(self.window.video_preview.is_playing)
        self.assertFalse(self.window._export_in_progress)

    def test_late_old_service_terminal_does_not_change_current_dialog(self):
        self._run_export_with(_CompletedExportService)
        old_service = self.window._export_service
        self._run_export_with(_CompletedExportService)
        current_dialog = self.window._export_dialog

        old_service.export_failed.emit("旧导出迟到的结果")

        self.assertEqual(current_dialog.completed, [(True, "完成")])

    def test_pause_failure_restores_already_paused_tracks_and_page_state(self):
        page_state = [object()]
        self.window._videos_were_playing = page_state
        self.window.intro_preview = _PlayablePreview("intro.mp4")
        self.window.intro_preview.pause = mock.Mock(side_effect=RuntimeError("暂停失败"))

        with self.assertRaisesRegex(RuntimeError, "暂停失败"):
            self._run_export_with(_CompletedExportService)

        self.assertTrue(self.window.video_preview.is_playing)
        self.assertTrue(self.window.intro_preview.is_playing)
        self.assertIs(self.window._videos_were_playing, page_state)
        self.assertFalse(self.window._export_in_progress)

    def test_media_identity_is_captured_before_pause_signal_changes_media(self):
        preview = self.window.video_preview
        original_pause = preview.pause

        def pause_and_replace_media():
            original_pause()
            preview.video_path = "replacement.mp4"

        preview.pause = pause_and_replace_media
        self._run_export_with(_CompletedExportService)

        self.assertFalse(preview.is_playing)

    def test_connect_failure_restores_preview(self):
        service = _CompletedExportService(self.window)
        service.progress_updated.connect = mock.Mock(side_effect=RuntimeError("连接失败"))
        with mock.patch("core.export_service.ExportService", return_value=service):
            with self.assertRaisesRegex(RuntimeError, "连接失败"):
                self.window._on_export()

        self.assertTrue(self.window.video_preview.is_playing)
        self.assertFalse(self.window._export_in_progress)

    def test_cancelled_directory_selection_keeps_playback_unchanged(self):
        with mock.patch(
            "gui.main_window.QFileDialog.getExistingDirectory", return_value=""
        ):
            self.window._on_export()

        self.assertTrue(self.window.video_preview.is_playing)
        self.assertEqual(self.window.video_preview.pause_calls, 0)
        self.window._collect_export_data.assert_not_called()

    def test_synchronous_failure_signal_restores_after_dialog_exit(self):
        service = _CompletedExportService(self.window)
        service.export_all = lambda **kwargs: service.export_failed.emit("准备失败")
        with mock.patch("core.export_service.ExportService", return_value=service):
            self.window._on_export()

        self.assertEqual(self.window._export_dialog.completed, [(False, "准备失败")])
        self.assertTrue(self.window.video_preview.is_playing)
        self.assertFalse(self.window._export_in_progress)

    def test_success_and_failure_restore_only_unchanged_playing_tracks(self):
        for success in (True, False):
            with self.subTest(success=success):
                self.window = _OnExportWindow()
                self.window.intro_preview = _PlayablePreview("intro.mp4", False)
                service = _CompletedExportService(self.window)

                def complete_with_changed_media(**kwargs):
                    self.window.video_preview.video_path = "new-media.mp4"
                    terminal = (
                        service.export_completed if success else service.export_failed
                    )
                    terminal.emit("终态")

                service.export_all = complete_with_changed_media
                with mock.patch(
                    "core.export_service.ExportService", return_value=service
                ):
                    self.window._on_export()

                self.assertFalse(self.window.video_preview.is_playing)
                self.assertFalse(self.window.intro_preview.is_playing)


class _QtExportHost(QMainWindow, _OnExportWindow):
    """真实 QWidget 宿主；跳过主窗口启动时的配置持久化和无关页面。"""

    def __init__(self):
        QMainWindow.__init__(self)
        _OnExportWindow.__init__(self)


class _HeldExportThread(QThread):
    progress_updated = pyqtSignal(int, str)
    export_completed = pyqtSignal(str)
    export_failed = pyqtSignal(str)

    def __init__(self, parent, *, success):
        super().__init__(parent)
        self.success = success
        self.result_sent = threading.Event()
        self.release_exit = threading.Event()
        self.cancel_calls = 0
        self.exit_timed_out = False

    def setup(self, **kwargs):
        pass

    def cancel(self):
        self.cancel_calls += 1

    def run(self):
        terminal = self.export_completed if self.success else self.export_failed
        terminal.emit("线程业务结果")
        self.result_sent.set()
        self.exit_timed_out = not self.release_exit.wait(5)


class MainWindowExportQtLifecycleTests(unittest.TestCase):
    def test_real_service_and_dialog_wait_for_thread_finished_before_restoring(self):
        from config.epconfig import EPConfig
        from core.export_service import ExportService

        app = ensure_app()
        for success in (True, False):
            with self.subTest(success=success):
                window = _QtExportHost()
                window._config = EPConfig()
                window._collect_export_data.return_value = {}
                workers = []
                observed = []
                callback_errors = []
                timed_out = []
                phase = 0

                def make_worker(parent):
                    worker = _HeldExportThread(parent, success=success)
                    workers.append(worker)
                    return worker

                def advance():
                    nonlocal phase
                    try:
                        if not workers or window._export_dialog is None:
                            return
                        worker = workers[0]
                        dialog = window._export_dialog
                        if phase == 0 and worker.result_sent.is_set():
                            observed.append((
                                window._export_service._worker is worker,
                                dialog._is_completed,
                                window.video_preview.is_playing,
                            ))
                            QTest.keyClick(dialog, Qt.Key.Key_Escape)
                            observed.append((dialog.isVisible(), worker.cancel_calls))
                            phase = 1
                        elif phase == 1:
                            observed.append((window._export_in_progress,
                                             window.video_preview.is_playing))
                            worker.release_exit.set()
                            phase = 2
                        elif phase == 2 and dialog._is_completed:
                            observed.append((window._export_service._worker,
                                             dialog.was_successful))
                            QTest.mouseClick(
                                dialog.btn_action, Qt.MouseButton.LeftButton
                            )
                            phase = 3
                    except BaseException as exc:
                        callback_errors.append(repr(exc))
                        window._export_dialog.done(QDialog.DialogCode.Rejected)

                def force_exit():
                    timed_out.append(True)
                    for worker in workers:
                        worker.release_exit.set()
                    if window._export_dialog is not None:
                        window._export_dialog.done(QDialog.DialogCode.Rejected)

                poll = QTimer(window)
                poll.timeout.connect(advance)
                watchdog = QTimer(window)
                watchdog.setSingleShot(True)
                watchdog.timeout.connect(force_exit)
                try:
                    with (
                        mock.patch("core.validator.EPConfigValidator", _ValidValidator),
                        mock.patch(
                            "gui.main_window.QFileDialog.getExistingDirectory",
                            return_value="unused",
                        ),
                        mock.patch.object(
                            ExportService, "_prepare_export_package",
                            return_value=object(),
                        ),
                        mock.patch(
                            "core.export_service.ExportWorker", side_effect=make_worker
                        ),
                    ):
                        poll.start(10)
                        watchdog.start(3000)
                        window._on_export()
                    self.assertEqual(callback_errors, [])
                    self.assertEqual(timed_out, [])
                    self.assertEqual(phase, 3)
                    self.assertEqual(observed, [
                        (True, False, False), (True, 1),
                        (True, False), (None, success),
                    ])
                    self.assertTrue(window.video_preview.is_playing)
                    self.assertFalse(window._export_in_progress)
                    self.assertFalse(workers[0].exit_timed_out)
                finally:
                    poll.stop()
                    watchdog.stop()
                    for worker in workers:
                        worker.release_exit.set()
                        try:
                            # 仅测试清理使用 wait；生产 GUI 从不阻塞等待 QThread。
                            self.assertTrue(worker.wait(6000))
                        except RuntimeError:
                            # 正常终态路径的 deleteLater 已在 exec 内完成。
                            pass
                    deadline = time.monotonic() + 2
                    while (
                        window._export_service_has_pending_worker()
                        and time.monotonic() < deadline
                    ):
                        app.processEvents()
                        QTest.qWait(10)
                    window.deleteLater()


class ExportProgressDialogRejectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ensure_app()

    def test_reject_requests_cancel_and_keeps_modal_open_until_completion(self):
        """删除 reject 覆盖时，Esc 会在服务终态前退出嵌套事件循环。"""
        dialog = ExportProgressDialog()
        cancel_requests = []
        remained_open = []
        finished = []
        timed_out = []
        dialog.cancel_requested.connect(lambda: cancel_requests.append(True))
        dialog.finished.connect(finished.append)

        watchdog = QTimer(dialog)
        watchdog.setSingleShot(True)

        def force_exit_after_timeout():
            timed_out.append(True)
            dialog.done(QDialog.DialogCode.Rejected)

        watchdog.timeout.connect(force_exit_after_timeout)

        def reject_while_running():
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            remained_open.append(dialog.isVisible())
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            dialog._on_action_clicked()
            QTimer.singleShot(0, finish_export)

        def finish_export():
            dialog.set_completed(False, "已取消")
            QTest.keyClick(dialog, Qt.Key.Key_Escape)

        QTimer.singleShot(0, reject_while_running)
        watchdog.start(500)
        dialog.exec()
        watchdog.stop()

        self.assertEqual(cancel_requests, [True])
        self.assertEqual(remained_open, [True])
        self.assertEqual(timed_out, [])
        self.assertEqual(finished, [QDialog.DialogCode.Rejected])

    def test_completed_cancel_dialog_reenables_confirm_button(self):
        """删除完成时的重新启用时，取消后的“确定”按钮仍不可点击。"""
        dialog = ExportProgressDialog()
        dialog._on_action_clicked()
        self.assertFalse(dialog.btn_action.isEnabled())

        dialog.set_completed(False, "已取消")
        dialog._on_action_clicked()

        self.assertTrue(dialog.btn_action.isEnabled())
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def _assert_synchronous_cancel_completion(self, *, via_escape):
        for success in (True, False):
            with self.subTest(success=success, via_escape=via_escape):
                dialog = ExportProgressDialog()
                self.addCleanup(dialog.deleteLater)
                self.addCleanup(dialog.done, QDialog.DialogCode.Rejected)
                completed = []
                finished = []

                def complete_synchronously():
                    completed.append(success)
                    dialog.set_completed(success, "同步终态")

                dialog.cancel_requested.connect(
                    complete_synchronously, Qt.ConnectionType.DirectConnection
                )
                dialog.finished.connect(finished.append)
                dialog.open()
                if via_escape:
                    QTest.keyClick(dialog, Qt.Key.Key_Escape)
                else:
                    QTest.mouseClick(dialog.btn_action, Qt.MouseButton.LeftButton)

                self.assertEqual(completed, [success])
                self.assertEqual(
                    dialog.label_status.text(), "导出完成!" if success else "导出失败"
                )
                self.assertEqual(dialog.label_detail.text(), "同步终态")
                self.assertEqual(dialog.btn_action.text(), "确定")
                self.assertTrue(dialog.btn_action.isEnabled())
                self.assertEqual(dialog.was_successful, success)
                self.assertEqual(finished, [])

                QTest.mouseClick(dialog.btn_action, Qt.MouseButton.LeftButton)

                self.assertEqual(finished, [QDialog.DialogCode.Accepted])
                self.assertFalse(dialog.isVisible())
                self.assertEqual(completed, [success])

    def test_button_synchronous_completion_preserves_success_and_failure_ui(self):
        self._assert_synchronous_cancel_completion(via_escape=False)

    def test_escape_synchronous_completion_preserves_success_and_failure_ui(self):
        self._assert_synchronous_cancel_completion(via_escape=True)


if __name__ == "__main__":
    unittest.main()
