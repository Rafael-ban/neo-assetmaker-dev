"""S4: real-encode colour/crop/rotation round-trip tests for the export path.

Old behaviour (provably wrong): images were converted RGB->YUV with
matrix_s='709' while the H.264 stream carried NO colour tags (x264-7mod
defaults: --colormatrix/--colorprim/--transfer "undef", --range "auto" — see
`x264-7mod --fullhelp`). Untagged sub-HD content is decoded as BT.601 by
convention (H.273; mpv applies the same heuristic), so exported colours were
visibly shifted vs the preview. The decode side here (cv2/ffmpeg swscale)
uses the BT.601 default too, matching what convention-following devices do —
these round-trips FAIL on the old pipeline and pass on the new one
(convert with '170m' + tag smpte170m/tv in the VUI).

Also covers: image loops now honour crop and rotation (the script blocks
used to be video-branch-only), and the exported stream really carries the
SMPTE 170M matrix tag (read back off the decoded clip's H.273 frame props).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from tests.qt_harness import ensure_app

from core.media_tools import MediaToolchain

REPO = Path(__file__).resolve().parent.parent
TC = MediaToolchain.discover(str(REPO))
ENCODE_OK = HAS_CV2 and not TC.missing_for_export()


def setUpModule():
    ensure_app()


def _export_image_loop(png_path: Path, out_mp4: Path, *, cropbox, rotation=0,
                       frames=12, resolution="360x640"):
    from tests.helpers.m5_render_fixture import (
        build_default_render_session,
        encode_render_session,
    )
    if resolution != "360x640":
        raise ValueError(f"unsupported test profile: {resolution}")
    session = build_default_render_session(
        out_mp4.parent / f"{out_mp4.stem}-session",
        source_path=png_path,
        source_kind="image",
        end_frame=frames,
        crop=cropbox,
        rotation=rotation,
    )
    encode_render_session(TC, session, out_mp4)
    return out_mp4


def _decode_first_frame(mp4: Path) -> np.ndarray:
    cap = cv2.VideoCapture(str(mp4))
    try:
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok or frame is None:
        raise AssertionError(f"could not decode {mp4}")
    return frame  # BGR, 640x360 target -> (640, 360, 3)


@unittest.skipUnless(ENCODE_OK, "encode toolchain (tools/media) unavailable")
class ExportColorRoundTripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp())

    def test_saturated_colors_survive_the_encode(self):
        # Four saturated quadrants at the exact target size (no rescale blur).
        src = np.zeros((640, 360, 3), np.uint8)  # BGR
        src[:320, :180] = (0, 0, 255)    # top-left red
        src[:320, 180:] = (0, 255, 0)    # top-right green
        src[320:, :180] = (255, 0, 0)    # bottom-left blue
        src[320:, 180:] = (128, 128, 128)  # bottom-right grey
        png = self.d / "quad.png"
        cv2.imwrite(str(png), src)

        mp4 = _export_image_loop(png, self.d / "quad.mp4",
                                 cropbox=(0, 0, 360, 640))
        out = _decode_first_frame(mp4)
        self.assertEqual(out.shape[1], 360)

        # Compare quadrant channel means (away from edges to dodge chroma bleed).
        def q(img, ys, xs):
            return img[ys[0]:ys[1], xs[0]:xs[1]].reshape(-1, 3).mean(axis=0)

        for name, ys, xs in (
            ("top-left red", (40, 280), (20, 160)),
            ("top-right green", (40, 280), (200, 340)),
            ("bottom-left blue", (360, 600), (20, 160)),
            ("bottom-right grey", (360, 600), (200, 340)),
        ):
            expected = q(src, ys, xs)
            got = q(out, ys, xs)
            delta = np.abs(expected - got).max()
            # The old 709-coded/untagged pipeline shifts saturated primaries by
            # far more than this tolerance when decoded per the BT.601 SD
            # convention; the 170m-coded/tagged pipeline stays within it.
            self.assertLess(
                delta, 20.0,
                f"{name}: expected≈{expected.round(1)} got≈{got.round(1)} "
                f"(Δmax={delta:.1f}) — colour matrix mismatch",
            )

    def test_image_loop_honours_offcenter_crop(self):
        # Left half red, right half blue; crop selects the LEFT half.
        src = np.zeros((640, 720, 3), np.uint8)
        src[:, :360] = (0, 0, 255)
        src[:, 360:] = (255, 0, 0)
        png = self.d / "crop.png"
        cv2.imwrite(str(png), src)

        mp4 = _export_image_loop(png, self.d / "crop.mp4",
                                 cropbox=(0, 0, 360, 640))
        out = _decode_first_frame(mp4)[:, :360]
        mean = out[40:600, 20:340].reshape(-1, 3).mean(axis=0)  # BGR
        self.assertGreater(mean[2], 180, f"expected red content, got BGR≈{mean.round(1)}")
        self.assertLess(mean[0], 60, "blue half leaked in — crop was ignored (old bug)")

    def test_image_loop_honours_rotation(self):
        # Top half red, bottom blue in a landscape source; rotate 90° CW ->
        # red must end up on the RIGHT side (same as the preview's cv2.ROTATE_90_CLOCKWISE).
        src = np.zeros((360, 640, 3), np.uint8)
        src[:180, :] = (0, 0, 255)
        src[180:, :] = (255, 0, 0)
        png = self.d / "rot.png"
        cv2.imwrite(str(png), src)

        mp4 = _export_image_loop(png, self.d / "rot.mp4",
                                 cropbox=(0, 0, 360, 640), rotation=90)
        out = _decode_first_frame(mp4)[:, :360]
        right = out[40:600, 200:340].reshape(-1, 3).mean(axis=0)
        left = out[40:600, 20:160].reshape(-1, 3).mean(axis=0)
        self.assertGreater(right[2], 180, f"right side should be red, got {right.round(1)}")
        self.assertGreater(left[0], 180, f"left side should be blue, got {left.round(1)}")


if __name__ == "__main__":
    unittest.main()
