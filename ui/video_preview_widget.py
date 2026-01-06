"""
视频预览组件 - 支持视频播放和裁剪框交互
"""

import cv2
import logging
from typing import Optional, Tuple

from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QSizePolicy
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPoint
from PyQt6.QtGui import QImage, QPixmap, QMouseEvent, QKeyEvent

logger = logging.getLogger(__name__)

TARGET_WIDTH = 360
TARGET_HEIGHT = 640
TARGET_ASPECT_RATIO = TARGET_WIDTH / TARGET_HEIGHT


class VideoPreviewWidget(QWidget):
    """视频预览组件，支持裁剪框交互"""

    cropbox_changed = pyqtSignal(int, int, int, int)
    frame_changed = pyqtSignal(int)
    playback_state_changed = pyqtSignal(bool)
    video_loaded = pyqtSignal(int, float)  # frame_count, fps

    DRAG_NONE = 0
    DRAG_MOVE = 1
    DRAG_RESIZE_TL = 2
    DRAG_RESIZE_TR = 3
    DRAG_RESIZE_BL = 4
    DRAG_RESIZE_BR = 5

    def __init__(self, parent=None):
        super().__init__(parent)

        self.cap: Optional[cv2.VideoCapture] = None
        self.video_path: str = ""
        self.video_fps: float = 30.0
        self.video_width: int = 0
        self.video_height: int = 0
        self.total_frames: int = 0
        self.current_frame_index: int = 0
        self.current_frame = None

        self.is_playing: bool = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer_tick)

        self.cropbox = [0, 0, TARGET_WIDTH, TARGET_HEIGHT]

        self.display_scale: float = 1.0
        self.display_offset_x: int = 0
        self.display_offset_y: int = 0

        self.drag_mode: int = self.DRAG_NONE
        self.drag_start_pos: Optional[QPoint] = None
        self.drag_start_cropbox: list = []
        self.handle_size: int = 15

        self._setup_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(480, 270)
        self.video_label.setStyleSheet("background-color: #1a1a1a; border: 1px solid #333;")
        self.video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_label.setText("No video loaded")
        self.video_label.setMouseTracking(True)
        layout.addWidget(self.video_label)

        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(5)

        self.btn_prev_frame = QPushButton("< Prev")
        self.btn_prev_frame.clicked.connect(self.prev_frame)
        controls_layout.addWidget(self.btn_prev_frame)

        self.btn_play_pause = QPushButton("Play")
        self.btn_play_pause.clicked.connect(self.toggle_play)
        controls_layout.addWidget(self.btn_play_pause)

        self.btn_next_frame = QPushButton("Next >")
        self.btn_next_frame.clicked.connect(self.next_frame)
        controls_layout.addWidget(self.btn_next_frame)

        controls_layout.addStretch()

        self.info_label = QLabel("Frame: 0/0 | Cropbox: (0, 0, 0, 0)")
        self.info_label.setStyleSheet("color: #888; font-size: 11px;")
        controls_layout.addWidget(self.info_label)

        layout.addLayout(controls_layout)

    def load_video(self, path: str) -> bool:
        if self.cap is not None:
            self.cap.release()
        self.pause()

        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            self.video_label.setText("Failed to load video")
            return False

        self.video_path = path
        self.video_fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.video_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.video_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.current_frame_index = 0

        self._init_cropbox()
        self._read_and_display_frame()
        self.video_loaded.emit(self.total_frames, self.video_fps)
        return True

    def _init_cropbox(self):
        w, h = TARGET_WIDTH, TARGET_HEIGHT
        if w > self.video_width:
            w = self.video_width
            h = int(w / TARGET_ASPECT_RATIO)
        if h > self.video_height:
            h = self.video_height
            w = int(h * TARGET_ASPECT_RATIO)

        x = (self.video_width - w) // 2
        y = (self.video_height - h) // 2
        self.cropbox = [x, y, w, h]
        self._emit_cropbox_changed()

    def _bound_cropbox(self):
        x, y, w, h = self.cropbox
        if w > self.video_width:
            w = self.video_width
            h = int(w / TARGET_ASPECT_RATIO)
        if h > self.video_height:
            h = self.video_height
            w = int(h * TARGET_ASPECT_RATIO)

        w = max(w, 90)
        h = max(h, int(90 / TARGET_ASPECT_RATIO))

        x = max(0, min(x, self.video_width - w))
        y = max(0, min(y, self.video_height - h))
        self.cropbox = [x, y, w, h]

    def _emit_cropbox_changed(self):
        x, y, w, h = self.cropbox
        self.cropbox_changed.emit(x, y, w, h)
        self._update_info_label()

    def _update_info_label(self):
        x, y, w, h = self.cropbox
        self.info_label.setText(f"Frame: {self.current_frame_index}/{self.total_frames} | Cropbox: ({x}, {y}, {w}, {h})")

    def _read_and_display_frame(self):
        if self.cap is None:
            return
        ret, frame = self.cap.read()
        if not ret:
            self.pause()
            return
        self.current_frame = frame
        self._display_frame(frame)
        self.frame_changed.emit(self.current_frame_index)
        self._update_info_label()

    def _display_frame(self, frame):
        if frame is None:
            return

        display_frame = frame.copy()
        x, y, w, h = self.cropbox
        cv2.rectangle(display_frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

        # 绘制角落手柄
        hs = 8
        handle_color = (0, 200, 255)
        for px, py in [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]:
            cv2.rectangle(display_frame, (px - hs, py - hs), (px + hs, py + hs), handle_color, -1)

        # 信息叠加
        cv2.putText(display_frame, f"Frame: {self.current_frame_index}/{self.total_frames}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)
        cv2.putText(display_frame, f"Crop: x={x} y={y} w={w} h={h}",
                    (10, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)

        rgb_frame = cv2.cvtColor(display_frame, cv2.COLOR_BGR2RGB)
        h_frame, w_frame, ch = rgb_frame.shape
        q_image = QImage(rgb_frame.data, w_frame, h_frame, ch * w_frame, QImage.Format.Format_RGB888)

        label_size = self.video_label.size()
        pixmap = QPixmap.fromImage(q_image).scaled(
            label_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )

        self.display_scale = pixmap.width() / self.video_width if self.video_width > 0 else 1.0
        self.display_offset_x = (label_size.width() - pixmap.width()) // 2
        self.display_offset_y = (label_size.height() - pixmap.height()) // 2

        self.video_label.setPixmap(pixmap)

    def _on_timer_tick(self):
        if self.cap is None:
            return
        self.current_frame_index += 1
        if self.current_frame_index >= self.total_frames:
            self.current_frame_index = 0
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        self._read_and_display_frame()

    def play(self):
        if self.cap is None or self.is_playing:
            return
        interval = int(1000 / self.video_fps)
        self.timer.start(interval)
        self.is_playing = True
        self.btn_play_pause.setText("Pause")
        self.playback_state_changed.emit(True)

    def pause(self):
        self.timer.stop()
        self.is_playing = False
        self.btn_play_pause.setText("Play")
        self.playback_state_changed.emit(False)

    def toggle_play(self):
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def toggle_playback(self):
        """Alias for toggle_play for compatibility"""
        self.toggle_play()

    def step_frame(self, delta: int):
        """Step forward or backward by delta frames"""
        if delta > 0:
            for _ in range(delta):
                self.next_frame()
        elif delta < 0:
            for _ in range(-delta):
                self.prev_frame()

    def next_frame(self):
        if self.cap is None:
            return
        self.pause()
        self.current_frame_index = min(self.current_frame_index + 1, self.total_frames - 1)
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.current_frame_index)
        self._read_and_display_frame()

    def prev_frame(self):
        if self.cap is None:
            return
        self.pause()
        self.current_frame_index = max(self.current_frame_index - 1, 0)
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.current_frame_index)
        self._read_and_display_frame()

    def seek_to_frame(self, index: int):
        if self.cap is None:
            return
        index = max(0, min(index, self.total_frames - 1))
        self.current_frame_index = index
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        self._read_and_display_frame()

    def get_current_frame(self) -> int:
        return self.current_frame_index

    def get_cropbox(self) -> Tuple[int, int, int, int]:
        return tuple(self.cropbox)

    def set_cropbox(self, x: int, y: int, w: int, h: int):
        self.cropbox = [x, y, w, h]
        self._bound_cropbox()
        self._emit_cropbox_changed()
        if self.current_frame is not None:
            self._display_frame(self.current_frame)

    def get_video_info(self) -> Tuple[float, int, int, int]:
        return (self.video_fps, self.total_frames, self.video_width, self.video_height)

    def _display_to_video_coords(self, pos: QPoint) -> Tuple[int, int]:
        label_pos = self.video_label.mapFrom(self, pos)
        x = int((label_pos.x() - self.display_offset_x) / self.display_scale) if self.display_scale > 0 else 0
        y = int((label_pos.y() - self.display_offset_y) / self.display_scale) if self.display_scale > 0 else 0
        return (x, y)

    def _get_drag_mode(self, vx: int, vy: int) -> int:
        x, y, w, h = self.cropbox
        hs = self.handle_size
        if abs(vx - x) < hs and abs(vy - y) < hs:
            return self.DRAG_RESIZE_TL
        if abs(vx - (x + w)) < hs and abs(vy - y) < hs:
            return self.DRAG_RESIZE_TR
        if abs(vx - x) < hs and abs(vy - (y + h)) < hs:
            return self.DRAG_RESIZE_BL
        if abs(vx - (x + w)) < hs and abs(vy - (y + h)) < hs:
            return self.DRAG_RESIZE_BR
        if x <= vx <= x + w and y <= vy <= y + h:
            return self.DRAG_MOVE
        return self.DRAG_NONE

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self.cap is not None:
            vx, vy = self._display_to_video_coords(event.pos())
            self.drag_mode = self._get_drag_mode(vx, vy)
            if self.drag_mode != self.DRAG_NONE:
                self.drag_start_pos = event.pos()
                self.drag_start_cropbox = self.cropbox.copy()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.drag_mode != self.DRAG_NONE and self.drag_start_pos is not None:
            cvx, cvy = self._display_to_video_coords(event.pos())
            svx, svy = self._display_to_video_coords(self.drag_start_pos)
            dx, dy = cvx - svx, cvy - svy
            sx, sy, sw, sh = self.drag_start_cropbox

            if self.drag_mode == self.DRAG_MOVE:
                self.cropbox = [sx + dx, sy + dy, sw, sh]
            elif self.drag_mode == self.DRAG_RESIZE_BR:
                new_w = sw + dx
                self.cropbox = [sx, sy, new_w, int(new_w / TARGET_ASPECT_RATIO)]
            elif self.drag_mode == self.DRAG_RESIZE_TL:
                new_w = sw - dx
                new_h = int(new_w / TARGET_ASPECT_RATIO)
                self.cropbox = [sx + (sw - new_w), sy + (sh - new_h), new_w, new_h]
            elif self.drag_mode == self.DRAG_RESIZE_TR:
                new_w = sw + dx
                new_h = int(new_w / TARGET_ASPECT_RATIO)
                self.cropbox = [sx, sy + (sh - new_h), new_w, new_h]
            elif self.drag_mode == self.DRAG_RESIZE_BL:
                new_w = sw - dx
                new_h = int(new_w / TARGET_ASPECT_RATIO)
                self.cropbox = [sx + (sw - new_w), sy, new_w, new_h]

            self._bound_cropbox()
            self._emit_cropbox_changed()
            if self.current_frame is not None:
                self._display_frame(self.current_frame)

        elif self.cap is not None:
            vx, vy = self._display_to_video_coords(event.pos())
            mode = self._get_drag_mode(vx, vy)
            cursors = {
                self.DRAG_RESIZE_TL: Qt.CursorShape.SizeFDiagCursor,
                self.DRAG_RESIZE_BR: Qt.CursorShape.SizeFDiagCursor,
                self.DRAG_RESIZE_TR: Qt.CursorShape.SizeBDiagCursor,
                self.DRAG_RESIZE_BL: Qt.CursorShape.SizeBDiagCursor,
                self.DRAG_MOVE: Qt.CursorShape.SizeAllCursor,
            }
            self.setCursor(cursors.get(mode, Qt.CursorShape.ArrowCursor))

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_mode = self.DRAG_NONE
            self.drag_start_pos = None
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent):
        if self.cap is None:
            super().keyPressEvent(event)
            return

        key = event.key()
        step = 10

        if key == Qt.Key.Key_Space:
            self.toggle_play()
        elif key == Qt.Key.Key_Left:
            self.prev_frame()
        elif key == Qt.Key.Key_Right:
            self.next_frame()
        elif key == Qt.Key.Key_W:
            self.cropbox[1] -= step
        elif key == Qt.Key.Key_S:
            self.cropbox[1] += step
        elif key == Qt.Key.Key_A:
            self.cropbox[0] -= step
        elif key == Qt.Key.Key_D:
            self.cropbox[0] += step
        elif key == Qt.Key.Key_Y:
            self.cropbox[2] -= step
            self.cropbox[3] = int(self.cropbox[2] / TARGET_ASPECT_RATIO)
        elif key == Qt.Key.Key_U:
            self.cropbox[2] += step
            self.cropbox[3] = int(self.cropbox[2] / TARGET_ASPECT_RATIO)
        else:
            super().keyPressEvent(event)
            return

        self._bound_cropbox()
        self._emit_cropbox_changed()
        if self.current_frame is not None:
            self._display_frame(self.current_frame)

    def closeEvent(self, event):
        self.pause()
        if self.cap is not None:
            self.cap.release()
        super().closeEvent(event)
