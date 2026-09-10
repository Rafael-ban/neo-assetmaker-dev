from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.vs_runtime.script_header import parse_script_header


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PIPELINE = ROOT / "resources" / "vapoursynth" / "default_pipeline.vpy"
CHILD = ROOT / "tests" / "helpers" / "run_vs_contract_case.py"


def _run_child(case: str, *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHILD), case],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


class DefaultPipelineHeaderTests(unittest.TestCase):
    def test_default_header_declares_the_full_compatible_contract(self):
        self.assertTrue(
            DEFAULT_PIPELINE.is_file(), "默认 compatible pipeline 尚未实现"
        )
        header = parse_script_header(DEFAULT_PIPELINE)

        self.assertEqual(header.api_version, 1)
        self.assertEqual(header.mode, "compatible")
        self.assertEqual(header.editor_output, 1)
        self.assertEqual(
            header.capabilities,
            ("source", "trim", "crop", "rotation", "resolution", "image_loop"),
        )
        self.assertEqual(
            header.requires,
            ("lsmas.LWLibavSource", "imwri.Read"),
        )


class DefaultPipelineSemanticTests(unittest.TestCase):
    def test_default_script_orders_rotation_loop_trim_crop_and_padding(self):
        """默认 compatible 脚本保留旧 goldens 覆盖的渲染语义，不锁定字节。"""
        source = DEFAULT_PIPELINE.read_text(encoding="utf-8")
        ordered_steps = (
            "clip = rotate(clip, transform[\"rotation\"])",
            "clip = core.std.Loop(clip, times=virtual_count)[:virtual_count]",
            "clip.set_output(1)",
            'clip = clip[timeline["start_frame"] : timeline["end_frame"]]',
            "clip = crop_safely(",
            "clip = core.resize.Bicubic(",
            "clip = core.std.AddBorders(",
            "clip.set_output(0)",
        )
        positions = [source.index(step) for step in ordered_steps]
        self.assertEqual(positions, sorted(positions))


class DefaultPipelineRealSubprocessTests(unittest.TestCase):
    def test_contract_file_entry_forces_its_root_ahead_of_discoverable_competitor(self):
        """A real helper entry must override a foreign project in both path layouts."""
        with TemporaryDirectory() as temporary:
            competitor = Path(temporary) / "discoverable-competitor"
            module_sources = {
                "config/__init__.py": "",
                "config/vs_runtime.py": "ORIGIN = 'competitor'\n",
                "core/__init__.py": "",
                "core/vs_runtime/__init__.py": "",
                "core/vs_runtime/job.py": "class RationalFPS: pass\n",
                "core/vs_runtime/vs_loader.py": "ORIGIN = 'competitor'\n",
                # Keep the resource parents as namespace packages, like ROOT.
                # A foreign regular parent masks ROOT despite path precedence.
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
                ("root_after_foreign", (str(competitor), str(ROOT))),
            ):
                result = subprocess.run(
                    [sys.executable, str(CHILD), "import_origin"],
                    cwd=competitor,
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
                    result.returncode,
                    0,
                    f"{name}: {result.stderr or result.stdout}",
                )
                origins = json.loads(result.stdout)
                for key in ("config", "loader", "portable"):
                    self.assertTrue(
                        Path(origins[key]).is_relative_to(ROOT),
                        f"{name}: {key} came from a foreign root: {origins[key]}",
                    )

    def test_image_loops_full_editor_timeline_before_nonzero_trim(self):
        result = _run_child("default_image")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        output0 = payload["output0"]
        output1 = payload["output1"]
        self.assertEqual(
            (output0["width"], output0["height"]), (384, 640)
        )
        self.assertEqual(output0["num_frames"], 5)
        self.assertEqual(output0["fps"], [30, 1])
        self.assertEqual(output0["format"], "YUV420P8")
        self.assertEqual(
            output0["props"],
            {
                "_Matrix": 6,
                "_Transfer": 6,
                "_Primaries": 6,
                "_Range": 0,
            },
        )
        self.assertEqual(
            (output1["width"], output1["height"]), (6, 8)
        )
        self.assertEqual(output1["num_frames"], 9)
        self.assertEqual(output1["fps"], [30, 1])
        self.assertEqual(payload["runner"]["returncode"], 0)
        self.assertIn("Width: 384", payload["runner"]["stdout"])
        self.assertGreater(payload["encoded"]["size"], 0)

    def test_video_bootstrap_and_resolved_jobs_share_full_editor_output(self):
        result = _run_child("default_video")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(
            (payload["bootstrap0"]["width"], payload["bootstrap0"]["height"]),
            (720, 1080),
        )
        self.assertEqual(payload["bootstrap0"]["num_frames"], 8)
        self.assertEqual(payload["bootstrap0"]["fps"], [30000, 1001])
        self.assertEqual(payload["resolved0"]["num_frames"], 5)
        self.assertEqual(payload["resolved0"]["fps"], [30000, 1001])
        for key in ("bootstrap1", "resolved1"):
            self.assertEqual(
                (payload[key]["width"], payload[key]["height"]), (8, 12)
            )
            self.assertEqual(payload[key]["num_frames"], 8)
            self.assertEqual(payload[key]["fps"], [30000, 1001])
        self.assertEqual(
            payload["resolved0"]["props"],
            {
                "_Matrix": 6,
                "_Transfer": 6,
                "_Primaries": 6,
                "_Range": 0,
            },
        )

    def test_true_709_untagged_source_does_not_flip_at_p7_crop_height(self):
        result = _run_child("default_p7", timeout=240)

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["source_matrix"], 2)
        self.assertEqual(len(payload["digests"]), 2)
        self.assertTrue(payload["equal"], payload["digests"])


if __name__ == "__main__":
    unittest.main()
