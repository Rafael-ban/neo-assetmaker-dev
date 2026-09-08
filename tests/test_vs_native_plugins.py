"""F3-P3：配置的 native 插件目录必须以显式、可验证策略加载。"""

from __future__ import annotations

import hashlib
import tempfile
import types
import unittest
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class _Plugin:
    def __init__(self, namespace: str, plugin_path: str | None, functions=()):
        self.namespace = namespace
        self.plugin_path = plugin_path
        for name in functions:
            setattr(self, name, lambda: None)


class _Core:
    def __init__(
        self,
        by_directory: dict[Path, tuple[_Plugin, ...]],
        warnings_by_directory: dict[
            Path, tuple[str | tuple[str, str], ...]
        ]
        | None = None,
    ):
        self._by_directory = by_directory
        self._warnings_by_directory = warnings_by_directory or {}
        self._plugins: list[_Plugin] = []
        self.loaded_paths: list[Path] = []
        self.removed_log_handles: list[int] = []
        self._log_handlers: dict[int, object] = {}
        self._next_log_handle = 1
        self.std = types.SimpleNamespace(LoadAllPlugins=self._load_all)

    def _load_all(self, *, path: str) -> None:
        directory = Path(path).resolve()
        self.loaded_paths.append(directory)
        existing = {plugin.namespace for plugin in self._plugins}
        for plugin in self._by_directory.get(directory, ()):
            if plugin.namespace not in existing:
                self._plugins.append(plugin)
                existing.add(plugin.namespace)
                setattr(self, plugin.namespace, plugin)

        for event in self._warnings_by_directory.get(directory, ()):
            if isinstance(event, tuple):
                level, message = event
            else:
                level, message = "warning", event
            for handler in tuple(self._log_handlers.values()):
                handler(level, message)

    def add_log_handler(self, handler):
        handle = self._next_log_handle
        self._next_log_handle += 1
        self._log_handlers[handle] = handler
        return handle

    def remove_log_handler(self, handle) -> None:
        self.removed_log_handles.append(handle)
        self._log_handlers.pop(handle)

    def plugins(self):
        return iter(self._plugins)


