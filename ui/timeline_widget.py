"""
时间轴组件 - 播放控制和时间标记
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSizePolicy
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QPoint
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QMouseEvent, QPaintEvent


class TimelineSlider(QWidget):
    """自定义时间轴滑块"""

    seek_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._total_frames = 100
        self._current_frame = 0
        self._in_point = 0
        self._out_point = 100
        self._dragging = False

        self._margin = 10
        self._track_height = 30

        self._bg_color = QColor(50, 50, 50)
        self._track_color = QColor(70, 70, 70)
        self._selection_color = QColor(66, 133, 244, 100)
        self._in_color = QColor(76, 175, 80)
        self._out_color = QColor(244, 67, 54)
        self._current_color = QColor(255, 255, 255)

        self.setMinimumHeight(50)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

    def set_total_frames(self, count: int):
        self._total_frames = max(1, count)
        if self._out_point > self._total_frames:
            self._out_point = self._total_frames
        self.update()

    def set_current_frame(self, index: int):
        self._current_frame = max(0, min(index, self._total_frames - 1))
        self.update()

    def set_in_point(self, frame: int):
        self._in_point = max(0, min(frame, self._total_frames - 1))
        if self._in_point > self._out_point:
            self._out_point = self._in_point
        self.update()

    def set_out_point(self, frame: int):
        self._out_point = max(0, min(frame, self._total_frames - 1))
        if self._out_point < self._in_point:
            self._in_point = self._out_point
        self.update()

    def get_in_point(self) -> int:
        return self._in_point

    def get_out_point(self) -> int:
        return self._out_point

    def _frame_to_x(self, frame: int) -> int:
        track_width = self.width() - 2 * self._margin
        if self._total_frames <= 1:
            return self._margin
        return int(self._margin + (frame / (self._total_frames - 1)) * track_width)

    def _x_to_frame(self, x: int) -> int:
        track_width = self.width() - 2 * self._margin
        if track_width <= 0:
            return 0
        ratio = max(0, min(1, (x - self._margin) / track_width))
        return int(ratio * (self._total_frames - 1))

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        track_y = (h - self._track_height) // 2
        track_width = w - 2 * self._margin

        painter.fillRect(0, 0, w, h, self._bg_color)
        painter.fillRect(QRect(self._margin, track_y, track_width, self._track_height), self._track_color)

        if self._total_frames > 1:
            in_x, out_x = self._frame_to_x(self._in_point), self._frame_to_x(self._out_point)
            painter.fillRect(QRect(in_x, track_y, out_x - in_x, self._track_height), self._selection_color)

        # 入点标记
        in_x = self._frame_to_x(self._in_point)
        painter.setBrush(QBrush(self._in_color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPolygon([QPoint(in_x - 6, track_y - 6), QPoint(in_x + 6, track_y - 6), QPoint(in_x, track_y)])

        # 出点标记
        out_x = self._frame_to_x(self._out_point)
        painter.setBrush(QBrush(self._out_color))
        bottom_y = track_y + self._track_height
        painter.drawPolygon([QPoint(out_x - 6, bottom_y + 6), QPoint(out_x + 6, bottom_y + 6), QPoint(out_x, bottom_y)])

        # 当前位置
        cur_x = self._frame_to_x(self._current_frame)
        painter.setPen(QPen(self._current_color, 2))
        painter.drawLine(cur_x, track_y - 5, cur_x, track_y + self._track_height + 5)
        painter.setBrush(QBrush(self._current_color))
        painter.drawEllipse(QPoint(cur_x, track_y + self._track_height // 2), 5, 5)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.seek_requested.emit(self._x_to_frame(int(event.position().x())))

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            self.seek_requested.emit(self._x_to_frame(int(event.position().x())))

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False


class TimelineWidget(QWidget):
    """时间轴组件"""

    play_pause_clicked = pyqtSignal()
    seek_requested = pyqtSignal(int)
    prev_frame_clicked = pyqtSignal()
    next_frame_clicked = pyqtSignal()
    goto_start_clicked = pyqtSignal()
    goto_end_clicked = pyqtSignal()
    set_in_point_clicked = pyqtSignal()
    set_out_point_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._total_frames = 100
        self._current_frame = 0
        self._fps = 30.0
        self._is_playing = False

        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        control_layout = QHBoxLayout()
        control_layout.setSpacing(3)

        self.btn_goto_start = QPushButton("|<")
        self.btn_goto_start.setFixedWidth(35)
        control_layout.addWidget(self.btn_goto_start)

        self.btn_prev_frame = QPushButton("<")
        self.btn_prev_frame.setFixedWidth(35)
        control_layout.addWidget(self.btn_prev_frame)

        self.btn_play_pause = QPushButton("▶")
        self.btn_play_pause.setFixedWidth(40)
        control_layout.addWidget(self.btn_play_pause)

        self.btn_next_frame = QPushButton(">")
        self.btn_next_frame.setFixedWidth(35)
        control_layout.addWidget(self.btn_next_frame)

        self.btn_goto_end = QPushButton(">|")
        self.btn_goto_end.setFixedWidth(35)
        control_layout.addWidget(self.btn_goto_end)

        control_layout.addWidget(QLabel("|"))

        self.btn_set_in = QPushButton("[ 入点")
        control_layout.addWidget(self.btn_set_in)

        self.btn_set_out = QPushButton("] 出点")
        control_layout.addWidget(self.btn_set_out)

        control_layout.addWidget(QLabel("|"))

        self.label_frame = QLabel("0 / 100")
        self.label_frame.setMinimumWidth(80)
        control_layout.addWidget(self.label_frame)

        self.label_fps = QLabel("30.0 FPS")
        control_layout.addWidget(self.label_fps)

        control_layout.addStretch()

        self.timeline_slider = TimelineSlider()

        main_layout.addLayout(control_layout)
        main_layout.addWidget(self.timeline_slider)

        self.setFixedHeight(100)
        self.setStyleSheet("""
            TimelineWidget { background-color: #2d2d2d; border-top: 1px solid #444; }
            QPushButton { background-color: #444; color: #ddd; border: 1px solid #555; border-radius: 3px; padding: 4px 8px; }
            QPushButton:hover { background-color: #555; }
            QLabel { color: #ccc; }
        """)

    def _connect_signals(self):
        self.btn_goto_start.clicked.connect(self.goto_start_clicked.emit)
        self.btn_prev_frame.clicked.connect(self.prev_frame_clicked.emit)
        self.btn_play_pause.clicked.connect(self.play_pause_clicked.emit)
        self.btn_next_frame.clicked.connect(self.next_frame_clicked.emit)
        self.btn_goto_end.clicked.connect(self.goto_end_clicked.emit)
        self.btn_set_in.clicked.connect(self.set_in_point_clicked.emit)
        self.btn_set_out.clicked.connect(self.set_out_point_clicked.emit)
        self.timeline_slider.seek_requested.connect(self.seek_requested.emit)

    def set_total_frames(self, count: int):
        self._total_frames = max(1, count)
        self.timeline_slider.set_total_frames(count)
        self._update_label()

    def set_current_frame(self, index: int):
        self._current_frame = max(0, min(index, self._total_frames - 1))
        self.timeline_slider.set_current_frame(index)
        self._update_label()

    def set_in_point(self, frame: int):
        self.timeline_slider.set_in_point(frame)

    def set_out_point(self, frame: int):
        self.timeline_slider.set_out_point(frame)

    def get_in_point(self) -> int:
        return self.timeline_slider.get_in_point()

    def get_out_point(self) -> int:
        return self.timeline_slider.get_out_point()

    def set_fps(self, fps: float):
        self._fps = fps
        self.label_fps.setText(f"{fps:.1f} FPS")

    def set_playing(self, is_playing: bool):
        self._is_playing = is_playing
        self.btn_play_pause.setText("⏸" if is_playing else "▶")

    def _update_label(self):
        self.label_frame.setText(f"{self._current_frame} / {self._total_frames}")
