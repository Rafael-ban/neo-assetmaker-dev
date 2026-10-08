"""M4：真实 GUI + VSWorkerClient 的用户 vpy 验收。

本模块刻意不在父 Qt 进程导入 ``vapoursynth`` 或 ``core.vs_engine``。测试
素材由 VSPipe 子进程生成；预览图和所有帧转换则只发生在 VSWorkerClient 启动
的独立 worker 中。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from core.media_tools import MediaToolchain
from core.vs_runtime.job import RationalFPS
from core.vs_runtime.script_header import parse_script_header
from core.vs_runtime.session import ScriptSelection, compute_script_bundle_hash
from tests.qt_harness import ensure_app


REPO = Path(__file__).resolve().parents[1]
TC = MediaToolchain.discover(str(REPO))
# 复用仓库已有的媒体工具发现和 preview 分发门控；门控本身不会触发 VS import。
REAL_WORKER_OK = (
    HAS_CV2
    and not TC.missing_for_export()
    and not TC.missing_for_preview()
)
_FPS = RationalFPS(30_000, 1_001)
_SOURCE_FRAME_COUNT = 90


def setUpModule():
    ensure_app()


class _RecordingWorkerClientMixin:
    """保留真 client 行为，仅记录 GUI 送入真实 IPC 的帧请求。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.frame_requests: list[dict[str, object]] = []

    def request_frame(self, **kwargs):
        request_id = super().request_frame(**kwargs)
        if request_id is not None:
            self.frame_requests.append({"request_id": request_id, **kwargs})
        return request_id


class ParentProcessIsolationTests(unittest.TestCase):
    def test_parent_qt_process_never_imports_vapoursynth_or_vs_engine(self):
        """若 GUI 回退到旧 in-process 路径，此断言会立即失败。"""
        self.assertNotIn("vapoursynth", sys.modules)
        self.assertNotIn("core.vs_engine", sys.modules)