class NativePluginPolicyTests(unittest.TestCase):
    def _api(self):
        try:
            from resources.vapoursynth.python.assetmaker_vs import native_plugins
        except ImportError as error:
            self.fail(f"P3 native plugin policy helper 尚未实现: {error}")
        return native_plugins

    def test_loads_normalized_directories_in_order_and_keeps_dependency_dlls_valid(self):
        """去重/顺序错误，或把未注册依赖 DLL 判成插件时应失败。"""
        configure_native_plugins = self._api().configure_native_plugins

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "插件 空格 一"
            second = root / "插件 中文 二"
            first.mkdir()
            second.mkdir()
            # 这不是 VS plugin，真实目录常包含这类相邻依赖 DLL。
            (second / "avcodec-60.dll").write_bytes(b"dependency")
            core = _Core(
                {
                    first.resolve(): (_Plugin("alpha", str(first / "alpha.dll"), ("Open",)),),
                    second.resolve(): (_Plugin("beta", str(second / "beta.dll"), ("Read",)),),
                }
            )

            state = configure_native_plugins(
                core,
                (str(first), str(first / "."), str(second)),
            )

            self.assertEqual(core.loaded_paths, [first.resolve(), second.resolve()])
            self.assertEqual(state.configured_dirs, (first.resolve(), second.resolve()))

    def test_reports_required_callable_with_unconfigured_external_source(self):
        """外部同 namespace 抢先注册时，不能只因 callable 存在就通过。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        verify_native_plugin_requirements = api.verify_native_plugin_requirements

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = root / "配置插件"
            configured.mkdir()
            other = root / "旧插件"
            other.mkdir()
            core = _Core({})
            old = _Plugin("source", str(other / "source.dll"), ("Open",))
            core._plugins.append(old)
            core.source = old
            state = configure_native_plugins(core, (str(configured),))

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.source_mismatch"
            ):
                verify_native_plugin_requirements(core, state, ("source.Open",))

    def test_reports_same_plugin_dll_in_later_directory_as_conflict(self):
        """LoadAllPlugins 静默跳过时，同名已注册 DLL 的第二来源仍须拒绝。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "目录 一"
            second = root / "目录 二"
            first.mkdir()
            second.mkdir()
            (first / "source.dll").write_bytes(b"first")
            (second / "source.dll").write_bytes(b"second")
            core = _Core(
                {
                    first.resolve(): (_Plugin("source", str(first / "source.dll"), ("Open",)),),
                    second.resolve(): (_Plugin("source", str(second / "source.dll"), ("Open",)),),
                },
                {
                    second.resolve(): (
                        f"Plugin {second / 'source.dll'} already loaded "
                        f"(com.example.source) from {first / 'source.dll'}",
                    )
                },
            )

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.conflict"
            ):
                configure_native_plugins(core, (str(first), str(second)))

    def test_reports_renamed_duplicate_namespace_without_using_dll_basename(self):
        """I1：LoadAllPlugins warning 的改名冲突必须含候选与既有路径。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "目录 一"
            second = root / "目录 二"
            first.mkdir()
            second.mkdir()
            original = first / "imwri.dll"
            renamed = second / "renamed-plugin.dll"
            original.write_bytes(b"plugin")
            renamed.write_bytes(b"plugin")
            core = _Core(
                {
                    first.resolve(): (_Plugin("imwri", str(original), ("Read",)),),
                    second.resolve(): (_Plugin("imwri", str(renamed), ("Read",)),),
                },
                {
                    second.resolve(): (
                        f"Plugin {renamed} already loaded (com.vapoursynth.imwri) "
                        f"from {original}",
                    )
                },
            )

            with self.assertRaisesRegex(NativePluginPolicyError, "native_plugin.conflict") as raised:
                configure_native_plugins(core, (str(first), str(second)))
            self.assertIn(str(renamed), str(raised.exception))
            self.assertIn(str(original), str(raised.exception))
            self.assertEqual(core.removed_log_handles, [1, 2])

    def test_reports_loader_failure_but_keeps_silent_dependency_dll_valid(self):
        """I3：仅 warning 中的坏候选拒绝，静默的普通依赖 DLL 保持合法。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "含依赖与损坏项"
            directory.mkdir()
            dependency = directory / "avcodec-60.dll"
            broken = directory / "broken-plugin.dll"
            dependency.write_bytes(b"dependency")
            broken.write_bytes(b"broken")
            core = _Core(
                {},
                {
                    directory.resolve(): (
                        f"Failed to load {broken}. GetLastError() returned 193.",
                    )
                },
            )

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.candidate_failed"
            ) as raised:
                configure_native_plugins(core, (str(directory),))
            self.assertIn(str(broken), str(raised.exception))
            self.assertNotIn(str(dependency), str(raised.exception))
            self.assertEqual(core.removed_log_handles, [1])

    def test_accepts_same_actual_path_already_registered_before_directory_scan(self):
        """I1：相同实际路径的重复扫描不是跨来源插件冲突。"""
        configure_native_plugins = self._api().configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "同路径重复"
            directory.mkdir()
            plugin_path = directory / "same.dll"
            plugin_path.write_bytes(b"plugin")
            plugin = _Plugin("same", str(plugin_path), ("Open",))
            core = _Core(
                {directory.resolve(): (plugin,)},
                {
                    directory.resolve(): (
                        f"Plugin {plugin_path} already loaded "
                        f"(com.example.same) from {plugin_path}",
                    )
                },
            )
            core._plugins.append(plugin)
            core.same = plugin

            state = configure_native_plugins(core, (str(directory),))

            self.assertEqual(state.plugin_sources["same"], plugin_path.resolve())
            self.assertEqual(core.removed_log_handles, [1])

    def test_rejects_same_directory_renamed_candidate_when_original_is_preloaded(self):
        """I1：warning 的 Plugin 字段而非 from 字段才是冲突候选。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "同目录改名冲突"
            directory.mkdir()
            original = directory / "a-original.dll"
            renamed = directory / "z-renamed.dll"
            original.write_bytes(b"original")
            renamed.write_bytes(b"renamed")
            existing = _Plugin("imwri", str(original), ("Read",))
            core = _Core(
                {directory.resolve(): (_Plugin("imwri", str(renamed), ("Read",)),)},
                {
                    directory.resolve(): (
                        f"Plugin {renamed} already loaded "
                        f"(com.vapoursynth.imwri) from {original}",
                    )
                },
            )
            core._plugins.append(existing)
            core.imwri = existing

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.conflict"
            ) as raised:
                configure_native_plugins(core, (str(directory),))

            self.assertIn(str(renamed), str(raised.exception))
            self.assertIn(str(original), str(raised.exception))
            self.assertEqual(core.removed_log_handles, [1])

    def test_ignores_non_failure_logs_that_name_a_loaded_candidate(self):
        """NB1：成功信息、调试及普通 warning 不能因路径存在而致命。"""
        configure_native_plugins = self._api().configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "成功日志"
            directory.mkdir()
            candidate = directory / "valid.dll"
            candidate.write_bytes(b"valid")
            core = _Core(
                {directory.resolve(): (_Plugin("valid", str(candidate), ("Open",)),)},
                {
                    directory.resolve(): (
                        ("information", f"Initialized successfully: {candidate}"),
                        ("debug", f"Debug details: {candidate}"),
                        ("warning", f"Optional setting ignored: {candidate}"),
                    )
                },
            )

            state = configure_native_plugins(core, (str(directory),))

            self.assertEqual(state.plugin_sources["valid"], candidate.resolve())
            self.assertEqual(core.removed_log_handles, [1])

    def test_rejects_namespace_collision_with_by_source(self):
        """NB2：R73 namespace 冲突使用 load-of/failed/by 模板。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "namespace 冲突"
            directory.mkdir()
            candidate = directory / "candidate.dll"
            source = directory / "existing.dll"
            candidate.write_bytes(b"candidate")
            source.write_bytes(b"source")
            core = _Core(
                {},
                {
                    directory.resolve(): (
                        "Plugin load of "
                        f"{candidate} failed, namespace imwri already populated "
                        f"by {source}",
                    )
                },
            )

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.conflict"
            ) as raised:
                configure_native_plugins(core, (str(directory),))

            self.assertIn(str(candidate), str(raised.exception))
            self.assertIn(str(source), str(raised.exception))

    def test_rejects_internal_id_collision_without_source(self):
        """NB2：R73 内置 ID 冲突可省略 from，仍必须拒绝。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "内置 ID 冲突"
            directory.mkdir()
            candidate = directory / "candidate.dll"
            candidate.write_bytes(b"candidate")
            core = _Core(
                {},
                {
                    directory.resolve(): (
                        f"Plugin {candidate} already loaded "
                        "(com.vapoursynth.builtin)",
                    )
                },
            )

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.conflict"
            ) as raised:
                configure_native_plugins(core, (str(directory),))

            self.assertIn(str(candidate), str(raised.exception))

    def test_rejects_windows_missing_dependency_126(self):
        """NB2：R73 的 126 缺依赖后缀仍须保留为候选失败。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "缺依赖"
            directory.mkdir()
            candidate = directory / "candidate.dll"
            candidate.write_bytes(b"candidate")
            message = (
                f"Failed to load {candidate}. GetLastError() returned 126. "
                "The file you tried to load or one of its dependencies is "
                "probably missing."
            )
            core = _Core({}, {directory.resolve(): (message,)})

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.candidate_failed"
            ) as raised:
                configure_native_plugins(core, (str(directory),))

            self.assertIn(str(candidate), str(raised.exception))
            self.assertIn("probably missing", str(raised.exception))

    def test_rejects_plugin_api_incompatibility_with_filename(self):
        """NB2：R73 API 不兼容消息的 Filename 字段是失败候选。"""
        api = self._api()
        NativePluginPolicyError = api.NativePluginPolicyError
        configure_native_plugins = api.configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "API 不兼容"
            directory.mkdir()
            candidate = directory / "candidate.dll"
            candidate.write_bytes(b"candidate")
            message = (
                "Core only supports API R4.1 but the loaded plugin requires "
                f"API R5.0; Filename: {candidate}; Name: Candidate Plugin"
            )
            core = _Core({}, {directory.resolve(): (message,)})

            with self.assertRaisesRegex(
                NativePluginPolicyError, "native_plugin.candidate_failed"
            ) as raised:
                configure_native_plugins(core, (str(directory),))

            self.assertIn(str(candidate), str(raised.exception))
            self.assertIn("Candidate Plugin", str(raised.exception))

    def test_missing_optional_portable_builtin_directory_is_ignored(self):
        """I4：可选的 vs-coreplugins 缺失不能阻断显式配置目录加载。"""
        configure_native_plugins = self._api().configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = root / "配置目录"
            configured.mkdir()
            optional_missing = root / "vs-coreplugins"

            state = configure_native_plugins(
                _Core({}),
                (str(configured),),
                builtin_plugin_dirs=(str(optional_missing),),
            )

            self.assertEqual(state.configured_dirs, (configured.resolve(),))
            self.assertEqual(state.builtin_plugin_dirs, ())

    def test_worker_recheck_accepts_the_same_normalized_duplicate_directory(self):
        """I2：worker 复核必须沿用 shared helper 的保序去重语义。"""
        from config.vs_runtime import PluginConfig, VSRuntimeConfig
        from core.vs_runtime import vs_loader

        configure_native_plugins = self._api().configure_native_plugins
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "重复 目录"
            directory.mkdir()
            core = _Core({})
            state = configure_native_plugins(core, (str(directory), str(directory)))
            old_state = vs_loader._native_plugin_state
            vs_loader._native_plugin_state = state
            self.addCleanup(setattr, vs_loader, "_native_plugin_state", old_state)

            vs_loader.verify_vapoursynth_native_plugins(
                types.SimpleNamespace(core=core),
                VSRuntimeConfig(
                    plugins=PluginConfig(
                        native_plugin_dirs=(str(directory), str(directory))
                    )
                ),
                [],
            )


