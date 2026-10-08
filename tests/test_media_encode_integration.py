"""Real-encode integration tests for the asset-making pipeline (M1).

These run the bundled VapourSynth -> x264 -> mp4box toolchain and decode the output
to assert on pixels. They are skipped automatically when tools/media (or cv2) is
absent, so they add no burden to a minimal CI.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from core.media_tools import MediaToolchain
from core.media_pipeline import MediaEncoder
from tests.helpers.m5_render_fixture import (
    build_default_render_session,
    preflight_encode_request,
)

REPO = Path(__file__).resolve().parent.parent
TC = MediaToolchain.discover(str(REPO))
TOOLS_OK = HAS_CV2 and not TC.missing_for_export()


def _marker(path, w=240, h=360):
    img = np.zeros((h, w, 3), np.uint8)
    img[0:h // 4, :] = (255, 255, 255)   # bright bar across the top (asymmetric)
    cv2.imwrite(str(path), img)
    return Path(path)


def _source(marker, out, frames=20, w=240, h=360):
    image = cv2.imread(str(marker))
    writer = cv2.VideoWriter(
        str(out), cv2.VideoWriter_fourcc(*"mp4v"), 30, (w, h)
    )
    if not writer.isOpened():
        raise RuntimeError("OpenCV cannot create the real-video source fixture")
    try:
        for _ in range(frames):
            writer.write(image)
    finally:
        writer.release()
    return Path(out)


def _decode0(mp4):
    cap = cv2.VideoCapture(str(mp4))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"decode failed: {mp4}")
    return frame


def _export(src, out, **kw):
    session = build_default_render_session(
        Path(out).with_suffix(""),
        source_path=Path(src),
        source_kind="image" if kw.get("is_image", False) else "video",
        end_frame=kw.get("end", 15),
        crop=kw.get("cropbox", (0, 0, 0, 0)),
        rotation=kw.get("rotation", 0),
    )
    request, fps, vui = preflight_encode_request(session)
    MediaEncoder(TC).encode_vpy_to_mp4(request, str(out), fps, vui=vui)
    return Path(out)


def _dominant(frame):
    h, w = frame.shape[:2]
    g = frame.mean(axis=2)
    d = {"top": g[:h // 3].mean(), "bottom": g[2 * h // 3:].mean(),
         "left": g[:, :w // 3].mean(), "right": g[:, 2 * w // 3:].mean()}
    return max(d, key=d.get)


@unittest.skipUnless(TOOLS_OK, "media toolchain (tools/media) or cv2 unavailable")
class MediaEncodeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp())
        cls.src = _source(_marker(cls.d / "m.png"), cls.d / "src.mp4")
        cls.src0 = _decode0(cls.src)

    def test_rotation_matches_cv2(self):
        """M1a: exported 90/180/270 rotation matches cv2.rotate (not the mirrored Transpose)."""
        cvmap = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
                 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
        for r in (90, 180, 270):
            out = _export(self.src, self.d / f"r{r}.mp4", rotation=r)
            self.assertEqual(
                _dominant(_decode0(out)),
                _dominant(cv2.rotate(self.src0, cvmap[r])),
                f"rotation {r} does not match cv2",
            )

    def test_odd_and_out_of_bounds_crop_encodes(self):
        """M1b: odd + oversized crop is clamped/aligned instead of aborting the encode."""
        out = _export(self.src, self.d / "crop.mp4", cropbox=(11, 21, 999, 999))
        self.assertEqual(_decode0(out).shape, (640, 360, 3))

    def test_image_loop_encodes(self):
        """M1f: image-loop RGB->YUV no longer fails on a missing colour matrix."""
        out = _export(_marker(self.d / "im.png"), self.d / "imgloop.mp4", is_image=True, end=20)
        cap = cv2.VideoCapture(str(out))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        self.assertGreaterEqual(n, 18)

    def test_long_encode_does_not_hang(self):
        """M1d: a long encode completes (stderr drained) instead of dead-locking."""
        src = _source(_marker(self.d / "lm.png"), self.d / "lsrc.mp4", frames=180)
        enc = MediaEncoder(TC)
        session = build_default_render_session(
            self.d / "long-session", source_path=src, end_frame=160
        )
        request, _fps, vui = preflight_encode_request(session)
        res = {}
        t = threading.Thread(
            target=lambda: res.update(
                r=enc._run_encode_pipeline(
                    request, str(self.d / "long.264"), vui
                )
            )
        )
        t.start()
        t.join(timeout=180)
        if t.is_alive():
            enc.terminate_active_processes()
            self.fail("long encode hung (pipe-buffer deadlock)")
        self.assertEqual(res["r"]["vspipe_returncode"], 0)
        self.assertEqual(res["r"]["x264_returncode"], 0)
        self.assertGreater(len(res["r"]["stderr"]), 0)


if __name__ == "__main__":
    unittest.main()
