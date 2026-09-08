"""F1: 裁剪手势的绘制、持久化、undo 和加载事务回归。"""
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QPointF, QTimer, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtTest import QSignalSpy, QTest

from config.epconfig import EPConfig
from core.auto_save_service import AutoSaveService
from core.vs_runtime.job import RationalFPS, load_render_job
from gui.main_window import MainWindow
from gui.widgets.video_preview import PreviewRenderContext, VideoPreviewWidget
from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


class _SlowWorker(QObject):
    """受理请求但永不回帧，确保断言不依赖解码完成。"""

    def __init__(self):
        super().__init__()
        self.loads = []
        self.requests = []
        self._request_id = 0

    def _next_id(self):
        self._request_id += 1
        return self._request_id

    def start(self):
        return self._next_id()

    def load(self, session):
        self.loads.append(session)
        return self._next_id()

    def request_frame(self, **kwargs):
        self.requests.append(kwargs)
        return self._next_id()

    def cancel_epoch(self, _epoch):
        return self._next_id()

    def unload(self):
        return self._next_id()

    def close(self):
        pass


def _mouse(kind, point, *, button=Qt.MouseButton.LeftButton):
    return QMouseEvent(
        kind,
        QPointF(point),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )


def _cyan_bounds(image):
    """返回真实 QLabel 图像中 cyan crop 线的水平边界。"""
    xs = []
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.red() < 80 and color.green() > 200 and color.blue() > 200:
                xs.append(x)
    if not xs:
        return None
    return min(xs), max(xs)


class CropTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.old_media = root / "old.mp4"
        self.new_media = root / "new.mp4"
        self.old_media.write_bytes(b"not decoded")
        self.new_media.write_bytes(b"not decoded")
        self.worker = _SlowWorker()
        self.widget = VideoPreviewWidget()
        self.addCleanup(self.widget.close)
        self.widget.supports_editor_capability = lambda capability: capability == "crop"
        self.widget.set_render_context(PreviewRenderContext.builtin(
            project_root=str(root), track="loop", cache_dir=str(root / "cache")
        ))
        self.widget.video_path = str(self.old_media)
        self.widget._metadata_resolved = True
        self.widget._fps_rational = RationalFPS(30, 1)
        self.widget._vs_active = True
        self.widget._has_video = True
        self.widget.total_frames = 300
        self.widget.video_width = 1920
        self.widget.video_height = 1080
        self.widget.cropbox = [480, 180, 360, 640]
        self.widget._worker_client = self.worker
        self.widget._worker_ready_for_frames = True
        self.widget._render_session = types.SimpleNamespace(epoch=1)
        self.widget._selection = types.SimpleNamespace(mode="compatible")
        self.widget._session_metadata = types.SimpleNamespace(editor=object())
        self.widget.video_label.resize(960, 1080)
        self.widget.resize(960, 1120)
        self.widget.show()
        QTest.qWait(10)

    def _gesture(self, end_x):
        x, y, width, height = self.widget.get_cropbox()
        start = QPoint(
            int((x + width / 2) * self.widget.display_scale + self.widget.display_offset_x),
            int((y + height / 2) * self.widget.display_scale + self.widget.display_offset_y),
        )
        self.widget._handle_mouse_press(
            self.widget.video_label,
            _mouse(QMouseEvent.Type.MouseButtonPress, start),
        )
        self.widget._handle_mouse_move(
            self.widget.video_label,
            _mouse(QMouseEvent.Type.MouseMove, QPoint(end_x, start.y())),
        )

    def _release(self, end_x):
        self.widget._handle_mouse_release(
            _mouse(QMouseEvent.Type.MouseButtonRelease, QPoint(end_x, 500))
        )

    def _window(self):
        window = MainWindow.__new__(MainWindow)
        window._config = EPConfig()
        window._config.loop.file = str(self.old_media)
        window._config.editor.loop.crop = list(self.widget.cropbox)
        window._restoring_editor_state = False
        window._editor_sync_suspended = set()
        window._pending_editor_restore = {}
        window._applying_undo = False
        window._undo_stack = []
        window._redo_stack = []
        window._undo_timer = QTimer()
        window._undo_timer.setSingleShot(True)
        window._max_history = 50
        window._loop_in_out = (0, 299)
        window._intro_in_out = (0, 0)
        window._is_modified = False
        window._update_title = lambda: None
        window._set_undo_redo_enabled = lambda: None
        window.status_bar = types.SimpleNamespace(showMessage=lambda *_a, **_k: None)
        window.video_preview = self.widget
        window.intro_preview = object()
        window._preview_has_loaded_media = lambda preview: preview is self.widget
        window._preview_supports = lambda preview, capability: (
            preview is self.widget and capability == "crop"
        )
        window._is_timeline_bound_to = lambda _preview: False
        window._get_cached_in_out = lambda _preview: (0, 299)
        window._snapshot_active_timeline_state = lambda: None
        window._reset_undo_history()
        self.widget.cropbox_changed.connect(
            lambda *_crop: MainWindow._on_editor_state_changed(window, self.widget)
        )
        self.widget.crop_edit_started.connect(
            lambda: MainWindow._on_crop_edit_started(window)
        )
        self.widget.crop_edit_committed.connect(
            lambda: MainWindow._on_crop_edit_committed(window)
        )
        return window

    def test_i1_real_label_draws_draft_position_without_worker_frame_or_formal_crop(self):
        formal = []
        self.widget.cropbox_changed.connect(lambda *crop: formal.append(crop))
        before = _cyan_bounds(self.widget.video_label.grab().toImage())
        requests_before = list(self.worker.requests)

        self._gesture(390)
        QTest.qWait(20)
        after = _cyan_bounds(self.widget.video_label.grab().toImage())

        self.assertEqual(before, (239, 420))
        self.assertEqual(after, (299, 480))
        self.assertEqual(self.widget.cropbox, [480, 180, 360, 640])
        self.assertEqual(self.widget.get_draft_cropbox(), (600, 180, 360, 640))
        self.assertEqual(formal, [])
        self.assertEqual(self.worker.requests, requests_before)

    def test_i2_two_fast_gestures_are_two_undo_steps_and_flush_old_pending_first(self):
        window = self._window()
        window._config.name = "older non-crop edit"
        MainWindow._mark_undo_change(window)

        self._gesture(390)
        self._release(390)
        self._gesture(450)
        self._release(450)

        self.assertFalse(window._undo_timer.isActive())
        self.assertEqual(len(window._undo_stack), 3)
        self.assertEqual(window._undo_stack[1]["name"], "older non-crop edit")
        self.assertEqual(window._undo_stack[2]["editor"]["loop"]["crop"][0], 600)

        window._apply_undo_state = lambda state: setattr(
            window, "_config", EPConfig.from_dict(state)
        )
        MainWindow._on_undo(window)
        self.assertEqual(window._config.editor.loop.crop[0], 600)
        MainWindow._on_undo(window)
        self.assertEqual(window._config.editor.loop.crop[0], 480)
        self.assertEqual(window._config.name, "older non-crop edit")

    def test_i3_redo_does_not_consume_history_after_ensure_commits_new_draft(self):
        window = self._window()
        self._gesture(390)
        self._release(390)
        window._apply_undo_state = lambda state: setattr(
            window, "_config", EPConfig.from_dict(state)
        )
        MainWindow._on_undo(window)
        self.assertEqual(window._config.editor.loop.crop[0], 480)
        self.assertEqual(len(window._redo_stack), 1)

        self._gesture(450)
        MainWindow._on_redo(window)

        self.assertEqual(window._config.editor.loop.crop[0], 720)
        self.assertEqual(window._redo_stack, [])
        self.assertFalse(window._undo_timer.isActive())

    def test_i4_forced_end_and_label_ungrab_reset_all_drag_state(self):
        formal = []
        self.widget.cropbox_changed.connect(lambda *crop: formal.append(crop))
        self._gesture(390)
        self.widget.ensure_render_state_committed()
        requests_after_end = len(self.worker.requests)

        self.assertEqual(formal, [(600, 180, 360, 640)])
        self.assertEqual(self.widget.drag_mode, self.widget.DRAG_NONE)
        self.assertIsNone(self.widget.drag_start_pos)
        self.assertEqual(self.widget.drag_start_cropbox, [])
        self.widget._handle_mouse_move(
            self.widget.video_label,
            _mouse(QMouseEvent.Type.MouseMove, QPoint(420, 500)),
        )
        self._release(420)
        self.assertEqual(formal, [(600, 180, 360, 640)])
        self.assertEqual(len(self.worker.requests), requests_after_end)

        self._gesture(450)
        QCoreApplication.sendEvent(self.widget.video_label, QEvent(QEvent.Type.UngrabMouse))
        self.assertFalse(self.widget.has_active_crop_edit())
        self.assertEqual(self.widget.drag_mode, self.widget.DRAG_NONE)
        self.assertIsNone(self.widget.drag_start_pos)
        self.assertEqual(self.widget.drag_start_cropbox, [])
        self._release(450)
        self.assertEqual(len(formal), 2)

    def test_i5_active_draft_ignores_wasd_and_escape_restores_committed_crop(self):
        window = self._window()
        self._gesture(390)
        requests_before = len(self.worker.requests)
        self.widget.keyPressEvent(QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_D, Qt.KeyboardModifier.NoModifier
        ))

        self.assertEqual(self.widget.cropbox[0], 480)
        self.assertEqual(self.widget.get_draft_cropbox()[0], 600)
        self.assertEqual(window._config.editor.loop.crop[0], 480)
        self.assertEqual(window._undo_stack, [])
        self.assertEqual(len(self.worker.requests), requests_before)

        self.widget.keyPressEvent(QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
        ))
        self.assertEqual(self.widget.cropbox[0], 480)
        self.assertIsNone(self.widget.get_draft_cropbox())
        self.assertFalse(self.widget.has_active_crop_edit())
        self.assertEqual(window._config.editor.loop.crop[0], 480)

    def test_i6_source_load_commits_old_draft_before_new_job_starts(self):
        sequence = []
        self.widget.cropbox_changed.connect(
            lambda *crop: sequence.append(("commit", crop, self.widget.has_active_crop_edit()))
        )
        self.widget._load_render_job = lambda *, bootstrap: sequence.append(
            ("load", bootstrap, self.widget.has_active_crop_edit(), tuple(self.widget.cropbox))
        )
        self.widget.begin_crop_edit()
        self.widget._set_draft_cropbox([600, 180, 360, 640])

        self.assertTrue(self.widget.load_video(str(self.new_media)))
        self.assertEqual(sequence[0], ("commit", (600, 180, 360, 640), False))
        self.assertEqual(sequence[1], ("load", True, False, (600, 180, 360, 640)))

    def test_i7_check_save_cancel_and_autosave_snapshot_keep_real_crop_contracts(self):
        window = self._window()
        self._gesture(390)
        from PyQt6.QtWidgets import QMessageBox
        with mock.patch("gui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel) as question:
            self.assertFalse(MainWindow._check_save(window))
        self.assertEqual(question.call_count, 1)
        self.assertTrue(window._is_modified)
        self.assertEqual(window._config.editor.loop.crop[0], 600)
        self.assertFalse(self.widget.has_active_crop_edit())

        self._gesture(450)
        undo_before = list(window._undo_stack)
        live_crop = list(window._config.editor.loop.crop)
        with tempfile.TemporaryDirectory() as directory:
            service = AutoSaveService()
            service.start(
                window._config,
                os.path.join(directory, "epconfig.json"),
                directory,
                snapshot_provider=window._autosave_snapshot,
            )
            service.save_now()
            restored = EPConfig.load_from_file(service.get_latest_backup())

        self.assertEqual(restored.editor.loop.crop[0], 720)
        self.assertEqual(window._config.editor.loop.crop, live_crop)
        self.assertTrue(self.widget.has_active_crop_edit())
        self.assertEqual(window._undo_stack, undo_before)
        self.assertEqual(self.worker.loads, [])

    def test_i7_active_drag_blocks_job_load_and_frame_requests_until_single_release(self):
        """错误回归：拖中调度/flush 生成 session，或重复收口重复 load。"""
        formal = []
        self.widget.cropbox_changed.connect(lambda *crop: formal.append(crop))
        loads_before = len(self.worker.loads)
        requests_before = list(self.worker.requests)

        self._gesture(390)
        start_y = self.widget.drag_start_pos.y()
        for end_x in (405, 420, 450):
            self.widget._handle_mouse_move(
                self.widget.video_label,
                _mouse(QMouseEvent.Type.MouseMove, QPoint(end_x, start_y)),
            )
        QTest.qWait(120)
        self.widget.flush_render_job()

        self.assertEqual(len(self.worker.loads), loads_before)
        self.assertEqual(self.worker.requests, requests_before)
        self.assertEqual(formal, [])

        timeout_spy = QSignalSpy(self.widget._job_debounce.timeout)
        self._release(450)
        self.assertTrue(self.widget._job_dirty)
        self.assertTrue(self.widget._job_debounce.isActive())
        self.assertTrue(timeout_spy.wait(1000))
        self.assertEqual(len(timeout_spy), 1)
        self.assertEqual(len(self.worker.loads), loads_before + 1)
        self.assertFalse(self.widget._job_dirty)
        self.assertFalse(self.widget._job_debounce.isActive())
        self.assertEqual(formal, [(720, 180, 360, 640)])

        self.widget.ensure_render_state_committed()
        self._release(450)
        self.assertFalse(self.widget._job_debounce.isActive())
        QTest.qWait(120)
        self.assertEqual(len(self.worker.loads), loads_before + 1)
        self.assertEqual(formal, [(720, 180, 360, 640)])

    def test_i7_noop_gestures_do_not_commit_crop_but_preserve_non_crop_pending(self):
        """错误回归：no-op 写 crop/undo/load，或手势吞掉既有 pending。"""
        window = self._window()
        formal = []
        self.widget.cropbox_changed.connect(lambda *crop: formal.append(crop))

        cases = (
            ("未移动", [480, 180, 360, 640], None),
            ("左边界钳制", [0, 180, 360, 640], [-500, 180, 360, 640]),
            ("返回起点", [480, 180, 360, 640], "return_to_start"),
        )
        for name, crop, draft in cases:
            with self.subTest(name=name):
                self.widget.cropbox = list(crop)
                self.widget._job_dirty = False
                self.widget._job_debounce.stop()
                loads_before = len(self.worker.loads)
                formal_before = len(formal)
                undo_before = len(window._undo_stack)

                self.assertTrue(self.widget.begin_crop_edit())
                if draft == "return_to_start":
                    self.widget._set_draft_cropbox([600, 180, 360, 640])
                    self.assertNotEqual(
                        self.widget.get_draft_cropbox(), tuple(crop)
                    )
                    self.widget._set_draft_cropbox(list(crop))
                elif draft is not None:
                    self.widget._set_draft_cropbox(draft)
                QTest.qWait(120)
                self.widget.flush_render_job()
                self.assertFalse(self.widget.end_crop_edit(commit=True))

                self.assertFalse(self.widget._job_debounce.isActive())
                QTest.qWait(120)
                self.assertEqual(len(formal), formal_before)
                self.assertEqual(len(window._undo_stack), undo_before)
                self.assertEqual(len(self.worker.loads), loads_before)

        window._config.name = "保留的非裁剪编辑"
        MainWindow._mark_undo_change(window)
        self.widget.supports_editor_capability = (
            lambda capability: capability in {"crop", "trim"}
        )
        self.assertTrue(self.widget.set_timeline_range(2, 200))
        loads_before = len(self.worker.loads)
        formal_before = len(formal)

        self.assertTrue(self.widget.begin_crop_edit())
        undo_after_begin = len(window._undo_stack)
        QTest.qWait(120)
        self.widget.flush_render_job()
        self.assertEqual(len(self.worker.loads), loads_before)
        self.assertEqual(window._config.name, "保留的非裁剪编辑")

        timeout_spy = QSignalSpy(self.widget._job_debounce.timeout)
        self.assertFalse(self.widget.end_crop_edit(commit=True))
        self.assertTrue(self.widget._job_dirty)
        self.assertTrue(self.widget._job_debounce.isActive())
        self.assertTrue(timeout_spy.wait(1000))
        self.assertEqual(len(timeout_spy), 1)
        self.assertEqual(len(formal), formal_before)
        self.assertEqual(len(window._undo_stack), undo_after_begin)
        self.assertEqual(len(self.worker.loads), loads_before + 1)
        self.assertEqual(window._config.name, "保留的非裁剪编辑")
        self.assertFalse(self.widget._job_dirty)
        self.assertFalse(self.widget._job_debounce.isActive())
        job = load_render_job(self.worker.loads[-1].job_path)
        self.assertEqual(job.timeline.start_frame, 2)
        self.assertEqual(job.timeline.end_frame, 200)


if __name__ == "__main__":
    unittest.main()