@unittest.skipUnless(
    REAL_WORKER_OK,
    "VapourSynth preview / encode toolchain unavailable",
)
class RealPreviewWorkerAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """生成 30000/1001 的非黑色视频，再改名到中文路径。"""
        from tests.helpers.m5_render_fixture import (
            build_default_render_session,
            encode_render_session,
        )

        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        source_png = cls.root / "source.png"
        image = np.zeros((360, 240, 3), np.uint8)
        image[:, :] = (24, 64, 224)
        cv2.imwrite(str(source_png), image)
        encoded = cls.root / "source.mp4"
        session = build_default_render_session(
            cls.root / "render-session",
            source_path=source_png,
            source_kind="image",
            end_frame=_SOURCE_FRAME_COUNT,
            fps=_FPS,
        )
        encode_render_session(TC, session, encoded)
        chinese_dir = cls.root / "中文目录"
        chinese_dir.mkdir()
        cls.media = chinese_dir / "验收素材.mp4"
        os.replace(encoded, cls.media)

        script = REPO / "resources" / "vapoursynth" / "default_pipeline.vpy"
        header = parse_script_header(script)
        cls.selection = ScriptSelection.from_header(
            script, header, compute_script_bundle_hash(script)
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @staticmethod
    def _wait_until(condition, timeout_s: float = 20.0) -> bool:
        """用 Qt 事件循环和单调 deadline 等待，不使用 sleep 轮询。"""
        if condition():
            return True
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(10)
        deadline = time.monotonic() + timeout_s

        def check():
            if condition() or time.monotonic() >= deadline:
                loop.quit()

        poll.timeout.connect(check)
        poll.start()
        QTimer.singleShot(max(1, int(timeout_s * 1000) + 50), loop.quit)
        loop.exec()
        poll.stop()
        return bool(condition())

    @staticmethod
    def _pump_for(duration_s: float) -> None:
        """在 deadline 内持续投递 Qt 事件，用于观察是否有迟到回写。"""
        loop = QEventLoop()
        QTimer.singleShot(max(1, int(duration_s * 1000)), loop.quit)
        loop.exec()

    def _make_widget(self):
        from gui.widgets.video_preview import PreviewRenderContext, VideoPreviewWidget
        from gui.workers.vs_worker_client import VSWorkerClient

        class RecordingWorkerClient(_RecordingWorkerClientMixin, VSWorkerClient):
            pass

        widget = VideoPreviewWidget(
            worker_client_factory=lambda parent: RecordingWorkerClient(parent)
        )
        widget.resize(720, 960)
        widget.show()
        widget.set_render_context(
            PreviewRenderContext(
                project_root=str(self.root),
                track="loop",
                selection=self.selection,
                cache_dir=str(self.root / "cache"),
            )
        )
        self.addCleanup(lambda: widget.clear(sync_shutdown=True))
        return widget

    def _load_real_widget(self):
        widget = self._make_widget()
        self.assertTrue(widget.load_video(str(self.media)))
        self.assertTrue(
            self._wait_until(
                lambda: widget._metadata_resolved and widget.current_frame is not None
            ),
            "真实 worker 未返回 metadata 与首帧",
        )
        return widget, widget._worker_client

    def _wait_for_new_frame(self, widget, client, previous, request_count, note):
        self.assertTrue(
            self._wait_until(
                lambda: (
                    len(client.frame_requests) > request_count
                    and widget.current_frame is not None
                    and widget.current_frame is not previous
                )
            ),
            note,
        )
        return client.frame_requests[-1]

    def test_real_user_vpy_worker_covers_metadata_seek_clock_zoom_trim_capture_clear_and_exit(self):
        """端到端验收所有 fake 无法替代的 worker 边界。"""
        widget, client = self._load_real_widget()
        self.assertIn("中文目录", widget.video_path)
        self.assertEqual(widget._session_metadata.mode, "compatible")
        self.assertIsNotNone(widget._session_metadata.editor)
        editor = widget._session_metadata.editor
        self.assertEqual((editor.width, editor.height), (360, 640))
        self.assertEqual(editor.num_frames, _SOURCE_FRAME_COUNT)
        # 此断言故意读取私有有理数；若实现先转 float 再反推，分母会丢失。
        self.assertEqual(widget._fps_rational, _FPS)
        self.assertEqual((editor.fps_num, editor.fps_den), (30_000, 1_001))
        self.assertEqual((widget.video_width, widget.video_height), (360, 640))

        first = widget.current_frame
        self.assertEqual(first.ndim, 3)
        self.assertEqual(first.shape[2], 3)
        self.assertGreater(float(first.mean()), 20.0, "首帧不得是黑帧")
        self.assertGreater(
            int(first[0, 0, 2]), int(first[0, 0, 0]), "worker 返回帧必须是 BGR"
        )

        # 精确 seek 既观察 GUI frame index，也观察实际 client 送入 worker 的 index。
        request_count = len(client.frame_requests)
        widget.seek_to_frame(37)
        seek = self._wait_for_new_frame(
            widget, client, first, request_count, "精确 seek 未返回新帧"
        )
        self.assertEqual(widget.current_frame_index, 37)
        self.assertEqual((seek["surface"], seek["index"]), ("editor", 37))

        start = widget.current_frame_index
        widget.play()
        self.assertTrue(
            self._wait_until(lambda: widget.current_frame_index != start),
            "Qt 播放时钟没有前进",
        )
        self.assertTrue(widget.is_playing)
        widget.pause()

        # 在 trim 外切 final，真实 reload 后必须夹到 start，并请求 final 的相对帧 0。
        widget.seek_to_frame(3)
        previous_epoch = widget.current_render_session().epoch
        request_count = len(client.frame_requests)
        widget.set_timeline_range(20, 60)
        widget.set_preview_mode(True)
        self.assertTrue(
            self._wait_until(
                lambda: (
                    widget.current_render_session().epoch > previous_epoch
                    and widget._worker_ready_for_frames
                    and any(
                        item["epoch"] == widget.current_render_session().epoch
                        and item["surface"] == "final"
                        and item["index"] == 0
                        for item in client.frame_requests[request_count:]
                    )
                )
            ),
            "切换 final 后未以 trim 起点请求真实 worker",
        )
        self.assertEqual(widget.current_frame_index, 20)

        # 1% / 100% / 10000% 都必须由真实 worker 返回受 viewport 限制的 BGR 帧。
        for factor, label in ((0.01, "1%"), (1.0, "100%"), (100.0, "10000%")):
            with self.subTest(zoom=label):
                previous = widget.current_frame
                request_count = len(client.frame_requests)
                widget.set_zoom_factor(factor)
                zoom_request = self._wait_for_new_frame(
                    widget,
                    client,
                    previous,
                    request_count,
                    f"{label} zoom 未返回真实 worker 帧",
                )
                self.assertEqual(zoom_request["surface"], "final")
                self.assertAlmostEqual(widget.get_zoom_factor(), factor, places=6)
                self.assertEqual(widget.zoom_label.text(), label)
                height, width = widget.current_frame.shape[:2]
                viewport_width, viewport_height = widget._viewport_size()
                self.assertLessEqual(width, viewport_width)
                self.assertLessEqual(height, viewport_height)

        captured = []
        widget.capture_frame_async(captured.append)
        self.assertTrue(
            self._wait_until(lambda: len(captured) == 1),
            "capture 未收到真实 worker 帧",
        )
        capture = captured[0]
        self.assertIsNotNone(capture)
        self.assertEqual(capture.shape[2], 3)
        self.assertGreater(int(capture[0, 0, 2]), int(capture[0, 0, 0]))
        self.assertFalse(np.shares_memory(capture, widget.current_frame))
        current_value = int(widget.current_frame[0, 0, 0])
        capture[0, 0, 0] ^= np.uint8(255)
        self.assertEqual(int(widget.current_frame[0, 0, 0]), current_value)

        # 在 frame request 已送入真实 worker 后立即 clear；取消 epoch 后任何迟到帧
        # 都不得重建 GUI 状态。
        widget._request_current_frame(coalesce=False)
        widget.clear()
        self._pump_for(0.35)
        self.assertIsNone(widget.current_frame)
        self.assertEqual(widget.total_frames, 0)
        self.assertEqual(widget.video_label.text(), "No media loaded")

        stopped = []
        crashes = []
        client.worker_stopped.connect(lambda: stopped.append(True))
        client.worker_crashed.connect(crashes.append)
        client.shutdown()
        self.assertTrue(
            self._wait_until(lambda: stopped), "worker 未以正常 shutdown 路径退出"
        )
        self.assertEqual(crashes, [])
        self.assertFalse(client.transport.alive)
        self.assertNotIn("vapoursynth", sys.modules)
        self.assertNotIn("core.vs_engine", sys.modules)


if __name__ == "__main__":
    unittest.main()
