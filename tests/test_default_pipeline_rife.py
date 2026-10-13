"""RIFE 图参数与时间轴契约；无需 Windows/Vulkan GPU 的模拟测试。"""

from __future__ import annotations

import ast
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from core.vs_runtime.script_header import parse_script_header


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "resources" / "vapoursynth" / "rife_pipeline.vpy"


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


class RifeTemplateTests(unittest.TestCase):
    def test_optional_header_declares_rife_and_enables_60_fps(self):
        header = parse_script_header(PIPELINE)
        self.assertEqual(header.api_version, 1)
        self.assertEqual(header.mode, "compatible")
        self.assertEqual(header.editor_output, 1)
        self.assertEqual(
            header.capabilities,
            ("source", "trim", "crop", "rotation", "resolution", "image_loop"),
        )
        self.assertEqual(header.requires, ("lsmas.LWLibavSource", "imwri.Read", "rife.RIFE"))
        tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
        settings = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in ("RIFE_ENABLED", "RIFE_TARGET_FPS")
        }
        self.assertEqual(settings, {"RIFE_ENABLED": True, "RIFE_TARGET_FPS": 60})
        self.assertFalse(any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("LoadPlugin", "LoadAllPlugins")
            for node in ast.walk(tree)
        ), "Native plugins must be loaded by the runtime override policy")

    def test_copied_script_uses_supplied_runtime_root_for_models(self):
        tree = ast.parse(PIPELINE.read_text(encoding="utf-8"))
        setting = next(
            node for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "RIFE_MODEL_PATH"
        )
        with TemporaryDirectory() as directory:
            runtime_root = Path(directory) / "runtime"
            helper_root = Path(directory) / "fallback-runtime"
            helper = helper_root / "resources/vapoursynth/python/assetmaker_vs/job_api.py"
            for script in (
                runtime_root / "resources/vapoursynth/rife_pipeline.vpy",
                Path(directory) / "project/vs/rife_pipeline.vpy",
                Path(directory) / "external/custom.vpy",
            ):
                for environment, expected_root in (
                    ({"ASSETMAKER_VS_APP_DIR": str(runtime_root)}, runtime_root),
                    ({}, helper_root),
                ):
                    with self.subTest(script=script, environment=environment):
                        namespace = {
                            "Path": Path, "os": SimpleNamespace(environ=environment),
                            "__file__": str(script), "job_api": SimpleNamespace(__file__=str(helper)),
                        }
                        exec(compile(ast.Module(body=[setting], type_ignores=[]), str(script), "exec"), namespace)
                        self.assertEqual(
                            namespace["RIFE_MODEL_PATH"],
                            expected_root.resolve() / "tools/vs-rife/rife/models/rife-v4.6_ensembleFalse",
                        )

    def test_common_editor_colour_and_output_stages_match_default(self):
        default = ast.parse((PIPELINE.parent / "default_pipeline.vpy").read_text(encoding="utf-8"))
        rife = ast.parse(PIPELINE.read_text(encoding="utf-8"))
        shared_functions = ("rotate", "crop_safely", "process_source", "fill_missing_source_colour")
        for name in shared_functions:
            with self.subTest(function=name):
                default_fn = next(node for node in default.body if isinstance(node, ast.FunctionDef) and node.name == name)
                rife_fn = next(node for node in rife.body if isinstance(node, ast.FunctionDef) and node.name == name)
                self.assertEqual(ast.dump(default_fn), ast.dump(rife_fn))
        def output_stages(tree):
            start = next(index for index, node in enumerate(tree.body)
                         if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                         and node.targets[0].id == "source")
            return [ast.dump(node) for node in tree.body[start:]
                    if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                            and isinstance(node.value.func, ast.Name)
                            and node.value.func.id == "interpolate_timeline")]
        self.assertEqual(output_stages(default), output_stages(rife))
        source = PIPELINE.read_text(encoding="utf-8")
        self.assertLess(source.index("clip = rotate("), source.index("clip = interpolate_timeline("))
        self.assertLess(source.index("clip = interpolate_timeline("), source.index("clip.set_output(1)"))
        self.assertLess(source.index("clip.set_output(1)"), source.index('clip = clip[timeline["start_frame"]'))


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

    def test_unknown_or_nonpositive_source_rate_is_a_clear_error(self):
        for numerator, denominator in ((0, 1), (25, 0), (-1, 1)):
            source = SimpleNamespace(fps_num=numerator, fps_den=denominator, num_frames=11)
            with self.subTest(fps=(numerator, denominator)), self.assertRaisesRegex(RuntimeError, "固定的正帧率"):
                _rife_function(SimpleNamespace(), ROOT)(source, kind="video")

    def test_invalid_target_rate_is_rejected(self):
        for target in (0, -1, True, 60.0):
            with self.subTest(target=target), self.assertRaisesRegex(RuntimeError, "正整数"):
                _rife_function(SimpleNamespace(), ROOT, target_fps=target)(_clip(), kind="video")


if __name__ == "__main__":
    unittest.main()
