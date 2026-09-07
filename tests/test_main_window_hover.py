import unittest

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QCursor, QMouseEvent

from gui.main_window import MainWindow
from tests.qt_harness import ensure_app


class _CursorProbe:
    def __init__(self):
        self._is_resizing = False
        self._resize_margin = 10
        self._cursor_shape = Qt.CursorShape.ArrowCursor
        self.cursor_changes = []

    def rect(self):
        return QRect(0, 0, 100, 100)

    def cursor(self):
        return QCursor(self._cursor_shape)

    def setCursor(self, cursor):
        self._cursor_shape = cursor
        self.cursor_changes.append(cursor)

    def cursorAtPosition(self, pos):
        return MainWindow.cursorAtPosition(self, pos)


def _mouse_move(x, y):
    point = QPointF(x, y)
    return QMouseEvent(
        QEvent.Type.MouseMove,
        point,
        point,
        point,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


class MainWindowHoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ensure_app()

    def test_repeated_moves_in_same_region_change_cursor_once(self):
        """移除光标去重时，同一区域的一千次移动会产生一千次 setCursor 调用。"""
        window = _CursorProbe()

        for _ in range(1000):
            MainWindow.mouseMoveEvent(window, _mouse_move(1, 50))

        self.assertEqual(window.cursor_changes, [Qt.CursorShape.SizeHorCursor])

    def test_corner_edge_and_center_switch_to_their_respective_cursors(self):
        """错误复用上一个区域光标时，跨边角、边缘和中心的反馈会失真。"""
        window = _CursorProbe()

        for position in ((1, 1), (98, 1), (98, 50), (50, 50)):
            MainWindow.mouseMoveEvent(window, _mouse_move(*position))

        self.assertEqual(
            window.cursor_changes,
            [
                Qt.CursorShape.SizeFDiagCursor,
                Qt.CursorShape.SizeBDiagCursor,
                Qt.CursorShape.SizeHorCursor,
                Qt.CursorShape.ArrowCursor,
            ],
        )

    def test_resize_drag_keeps_existing_geometry_behavior(self):
        """把拖动分支误当作悬停分支时，右边缘拖动不会更新窗口宽度。"""
        window = _CursorProbe()
        window._is_resizing = True
        window._resize_direction = "right"
        window._resize_start_pos = QPoint(0, 0)
        window._resize_start_geometry = QRect(10, 20, 100, 80)
        window.minimumWidth = lambda: 50
        window.setGeometry = lambda *args: setattr(window, "geometry_args", args)

        MainWindow.mouseMoveEvent(window, _mouse_move(25, 0))

        self.assertEqual(window.geometry_args, (10, 20, 125, 80))
        self.assertEqual(window.cursor_changes, [])


if __name__ == "__main__":
    unittest.main()
