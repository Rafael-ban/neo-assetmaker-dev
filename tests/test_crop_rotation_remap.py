"""S6.8 + S6.11: rotation preserves the crop box; basic panel doesn't clobber
a custom class icon."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest

from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


class RotationCropRemapTests(unittest.TestCase):
    """Rotation behaviour under the strict target-ratio lock.

    TRADE-OFF (deliberate): rotating by 90/270 swaps the box's w/h, which
    INVERTS its aspect ratio relative to the target (measured: 0.5625 ->
    1.7778). Since the export resizes the cropped region straight to the
    target with no aspect-preserving parameter (VS R73 `resize.Bicubic` takes
    independent width/height), an inverted-ratio box would be exported
    anisotropically stretched. Ratio correctness therefore wins over
    "the box covers the exact same source pixels after a rotation":
    `set_rotation` re-fits the box to the target ratio around its centre.
    The two tests that previously asserted round-trip pixel identity were
    rewritten accordingly.
    """

    def _widget(self, crop, target=(360, 640)):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        # 此组只验证 compatible 脚本下的旋转/裁剪几何。
        w.supports_editor_capability = lambda _capability: True
        w.video_width, w.video_height = 240, 360
        w.set_target_resolution(*target)
        w.cropbox = list(crop)
        w._rotation = 0
        return w

    def _ar_off(self, w):
        bw, bh = w.cropbox[2], w.cropbox[3]
        return abs(bw / bh - w.target_aspect_ratio) / w.target_aspect_ratio

    def test_rotation_keeps_target_ratio_at_every_angle(self):
        w = self._widget([20, 40, 100, 150])
        for angle in (90, 180, 270, 0):
            w.set_rotation(angle)
            self.assertLess(
                self._ar_off(w), 0.02,
                f"box left the target ratio after rotating to {angle}° "
                f"(box={w.cropbox}, AR={w.cropbox[2]/w.cropbox[3]:.4f}, "
                f"target={w.target_aspect_ratio:.4f})",
            )

    def test_rotation_keeps_box_inside_frame_and_centred(self):
        w = self._widget([20, 40, 100, 150])
        for angle in (90, 180, 270, 0):
            w.set_rotation(angle)
            rw, rh = w._get_rotated_video_size()
            x, y, bw, bh = w.cropbox
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + bw, rw, f"box overruns width at {angle}°")
            self.assertLessEqual(y + bh, rh, f"box overruns height at {angle}°")

    def test_full_turn_returns_to_target_ratio(self):
        # A full turn no longer restores the exact original (non-conforming)
        # box; it restores a target-ratio box at rotation 0.
        w = self._widget([16, 48, 80, 120])
        for angle in (90, 180, 270, 0):
            w.set_rotation(angle)
        self.assertEqual(w._rotation, 0)
        self.assertLess(self._ar_off(w), 0.02)

    def test_rotation_does_not_reset_to_default_center(self):
        w = self._widget([10, 10, 60, 90])
        w.set_rotation(90)
        # The old code called _init_cropbox() here, which recomputed a default
        # centred 75%-of-frame box; the remapped box must NOT equal that.
        w2 = self._widget([0, 0, 0, 0])
        w2.set_rotation(90)
        w2._init_cropbox()
        self.assertNotEqual(w.cropbox, w2.cropbox)

    def test_inverse_helpers_round_trip(self):
        w = self._widget([12, 34, 56, 78])
        for angle in (0, 90, 180, 270):
            w._rotation = angle
            orig = w._cropbox_to_original_coords(12, 34, 56, 78)
            back = w._original_to_rotated_coords(*orig)
            self.assertEqual(back, (12, 34, 56, 78), f"round trip failed at {angle}°")


if __name__ == "__main__":
    unittest.main()
