"""由独立 VapourSynth worker 驱动的视频预览控件。"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Callable, Literal, Optional, Tuple, TYPE_CHECKING

import numpy as np

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from PyQt6.QtCore import QEvent, QPoint, QRectF, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import CaptionLabel, PushButton, Slider, setCustomStyleSheet

from config.vs_runtime import load_vs_runtime
from core.vs_runtime.job import (
    CropSpec,
    OutputSpec,
    PathSpec,
    RationalFPS,
    RenderJob,
    SourceSpec,
    TimelineSpec,
    TransformSpec,
    write_render_job,
)
from core.vs_runtime.script_header import ScriptHeader, parse_script_header
from core.vs_runtime.session import (
    RenderSession,
    ScriptSelection,
    SessionMetadata,
    compute_job_sha256,
    compute_script_bundle_hash,
)
from core.vs_runtime.vs_loader import compute_runtime_fingerprint
from gui.workers.vs_worker_client import VSWorkerClient
from utils.file_utils import get_app_dir

if TYPE_CHECKING:
    from config.epconfig import EPConfig

logger = logging.getLogger(__name__)

DEFAULT_TARGET_WIDTH = 360
DEFAULT_TARGET_HEIGHT = 640

# Smallest crop side (px) the ratio lock will shrink to. Below ~64px the
# integer rounding of w/h can no longer express a ratio like 0.5625 or 0.6667
# accurately (w=2 -> 2/4 = 0.5, i.e. 11% off), so the lock would appear to
# "break" at extreme zoom-out. 64px keeps the error well under 1%.
_MIN_CROP_SIDE = 64


@dataclass(frozen=True)
class PreviewRenderContext:
    """一个预览轨道冻结的脚本选择与作业路径。"""

    project_root: str
    track: Literal["loop", "intro"]
    selection: ScriptSelection
    cache_dir: str
    header: ScriptHeader | None = None

    def __post_init__(self) -> None:
        if self.track not in ("loop", "intro"):
            raise ValueError("track 必须是 loop/intro")
        if not isinstance(self.selection, ScriptSelection):
            raise TypeError("selection 必须是 ScriptSelection")
        for name in ("project_root", "cache_dir"):
            value = Path(getattr(self, name))
            if not value.is_absolute():
                raise ValueError(f"{name} 必须是绝对路径")
            object.__setattr__(self, name, str(value.resolve()))
        header = self.header or parse_script_header(self.selection.script_path)
        if (
            header.api_version != self.selection.api_version
            or header.mode != self.selection.mode
        ):
            raise ValueError("PreviewRenderContext 的 mode/API 必须与脚本 header 一致")
        object.__setattr__(self, "header", header)

    @classmethod
    def builtin(
        cls,
        *,
        project_root: str,
        track: Literal["loop", "intro"],
        cache_dir: str,
    ) -> "PreviewRenderContext":
        app_dir = str(Path(get_app_dir()).resolve())
        return cls(
            project_root=str(Path(project_root).resolve()),
            track=track,
            selection=_default_selection_for_app(app_dir),
            cache_dir=str(Path(cache_dir).resolve()),
        )


@dataclass(frozen=True)
class _RequestOwner:
    """UI 侧为每个 worker 请求保存的唯一终态身份。"""

    epoch: int
    kind: Literal["load", "frame", "capture"]
    surface: str | None = None
    index: int | None = None


@lru_cache(maxsize=4)
def _runtime_fingerprint_for_app(app_dir: str) -> str:
    """只哈希 runtime 文件；不得在 Qt 进程导入 VapourSynth。"""

    return compute_runtime_fingerprint(app_dir, load_vs_runtime())


@lru_cache(maxsize=4)
def _default_selection_for_app(app_dir: str) -> ScriptSelection:
    script = Path(app_dir) / "resources" / "vapoursynth" / "default_pipeline.vpy"
    header = parse_script_header(script)
    return ScriptSelection.from_header(
        script,
        header,
        compute_script_bundle_hash(script),
    )


class _PreviewLabel(QLabel):
    def __init__(self, owner: "VideoPreviewWidget"):
        super().__init__(owner)
        self._owner = owner

    def paintEvent(self, event):
        super().paintEvent(event)
        self._owner._paint_cropbox(self)

    def mousePressEvent(self, event: QMouseEvent):
        self._owner._handle_mouse_press(self, event)

    def mouseMoveEvent(self, event: QMouseEvent):
        self._owner._handle_mouse_move(self, event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._owner._handle_mouse_release(event)

    def event(self, event):
        if event.type() == QEvent.Type.UngrabMouse:
            self._owner.end_crop_edit(commit=True)
        return super().event(event)


class VideoPreviewWidget(QWidget):
    """Preview media, expose crop/trim state, and keep the legacy public API."""

    cropbox_changed = pyqtSignal(int, int, int, int)
    # 拖动中的裁剪框只影响控件绘制；正式 cropbox_changed 只在提交时发射。
    draft_cropbox_changed = pyqtSignal(int, int, int, int)
    crop_edit_started = pyqtSignal()
    crop_edit_committed = pyqtSignal()
    frame_changed = pyqtSignal(int)
    playback_state_changed = pyqtSignal(bool)
    video_loaded = pyqtSignal(int, float)
    load_failed = pyqtSignal(str)  # async metadata probe failure
    rotation_changed = pyqtSignal(int)

    DRAG_NONE = 0
    DRAG_MOVE = 1
    DRAG_RESIZE_TL = 2
    DRAG_RESIZE_TR = 3
    DRAG_RESIZE_BL = 4
    DRAG_RESIZE_BR = 5

    def __init__(
        self,
        parent=None,
        *,
        worker_client_factory: Callable[[QWidget], VSWorkerClient] | None = None,
    ):
        super().__init__(parent)
        self.video_path = ""
        self.video_fps = 30.0
        self.video_width = 0
        self.video_height = 0
        self.total_frames = 0
        self.current_frame_index = 0
        self.current_frame: Optional[np.ndarray] = None

        # 每个 job/session 使用独立 epoch。旧 worker 事件即使迟到，也只能
        # 释放传输资源，不能再回写这个 widget。
        self._load_epoch = 0
        self._worker_client_factory = worker_client_factory or (
            lambda parent: VSWorkerClient(parent)
        )
        self._worker_client: VSWorkerClient | None = None
        self._worker_started = False
        self._worker_ready_for_frames = False
        self._restart_pending = False
        self._render_context: PreviewRenderContext | None = None
        self._execution_blocked_reason = ""
        self._render_session: RenderSession | None = None
        self._selection: ScriptSelection | None = None
        self._session_metadata: SessionMetadata | None = None
        self._fps_rational: RationalFPS | None = None
        self._output0_frames = 0
        self._timeline_start = 0
        self._timeline_end_exclusive: int | None = None
        self._metadata_resolved = False
        self._job_dirty = False
        self._load_request_id: int | None = None
        self._request_epochs: dict[int, _RequestOwner] = {}
        self._latest_display_request_id: int | None = None
        self._pending_captures: dict[int, Callable] = {}
        self._job_paths: set[Path] = set()
        self._retiring_job_paths: set[Path] = set()
        self._retire_after_unload: dict[int, set[Path]] = {}
        self._owned_cache_dir: Path | None = None
        self._job_debounce = QTimer(self)
        self._job_debounce.setSingleShot(True)
        self._job_debounce.timeout.connect(self._flush_debounced_render_job)
        self._timeout_dialogs: dict[int, QMessageBox] = {}
        # 兼容旧测试/调用者：名字保留，但含义是“worker session 已加载”。
        self._vs_active = False
        self._has_video = False
        self._loop_frame: Optional[np.ndarray] = None
        self._preview_mode = False
        self._epconfig: Optional["EPConfig"] = None
        self._overlay_renderer = None
        self._rotation = 0
        self._zoom_factor = 1.0
        # Point keeps pixel edges hard, so single source pixels stay countable
        # at 10000% — that is what the zoom is for (checking crop boundaries).
        self._zoom_kernel = "Point"
        self._zoom_pan = (0.5, 0.5)  # normalised centre of the magnified window

        self.is_playing = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._on_timer_tick)
        self._play_origin_ns = 0
        self._play_origin_frame = 0

        self.target_width = DEFAULT_TARGET_WIDTH
        self.target_height = DEFAULT_TARGET_HEIGHT
        self.target_aspect_ratio = self.target_width / self.target_height
        self.cropbox = [0, 0, self.target_width, self.target_height]

        self.display_scale = 1.0
        self.display_offset_x = 0
        self.display_offset_y = 0
        self.drag_mode = self.DRAG_NONE
        self.drag_start_pos: Optional[QPoint] = None
        self.drag_start_cropbox: list[int] = []
        self._crop_edit_start: tuple[int, int, int, int] | None = None
        self._draft_cropbox: list[int] | None = None
        self.handle_size = 15

        self._setup_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self._display_stack = QStackedWidget()
        self._display_stack.setMinimumSize(320, 180)
        self._display_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        self.video_label = _PreviewLabel(self)
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setText("No media loaded")
        self.video_label.setMouseTracking(True)
        setCustomStyleSheet(
            self.video_label,
            "background-color: #1a1a1a; border: none; border-radius: 8px; "
            "color: #888; font-size: 14px; font-weight: 500;",
            "background-color: #0a0a0a; border: none; border-radius: 8px; "
            "color: #666; font-size: 14px; font-weight: 500;",
        )
        self._display_stack.addWidget(self.video_label)

        layout.addWidget(self._display_stack)
        self.restart_button = PushButton("重启渲染")
        self.restart_button.setVisible(False)
        self.restart_button.clicked.connect(self.restart_rendering)
        layout.addWidget(self.restart_button)
        self.info_label = CaptionLabel("Frame 0/0 | Crop: (0, 0, 0, 0)")
        setCustomStyleSheet(
            self.info_label,
            "color: #999; padding: 4px 10px; background-color: transparent; border: none;",
            "color: #777; padding: 4px 10px; background-color: transparent; border: none;",
        )
        layout.addWidget(self.info_label)

        # Zoom controls
        zoom_container = QWidget()
        zoom_layout = QHBoxLayout(zoom_container)
        zoom_layout.setContentsMargins(10, 0, 10, 0)
        zoom_layout.setSpacing(10)

        # 对数刻度:slider 0..200 → 10^(v/100) → 1.0x..100.0x(100%..10000%)
        self.zoom_slider = Slider(Qt.Orientation.Horizontal)
        self.zoom_slider.setMinimum(-200)
        self.zoom_slider.setMaximum(200)
        self.zoom_slider.setValue(0)
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider_changed)
        zoom_layout.addWidget(CaptionLabel("缩放:"))
        zoom_layout.addWidget(self.zoom_slider, 1)

        self.zoom_label = CaptionLabel("100%")
        self.zoom_label.setMinimumWidth(60)
        zoom_layout.addWidget(self.zoom_label)

        for percent in (1, 100, 10000):
            btn = PushButton(f"{percent}%")
            btn.setMaximumWidth(70)
            btn.clicked.connect(lambda checked, p=percent: self._set_zoom_percent(p))
            zoom_layout.addWidget(btn)

        layout.addWidget(zoom_container)

    def _teardown_media(self, sync_shutdown: bool = False):
        """取消当前 epoch；close 时再终止该 widget 独占的 worker。"""
        self._job_debounce.stop()
        self._resolve_all_pending_captures()
        self._dismiss_timeout_dialogs()
        self._request_epochs.clear()
        self._latest_display_request_id = None
        old_session = self._render_session
        client = self._worker_client
        if old_session is not None:
            self._retire_render_session(old_session)
        self._worker_ready_for_frames = False
        self._render_session = None
        self._session_metadata = None
        self._metadata_resolved = False
        self._selection = None
        self._load_request_id = None
        self._vs_active = False
        self._has_video = False
        if sync_shutdown and client is not None:
            client.close()
            self._worker_client = None
            self._worker_started = False
            self._cleanup_job_files()
            if self._owned_cache_dir is not None:
                shutil.rmtree(self._owned_cache_dir, ignore_errors=True)
                self._owned_cache_dir = None

    # ------------------------------------------------------------------
    # 独立 VapourSynth worker 预览
    # ------------------------------------------------------------------

    def _is_image_source(self) -> bool:
        ext = os.path.splitext(self.video_path)[1].lower()
        return ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp")

    def set_render_context(self, context: PreviewRenderContext | None) -> None:
        if context is not None and not isinstance(context, PreviewRenderContext):
            raise TypeError("context 必须是 PreviewRenderContext 或 None")
        self._render_context = context
        if context is not None:
            self._execution_blocked_reason = ""

    def set_execution_blocked(self, reason: str) -> None:
        """在 trust 或脚本解析失败时阻止任何新的 worker load。"""
        if not isinstance(reason, str) or not reason:
            raise ValueError("执行阻断原因不能为空")
        self._execution_blocked_reason = reason
        self._render_context = None
        self._teardown_media()
        self.video_label.setText(reason)

    def supports_editor_capability(self, capability: str) -> bool:
        """只接受当前 header 明确声明的编辑能力；raw 永远不接受。"""
        context = self._render_context
        return bool(
            context is not None
            and context.selection.mode == "compatible"
            and capability in context.header.capabilities
        )

    def current_render_session(self) -> RenderSession | None:
        return self._render_session

    def _effective_context(self) -> PreviewRenderContext:
        if self._execution_blocked_reason:
            raise RuntimeError(self._execution_blocked_reason)
        if self._render_context is not None:
            return self._render_context
        root = Path(self.video_path).resolve().parent
        if self._owned_cache_dir is None:
            self._owned_cache_dir = Path(
                tempfile.mkdtemp(prefix="assetmaker-vs-preview-")
            ).resolve()
        app_dir = str(Path(get_app_dir()).resolve())
        return PreviewRenderContext(
            project_root=str(root),
            track="loop",
            selection=_default_selection_for_app(app_dir),
            cache_dir=str(self._owned_cache_dir),
        )

    def _ensure_worker(self) -> VSWorkerClient:
        if self._worker_client is None:
            client = self._worker_client_factory(self)
            self._worker_client = client
            client.ready.connect(self._on_worker_ready)
            client.metadata_ready.connect(self._on_worker_metadata)
            client.frame_ready.connect(self._on_worker_frame)
            frame_discarded = getattr(client, "frame_discarded", None)
            if frame_discarded is not None:
                frame_discarded.connect(self._on_worker_frame_discarded)
            frame_submitted = getattr(client, "frame_submitted", None)
            if frame_submitted is not None:
                frame_submitted.connect(self._on_worker_frame_submitted)
            client.request_failed.connect(self._on_worker_request_failed)
            client.request_timed_out.connect(self._on_worker_timeout)
            operation_completed = getattr(client, "operation_completed", None)
            if operation_completed is not None:
                operation_completed.connect(self._on_worker_operation_completed)
            client.worker_crashed.connect(self._on_worker_crashed)
            worker_stopped = getattr(client, "worker_stopped", None)
            if worker_stopped is not None:
                worker_stopped.connect(self._on_worker_stopped)
            client.log_received.connect(self._on_worker_log)
        if not self._worker_started:
            self._worker_client.start()
            self._worker_started = True
        return self._worker_client

    def _next_epoch(self) -> int:
        self._load_epoch += 1
        return self._load_epoch

    def _make_render_job(self, *, bootstrap: bool) -> RenderJob:
        context = self._effective_context()
        epoch = self._next_epoch()
        is_image = self._is_image_source()
        trim_enabled = self.supports_editor_capability("trim")
        if bootstrap and not is_image:
            timeline = TimelineSpec(start_frame=0, end_frame=None, fps=None)
        else:
            if self._fps_rational is None:
                raise RuntimeError("视频元数据尚未解析，无法生成 resolved job")
            if trim_enabled:
                end = self._timeline_end_exclusive
                if end is None:
                    end = max(1, self.total_frames)
                start = max(0, min(self._timeline_start, end - 1))
            else:
                start = 0
                end = max(1, self.total_frames)
            timeline = TimelineSpec(start_frame=start, end_frame=end, fps=self._fps_rational)
        crop_enabled = self.supports_editor_capability("crop")
        crop = CropSpec(
            coordinate_space="post_rotation_source_pixels",
            x=int(self.cropbox[0]) if crop_enabled else 0,
            y=int(self.cropbox[1]) if crop_enabled else 0,
            width=int(self.cropbox[2]) if crop_enabled else 0,
            height=int(self.cropbox[3]) if crop_enabled else 0,
        )
        virtual_count = max(1, self.total_frames) if is_image else None
        job = RenderJob(
            api_version=1,
            epoch=epoch,
            track=context.track,
            project_root=context.project_root,
            source=SourceSpec(
                path=str(Path(self.video_path).resolve()),
                kind="image" if is_image else "video",
                virtual_frame_count=virtual_count,
            ),
            timeline=timeline,
            transform=TransformSpec(
                rotation=(
                    self._rotation
                    if self.supports_editor_capability("rotation")
                    else 0
                ),
                crop=crop,
            ),
            output=OutputSpec.from_profile(
                f"{self.target_width}x{self.target_height}"
            ),
            paths=PathSpec(cache_dir=context.cache_dir),
        )
        return job

    def _load_render_job(self, *, bootstrap: bool) -> RenderSession:
        retired_session = self._render_session
        if retired_session is not None:
            # timeout 属于旧 session 的 frame 请求。换代后它不再有资格
            # continue/restart 当前 session；只关闭同 epoch 的框，绝不影响
            # 随后为新 epoch 创建的合法 timeout dialog。
            self._dismiss_timeout_dialogs(epoch=retired_session.epoch)
            self._retire_render_session(retired_session)
        context = self._effective_context()
        job = self._make_render_job(bootstrap=bootstrap)
        job_path = write_render_job(job)
        self._job_paths.add(job_path)
        session = RenderSession(
            epoch=job.epoch,
            track=context.track,
            selection=context.selection,
            job_path=str(job_path),
            job_sha256=compute_job_sha256(job_path),
            runtime_fingerprint=_runtime_fingerprint_for_app(
                str(Path(get_app_dir()).resolve())
            ),
        )
        self._selection = context.selection
        self._render_session = session
        self._worker_ready_for_frames = False
        client = self._ensure_worker()
        request_id = client.load(session)
        self._load_request_id = request_id
        self._request_epochs[request_id] = _RequestOwner(session.epoch, "load")
        return session

    def _viewport_size(self) -> Tuple[int, int]:
        """Visible preview area in device pixels (what zoom renders into)."""
        size = self.video_label.size()
        dpr = self.video_label.devicePixelRatioF()
        return (max(2, round(size.width() * dpr)),
                max(2, round(size.height() * dpr)))

    def _schedule_render_job(self) -> None:
        if not self._metadata_resolved or not self.video_path:
            return
        self._job_dirty = True
        if self._crop_edit_start is not None:
            # 手势中的 draft 不能让 100ms debounce 抢先创建新 session；已有
            # trim/rotation 等 pending 状态仍由 _job_dirty 保留到提交后。
            return
        self._job_debounce.start(100)

    def _flush_debounced_render_job(self) -> None:
        try:
            self.flush_render_job()
        except Exception as exc:
            self._fail_current_load(f"无法更新 VapourSynth 预览作业：{exc}")

    def flush_render_job(self) -> RenderSession:
        if self._crop_edit_start is not None:
            # 调用方若没有先通过 ensure_render_state_committed 收口，绝不能
            # 把中间 draft 写成 worker job。正常导出/保存边界会先显式提交。
            return self._render_session
        if self._execution_blocked_reason:
            raise RuntimeError(self._execution_blocked_reason)
        if not self._metadata_resolved or self._fps_rational is None:
            raise RuntimeError("视频元数据尚未解析")
        self._job_debounce.stop()
        self._job_dirty = False
        return self._load_render_job(bootstrap=False)

    def set_timeline_range(self, start_frame: int, end_exclusive: int) -> bool:
        if not self.supports_editor_capability("trim"):
            return False
        self.ensure_render_state_committed()
        if type(start_frame) is not int or type(end_exclusive) is not int:
            raise TypeError("timeline 边界必须是整数")
        if start_frame < 0 or end_exclusive <= start_frame:
            raise ValueError("timeline 必须满足 0 <= start < end_exclusive")
        if self.total_frames > 0:
            end_exclusive = min(end_exclusive, self.total_frames)
            start_frame = min(start_frame, end_exclusive - 1)
        changed = (
            start_frame != self._timeline_start
            or end_exclusive != self._timeline_end_exclusive
        )
        self._timeline_start = start_frame
        self._timeline_end_exclusive = end_exclusive
        if changed:
            self._schedule_render_job()
        return True

    def _frame_request_target(self) -> tuple[str, int]:
        if self._selection is None or self._session_metadata is None:
            raise RuntimeError("预览 session 尚未加载")
        if self._selection.mode == "raw":
            return (
                "final",
                min(max(0, self.current_frame_index), self._output0_frames - 1),
            )
        end = self._timeline_end_exclusive or self.total_frames
        source_index = min(
            max(self._timeline_start, self.current_frame_index), end - 1
        )
        if self._preview_mode or self._session_metadata.editor is None:
            return "final", source_index - self._timeline_start
        return "editor", source_index

    def _request_current_frame(self, *, coalesce: bool = False) -> None:
        if (
            not self._worker_ready_for_frames
            or self._worker_client is None
            or self._render_session is None
        ):
            return
        surface, index = self._frame_request_target()
        request_id = self._worker_client.request_frame(
            epoch=self._render_session.epoch,
            index=index,
            surface=surface,
            viewport=self._viewport_size(),
            zoom_factor=self._zoom_factor,
            pan=self._zoom_pan,
            coalesce=coalesce,
        )
        if request_id is not None:
            self._register_display_request(
                request_id,
                self._render_session.epoch,
                surface,
                index,
            )

    def _register_display_request(
        self, request_id: int, epoch: int, surface: str, index: int
    ) -> None:
        """登记最新显示请求；只允许它覆盖当前图像。"""

        self._request_epochs[request_id] = _RequestOwner(
            epoch, "frame", surface, index
        )
        self._latest_display_request_id = request_id

    def _on_worker_frame_submitted(
        self, request_id: int, epoch: int, surface: str, index: int
    ) -> None:
        """补登记 transport 延后提交的 coalesced frame。"""

        session = self._render_session
        if (
            session is None
            or epoch != session.epoch
            or surface not in {"editor", "final"}
            or type(request_id) is not int
            or type(index) is not int
        ):
            return
        self._register_display_request(request_id, epoch, surface, index)

    def _on_worker_ready(self) -> None:
        if not self._restart_pending or self._render_session is None:
            return
        self._restart_pending = False
        request_id = self._worker_client.load(self._render_session)
        self._load_request_id = request_id
        self._request_epochs[request_id] = _RequestOwner(
            self._render_session.epoch, "load"
        )

    def _on_worker_metadata(
        self, request_id: int, epoch: int, metadata: SessionMetadata
    ) -> None:
        # 先按 terminal request_id 收束 owner。它必须发生在 current-epoch
        # guard 前，否则 A metadata 在 B 已 current 时永远无法释放 A owner。
        owner = self._request_epochs.pop(request_id, None)
        if owner is None or owner.kind != "load" or owner.epoch != epoch:
            return
        session = self._render_session
        if session is None or epoch != session.epoch:
            return
        if self._load_request_id == request_id:
            self._load_request_id = None
        if metadata.mode != self._selection.mode:
            self._fail_current_load("worker 返回的脚本模式与冻结选择不一致")
            return
        if (
            self._render_context is not None
            and self._render_context.header.editor_output == 1
            and metadata.editor is None
        ):
            self._fail_current_load("脚本声明 output 1，但 worker 未返回 editor metadata")
            return
        source_meta = (
            metadata.editor
            if metadata.mode == "compatible" and metadata.editor is not None
            else metadata.output0
        )
        first_metadata = not self._metadata_resolved
        self._session_metadata = metadata
        self._output0_frames = metadata.output0.num_frames
        self._worker_ready_for_frames = True
        self._vs_active = True
        self.restart_button.setVisible(False)
        if first_metadata:
            self._fps_rational = RationalFPS(source_meta.fps_num, source_meta.fps_den)
            self.video_fps = source_meta.fps_num / source_meta.fps_den
            self.video_width = source_meta.width
            self.video_height = source_meta.height
            self.total_frames = source_meta.num_frames
            self.current_frame_index = 0
            self._timeline_start = 0
            self._timeline_end_exclusive = self.total_frames
            self.current_frame = None
            self._has_video = True
            self._metadata_resolved = True
            self._init_cropbox()
            self.video_loaded.emit(self.total_frames, self.video_fps)
        self._request_current_frame()

    def _on_worker_frame(
        self, request_id: int, epoch: int, surface: str, index: int, array
    ) -> None:
        session = self._render_session
        owner = self._request_epochs.pop(request_id, None)
        if (
            session is None
            or owner is None
            or owner.epoch != epoch
            or epoch != session.epoch
            or owner.surface != surface
            or owner.index != index
        ):
            return
        if array is None:
            if owner.kind == "capture":
                self._resolve_capture(request_id, None)
            if request_id == self._latest_display_request_id:
                self._latest_display_request_id = None
            return
        if owner.kind == "capture":
            self._resolve_capture(request_id, np.array(array, copy=True))
            return
        if owner.kind != "frame" or request_id != self._latest_display_request_id:
            return
        self._latest_display_request_id = None
        owned = np.array(array, copy=True)
        self.current_frame = owned
        self._display_frame(owned)

    def _on_worker_frame_discarded(
        self, request_id: int, epoch: int, surface: str, index: int
    ) -> None:
        owner = self._request_epochs.pop(request_id, None)
        if owner is None or (
            owner.epoch != epoch
            or owner.surface != surface
            or owner.index != index
        ):
            return
        if request_id == self._latest_display_request_id:
            self._latest_display_request_id = None
        if owner.kind == "capture":
            self._resolve_capture(request_id, None)

    def _resolve_capture(self, request_id: int, frame: np.ndarray | None) -> None:
        callback = self._pending_captures.pop(request_id, None)
        if callback is None:
            return
        try:
            callback(None if frame is None else np.array(frame, copy=True))
        except Exception:
            logger.exception("截帧回调失败")

    def _resolve_all_pending_captures(self) -> None:
        callbacks = tuple(self._pending_captures.values())
        self._pending_captures.clear()
        for callback in callbacks:
            try:
                callback(None)
            except Exception:
                logger.exception("截帧回调失败")

    def _on_worker_request_failed(
        self, request_id: int, code: str, message: str
    ) -> None:
        owner = self._request_epochs.pop(request_id, None)
        if owner is not None and owner.kind == "capture":
            self._resolve_capture(request_id, None)
        if request_id == self._latest_display_request_id:
            self._latest_display_request_id = None
        if owner is not None:
            session = self._render_session
            if session is None or owner.epoch != session.epoch:
                return
        elif request_id != 0:
            return
        detail = message or code or "VapourSynth worker 请求失败"
        self._retire_job_paths(self._retire_after_unload.pop(request_id, set()))
        if owner is not None and owner.kind == "load":
            self._fail_current_load(detail)
        elif request_id == 0 and code in {
            "worker.restart_failed",
            "worker.staging_cleanup_failed",
        }:
            # These transport-level terminals have no media request owner.  They
            # still mean the worker can no longer serve the current graph; merely
            # logging them strands the UI at "正在重启" forever.
            self._show_worker_unavailable(detail)
        else:
            logger.warning("VapourSynth worker 请求失败 [%s]: %s", code, detail)

    def _on_worker_operation_completed(self, request_id: int, operation: str) -> None:
        if operation == "unload":
            self._retire_job_paths(self._retire_after_unload.pop(request_id, set()))

    def _retire_render_session(self, session: RenderSession) -> None:
        """让旧 session 先排队 cancel/unload，再异步等待 unload terminal。"""

        try:
            retired_paths = {Path(session.job_path)}
            epoch = session.epoch
        except AttributeError:
            # 测试替身或半初始化 session 不会被 worker 使用，无须异步退休。
            return
        self._retiring_job_paths.update(retired_paths)
        client = self._worker_client
        if client is None or not self._worker_started:
            self._retire_job_paths(retired_paths)
            return
        try:
            client.cancel_epoch(epoch)
        except Exception:
            logger.debug("取消旧预览 epoch 失败", exc_info=True)
        # 两个请求必须在新 load 之前按顺序进入 worker 管道。cancel 的 ACK
        # 同时保证此前的帧 terminal 不会再写入；unload 的 terminal 才允许
        # 删除被 worker 读取过的 job 文件。
        self._begin_unload_retirement(retired_paths)

    def _begin_unload_retirement(self, paths: set[Path]) -> None:
        if not paths:
            return
        client = self._worker_client
        if client is None or not self._worker_started:
            return
        try:
            unload_id = client.unload()
        except Exception:
            logger.debug("卸载退休预览 session 失败", exc_info=True)
            return
        self._retire_after_unload[unload_id] = paths

    def _retire_job_paths(self, paths: set[Path]) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.debug("无法删除退休预览 job: %s", path)
                continue
            self._job_paths.discard(path)
            self._retiring_job_paths.discard(path)

    def _on_worker_stopped(self) -> None:
        """worker 已停止时，所有退休 job 都已不可能再被其读取。"""

        self._retire_job_paths(set(self._retiring_job_paths))
        self._retire_after_unload.clear()
        self._resolve_all_pending_captures()
        self._dismiss_timeout_dialogs()
        self._request_epochs.clear()
        self._latest_display_request_id = None

    def _dismiss_timeout_dialogs(self, *, epoch: int | None = None) -> None:
        dialogs: list[QMessageBox] = []
        for request_id, box in tuple(self._timeout_dialogs.items()):
            owner = self._request_epochs.get(request_id)
            if epoch is not None and (owner is None or owner.epoch != epoch):
                continue
            self._timeout_dialogs.pop(request_id, None)
            dialogs.append(box)
        for box in dialogs:
            box.close()
            box.deleteLater()

    def _on_worker_timeout(self, request_id: int, epoch: int) -> None:
        session = self._render_session
        owner = self._request_epochs.get(request_id)
        if (
            session is None
            or owner is None
            or owner.kind != "frame"
            or owner.epoch != epoch
            or epoch != session.epoch
        ):
            return
        box = QMessageBox(self)
        box.setWindowTitle("渲染超时")
        box.setText("VapourSynth 渲染帧超时。")
        keep_waiting = box.addButton("继续等待", QMessageBox.ButtonRole.AcceptRole)
        restart = box.addButton("终止并重启", QMessageBox.ButtonRole.DestructiveRole)

        def _finished(_result: int) -> None:
            if self._timeout_dialogs.get(request_id) is not box:
                box.deleteLater()
                return
            self._timeout_dialogs.pop(request_id, None)
            active_owner = self._request_epochs.get(request_id)
            current_session = self._render_session
            if (
                active_owner != owner
                or self._worker_client is not client
                or current_session is None
                or current_session.epoch != owner.epoch
            ):
                box.deleteLater()
                return
            if box.clickedButton() is keep_waiting:
                client.continue_wait(request_id)
            elif box.clickedButton() is restart:
                self.restart_rendering()
            box.deleteLater()

        box.finished.connect(_finished)
        self._timeout_dialogs[request_id] = box
        client = self._worker_client
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.setModal(False)
        box.show()

    def _on_worker_crashed(self, message: str) -> None:
        # 旧测试替身没有 worker_stopped；crash 本身同样是安全的退休边界。
        self._on_worker_stopped()
        self._show_worker_unavailable(message)

    def _show_worker_unavailable(self, message: str) -> None:
        """Put a transport-level failure into a recoverable preview state."""
        self.pause()
        self._resolve_all_pending_captures()
        self._dismiss_timeout_dialogs()
        self._request_epochs.clear()
        self._latest_display_request_id = None
        self._restart_pending = False
        self._worker_ready_for_frames = False
        self._vs_active = False
        self.current_frame = None
        self.video_label.clear()
        self.video_label.setText("渲染进程已退出")
        self.restart_button.setVisible(self._render_session is not None)
        logger.error("VapourSynth worker 不可用: %s", message)

    @staticmethod
    def _on_worker_log(level: str, message: str) -> None:
        getattr(logger, level if hasattr(logger, level) else "debug")(
            "VS worker: %s", message
        )

    def restart_rendering(self) -> None:
        if self._worker_client is None or self._render_session is None:
            return
        self._worker_ready_for_frames = False
        self.current_frame = None
        self.video_label.clear()
        self.video_label.setText("正在重启渲染进程…")
        self._restart_pending = True
        self._worker_client.terminate_and_restart()

    def _fail_current_load(self, message: str) -> None:
        self._worker_ready_for_frames = False
        self._vs_active = False
        self._has_video = False
        self.current_frame = None
        self.video_label.clear()
        self.video_label.setText("无法加载视频元数据")
        self.load_failed.emit(message)

    def _cleanup_job_files(self) -> None:
        for path in tuple(self._job_paths):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.debug("无法删除退休预览 job: %s", path)
            self._job_paths.discard(path)

    def set_target_resolution(self, width: int, height: int):
        self.ensure_render_state_committed()
        if self.target_width == width and self.target_height == height:
            return
        self.target_width = width
        self.target_height = height
        self.target_aspect_ratio = width / height
        if self.video_width > 0 and self.video_height > 0:
            self._init_cropbox()
            self._refresh_display()
        else:
            # No media yet: re-fit the standing box to the NEW ratio anyway.
            # Previously this branch did nothing, so the aspect ratio changed
            # while the box kept the OLD ratio — and _apply_project_config
            # calls clear() (zeroing video_width) BEFORE set_target_resolution,
            # so this was the common path on every project open.
            self.cropbox = self._fit_cropbox_to_ratio(*self.cropbox) if (
                self.video_width > 0
            ) else [0, 0, width, height]
        self._schedule_render_job()

    def load_video(self, path: str) -> bool:
        """写 bootstrap job 并异步交给独立 worker；本方法不加载 VS。"""
        logger.info("Loading video: %s", path)
        if self._execution_blocked_reason:
            self.video_label.setText(self._execution_blocked_reason)
            return False
        if not os.path.exists(path):
            self.video_label.setText(f"File not found: {path}")
            return False
        self.ensure_render_state_committed()
        self._teardown_media()
        self.pause()
        self.video_path = str(Path(path).resolve())
        self._loop_frame = None
        self._has_video = False
        self._metadata_resolved = False
        self._fps_rational = None
        self._timeline_start = 0
        self._timeline_end_exclusive = None
        self.current_frame = None
        self._display_stack.setCurrentIndex(0)
        self.video_label.setText("正在加载视频元数据…")
        try:
            self._load_render_job(bootstrap=True)
        except Exception as exc:
            self._fail_current_load(str(exc))
            return False
        return True

    def load_static_image_from_file(self, image_path: str) -> bool:
        if not HAS_CV2:
            logger.error("OpenCV is required to load images")
            return False
        if not os.path.exists(image_path):
            logger.error("Image file does not exist: %s", image_path)
            return False
        with open(image_path, "rb") as fh:
            data = np.frombuffer(fh.read(), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None:
            logger.error("Unable to read image: %s", image_path)
            return False
        if len(img.shape) == 3 and img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        return self._load_static_frame(img)

    def load_static_image_from_array(self, frame: np.ndarray) -> bool:
        if frame is None:
            return False
        return self._load_static_frame(frame.copy())

    def update_static_frame(self, frame: np.ndarray) -> bool:
        if frame is None:
            return False
        self.current_frame = frame.copy()
        self.video_width = frame.shape[1]
        self.video_height = frame.shape[0]
        self._bound_cropbox()
        self._display_frame(self.current_frame)
        return True

    def load_image_as_loop(
        self, path: str, fps: float = 30.0, duration: float = 5.0
    ) -> bool:
        if self._execution_blocked_reason:
            self.video_label.setText(self._execution_blocked_reason)
            return False
        if not os.path.exists(path) or not HAS_CV2:
            return False
        data = np.fromfile(path, dtype=np.uint8)
        image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if image is None:
            return False
        self.ensure_render_state_committed()
        self._teardown_media()
        self.pause()
        self.video_path = str(Path(path).resolve())
        self.video_width = image.shape[1]
        self.video_height = image.shape[0]
        fraction = Fraction(str(fps)).limit_denominator(1_000_000)
        self._fps_rational = RationalFPS(fraction.numerator, fraction.denominator)
        self.video_fps = self._fps_rational.numerator / self._fps_rational.denominator
        self.total_frames = max(1, round(self.video_fps * duration))
        self._timeline_start = 0
        self._timeline_end_exclusive = self.total_frames
        self._metadata_resolved = False
        self._loop_frame = None
        self.current_frame = None
        self.current_frame_index = 0
        self._init_cropbox()
        self.video_label.setText("正在加载图片渲染作业…")
        try:
            self._load_render_job(bootstrap=False)
        except Exception as exc:
            self._fail_current_load(str(exc))
            return False
        return True

    def _load_static_frame(self, frame: np.ndarray) -> bool:
        self.ensure_render_state_committed()
        self.pause()
        self._teardown_media()
        self._load_epoch += 1
        self._loop_frame = None
        self._display_stack.setCurrentIndex(0)
        self.video_width = frame.shape[1]
        self.video_height = frame.shape[0]
        self.current_frame = frame
        self.total_frames = 1
        self.current_frame_index = 0
        self._has_video = False
        self._init_cropbox()
        self._display_frame(frame)
        return True

    def _fit_cropbox_to_ratio(self, x, y, w, h) -> list:
        """Regularize any box to the TARGET aspect ratio inside the frame.

        Single ratio guard for every crop-box write. The export resizes the
        cropped region straight to the target with `core.resize.<kernel>(clip,
        width=..., height=...)` — the VS R73 API has no aspect-preserving
        parameter (stub `resize.Bicubic` takes independent width/height; no
        keep_aspect/pad), so ANY box whose ratio != target is scaled
        anisotropically, i.e. geometrically distorted, in the exported mp4.

        The old `_bound_cropbox` clamped w and h INDEPENDENTLY against the
        frame edges, which silently destroyed the ratio the drag handles had
        just enforced (measured: a landscape source drifted 7.8% -> 40.7% off
        target in a single drag, yielding a 1.41x anisotropic stretch). Here
        both axes are derived from ONE scale factor, so the ratio survives:
        the box shrinks proportionally instead of collapsing on one axis.

        Keeps the box centred on its original centre, as large as fits, and
        even-sized-safe (>= 2px) for the YUV420 crop downstream.
        """
        rotated_w, rotated_h = self._get_rotated_video_size()
        ar = self.target_aspect_ratio if self.target_aspect_ratio > 0 else 1.0
        if rotated_w <= 0 or rotated_h <= 0:
            return [int(x), int(y), int(w), int(h)]

        # Largest target-ratio box that fits the frame.
        if rotated_w / rotated_h > ar:
            max_h = float(rotated_h)
            max_w = max_h * ar
        else:
            max_w = float(rotated_w)
            max_h = max_w / ar

        # A box must be big enough for the ratio to be expressible in integers:
        # at w=2 a 0.5625 target can only round to 2/4 = 0.5 (11% off), and at
        # w=11 a 0.6667 target rounds to 11/16 = 0.6875 (3.1% off). Require at
        # least MIN_CROP_SIDE px on BOTH axes so the rounding error stays well
        # under 1%, while never exceeding what the frame can hold.
        min_w = float(_MIN_CROP_SIDE if ar >= 1.0 else _MIN_CROP_SIDE * ar)
        min_w = max(min_w, float(_MIN_CROP_SIDE) * ar)
        min_w = min(max(2.0, min_w), max_w)

        # Requested size, ratio-locked: honour the larger requested axis but
        # never exceed the fitting box (one shared scale -> ratio preserved).
        req_w = max(2.0, float(w))
        req_h = max(2.0, float(h))
        want_w = max(min_w, min(max_w, max(req_w, req_h * ar)))
        new_w = max(2, int(round(want_w)))
        new_h = max(2, int(round(new_w / ar)))
        # Rounding can push one axis 1px past the frame; step down together
        # while the box is still large enough to express the ratio.
        while (new_w > rotated_w or new_h > rotated_h) and new_w > int(min_w):
            new_w -= 1
            new_h = max(2, int(round(new_w / ar)))
        new_w = min(new_w, rotated_w)
        new_h = min(new_h, rotated_h)

        # Preserve the requested centre, then clamp translation only.
        cx = float(x) + float(w) / 2.0
        cy = float(y) + float(h) / 2.0
        new_x = int(round(cx - new_w / 2.0))
        new_y = int(round(cy - new_h / 2.0))
        new_x = max(0, min(new_x, max(0, rotated_w - new_w)))
        new_y = max(0, min(new_y, max(0, rotated_h - new_h)))
        return [new_x, new_y, new_w, new_h]

    def _init_cropbox(self):
        rotated_w, rotated_h = self._get_rotated_video_size()
        if rotated_w <= 0 or rotated_h <= 0:
            self.cropbox = [0, 0, self.target_width, self.target_height]
            return
        # 75% of the largest fitting target-ratio box, centred. Sizing/centring
        # and the ratio itself are delegated to the single guard so the initial
        # box can no longer differ from every other write path.
        if rotated_w / rotated_h > self.target_aspect_ratio:
            max_w = rotated_h * self.target_aspect_ratio
        else:
            max_w = float(rotated_w)
        want_w = max(2.0, max_w * 0.75)
        self.cropbox = self._fit_cropbox_to_ratio(
            (rotated_w - want_w) / 2.0,
            (rotated_h - want_w / self.target_aspect_ratio) / 2.0,
            want_w,
            want_w / self.target_aspect_ratio,
        )
        self._emit_cropbox_changed()

    def _bound_cropbox(self):
        """Clamp + re-lock the crop box (delegates to the single ratio guard)."""
        rotated_w, rotated_h = self._get_rotated_video_size()
        if rotated_w <= 0 or rotated_h <= 0:
            return
        self.cropbox = self._fit_cropbox_to_ratio(*self.cropbox)

    def _display_cropbox(self) -> list[int]:
        return self._draft_cropbox if self._draft_cropbox is not None else self.cropbox

    def _emit_cropbox_changed(self):
        """发出一次正式裁剪提交，并把它加入渲染 debounce。"""
        x, y, w, h = self._display_cropbox()
        self.cropbox_changed.emit(x, y, w, h)
        self._update_info_label()
        self._schedule_render_job()

    def _emit_draft_cropbox_changed(self) -> None:
        """仅更新实时覆盖层，绝不写 config/undo 或 worker job。"""
        x, y, w, h = self._display_cropbox()
        self.draft_cropbox_changed.emit(x, y, w, h)
        self._update_info_label()
        self.video_label.update()

    def _update_info_label(self):
        x, y, w, h = self._display_cropbox()
        rotation = f" | Rotation: {self._rotation}" if self._rotation else ""
        self.info_label.setText(
            f"Frame {self.current_frame_index}/{self.total_frames} | "
            f"Crop: ({x}, {y}, {w}, {h}){rotation}"
        )

    def _display_frame(self, frame: np.ndarray):
        if frame is None:
            return
        display_frame = self._make_display_frame(frame)
        rgb = self._to_rgb(display_frame)
        rgb = np.ascontiguousarray(rgb)
        h, w = rgb.shape[:2]
        qimage = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888)
        # Build the pixmap at the device pixel ratio so the preview is sharp on HiDPI
        # displays (scaling only to the logical size renders at half resolution).
        dpr = self.video_label.devicePixelRatioF()
        logical = self.video_label.size()
        physical = QSize(round(logical.width() * dpr), round(logical.height() * dpr))
        pixmap = QPixmap.fromImage(qimage.copy()).scaled(
            physical,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pixmap.setDevicePixelRatio(dpr)
        self.video_label.setPixmap(pixmap)
        self._update_display_geometry(self.video_label, w, h)
        self.frame_changed.emit(self.current_frame_index)
        self._update_info_label()

    def _make_display_frame(self, frame: np.ndarray) -> np.ndarray:
        if self._vs_active:
            # The VapourSynth graph already applied rotation, and in preview mode
            # the crop/resize/padding too — re-doing either here (with cv2's own
            # resampler) is what used to make 预览 differ from 导出.
            return frame
        rotated = self._apply_rotation(frame)
        if not self._preview_mode:
            return rotated
        x, y, w, h = self.cropbox
        y2 = min(rotated.shape[0], y + h)
        x2 = min(rotated.shape[1], x + w)
        cropped = rotated[max(0, y):y2, max(0, x):x2]
        if cropped.size == 0:
            return rotated
        return cv2.resize(cropped, (self.target_width, self.target_height))

    def _to_rgb(self, frame: np.ndarray) -> np.ndarray:
        if len(frame.shape) == 2:
            return cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
        if frame.shape[2] == 4:
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def _refresh_display(self):
        if self._vs_active and self.video_path:
            self._request_current_frame(coalesce=True)
            self._update_info_label()
            return
        if self.current_frame is not None:
            self._display_frame(self.current_frame)
        self._update_info_label()

    def _update_display_geometry(self, widget: QWidget, media_w: int, media_h: int):
        if media_w <= 0 or media_h <= 0:
            self.display_scale = 1.0
            self.display_offset_x = 0
            self.display_offset_y = 0
            return
        area = widget.size()
        scale = min(area.width() / media_w, area.height() / media_h)
        shown_w = int(media_w * scale)
        shown_h = int(media_h * scale)
        self.display_scale = scale if scale > 0 else 1.0
        self.display_offset_x = (area.width() - shown_w) // 2
        self.display_offset_y = (area.height() - shown_h) // 2

    def _paint_cropbox(self, widget: QWidget):
        if not self._crop_interaction_enabled():
            return
        # Zoomed frames are a magnified VIEWPORT WINDOW of the source, not the
        # whole (scaled) source, so display_scale/offset no longer map source
        # coordinates onto the label. `_crop_interaction_enabled` therefore
        # keeps the box hidden and locked while zoomed, just like mouse/keyboard.
        left, top, right, bottom = self._cropbox_control_rect(widget)
        painter = QPainter(widget)
        pen = QPen(Qt.GlobalColor.cyan, 2)
        painter.setPen(pen)
        painter.drawRect(QRectF(left, top, right - left, bottom - top))
        # 手柄与鼠标命中都使用 QLabel 的逻辑像素；若按源像素缩放，25%
        # 显示下 15px 手柄会退化成不足 4px，HiDPI 下也会与绘制不同步。
        half = self.handle_size / 2.0
        painter.setBrush(Qt.GlobalColor.cyan)
        for corner_x, corner_y in (
            (left, top),
            (right, top),
            (left, bottom),
            (right, bottom),
        ):
            painter.drawRect(
                QRectF(
                    corner_x - half,
                    corner_y - half,
                    self.handle_size,
                    self.handle_size,
                )
            )

    def _on_timer_tick(self):
        if not self.is_playing:
            return
        if not (self._has_video or self._loop_frame is not None):
            return
        numerator = self._fps_rational.numerator if self._fps_rational else 30
        denominator = self._fps_rational.denominator if self._fps_rational else 1
        elapsed_ns = max(0, time.perf_counter_ns() - self._play_origin_ns)
        elapsed_frames = max(
            1,
            (elapsed_ns * numerator) // (1_000_000_000 * denominator),
        )
        low, high = self._playback_bounds()
        span = max(1, high - low)
        self.current_frame_index = low + (
            self._play_origin_frame - low + elapsed_frames
        ) % span
        self._request_current_frame(coalesce=True)
        self.frame_changed.emit(self.current_frame_index)
        self._update_info_label()
        self._schedule_next_playback_tick(elapsed_frames + 1)

    def _playback_bounds(self) -> tuple[int, int]:
        if self._preview_mode and self._selection is not None and self._selection.mode == "compatible":
            return self._timeline_start, self._timeline_end_exclusive or self.total_frames
        return 0, max(1, self.total_frames)

    def _schedule_next_playback_tick(self, sequence: int) -> None:
        if not self.is_playing:
            return
        fps = self._fps_rational or RationalFPS(30, 1)
        target_ns = self._play_origin_ns + (
            sequence * 1_000_000_000 * fps.denominator
        ) // fps.numerator
        remaining_ns = max(0, target_ns - time.perf_counter_ns())
        self.timer.start(max(0, (remaining_ns + 999_999) // 1_000_000))

    def play(self):
        if self.is_playing or not (self._has_video or self._loop_frame is not None):
            return
        self.is_playing = True
        self._play_origin_ns = time.perf_counter_ns()
        self._play_origin_frame = self.current_frame_index
        self._schedule_next_playback_tick(1)
        self.playback_state_changed.emit(True)

    def pause(self):
        self.timer.stop()
        was_playing = self.is_playing
        self.is_playing = False
        if was_playing:
            self.playback_state_changed.emit(False)

    def toggle_play(self):
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def next_frame(self):
        if not (self._has_video or self._loop_frame is not None):
            return
        self.pause()
        self.current_frame_index = min(
            self.current_frame_index + 1, max(0, self.total_frames - 1)
        )
        self._seek_backend_to_current_frame()
        self.frame_changed.emit(self.current_frame_index)
        self._update_info_label()

    def prev_frame(self):
        if not (self._has_video or self._loop_frame is not None):
            return
        self.pause()
        self.current_frame_index = max(self.current_frame_index - 1, 0)
        self._seek_backend_to_current_frame()
        self.frame_changed.emit(self.current_frame_index)
        self._update_info_label()

    def seek_to_frame(self, index: int):
        if not (self._has_video or self._loop_frame is not None):
            return
        self.pause()
        self.current_frame_index = max(0, min(index, max(0, self.total_frames - 1)))
        self._seek_backend_to_current_frame()
        self.frame_changed.emit(self.current_frame_index)
        self._update_info_label()

    def _seek_backend_to_current_frame(self):
        """Seek to ``current_frame_index`` — one frame request.

        VapourSynth is frame-indexed, so a seek needs no fps→seconds→re-quantize
        round trip and carries no ±1 drift on VFR sources, which is what mpv's
        ``round(time_pos * fps)`` could produce. This is why preview indices and
        the export's ``clip[start:end]`` now agree exactly.
        """
        if self._vs_active:
            self._request_current_frame(coalesce=True)

    def capture_frame_async(self, callback):
        """非合并地请求当前 surface/index；绝不返回可能过期的缓存帧。"""
        if not self._vs_active or self._worker_client is None or self._render_session is None:
            callback(None if self.current_frame is None else self.current_frame.copy())
            return
        self.pause()
        surface, index = self._frame_request_target()
        request_id = self._worker_client.request_frame(
            epoch=self._render_session.epoch,
            index=index,
            surface=surface,
            viewport=self._viewport_size(),
            zoom_factor=self._zoom_factor,
            pan=self._zoom_pan,
            coalesce=False,
        )
        if request_id is None:
            callback(None)
            return
        self._request_epochs[request_id] = _RequestOwner(
            self._render_session.epoch, "capture", surface, index
        )
        self._pending_captures[request_id] = callback

    def get_current_frame(self) -> int:
        return self.current_frame_index

    def get_cropbox(self) -> Tuple[int, int, int, int]:
        return tuple(self._display_cropbox())

    def get_cropbox_in_rotated_space(self) -> Tuple[int, int, int, int]:
        return tuple(self._display_cropbox())

    def get_draft_cropbox(self) -> Tuple[int, int, int, int] | None:
        """返回尚未提交的裁剪框，供自动保存快照使用。"""
        if self._draft_cropbox is None:
            return None
        return tuple(self._draft_cropbox)

    def has_active_crop_edit(self) -> bool:
        return self._crop_edit_start is not None

    def begin_crop_edit(self) -> bool:
        """开始一次裁剪手势，冻结正式 cropbox 与现有 job debounce。"""
        if self._crop_edit_start is not None:
            return False
        if not self.supports_editor_capability("crop"):
            return False
        self._crop_edit_start = tuple(self.cropbox)
        self._draft_cropbox = list(self.cropbox)
        self._job_debounce.stop()
        self.crop_edit_started.emit()
        return True

    def _reset_crop_drag_state(self) -> None:
        self.drag_mode = self.DRAG_NONE
        self.drag_start_pos = None
        self.drag_start_cropbox = []

    def _normalise_draft_cropbox(self, cropbox: list[int]) -> list[int]:
        committed = self.cropbox
        try:
            self.cropbox = list(cropbox)
            self._bound_cropbox()
            return list(self.cropbox)
        finally:
            self.cropbox = committed

    def _set_draft_cropbox(self, cropbox: list[int]) -> None:
        if self._crop_edit_start is None:
            raise RuntimeError("未开始裁剪事务")
        draft = self._normalise_draft_cropbox(cropbox)
        if draft == self._draft_cropbox:
            return
        self._draft_cropbox = draft
        self._emit_draft_cropbox_changed()

    def end_crop_edit(self, *, commit: bool = True) -> bool:
        """提交或取消当前手势；重复调用不会制造新的信号或作业。"""
        if self._crop_edit_start is None:
            self._reset_crop_drag_state()
            return False
        start = self._crop_edit_start
        draft = tuple(self._draft_cropbox or self.cropbox)
        self._crop_edit_start = None
        self._draft_cropbox = None
        self._reset_crop_drag_state()
        changed = commit and draft != start
        if changed:
            self.cropbox = list(draft)
            self._emit_cropbox_changed()
            self.crop_edit_committed.emit()
        else:
            self._update_info_label()
            if self._job_dirty:
                self._schedule_render_job()
        self.video_label.update()
        return changed

    def ensure_render_state_committed(self) -> bool:
        """供保存/导出/模式切换等外部边界在消费状态前调用。"""
        return self.end_crop_edit(commit=True)

    def set_cropbox(self, x: int, y: int, w: int, h: int) -> bool:
        if not self.supports_editor_capability("crop"):
            return False
        self.ensure_render_state_committed()
        self.cropbox = [x, y, w, h]
        self._bound_cropbox()
        self._emit_cropbox_changed()
        self._refresh_display()
        return True

    def get_video_info(self) -> Tuple[float, int, int, int]:
        return self.video_fps, self.total_frames, self.video_width, self.video_height

    def set_preview_mode(self, enabled: bool):
        self.ensure_render_state_committed()
        if self._preview_mode == bool(enabled):
            return
        self._preview_mode = bool(enabled)
        if self._vs_active:
            if self._job_dirty:
                self.flush_render_job()
            if self._preview_mode and self._selection is not None and self._selection.mode == "compatible":
                end = self._timeline_end_exclusive or self.total_frames
                clamped = min(max(self._timeline_start, self.current_frame_index), end - 1)
                if clamped != self.current_frame_index:
                    self.current_frame_index = clamped
                    self.frame_changed.emit(clamped)
            # surface 已改变，先清掉旧 editor/final 帧，避免短暂闪回。
            self.current_frame = None
            self.video_label.clear()
            self.video_label.setText("正在渲染…")
            self._request_current_frame()
        else:
            self._refresh_display()

    def is_preview_mode(self) -> bool:
        return self._preview_mode

    def set_rotation(self, degrees: int) -> bool:
        if not self.supports_editor_capability("rotation"):
            return False
        self.ensure_render_state_committed()
        # Snap to a cardinal angle: the VapourSynth export graph only
        # support 0/90/180/270, so keep preview and export in lockstep and never let an
        # arbitrary angle through the UI (timeline SpinBox also steps by 90).
        degrees = (round(int(degrees) / 90) * 90) % 360
        if self._rotation == degrees:
            return True
        has_video = self.video_width > 0 and self.video_height > 0
        # Remap the crop box through the rotation change instead of resetting it
        # to a default centred rectangle (which silently discarded the user's
        # crop on every rotate). Map current rotated-space box -> original
        # coords (using the OLD angle) -> new rotated-space (using the NEW angle).
        original_box = None
        if has_video:
            original_box = self._cropbox_to_original_coords(*self.cropbox)
        self._rotation = degrees
        if has_video:
            if original_box is not None:
                self.cropbox = list(self._original_to_rotated_coords(*original_box))
                self._bound_cropbox()
                self._emit_cropbox_changed()
            else:
                self._init_cropbox()
        self.rotation_changed.emit(degrees)
        self._schedule_render_job()
        self._refresh_display()
        return True

    def get_rotation(self) -> int:
        return self._rotation

    def set_epconfig(self, config: "EPConfig"):
        self._epconfig = config

    def _get_rotated_video_size(self) -> Tuple[int, int]:
        if self._rotation in (90, 270):
            return self.video_height, self.video_width
        if self._rotation in (0, 180):
            return self.video_width, self.video_height
        import math

        rad = math.radians(self._rotation)
        cos_a = abs(math.cos(rad))
        sin_a = abs(math.sin(rad))
        return (
            int(self.video_width * cos_a + self.video_height * sin_a),
            int(self.video_width * sin_a + self.video_height * cos_a),
        )

    def _apply_rotation(self, frame: np.ndarray) -> np.ndarray:
        return self.apply_rotation_to_frame(frame, self._rotation)

    @staticmethod
    def apply_rotation_to_frame(frame: np.ndarray, rotation: int) -> np.ndarray:
        if rotation == 0:
            return frame
        if not HAS_CV2:
            return frame
        rotation = rotation % 360
        if rotation == 90:
            return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        if rotation == 180:
            return cv2.rotate(frame, cv2.ROTATE_180)
        if rotation == 270:
            return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        h, w = frame.shape[:2]
        matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), -rotation, 1.0)
        cos_a = abs(matrix[0, 0])
        sin_a = abs(matrix[0, 1])
        new_w = int(w * cos_a + h * sin_a)
        new_h = int(w * sin_a + h * cos_a)
        matrix[0, 2] += (new_w - w) / 2.0
        matrix[1, 2] += (new_h - h) / 2.0
        return cv2.warpAffine(
            frame,
            matrix,
            (new_w, new_h),
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )

    def _cropbox_to_original_coords(
        self, x: int, y: int, w: int, h: int
    ) -> Tuple[int, int, int, int]:
        """Map a crop box from the CURRENT rotated display space to source coords."""
        if self._rotation == 90:
            return y, self.video_height - x - w, h, w
        if self._rotation == 180:
            return self.video_width - x - w, self.video_height - y - h, w, h
        if self._rotation == 270:
            return self.video_width - y - h, x, h, w
        return x, y, w, h

    def _original_to_rotated_coords(
        self, x: int, y: int, w: int, h: int
    ) -> Tuple[int, int, int, int]:
        """Inverse of _cropbox_to_original_coords for the CURRENT rotation."""
        if self._rotation == 90:
            return self.video_height - h - y, x, h, w
        if self._rotation == 180:
            return self.video_width - x - w, self.video_height - y - h, w, h
        if self._rotation == 270:
            return y, self.video_width - x - w, h, w
        return x, y, w, h

    def _display_to_rotated_coords(self, widget: QWidget, pos: QPoint) -> Tuple[int, int]:
        rotated_w, rotated_h = self._get_rotated_video_size()
        self._update_display_geometry(widget, rotated_w, rotated_h)
        x = int((pos.x() - self.display_offset_x) / max(self.display_scale, 1e-6))
        y = int((pos.y() - self.display_offset_y) / max(self.display_scale, 1e-6))
        return max(0, x), max(0, y)

    def _crop_interaction_enabled(self) -> bool:
        """裁剪框绘制、鼠标和键盘共用同一编辑门控。"""
        return (
            not self._preview_mode
            and self.supports_editor_capability("crop")
            and self._zoom_factor <= 1.0
            and self.video_width > 0
            and self.video_height > 0
        )

    def _cropbox_control_rect(
        self, widget: QWidget
    ) -> tuple[float, float, float, float]:
        """返回当前草稿框在控件逻辑坐标中的四条边。"""
        rotated_w, rotated_h = self._get_rotated_video_size()
        self._update_display_geometry(widget, rotated_w, rotated_h)
        x, y, width, height = self._display_cropbox()
        left = self.display_offset_x + x * self.display_scale
        top = self.display_offset_y + y * self.display_scale
        return (
            left,
            top,
            left + width * self.display_scale,
            top + height * self.display_scale,
        )

    def _get_drag_mode(self, widget: QWidget, pos: QPoint) -> int:
        """只以控件逻辑像素判断手柄，保持与绘制半径完全一致。"""
        left, top, right, bottom = self._cropbox_control_rect(widget)
        half = self.handle_size / 2.0
        if abs(pos.x() - left) <= half and abs(pos.y() - top) <= half:
            return self.DRAG_RESIZE_TL
        if abs(pos.x() - right) <= half and abs(pos.y() - top) <= half:
            return self.DRAG_RESIZE_TR
        if abs(pos.x() - left) <= half and abs(pos.y() - bottom) <= half:
            return self.DRAG_RESIZE_BL
        if abs(pos.x() - right) <= half and abs(pos.y() - bottom) <= half:
            return self.DRAG_RESIZE_BR
        if left <= pos.x() <= right and top <= pos.y() <= bottom:
            return self.DRAG_MOVE
        return self.DRAG_NONE

    def _handle_mouse_press(self, widget: QWidget, event: QMouseEvent):
        if (event.button() != Qt.MouseButton.LeftButton
                or not self._crop_interaction_enabled()):
            # 预览模式下画面是导出结果(已裁剪/缩放/补边),裁剪框不绘制,
            # 此时的拖拽会按导出几何去改框——无反馈且坐标系不对。
            # 放大后画面是源的一个视口窗口,display_scale 不再对应源坐标,
            # 同理拖拽会错位——放大是查看模式,裁剪框锁定(见 _paint_cropbox)。
            return
        self.drag_mode = self._get_drag_mode(widget, event.pos())
        if self.drag_mode != self.DRAG_NONE:
            if not self.begin_crop_edit():
                self._reset_crop_drag_state()
                return
            self.drag_start_pos = event.pos()
            self.drag_start_cropbox = self.cropbox.copy()
            self.setFocus()

    def _handle_mouse_move(self, widget: QWidget, event: QMouseEvent):
        if not self._crop_interaction_enabled():
            widget.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self.drag_mode == self.DRAG_NONE or self.drag_start_pos is None:
            mode = self._get_drag_mode(widget, event.pos())
            cursors = {
                self.DRAG_RESIZE_TL: Qt.CursorShape.SizeFDiagCursor,
                self.DRAG_RESIZE_BR: Qt.CursorShape.SizeFDiagCursor,
                self.DRAG_RESIZE_TR: Qt.CursorShape.SizeBDiagCursor,
                self.DRAG_RESIZE_BL: Qt.CursorShape.SizeBDiagCursor,
                self.DRAG_MOVE: Qt.CursorShape.SizeAllCursor,
            }
            widget.setCursor(cursors.get(mode, Qt.CursorShape.ArrowCursor))
            return

        sx, sy, sw, sh = self.drag_start_cropbox
        if self.drag_mode == self.DRAG_MOVE:
            # 保留既有移动语义：移动框本身仍使用受画面边界截断的坐标。
            crx, cry = self._display_to_rotated_coords(widget, event.pos())
            srx, sry = self._display_to_rotated_coords(widget, self.drag_start_pos)
            dx, dy = crx - srx, cry - sry
            cropbox = [sx + dx, sy + dy, sw, sh]
        elif self.drag_mode in (
            self.DRAG_RESIZE_TL,
            self.DRAG_RESIZE_TR,
            self.DRAG_RESIZE_BL,
            self.DRAG_RESIZE_BR,
        ):
            # 缩放的二维投影必须看见完整鼠标位移；不能复用会把负源坐标
            # 截成 0 的 _display_to_rotated_coords，否则越过左/上边界时会
            # 提前饱和，并把本应为零的投影变成缩放。
            rotated_w, rotated_h = self._get_rotated_video_size()
            self._update_display_geometry(widget, rotated_w, rotated_h)
            scale = max(self.display_scale, 1e-6)
            dx = (event.pos().x() - self.drag_start_pos.x()) / scale
            dy = (event.pos().y() - self.drag_start_pos.y()) / scale
            cropbox = self._resize_cropbox_from_drag(dx, dy)
        else:
            return
        if self._crop_edit_start is None:
            return
        self._set_draft_cropbox(cropbox)

    def _resize_cropbox_from_drag(self, dx: float, dy: float) -> list[int]:
        """按固定比例投影鼠标位移，并固定拖拽角的对角锚点。"""
        sx, sy, sw, sh = self.drag_start_cropbox
        directions = {
            self.DRAG_RESIZE_TL: (-1, -1, sx + sw, sy + sh),
            self.DRAG_RESIZE_TR: (1, -1, sx, sy + sh),
            self.DRAG_RESIZE_BL: (-1, 1, sx + sw, sy),
            self.DRAG_RESIZE_BR: (1, 1, sx, sy),
        }
        direction_x, direction_y, anchor_x, anchor_y = directions[self.drag_mode]
        outward_x = dx * direction_x
        outward_y = dy * direction_y
        ratio = self.target_aspect_ratio
        height_delta = (
            ratio * outward_x + outward_y
        ) / (ratio * ratio + 1.0)

        rotated_w, rotated_h = self._get_rotated_video_size()
        available_w = anchor_x if direction_x < 0 else rotated_w - anchor_x
        available_h = anchor_y if direction_y < 0 else rotated_h - anchor_y
        max_height = min(float(available_h), float(available_w) / ratio)

        # 与 _fit_cropbox_to_ratio 的最小表示精度一致：360:640 时为约 36x64。
        min_width = max(2.0, _MIN_CROP_SIDE * ratio)
        min_width = min(min_width, float(available_w), max_height * ratio)
        min_height = min(max_height, min_width / ratio)
        wanted_height = min(max_height, max(min_height, sh + height_delta))

        new_width = max(2, int(round(wanted_height * ratio)))
        new_height = max(2, int(round(new_width / ratio)))
        # 整数化可能将候选推过边界；只共同缩小尺寸，绝不能移动锚点补偿。
        while new_width > available_w or new_height > available_h:
            new_width -= 1
            new_height = max(2, int(round(new_width / ratio)))

        new_x = anchor_x - new_width if direction_x < 0 else anchor_x
        new_y = anchor_y - new_height if direction_y < 0 else anchor_y
        return [new_x, new_y, new_width, new_height]

    def _handle_mouse_release(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.end_crop_edit(commit=True)

    def keyPressEvent(self, event: QKeyEvent):
        if self.has_active_crop_edit():
            if event.key() == Qt.Key.Key_Escape:
                self.end_crop_edit(commit=False)
            # 鼠标 draft 期间键盘不能越过事务直接改正式 crop；WASD 忽略。
            if event.key() in (
                Qt.Key.Key_W, Qt.Key.Key_A, Qt.Key.Key_S, Qt.Key.Key_D,
                Qt.Key.Key_Escape,
            ):
                event.accept()
                return
        if not (self._has_video or self.current_frame is not None):
            super().keyPressEvent(event)
            return
        has_modifier = event.modifiers() != Qt.KeyboardModifier.NoModifier
        key = event.key()
        crop_changed = False
        if key == Qt.Key.Key_Space and not has_modifier and self._has_video:
            self.toggle_play()
        elif key == Qt.Key.Key_Left and not has_modifier and self._has_video:
            self.prev_frame()
        elif key == Qt.Key.Key_Right and not has_modifier and self._has_video:
            self.next_frame()
        elif (key == Qt.Key.Key_W and not has_modifier
              and self._crop_interaction_enabled()):
            self.cropbox[1] -= 10
            crop_changed = True
        elif (key == Qt.Key.Key_S and not has_modifier
              and self._crop_interaction_enabled()):
            self.cropbox[1] += 10
            crop_changed = True
        elif (key == Qt.Key.Key_A and not has_modifier
              and self._crop_interaction_enabled()):
            self.cropbox[0] -= 10
            crop_changed = True
        elif (key == Qt.Key.Key_D and not has_modifier
              and self._crop_interaction_enabled()):
            self.cropbox[0] += 10
            crop_changed = True
        elif key == Qt.Key.Key_Equal and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            # Ctrl+= (zoom in)
            self.zoom_slider.setValue(self.zoom_slider.value() + 10)
        elif key == Qt.Key.Key_Minus and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            # Ctrl+- (zoom out)
            self.zoom_slider.setValue(self.zoom_slider.value() - 10)
        elif key == Qt.Key.Key_0 and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            # Ctrl+0 (reset zoom to 100%)
            self.zoom_slider.setValue(0)
        else:
            super().keyPressEvent(event)
            return
        if crop_changed:
            self._bound_cropbox()
            self._emit_cropbox_changed()
            self._refresh_display()

    def focusOutEvent(self, event) -> None:
        # 键盘焦点离开意味着用户不再能可靠地完成 release；提交当前 draft。
        self.end_crop_edit(commit=True)
        super().focusOutEvent(event)

    def event(self, event) -> bool:
        if event.type() in (QEvent.Type.WindowDeactivate, QEvent.Type.UngrabMouse):
            # 窗口失活或 grab 被别的控件夺走时同样必须只有一次明确收口。
            self.end_crop_edit(commit=True)
        return super().event(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_display()

    def closeEvent(self, event):
        # Window teardown: the event loop is going away, so use the blocking
        # (bounded) shutdown — async kill-escalation timers would never fire.
        self.clear(sync_shutdown=True)
        super().closeEvent(event)

    def _on_zoom_slider_changed(self, value: int):
        """Slider position → zoom factor (logarithmic 1% ~ 10000%)."""
        import math
        # value 0 → 1.0x, value 100 → 10.0x, value 200 → 100.0x
        factor = 10 ** (value / 100.0)
        if abs(factor - self._zoom_factor) < 0.001:
            return
        self.ensure_render_state_committed()
        self._zoom_factor = factor
        self.zoom_label.setText(f"{int(factor * 100)}%")
        self._request_current_frame(coalesce=True)

    def _set_zoom_percent(self, percent: int):
        """Quick-zoom button: set zoom to an exact percentage."""
        import math
        factor = percent / 100.0
        # Convert back to slider position
        slider_val = int(100.0 * math.log10(factor))
        self.zoom_slider.setValue(slider_val)

    def set_zoom_factor(self, factor: float):
        """Public API: set zoom programmatically (factor = 1.0 means 100%)."""
        import math
        if factor < 0.01 or factor > 100.0:
            raise ValueError(f"zoom factor {factor} out of range [0.01, 100.0]")
        slider_val = int(100.0 * math.log10(factor))
        self.zoom_slider.setValue(slider_val)

    def get_zoom_factor(self) -> float:
        """Public API: current zoom factor."""
        return self._zoom_factor

    def clear(self, sync_shutdown: bool = False):
        self.ensure_render_state_committed()
        self.pause()
        self._teardown_media(sync_shutdown=sync_shutdown)
        self._load_epoch += 1
        self._loop_frame = None
        self.video_path = ""
        self.total_frames = 0
        self.current_frame_index = 0
        self.video_width = 0
        self.video_height = 0
        self.current_frame = None
        self._fps_rational = None
        self._output0_frames = 0
        self._timeline_start = 0
        self._timeline_end_exclusive = None
        self._job_dirty = False
        self._display_stack.setCurrentIndex(0)
        self.video_label.clear()
        self.video_label.setText("No media loaded")
        self.cropbox = [0, 0, 0, 0]
        self._update_info_label()
