from __future__ import annotations

import importlib
import json
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER_ROOT = ROOT / "resources" / "vapoursynth" / "python"
CHILD = ROOT / "tests" / "helpers" / "run_vs_contract_case.py"


def _display_module():
    if str(HELPER_ROOT) not in sys.path:
        sys.path.insert(0, str(HELPER_ROOT))
    try:
        return importlib.import_module("assetmaker_vs.display")
    except ModuleNotFoundError as exc:
        raise AssertionError("portable assetmaker_vs.display 尚未实现") from exc


def _run_child(case: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHILD), case],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


class DisplayValidationTests(unittest.TestCase):
    def test_fit_combines_resize_and_rgb_conversion(self):
        display = _display_module()
        source = SimpleNamespace(
            width=3840, height=2160, format=SimpleNamespace(color_family=1)
        )
        resize = SimpleNamespace(Bicubic=Mock(), Spline36=Mock())
        vs = SimpleNamespace(RGB=2, RGB24=3, core=SimpleNamespace(resize=resize))
        with patch.dict(sys.modules, {"vapoursynth": vs}):
            display.to_display_clip(
                source, viewport=(480, 270), zoom_factor=1.0, pan=(0.5, 0.5)
            )
        resize.Bicubic.assert_not_called()
        args, kwargs = resize.Spline36.call_args
        self.assertIs(args[0], source)
        self.assertEqual((kwargs["width"], kwargs["height"]), (480, 270))
        self.assertEqual(kwargs["format"], vs.RGB24)
        self.assertEqual(kwargs["transfer_s"], "srgb")
        self.assertEqual(kwargs["range_s"], "full")

    def test_loaded_graph_reuses_only_last_display_parameters(self):
        from core.vs_runtime.worker_main import _LoadedGraph

        graph = _LoadedGraph(1, {}, {}, None, None, None)
        fields = dict(
            surface="editor", viewport=(480, 270), zoom_factor=1.0,
            pan=(0.5, 0.5), index=0,
        )
        source = object()
        with patch(
            "resources.vapoursynth.python.assetmaker_vs.display.to_display_clip",
            side_effect=lambda *args, **kwargs: object(),
        ) as build:
            first = graph.display_for(source, fields)
            fields["index"] = 12
            self.assertIs(graph.display_for(source, fields), first)
            fields["zoom_factor"] = 2.0
            self.assertIsNot(graph.display_for(source, fields), first)
            fields["zoom_factor"] = 1.0
            self.assertIsNot(graph.display_for(source, fields), first)
            self.assertEqual(build.call_count, 3)

    def test_zoom_and_viewport_bounds_are_explicit(self):
        display = _display_module()
        for zoom in (0.0, 0.009, 100.001):
            with self.subTest(zoom=zoom):
                with self.assertRaises(ValueError):
                    display.to_display_clip(
                        object(),
                        viewport=(480, 270),
                        zoom_factor=zoom,
                        pan=(0.5, 0.5),
                    )
        for viewport in ((0, 270), (480, 0), (-1, 20)):
            with self.subTest(viewport=viewport):
                with self.assertRaises(ValueError):
                    display.to_display_clip(
                        object(),
                        viewport=viewport,
                        zoom_factor=1.0,
                        pan=(0.5, 0.5),
                    )


class DisplayRealSubprocessTests(unittest.TestCase):
    def test_one_percent_fit_and_high_zoom_are_viewport_bounded(self):
        result = _run_child("display_geometry")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["one_percent"], [5, 3])
        self.assertEqual(payload["fit"], [480, 270])
        self.assertEqual(payload["two_x"], [480, 270])
        self.assertEqual(payload["hundred_x"], [480, 270])
        width, height = payload["large_capped"]
        self.assertLessEqual(width, 321)
        self.assertLessEqual(height, 181)

    def test_odd_rgb_center_and_one_pixel_window_do_not_shift(self):
        result = _run_child("display_center")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["dimensions"], [5, 3])
        self.assertTrue(payload["all_red"])
        self.assertTrue(payload["green_zero"])
        self.assertTrue(payload["blue_zero"])
        self.assertEqual(payload["odd_dimensions"], [4, 3])
        self.assertEqual(payload["one_pixel_dimensions"], [4, 3])


if __name__ == "__main__":
    unittest.main()
