"""RIFE 图参数与时间轴契约；无需 Windows/Vulkan GPU 的模拟测试。"""

from __future__ import annotations

import ast
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "resources" / "vapoursynth" / "default_pipeline.vpy"


def _clip(fps: Fraction = Fraction(25), frames: int = 11):
    return SimpleNamespace(fps_num=fps.numerator, fps_den=fps.denominator, num_frames=frames)


def _rife_function(core, model_path, *, enabled=True, target_fps=60):
    tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "interpolate_timeline"
    )
    namespace = {
        "core": core, "vs": SimpleNamespace(RGBS=901), "Path": Path,
        "Fraction": Fraction, "RIFE_ENABLED": enabled,
        "RIFE_TARGET_FPS": target_fps, "RIFE_MODEL_PATH": model_path,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(PIPELINE), "exec"), namespace)
    return namespace["interpolate_timeline"]


class RifeTimelineTests(unittest.TestCase):
    def test_25_and_ntsc_sources_use_real_target_rate_and_floor_frame_count(self):
        with TemporaryDirectory() as directory:
            for source_fps in (Fraction(25), Fraction(24000, 1001), Fraction(30000, 1001)):
                with self.subTest(fps=source_fps):
                    source = _clip(source_fps)
                    rgb = object()
                    result = _clip(Fraction(60), int(source.num_frames * 60 / source_fps))
                    rife = Mock(return_value=result)
                    resize = Mock(return_value=rgb)
                    core = SimpleNamespace(resize=SimpleNamespace(Lanczos=resize), rife=SimpleNamespace(RIFE=rife))
                    interpolate = _rife_function(core, Path(directory))
                    self.assertIs(interpolate(source, kind="video"), result)
                    resize.assert_called_once_with(source, format=901, range_s="full", dither_type="none")
                    rife.assert_called_once_with(rgb, fps_num=60, fps_den=1, model_path=str(Path(directory).resolve()))
                    # floor(N * target / source) 的时长损失小于一个目标帧。
                    duration_delta = source.num_frames / source_fps - Fraction(result.num_frames, 60)
                    self.assertGreaterEqual(duration_delta, 0)
                    self.assertLess(duration_delta, Fraction(1, 60))

    def test_disabled_images_single_frames_and_at_least_60_fps_need_no_gpu_or_models(self):
        core = SimpleNamespace(resize=SimpleNamespace(Lanczos=Mock()))
        missing_model = ROOT / "absent-rife-model"
        cases = (
            (_clip(), "video", False), (_clip(), "image", True),
            (_clip(frames=1), "video", True), (_clip(Fraction(60)), "video", True),
            (_clip(Fraction(120)), "video", True), (_clip(Fraction(60000, 1001)), "image", True),
        )
        for source, kind, enabled in cases:
            with self.subTest(kind=kind, enabled=enabled, fps=source.fps_num / source.fps_den):
                self.assertIs(_rife_function(core, missing_model, enabled=enabled)(source, kind=kind), source)
        core.resize.Lanczos.assert_not_called()

    def test_missing_plugin_and_model_are_clear_errors(self):
        with TemporaryDirectory() as directory:
            core = SimpleNamespace()
            with self.assertRaisesRegex(RuntimeError, "native_plugin_dirs"):
                _rife_function(core, directory)(_clip(), kind="video")
            core.rife = SimpleNamespace(RIFE=Mock())
            with self.assertRaisesRegex(RuntimeError, "模型目录不存在"):
                _rife_function(core, Path(directory) / "missing")(_clip(), kind="video")
            core.rife.RIFE.assert_not_called()

    def test_gpu_initialization_error_never_falls_back_to_source(self):
        with TemporaryDirectory() as directory:
            core = SimpleNamespace(
                resize=SimpleNamespace(Lanczos=Mock(return_value=object())),
                rife=SimpleNamespace(RIFE=Mock(side_effect=RuntimeError("no Vulkan device"))),
            )
            with self.assertRaisesRegex(RuntimeError, "Vulkan GPU.*no Vulkan device"):
                _rife_function(core, directory)(_clip(), kind="video")

    def test_bad_plugin_rate_or_count_is_rejected(self):
        with TemporaryDirectory() as directory:
            source = _clip()
            for result in (_clip(Fraction(25), 26), _clip(Fraction(60), 11)):
                with self.subTest(result=result):
                    core = SimpleNamespace(
                        resize=SimpleNamespace(Lanczos=Mock(return_value=object())),
                        rife=SimpleNamespace(RIFE=Mock(return_value=result)),
                    )
                    with self.assertRaisesRegex(RuntimeError, "目标帧率插帧契约"):
                        _rife_function(core, directory)(source, kind="video")

    def test_invalid_target_rate_is_rejected(self):
        for target in (0, -1, True, 60.0):
            with self.subTest(target=target), self.assertRaisesRegex(RuntimeError, "正整数"):
                _rife_function(SimpleNamespace(), ROOT, target_fps=target)(_clip(), kind="video")


if __name__ == "__main__":
    unittest.main()
