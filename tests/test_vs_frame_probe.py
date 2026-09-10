"""M4：帧转换与 metadata probe 的 VS 隔离回归。

VapourSynth 的 DLL 在已加载 PyQt 的解释器内初始化会使本 bundled runtime
异常退出。因此本模块的父测试进程只验证 worker metadata API；所有确实需要
初始化 VS core 的 frame 转换断言都由一个全新的、未导入 PyQt 的子进程执行。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from core.media_tools import MediaToolchain
from resources.vapoursynth.python.assetmaker_vs.runtime_layout import (
    RuntimeLayoutError,
    resolve_runtime_layout,
)


REPO = Path(__file__).resolve().parents[1]
CHILD_PROBE = REPO / "tests" / "helpers" / "run_vs_frame_probe.py"
TC = MediaToolchain.discover(str(REPO))
try:
    R79_LAYOUT = resolve_runtime_layout(REPO)
except RuntimeLayoutError:
    R79_LAYOUT = None
VS_OK = R79_LAYOUT is not None and sys.version_info >= (3, 12)
ENCODE_OK = HAS_CV2 and not TC.missing_for_export()


class _IsolatedVSCase(unittest.TestCase):
    """Run a core-owning assertion in a clean Python child process."""

    def _run_vs_child(self, case: str, *args: str) -> dict[str, object]:
        completed = subprocess.run(
            [sys.executable, str(CHILD_PROBE), case, *args],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
        detail = (
            f"VS child case {case!r} failed (exit {completed.returncode})\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
        self.assertEqual(completed.returncode, 0, detail)
        try:
            return json.loads(completed.stdout.splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            self.fail(f"VS child {case!r} did not return JSON: {detail}\n{exc}")


class ParentProcessIsolationTests(unittest.TestCase):
    def test_parent_never_imports_vapoursynth_for_frame_or_probe_tests(self):
        # This module runs in the same unittest process as PyQt preview tests.
        self.assertNotIn("vapoursynth", sys.modules)
        self.assertNotIn("core.vs_engine", sys.modules)

    def test_frame_probe_file_entry_forces_its_root_ahead_of_discoverable_competitor(self):
        """The VS-free helper must override a competing project in either layout."""
        with tempfile.TemporaryDirectory() as temporary:
            competitor = Path(temporary) / "discoverable-competitor"
            module_sources = {
                "config/__init__.py": "",
                "config/vs_runtime.py": "ORIGIN = 'competitor'\n",
                "core/__init__.py": "",
                "core/vs_runtime/__init__.py": "",
                "core/vs_runtime/vs_loader.py": "ORIGIN = 'competitor'\n",
                # Keep the resource parents as namespace packages, like REPO.
                # A foreign regular parent masks REPO despite path precedence.
                "resources/vapoursynth/python/assetmaker_vs/__init__.py": "",
                "resources/vapoursynth/python/assetmaker_vs/runtime_fingerprint.py": (
                    "ORIGIN = 'competitor'\n"
                ),
            }
            for relative, source in module_sources.items():
                target = competitor / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source, encoding="utf-8")

            preanchor = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    (
                        "from config import vs_runtime; "
                        "from core.vs_runtime import vs_loader; "
                        "from resources.vapoursynth.python.assetmaker_vs "
                        "import runtime_fingerprint; "
                        "assert (vs_runtime.ORIGIN, vs_loader.ORIGIN, "
                        "runtime_fingerprint.ORIGIN) == ('competitor',) * 3"
                    ),
                ],
                cwd=competitor,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=90,
                check=False,
            )
            self.assertEqual(
                preanchor.returncode, 0, preanchor.stderr or preanchor.stdout
            )

            for name, pythonpath in (
                ("root_absent", (str(competitor),)),
                ("root_after_foreign", (str(competitor), str(REPO))),
            ):
                completed = subprocess.run(
                    [sys.executable, str(CHILD_PROBE), "import_origin"],
                    cwd=str(competitor),
                    env=dict(
                        os.environ,
                        PYTHONPATH=os.pathsep.join(pythonpath),
                        PYTHONDONTWRITEBYTECODE="1",
                    ),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=90,
                    check=False,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    f"{name}: {completed.stderr or completed.stdout}",
                )
                origins = json.loads(completed.stdout.splitlines()[-1])
                for key in ("config", "loader", "portable"):
                    self.assertTrue(
                        Path(origins[key]).is_relative_to(REPO),
                        f"{name}: {key} came from a foreign root: {origins[key]}",
                    )


@unittest.skipUnless(VS_OK, "bundled VapourSynth unavailable")
class FrameConversionTests(_IsolatedVSCase):
    def test_planar_rgb_and_stride_conversion_contract(self):
        result = self._run_vs_child("frame_contract")
        self.assertEqual(result["status"], "ok")


@unittest.skipUnless(
    VS_OK and ENCODE_OK, "VapourSynth / encode toolchain unavailable"
)
class LoaderProbeTests(_IsolatedVSCase):
    @classmethod
    def setUpClass(cls):
        from tests.helpers.m5_render_fixture import (
            build_default_render_session,
            encode_render_session,
        )

        cls.d = Path(tempfile.mkdtemp())
        png = cls.d / "m.png"
        cv2.imwrite(str(png), np.full((360, 240, 3), 128, np.uint8))
        cls.mp4 = cls.d / "probe.mp4"
        session = build_default_render_session(
            cls.d / "render-session",
            source_path=png,
            source_kind="image",
            end_frame=45,
        )
        encode_render_session(TC, session, cls.mp4)

    def test_probe_reports_exact_geometry_and_frame_count(self):
        from core.video_processor import probe_video_info

        info = probe_video_info(str(self.mp4))
        self.assertEqual(info.width, 384)
        self.assertEqual(info.height, 640)
        self.assertEqual(info.total_frames, 45)
        self.assertAlmostEqual(info.fps, 30.0, places=6)
        self.assertAlmostEqual(info.duration, 1.5, places=3)
        self.assertNotIn("vapoursynth", sys.modules)

    def test_video_processor_prefers_worker_probe(self):
        from core.video_processor import VideoProcessor

        info = VideoProcessor().get_video_info(str(self.mp4))
        self.assertIsNotNone(info)
        self.assertEqual((info.width, info.height, info.total_frames), (384, 640, 45))
        self.assertNotIn("vapoursynth", sys.modules)

    def test_real_frame_round_trips_to_bgr_in_isolated_loader_process(self):
        result = self._run_vs_child("real_frame", str(self.mp4))
        self.assertEqual(result["shape"], [640, 384, 3])
        self.assertGreater(result["mean"], 100)

    def test_missing_file_returns_none(self):
        from core.video_processor import VideoProcessor

        self.assertIsNone(VideoProcessor().get_video_info(str(self.d / "nope.mp4")))


if __name__ == "__main__":
    unittest.main()
