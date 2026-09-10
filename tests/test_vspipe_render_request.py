"""M5 第一批：固定 runner argv 与编码 VUI 的最小契约。"""

from __future__ import annotations

import unittest
import json
import os
import tempfile
from pathlib import Path
from unittest import mock


class VSPipeRenderRequestTests(unittest.TestCase):
    def test_runner_argv_preserves_special_paths_as_individual_arguments(self):
        from config.vs_runtime import VSRuntimeConfig
        from core.media_pipeline import VSPipeRenderRequest, build_vspipe_command

        request = VSPipeRenderRequest(
            runner_path="D:/工具/Asset Maker/assetmaker_runner.vpy",
            script_path="D:/用户 脚本/黍 & '测试'/pipeline.vpy",
            job_path="D:/导出 工作区/任务 & '一'/job-42.json",
            expected_job_sha256="b" * 64,
            api_version=1,
            mode="compatible",
            app_dir="D:/应用 根",
            runtime=VSRuntimeConfig(),
            runtime_fingerprint="a" * 64,
        )

        command = build_vspipe_command("D:/工具/VSPipe.exe", request)

        self.assertEqual(
            command,
            [
                "D:/工具/VSPipe.exe",
                "-c",
                "y4m",
                "-p",
                "--arg",
                "assetmaker_job=D:/导出 工作区/任务 & '一'/job-42.json",
                "--arg",
                "expected_job_sha256=" + "b" * 64,
                "--arg",
                "assetmaker_script=D:/用户 脚本/黍 & '测试'/pipeline.vpy",
                "--arg",
                "assetmaker_api=1",
                "--arg",
                "assetmaker_mode=compatible",
                "D:/工具/Asset Maker/assetmaker_runner.vpy",
                "-",
            ],
        )

    def test_x264_command_requires_explicit_vui(self):
        from core.media_pipeline import build_x264_command

        with self.assertRaises(TypeError):
            build_x264_command("x264-7mod.exe", "D:/输出/out.264")

    def test_vspipe_environment_disables_implicit_native_autoload(self):
        """C1：native 目录留在 frozen runtime，绝不交给 R79 环境 autoload。"""
        from config.vs_runtime import (
            CoreConfig,
            PluginConfig,
            ScriptConfig,
            VSRuntimeConfig,
            WorkerConfig,
        )
        from core.media_pipeline import build_vspipe_render_env
        from resources.vapoursynth.python.assetmaker_vs.runtime_fingerprint import (
            canonical_runtime_json_bytes,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            from tests.test_vs_runtime_layout import _build_r79_fixture

            runtime_root = _build_r79_fixture(root)
            package = runtime_root / "Lib" / "site-packages" / "vapoursynth"
            vspipe = package / "vspipe.exe"
            native_dirs = (
                root / "运行时" / "原生 插件 一",
                root / "运行时" / "原生 插件 二",
            )
            python_dirs = (
                root / "运行时" / "Python 插件 一",
                root / "运行时" / "Python 插件 二",
            )
            for directory in (*native_dirs, *python_dirs):
                directory.mkdir(parents=True)
            runtime = VSRuntimeConfig(
                worker=WorkerConfig(12001, 12002, 12003),
                core=CoreConfig(num_threads=7, max_cache_size_mb=321),
                plugins=PluginConfig(
                    native_plugin_dirs=tuple(map(str, native_dirs)),
                    python_module_dirs=tuple(map(str, python_dirs)),
                ),
                scripts=ScriptConfig(global_script_path=str(root / "全局.vpy")),
            )
            with mock.patch.dict(
                os.environ,
                {
                    "VAPOURSYNTH_EXTRA_PLUGIN_PATH": "legacy-native",
                    "ASSETMAKER_VS_PYTHON_DIRS_JSON": '["legacy-python"]',
                    "ASSETMAKER_VS_RUNTIME_CONFIG_JSON": '{"legacy":true}',
                    "ASSETMAKER_VS_RUNTIME_FINGERPRINT": "legacy-fingerprint",
                    "ASSETMAKER_VS_APP_DIR": "legacy-app",
                    "PYTHONDONTWRITEBYTECODE": "0",
                },
                clear=False,
            ), mock.patch(
                "core.media_pipeline.build_media_subprocess_env",
                side_effect=AssertionError("legacy runtime read"),
            ):
                env = build_vspipe_render_env(
                    str(vspipe),
                    app_dir=str(root),
                    runtime=runtime,
                    expected_fingerprint="a" * 64,
                )

            self.assertEqual(
                env["ASSETMAKER_VS_RUNTIME_CONFIG_JSON"].encode("utf-8"),
                canonical_runtime_json_bytes(runtime.to_dict()),
            )
            self.assertEqual(
                env["VAPOURSYNTH_EXTRA_PLUGIN_PATH"],
                "",
            )
            self.assertEqual(
                env["ASSETMAKER_VS_PYTHON_DIRS_JSON"],
                json.dumps(
                    list(map(str, python_dirs)),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            self.assertEqual(env["ASSETMAKER_VS_RUNTIME_FINGERPRINT"], "a" * 64)
            self.assertEqual(env["ASSETMAKER_VS_APP_DIR"], str(root))
            self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")

            with self.subTest(
                "empty plugin dirs clear inherited legacy values"
            ), mock.patch.dict(
                os.environ,
                {
                    "VAPOURSYNTH_EXTRA_PLUGIN_PATH": "legacy-native",
                    "ASSETMAKER_VS_PYTHON_DIRS_JSON": '["legacy-python"]',
                },
                clear=False,
            ):
                empty_env = build_vspipe_render_env(
                    str(vspipe),
                    app_dir=str(root),
                    runtime=VSRuntimeConfig(),
                    expected_fingerprint="b" * 64,
                )
            self.assertEqual(empty_env["VAPOURSYNTH_EXTRA_PLUGIN_PATH"], "")
            self.assertEqual(empty_env["ASSETMAKER_VS_PYTHON_DIRS_JSON"], "[]")

    def test_vspipe_environment_rejects_external_runtime_root(self):
        from config.vs_runtime import VSRuntimeConfig
        from core.media_pipeline import build_vspipe_render_env

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            from tests.test_vs_runtime_layout import _build_r79_fixture

            _build_r79_fixture(root)
            external_vspipe = root / "external" / "VSPipe.exe"
            external_vspipe.parent.mkdir()
            external_vspipe.touch()

            with self.assertRaisesRegex(ValueError, "R79 runtime layout"):
                build_vspipe_render_env(
                    str(external_vspipe),
                    app_dir=str(root),
                    runtime=VSRuntimeConfig(),
                    expected_fingerprint="a" * 64,
                )


if __name__ == "__main__":
    unittest.main()
