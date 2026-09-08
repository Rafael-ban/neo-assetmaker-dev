"""F2：固定比例裁剪框的控件像素手柄与真实鼠标缩放路径。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QPainter

from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


def _mouse_event(kind, x, y, button, buttons):
    return QMouseEvent(
        kind,
        QPointF(x, y),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def _widget(source=(360, 640), rotation=0):
    from gui.widgets.video_preview import VideoPreviewWidget

    widget = VideoPreviewWidget()
    widget.supports_editor_capability = lambda _capability: True
    widget._has_video = True
    widget.video_width, widget.video_height = source
    widget._rotation = rotation
    rotated_w, rotated_h = widget._get_rotated_video_size()
    widget.video_label.setFixedSize(rotated_w, rotated_h)
    widget.cropbox = [80, 80, 180, 320]
    return widget


class ControlPixelHandleTests(unittest.TestCase):
    def test_handle_hit_uses_control_pixels_at_quarter_scale(self):
        """15 逻辑像素手柄在 25% 显示时仍可命中，而非缩成源像素半径。"""
        widget = _widget()
        widget.video_label.setFixedSize(90, 160)

        # 右下角在逻辑坐标 (65, 100)；+6 仍在 15px 方形手柄内。
        widget._handle_mouse_press(
            widget.video_label,
            _mouse_event(
                QMouseEvent.Type.MouseButtonPress,
                71,
                106,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
            ),
        )

        self.assertEqual(widget.drag_mode, widget.DRAG_RESIZE_BR)
        self.assertTrue(widget.has_active_crop_edit())

    def test_handle_is_painted_at_high_dpi_logical_position(self):
        """高 DPI QImage 中，手柄以标签逻辑坐标绘制为可观察的实心标记。"""
        widget = _widget()
        widget.video_label.setFixedSize(90, 160)
        image = QImage(180, 320, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(2.0)
        image.fill(Qt.GlobalColor.transparent)

        painter = QPainter(image)
        widget.video_label.render(painter)
        painter.end()

        # 右下角逻辑中心为 (65, 100)；手柄内部 (69, 104) 的物理像素应为 cyan。
        self.assertEqual(image.pixelColor(138, 208), QColor(Qt.GlobalColor.cyan))


class FixedRatioResizeMousePathTests(unittest.TestCase):
    _START = [80, 80, 180, 320]
    _EXPECTED_SIZES = {
        "horizontal": (190, 338),
        "vertical": (197, 350),
        "diagonal": (207, 368),
    }

    _HANDLES = {
        "TL": ("DRAG_RESIZE_TL", (80, 80), (260, 400), (-1, -1)),
        "TR": ("DRAG_RESIZE_TR", (260, 80), (80, 400), (1, -1)),
        "BL": ("DRAG_RESIZE_BL", (80, 400), (260, 80), (-1, 1)),
        "BR": ("DRAG_RESIZE_BR", (260, 400), (80, 80), (1, 1)),
    }

    def _drag(self, widget, start, delta):
        sx, sy = start
        dx, dy = delta
        widget._handle_mouse_press(
            widget.video_label,
            _mouse_event(
                QMouseEvent.Type.MouseButtonPress,
                sx,
                sy,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
            ),
        )
        widget._handle_mouse_move(
            widget.video_label,
            _mouse_event(
                QMouseEvent.Type.MouseMove,
                sx + dx,
                sy + dy,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
            ),
        )

    def test_all_corner_drag_directions_project_and_keep_opposite_anchor(self):
        """横/竖/斜拖均按二维投影改变尺寸，并固定相对对角锚点。"""
        directions = {
            "horizontal": (40, 0),
            "vertical": (0, 40),
            "diagonal": (40, 40),
        }
        for handle, (_mode, start, anchor, signs) in self._HANDLES.items():
            for name, (out_x, out_y) in directions.items():
                with self.subTest(handle=handle, direction=name):
                    widget = _widget()
                    dx, dy = out_x * signs[0], out_y * signs[1]
                    self._drag(widget, start, (dx, dy))
                    width, height = self._EXPECTED_SIZES[name]
                    ax, ay = anchor
                    expected = [
                        ax - width if signs[0] < 0 else ax,
                        ay - height if signs[1] < 0 else ay,
                        width,
                        height,
                    ]

                    self.assertEqual(widget.get_draft_cropbox(), tuple(expected))
                    self.assertEqual(widget.cropbox, self._START)
                    widget._handle_mouse_release(
                        _mouse_event(
                            QMouseEvent.Type.MouseButtonRelease,
                            start[0] + dx,
                            start[1] + dy,
                            Qt.MouseButton.LeftButton,
                            Qt.MouseButton.NoButton,
                        )
                    )
                    self.assertEqual(widget.cropbox, expected)

    def test_resize_boundary_and_minimum_keep_the_fixed_anchor(self):
        """越界和缩至最小时只共同限尺寸，绝不靠平移破坏锚点。"""
        widget = _widget()
        self._drag(widget, (80, 80), (-1000, -1000))
        self.assertEqual(widget.get_draft_cropbox(), (35, 0, 225, 400))

        widget = _widget()
        widget.cropbox = [100, 100, 45, 80]
        self._drag(widget, (145, 180), (-1000, -1000))
        self.assertEqual(widget.get_draft_cropbox(), (100, 100, 36, 64))

    def test_tl_horizontal_drag_beyond_left_projects_full_unclamped_delta(self):
        """TL 越过左边界后，投影仍使用完整的 -1000px 鼠标位移。"""
        widget = _widget()
        self._drag(widget, (80, 80), (-1000, 0))

        self.assertEqual(widget.get_draft_cropbox(), (35, 0, 225, 400))

    def test_tl_out_of_bounds_zero_projection_keeps_the_original_size(self):
        """越界终点的 r*(-160)+90=0，不能因坐标截断而制造缩放。"""
        widget = _widget()
        self._drag(widget, (80, 80), (160, -90))

        self.assertEqual(widget.get_draft_cropbox(), (80, 80, 180, 320))

    def test_resize_keeps_anchor_ratio_and_bounds_for_all_cardinal_rotations(self):
        """横竖素材在 0/90/180/270 度下均通过真实 BR 鼠标路径保持约束。"""
        for source in ((360, 640), (640, 360)):
            for rotation in (0, 90, 180, 270):
                with self.subTest(source=source, rotation=rotation):
                    widget = _widget(source, rotation)
                    rotated_w, rotated_h = widget._get_rotated_video_size()
                    if rotated_h < 640:
                        widget.cropbox = [80, 20, 180, 320]
                    start = (
                        widget.cropbox[0] + widget.cropbox[2],
                        widget.cropbox[1] + widget.cropbox[3],
                    )
                    anchor = tuple(widget.cropbox[:2])
                    self._drag(widget, start, (20, 20))
                    x, y, width, height = widget.get_draft_cropbox()

                    self.assertEqual((x, y), anchor)
                    self.assertLess(abs(width / height - 9 / 16), 0.002)
                    self.assertGreaterEqual(x, 0)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(x + width, rotated_w)
                    self.assertLessEqual(y + height, rotated_h)


if __name__ == "__main__":
    unittest.main()
