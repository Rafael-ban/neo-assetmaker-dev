"""The crop box must ALWAYS keep the target aspect ratio (fixed-ratio crop).

Old defect: the four resize handles locked the ratio
(`new_h = int(new_w / target_aspect_ratio)`) but the `_bound_cropbox` call
right after them clamped w and h INDEPENDENTLY against the frame edges,
silently destroying the lock. Measured on a 1920x1080 source with a 360x640
target: one BR drag drifted the box 7.8% -> 40.7% off target.

Why that matters: the export crops and then resizes straight to the target
with `core.resize.<kernel>(clip, width=..., height=...)`. The VS R73 API has
NO aspect-preserving parameter (stub `resize.Bicubic` takes independent
width/height; no keep_aspect / pad / force_original_aspect_ratio), so a box
whose ratio != target is scaled anisotropically — i.e. the exported video is
geometrically stretched (crop 855x1080 -> 360x640 = 1.41x anisotropy).

These tests pin the invariant on every crop-box write path.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest

from tests.qt_harness import ensure_app
from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent


def setUpModule():
    ensure_app()


SOURCES = {
    "landscape": (1920, 1080),
    "portrait": (1080, 1920),
    "square": (1000, 1000),
    "tiny": (240, 360),
}
TARGETS = {
    "360x640": (360, 640),
    "720x1080": (720, 1080),
    "square256": (256, 256),
}
# Integer quantization: a box of h px can only express the ratio to ~1/h.
TOL = 0.02


def _widget(src, target):
    from gui.widgets.video_preview import VideoPreviewWidget

    w = VideoPreviewWidget()
    # 此组只验证兼容脚本下的裁剪几何；M6 的脚本能力门由专属测试覆盖。
    w.supports_editor_capability = lambda _capability: True
    w.video_width, w.video_height = src
    w.set_target_resolution(*target)
    w._init_cropbox()
    # F2 命中以 QLabel 逻辑像素而非源像素计算。让标签与旋转后素材同大，
    # 下面的真实 QMouseEvent 坐标才恰好等于源坐标（scale=1, offset=0）。
    rotated_w, rotated_h = w._get_rotated_video_size()
    w.video_label.setFixedSize(rotated_w, rotated_h)
    return w


def _ar_off(w):
    bw, bh = w.cropbox[2], w.cropbox[3]
    return abs(bw / bh - w.target_aspect_ratio) / w.target_aspect_ratio


class InitialBoxTests(unittest.TestCase):
    def test_init_cropbox_is_target_ratio_for_every_combo(self):
        for sname, src in SOURCES.items():
            for tname, target in TARGETS.items():
                with self.subTest(src=sname, target=tname):
                    w = _widget(src, target)
                    self.assertLess(
                        _ar_off(w), TOL,
                        f"initial box {w.cropbox} AR="
                        f"{w.cropbox[2]/w.cropbox[3]:.4f} != target "
                        f"{w.target_aspect_ratio:.4f}",
                    )

    def test_init_cropbox_fits_inside_frame(self):
        for sname, src in SOURCES.items():
            for tname, target in TARGETS.items():
                with self.subTest(src=sname, target=tname):
                    w = _widget(src, target)
                    rw, rh = w._get_rotated_video_size()
                    x, y, bw, bh = w.cropbox
                    self.assertGreaterEqual(x, 0)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(x + bw, rw)
                    self.assertLessEqual(y + bh, rh)


class DragTests(unittest.TestCase):
    """Every resize handle, dragged well past the frame edge, stays locked."""

    def _drag(self, w, mode, dx, dy):
        # 标签尺寸已与旋转后素材一致，因此下面走实际控件坐标映射而非替换它。
        x, y, width, height = w.cropbox
        points = {
            w.DRAG_MOVE: QPoint(x + width // 2, y + height // 2),
            w.DRAG_RESIZE_BR: QPoint(x + width, y + height),
            w.DRAG_RESIZE_TL: QPoint(x, y),
            w.DRAG_RESIZE_TR: QPoint(x + width, y),
            w.DRAG_RESIZE_BL: QPoint(x, y + height),
        }
        start = points[mode]
        w._handle_mouse_press(
            w.video_label,
            QMouseEvent(
                QMouseEvent.Type.MouseButtonPress, QPointF(start),
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        ev = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            QPointF(start.x() + dx, start.y() + dy),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        w._handle_mouse_move(w.video_label, ev)
        w._handle_mouse_release(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonRelease,
                QPointF(start.x() + dx, start.y() + dy),
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

    def test_all_handles_stay_locked_when_dragged_out_of_bounds(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        modes = {
            "BR": VideoPreviewWidget.DRAG_RESIZE_BR,
            "TL": VideoPreviewWidget.DRAG_RESIZE_TL,
            "TR": VideoPreviewWidget.DRAG_RESIZE_TR,
            "BL": VideoPreviewWidget.DRAG_RESIZE_BL,
        }
        for sname, src in SOURCES.items():
            for tname, target in TARGETS.items():
                for mname, mode in modes.items():
                    for delta in (300, 1200, -300):
                        with self.subTest(src=sname, target=tname,
                                          handle=mname, delta=delta):
                            w = _widget(src, target)
                            self._drag(w, mode, delta, delta)
                            self.assertLess(
                                _ar_off(w), TOL,
                                f"{mname} drag {delta} broke the lock: "
                                f"{w.cropbox} AR="
                                f"{w.cropbox[2]/w.cropbox[3]:.4f} target="
                                f"{w.target_aspect_ratio:.4f}",
                            )
                            rw, rh = w._get_rotated_video_size()
                            x, y, bw, bh = w.cropbox
                            self.assertLessEqual(x + bw, rw)
                            self.assertLessEqual(y + bh, rh)

    def test_move_drag_translates_without_resizing(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = _widget((1920, 1080), (360, 640))
        before = list(w.cropbox)
        self._drag(w, VideoPreviewWidget.DRAG_MOVE, 40, 25)
        self.assertEqual(w.cropbox[2], before[2], "move must not resize width")
        self.assertEqual(w.cropbox[3], before[3], "move must not resize height")
        self.assertNotEqual(w.cropbox[:2], before[:2], "move must translate")

    def test_move_drag_out_of_bounds_keeps_size_and_ratio(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = _widget((1920, 1080), (360, 640))
        before = list(w.cropbox)
        self._drag(w, VideoPreviewWidget.DRAG_MOVE, 5000, 5000)
        self.assertEqual([w.cropbox[2], w.cropbox[3]], before[2:])
        self.assertLess(_ar_off(w), TOL)


class SetCropboxTests(unittest.TestCase):
    def test_foreign_resolution_box_is_refitted(self):
        # A box saved under 360x640 (AR 0.5625) restored under a 720x1080
        # project (AR 0.6667) used to be accepted verbatim.
        w = _widget((1080, 1920), (720, 1080))
        w.set_cropbox(135, 240, 810, 1440)
        self.assertLess(
            _ar_off(w), TOL,
            f"restored foreign box kept its old ratio: {w.cropbox}",
        )

    def test_absurd_box_is_clamped_and_locked(self):
        w = _widget((240, 360), (360, 640))
        w.set_cropbox(-500, -500, 99999, 99999)
        rw, rh = w._get_rotated_video_size()
        x, y, bw, bh = w.cropbox
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + bw, rw)
        self.assertLessEqual(y + bh, rh)
        self.assertLess(_ar_off(w), TOL)

    def test_degenerate_box_becomes_valid(self):
        w = _widget((240, 360), (360, 640))
        w.set_cropbox(0, 0, 0, 0)
        self.assertGreaterEqual(w.cropbox[2], 2)
        self.assertGreaterEqual(w.cropbox[3], 2)
        self.assertLess(_ar_off(w), TOL)


class TargetResolutionChangeTests(unittest.TestCase):
    def test_changing_target_refits_existing_box(self):
        w = _widget((1080, 1920), (360, 640))
        w.set_target_resolution(720, 1080)
        self.assertLess(
            _ar_off(w), TOL,
            f"box kept the old ratio after a resolution change: {w.cropbox}",
        )

    def test_changing_target_before_media_does_not_keep_stale_ratio(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        # _apply_project_config calls clear() (video_width=0) BEFORE
        # set_target_resolution — the old guard skipped the re-fit entirely.
        w = VideoPreviewWidget()
        w.set_target_resolution(720, 1080)
        w.video_width, w.video_height = 1080, 1920
        w._init_cropbox()
        self.assertLess(_ar_off(w), TOL)


class TransitionPreviewTests(unittest.TestCase):
    """The transition-image crop boxes were never given the target resolution
    (TransitionPreviewWidget.set_target_resolution existed but had zero callers),
    so they stayed locked to the module default 0.5625 — a 720x1080 project
    stretched its transition images by ~15.6%."""

    def test_forwarding_sets_ratio_on_both_inner_previews(self):
        from gui.widgets.transition_preview import TransitionPreviewWidget

        tp = TransitionPreviewWidget()
        tp.set_target_resolution(720, 1080)
        for name in ("preview_in", "preview_loop"):
            inner = getattr(tp, name)
            self.assertAlmostEqual(inner.target_aspect_ratio, 720 / 1080, places=6,
                                   msg=f"{name} kept a stale target ratio")

    def test_main_window_forwards_resolution_to_transition_preview(self):
        # Guard the wiring itself: main_window must call the forwarder.
        from pathlib import Path

        src = Path("gui/main_window.py").read_text(encoding="utf-8")
        self.assertIn("transition_preview.set_target_resolution", src)


class ValidatorCropTests(unittest.TestCase):
    def _cfg(self, crop, screen="360x640"):
        return {
            "version": 1,
            "uuid": "12345678-1234-1234-1234-123456789abc",
            "screen": screen,
            "loop": {"file": "loop.mp4"},
            "editor": {"loop": {"crop": list(crop)}},
        }

    def test_offratio_crop_is_warned(self):
        from core.validator import EPConfigValidator, ValidationLevel

        results = EPConfigValidator().validate(self._cfg((0, 0, 300, 300)))
        hits = [r for r in results
                if r.field == "editor.loop.crop" and r.level == ValidationLevel.WARNING]
        self.assertTrue(hits, "a square crop on a 0.5625 target must warn")

    def test_conforming_crop_is_silent(self):
        from core.validator import EPConfigValidator

        results = EPConfigValidator().validate(self._cfg((0, 0, 360, 640)))
        self.assertEqual([r for r in results if r.field == "editor.loop.crop"], [])


class KeyboardNudgeTests(unittest.TestCase):
    def test_wasd_translates_without_breaking_ratio(self):
        from PyQt6.QtGui import QKeyEvent

        w = _widget((1920, 1080), (360, 640))
        before = list(w.cropbox)
        for key in (Qt.Key.Key_W, Qt.Key.Key_A, Qt.Key.Key_S, Qt.Key.Key_D):
            ev = QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
            w.keyPressEvent(ev)
            self.assertLess(_ar_off(w), TOL, f"key {key} broke the ratio")
        self.assertEqual([w.cropbox[2], w.cropbox[3]], before[2:],
                         "WASD must not resize the box")


if __name__ == "__main__":
    unittest.main()