@unittest.skipUnless(
    (ROOT / "tools" / "media" / "vapoursynth.pyd").is_file(),
    "需要本机 portable VapourSynth fixture",
)
class NativePluginPortableIntegrationTests(unittest.TestCase):
    def test_two_chinese_space_directories_load_real_plugins_without_original_autoload(self):
        """临时 portable 副本只能从两个配置目录加载 imwri/lsmas。"""
        source_media = ROOT / "tools" / "media"
        source_plugins = source_media / "vs-plugins"
        image = ROOT / "resources" / "class_icons" / "ak_logo.png"
        for required in (
            source_media / "vapoursynth.pyd",
            source_media / "vapoursynth.dll",
            source_media / "portable.vs",
            source_plugins / "libimwri.dll",
            source_plugins / "LSMASHSource.dll",
            image,
        ):
            self.assertTrue(required.is_file(), required)

        with tempfile.TemporaryDirectory() as temporary:
            app_root = Path(temporary) / "独立 portable"
            media = app_root / "tools" / "media"
            media.mkdir(parents=True)
            for candidate in source_media.iterdir():
                if candidate.is_file() and (
                    candidate.suffix.casefold() in {".dll", ".pyd"}
                    or candidate.name == "portable.vs"
                ):
                    shutil.copy2(candidate, media / candidate.name)
            # 保留空 autoload 目录；原始 DLL 绝不能在此 fixture 中替代配置目录。
            (media / "vs-plugins").mkdir()
            (media / "vs-coreplugins").mkdir()
            first = app_root / "插件 中文 一"
            second = app_root / "插件 空格 二"
            first.mkdir()
            second.mkdir()
            shutil.copy2(source_plugins / "LSMASHSource.dll", first / "LSMASHSource.dll")
            shutil.copy2(source_plugins / "libimwri.dll", second / "libimwri.dll")
            # 普通依赖 DLL 与 plugin DLL 共存不应让该目录被拒绝。
            shutil.copy2(source_media / "avcodec-60.dll", second / "avcodec-60.dll")
            probe = app_root / "probe.py"
            probe.write_text(
                "from __future__ import annotations\n"
                "import json\n"
                "import sys\n"
                "from pathlib import Path\n"
                f"sys.path.insert(0, {str(ROOT)!r})\n"
                "from config.vs_runtime import PluginConfig, VSRuntimeConfig\n"
                "from core.vs_runtime.vs_loader import (\n"
                "    load_vapoursynth, verify_vapoursynth_native_plugins,\n"
                ")\n"
                f"root = Path({str(app_root)!r})\n"
                f"first = Path({str(first)!r})\n"
                f"second = Path({str(second)!r})\n"
                f"image = Path({str(image)!r})\n"
                "runtime = VSRuntimeConfig(plugins=PluginConfig(\n"
                "    native_plugin_dirs=(str(first), str(second)),\n"
                "))\n"
                "vs = load_vapoursynth(root, runtime)\n"
                "verify_vapoursynth_native_plugins(\n"
                "    vs, runtime, ('lsmas.LWLibavSource', 'imwri.Read'),\n"
                ")\n"
                "frame = vs.core.imwri.Read(str(image)).get_frame(0)\n"
                "try:\n"
                "    payload = {\n"
                "        plugin.namespace: plugin.plugin_path\n"
                "        for plugin in vs.core.plugins()\n"
                "        if plugin.namespace in {'lsmas', 'imwri'}\n"
                "    }\n"
                "    payload['frame'] = [frame.width, frame.height]\n"
                "    print(json.dumps(payload, ensure_ascii=True))\n"
                "finally:\n"
                "    frame.close()\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["VAPOURSYNTH_EXTRA_PLUGIN_PATH"] = str(source_plugins)
            result = subprocess.run(
                [sys.executable, "-B", str(probe)],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(Path(payload["lsmas"]).resolve(), first / "LSMASHSource.dll")
            self.assertEqual(Path(payload["imwri"]).resolve(), second / "libimwri.dll")
            self.assertEqual(payload["frame"], [75, 35])

    def test_worker_and_fresh_vspipe_use_isolated_native_directories(self):
        """I5：worker IPC 与 fixed runner 均排除 autoload 后实际取 imwri 帧。"""
        from config.vs_runtime import PluginConfig, VSRuntimeConfig
        from core.media_pipeline import (
            VSPipeRenderRequest,
            build_vspipe_command,
            build_vspipe_render_env,
        )
        from core.vs_runtime.session import (
            RenderSession,
            ScriptSelection,
            compute_job_sha256,
            compute_script_bundle_hash,
        )
        from core.vs_runtime.snapshot import RuntimeSnapshot
        from core.vs_runtime.vs_loader import compute_runtime_fingerprint
        from core.vs_runtime.worker_process import SyncVSWorkerProcess
        from core.vs_runtime.script_header import parse_script_header

        source_media = ROOT / "tools" / "media"
        source_plugins = source_media / "vs-plugins"
        source_resources = ROOT / "resources" / "vapoursynth"
        image = ROOT / "resources" / "class_icons" / "ak_logo.png"
        for required in (
            source_media / "VSPipe.exe",
            source_media / "vapoursynth.pyd",
            source_media / "vapoursynth.dll",
            source_media / "portable.vs",
            source_plugins / "LSMASHSource.dll",
            source_plugins / "libimwri.dll",
            image,
        ):
            self.assertTrue(required.is_file(), required)

        with tempfile.TemporaryDirectory() as temporary:
            app_root = Path(temporary) / "isolated portable 应用"
            media = app_root / "tools" / "media"
            media.mkdir(parents=True)
            for source in source_media.iterdir():
                if source.is_file() and (
                    source.suffix.casefold() in {".dll", ".pyd", ".exe"}
                    or source.name
                    in {"portable.vs", "python312.zip", "python312._pth", "python.cat"}
                ):
                    shutil.copy2(source, media / source.name)
            # VSPipe 使用 portable 内嵌解释器，不能只复制 exe/pyd/dll。
            shutil.copytree(source_media / "Lib", media / "Lib")
            shutil.copytree(source_resources, app_root / "resources" / "vapoursynth")
            (media / "vs-plugins").mkdir()
            (media / "vs-coreplugins").mkdir()
            first = app_root / "插件 中文 一"
            second = app_root / "插件 空格 二"
            first.mkdir()
            second.mkdir()
            shutil.copy2(source_plugins / "LSMASHSource.dll", first / "LSMASHSource.dll")
            shutil.copy2(source_plugins / "libimwri.dll", second / "libimwri.dll")
            shutil.copy2(source_media / "avcodec-60.dll", second / "avcodec-60.dll")
            runtime = VSRuntimeConfig(
                plugins=PluginConfig(native_plugin_dirs=(str(first), str(second)))
            )
            fingerprint = compute_runtime_fingerprint(app_root, runtime)
            project = app_root / "project 中文"
            project.mkdir()
            sources_marker = project / "native-sources.json"
            script = project / "pipeline.vpy"
            script.write_text(
                "# assetmaker-api: 1\n"
                "# assetmaker-mode: raw\n"
                "# assetmaker-capabilities: source\n"
                "# assetmaker-requires: lsmas.LWLibavSource, imwri.Read\n"
                "# assetmaker-editor-output: 0\n\n"
                "import json\n"
                "from pathlib import Path\n"
                "import vapoursynth as vs\n\n"
                f"image = Path({str(image)!r})\n"
                f"marker = Path({str(sources_marker)!r})\n"
                "frame = vs.core.imwri.Read(str(image)).get_frame(0)\n"
                "try:\n"
                "    assert (frame.width, frame.height) == (75, 35)\n"
                "finally:\n"
                "    frame.close()\n"
                "marker.write_text(json.dumps({\n"
                "    'lsmas': vs.core.lsmas.plugin_path,\n"
                "    'imwri': vs.core.imwri.plugin_path,\n"
                "}), encoding='utf-8')\n"
                "base = vs.core.std.BlankClip(width=384, height=640, length=3, "
                "fpsnum=30000, fpsden=1001, format=vs.YUV420P8, "
                "color=[16, 128, 128])\n"
                "base = vs.core.std.SetFrameProps(base, _Matrix=6, _Transfer=6, "
                "_Primaries=6, _ColorRange=1)\n"
                "base.set_output(0)\n",
                encoding="utf-8",
            )
            job = project / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "api_version": 1,
                        "epoch": 3,
                        "track": "loop",
                        "project_root": str(project),
                        "source": {"path": str(project / "source.mp4"), "kind": "video", "virtual_frame_count": None},
                        "timeline": {"start_frame": 0, "end_frame": 3, "fps": {"numerator": 30000, "denominator": 1001}},
                        "transform": {"rotation": 0, "crop": {"coordinate_space": "post_rotation_source_pixels", "x": 0, "y": 0, "width": 0, "height": 0}},
                        "output": {"profile": "360x640", "display_width": 360, "display_height": 640, "coded_width": 384, "coded_height": 640, "pixel_format": "YUV420P8", "matrix": "170m", "transfer": "170m", "primaries": "170m", "range": "limited", "final_rotate_180": False},
                        "paths": {"cache_dir": str(project / "cache")},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            worker_program = (
                "import sys\n"
                "from pathlib import Path\n"
                "owner = sys.stdout\n"
                "protocol_stream = owner.buffer\n"
                "sys.stdout = sys.stderr\n"
                "from core.vs_runtime.worker_main import main\n"
                f"raise SystemExit(main(protocol_stream=protocol_stream, app_dir=Path({str(app_root)!r})))\n"
            )
            snapshot = RuntimeSnapshot(str(app_root), runtime, fingerprint)
            worker_env = snapshot.worker_environment(
                {**os.environ, "PYTHONPATH": str(ROOT)}
            )
            client = SyncVSWorkerProcess(
                app_dir=app_root,
                command=[str(Path(sys.executable).resolve()), "-B", "-c", worker_program],
                env=worker_env,
            )
            self.addCleanup(client.close)
            session = RenderSession(
                epoch=3,
                track="loop",
                selection=ScriptSelection.from_header(
                    script, parse_script_header(script), compute_script_bundle_hash(script)
                ),
                job_path=str(job),
                job_sha256=compute_job_sha256(job),
                runtime_fingerprint=fingerprint,
            )
            self.assertEqual(client.start(timeout_ms=15_000)["operation"], "hello")
            self.assertEqual(client.load(session, timeout_ms=20_000).epoch, 3)
            frame = client.request_frame(
                epoch=3,
                index=0,
                surface="final",
                viewport=(384, 640),
                zoom_factor=1.0,
                pan=(0.5, 0.5),
                timeout_ms=15_000,
            )
            self.assertEqual(frame.shape, (640, 384, 3))
            worker_sources = json.loads(sources_marker.read_text(encoding="utf-8"))
            self.assertEqual(Path(worker_sources["lsmas"]).resolve(), first / "LSMASHSource.dll")
            self.assertEqual(Path(worker_sources["imwri"]).resolve(), second / "libimwri.dll")
            client.unload(timeout_ms=10_000)
            self.assertEqual(client.shutdown(timeout_ms=10_000)["operation"], "shutdown")

            sources_marker.unlink()
            request = VSPipeRenderRequest(
                runner_path=str(app_root / "resources" / "vapoursynth" / "assetmaker_runner.vpy"),
                script_path=str(script),
                job_path=str(job),
                expected_job_sha256=hashlib.sha256(job.read_bytes()).hexdigest(),
                api_version=1,
                mode="raw",
                app_dir=str(app_root),
                runtime=runtime,
                runtime_fingerprint=fingerprint,
            )
            env = build_vspipe_render_env(
                str(media / "VSPipe.exe"),
                app_dir=str(app_root),
                runtime=runtime,
                expected_fingerprint=fingerprint,
            )
            self.assertEqual(env["VAPOURSYNTH_EXTRA_PLUGIN_PATH"], "")
            result = subprocess.run(
                build_vspipe_command(str(media / "VSPipe.exe"), request),
                cwd=app_root,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            runner_sources = json.loads(sources_marker.read_text(encoding="utf-8"))
            self.assertEqual(Path(runner_sources["lsmas"]).resolve(), first / "LSMASHSource.dll")
            self.assertEqual(Path(runner_sources["imwri"]).resolve(), second / "libimwri.dll")

            blocked_env = dict(env)
            blocked_env["VAPOURSYNTH_EXTRA_PLUGIN_PATH"] = str(source_plugins)
            blocked = subprocess.run(
                build_vspipe_command(str(media / "VSPipe.exe"), request),
                cwd=app_root,
                env=blocked_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("runtime.native_plugin_env", blocked.stderr)


if __name__ == "__main__":
    unittest.main()
