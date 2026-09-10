from __future__ import annotations

import importlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

from config.vs_runtime import load_vs_runtime
from core.media_pipeline import (
    VSPipeRenderRequest,
    build_vspipe_command,
    build_vspipe_render_env,
)
from core.media_tools import MediaToolchain
from core.vs_runtime.job import RenderJobError, load_render_job
from core.vs_runtime.script_header import ScriptHeaderError, parse_script_header
from core.vs_runtime.vs_loader import compute_runtime_fingerprint


ROOT = Path(__file__).resolve().parents[1]
HELPER_ROOT = ROOT / "resources" / "vapoursynth" / "python"
RUNNER = ROOT / "resources" / "vapoursynth" / "assetmaker_runner.vpy"
FIXTURES = ROOT / "tests" / "fixtures" / "vs_scripts"
CHILD = ROOT / "tests" / "helpers" / "run_vs_contract_case.py"
TOOLCHAIN = MediaToolchain.discover(str(ROOT))


def _import_executor():
    if str(HELPER_ROOT) not in sys.path:
        sys.path.insert(0, str(HELPER_ROOT))
    try:
        return importlib.import_module("assetmaker_vs.executor")
    except ModuleNotFoundError as exc:
        raise AssertionError("portable assetmaker_vs.executor 尚未实现") from exc


def _fake_vapoursynth(*, fail_clear_call: int | None = None):
    fake = types.ModuleType("vapoursynth")
    outputs: dict[int, object] = {}
    fake.clear_calls = 0

    class FakeClip:
        def set_output(self, index: int = 0) -> None:
            outputs[index] = self

    def clear_outputs() -> None:
        fake.clear_calls += 1
        if fake.clear_calls == fail_clear_call:
            raise RuntimeError("clear outputs failed")
        outputs.clear()

    fake.clear_outputs = clear_outputs
    fake.get_outputs = lambda: dict(outputs)
    fake.make_clip = FakeClip
    return fake


def _write_executor_script(root: Path, body: str) -> tuple[Path, Path]:
    script = root / "pipeline.vpy"
    job = root / "job.json"
    script.write_text(body, encoding="utf-8")
    job.write_text("{}", encoding="utf-8")
    return script, job


def _write_job(path: Path, *, frame_count: int = 3) -> None:
    root = path.parent.resolve()
    payload = {
        "api_version": 1,
        "epoch": 3,
        "track": "loop",
        "project_root": str(root),
        "source": {
            "path": str(root / "source.mp4"),
            "kind": "video",
            "virtual_frame_count": None,
        },
        "timeline": {
            "start_frame": 0,
            "end_frame": frame_count,
            "fps": {"numerator": 30000, "denominator": 1001},
        },
        "transform": {
            "rotation": 0,
            "crop": {
                "coordinate_space": "post_rotation_source_pixels",
                "x": 0,
                "y": 0,
                "width": 0,
                "height": 0,
            },
        },
        "output": {
            "profile": "360x640",
            "display_width": 360,
            "display_height": 640,
            "coded_width": 384,
            "coded_height": 640,
            "pixel_format": "YUV420P8",
            "matrix": "170m",
            "transfer": "170m",
            "primaries": "170m",
            "range": "limited",
            "final_rotate_180": False,
        },
        "paths": {"cache_dir": str(root / "cache")},
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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


def _runner_request(
    *, script: Path, job: Path, mode: str, expected_job_sha256: str | None = None
) -> VSPipeRenderRequest:
    runtime = load_vs_runtime(ROOT / "config" / "vs_runtime.json")
    return VSPipeRenderRequest(
        runner_path=str(RUNNER),
        script_path=str(script),
        job_path=str(job),
        expected_job_sha256=(
            hashlib.sha256(job.read_bytes()).hexdigest()
            if expected_job_sha256 is None
            else expected_job_sha256
        ),
        api_version=1,
        mode=mode,
        app_dir=str(ROOT),
        runtime=runtime,
        runtime_fingerprint=compute_runtime_fingerprint(ROOT, runtime),
    )


def _run_vspipe(
    *, script: Path, job: Path, mode: str, expected_job_sha256: str | None = None
) -> subprocess.CompletedProcess[str]:
    request = _runner_request(
        script=script,
        job=job,
        mode=mode,
        expected_job_sha256=expected_job_sha256,
    )
    args = [
        TOOLCHAIN.vspipe_path,
        "--info",
        "--arg",
        f"assetmaker_job={request.job_path}",
        "--arg",
        f"expected_job_sha256={request.expected_job_sha256}",
        "--arg",
        f"assetmaker_script={request.script_path}",
        "--arg",
        "assetmaker_api=1",
        "--arg",
        f"assetmaker_mode={request.mode}",
        request.runner_path,
        "-",
    ]
    kwargs: dict[str, object] = {
        "cwd": ROOT,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 30,
        "check": False,
        "env": build_vspipe_render_env(
            TOOLCHAIN.vspipe_path,
            app_dir=request.app_dir,
            runtime=request.runtime,
            expected_fingerprint=request.runtime_fingerprint,
        ),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(args, **kwargs)


def _run_vspipe_y4m(
    *, script: Path, job: Path, mode: str
) -> subprocess.CompletedProcess[bytes]:
    request = _runner_request(script=script, job=job, mode=mode)
    args = build_vspipe_command(TOOLCHAIN.vspipe_path, request)
    kwargs: dict[str, object] = {
        "cwd": ROOT,
        "capture_output": True,
        "timeout": 30,
        "check": False,
        "env": build_vspipe_render_env(
            TOOLCHAIN.vspipe_path,
            app_dir=request.app_dir,
            runtime=request.runtime,
            expected_fingerprint=request.runtime_fingerprint,
        ),
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(args, **kwargs)


def _write_invalid_prop_script(
    path: Path,
    *,
    prop: str,
    value_expression: str,
) -> None:
    path.write_text(
        "# assetmaker-api: 1\n"
        "# assetmaker-mode: raw\n"
        "# assetmaker-capabilities: source\n"
        "# assetmaker-requires:\n"
        "# assetmaker-editor-output: 0\n\n"
        "import vapoursynth as vs\n\n"
        "base = vs.core.std.BlankClip(\n"
        "    width=384, height=640, length=3,\n"
        "    fpsnum=30000, fpsden=1001, format=vs.YUV420P8,\n"
        "    color=[16, 128, 128],\n"
        ")\n"
        "base = vs.core.std.SetFrameProps(\n"
        "    base, _Matrix=6, _Transfer=6, _Primaries=6, _ColorRange=1\n"
        ")\n\n"
        "def invalid(n, f):\n"
        "    changed = f.copy()\n"
        + f"    changed.props[{prop!r}] = {value_expression}\n"
        "    return changed\n\n"
        "vs.core.std.ModifyFrame(\n"
        "    clip=base, clips=base, selector=invalid\n"
        ").set_output(0)\n",
        encoding="utf-8",
    )


class PortableExecutorTests(unittest.TestCase):
    def _assert_mixed_namespace_payload(
        self, payload: dict[str, object], *, failed_first: bool
    ) -> None:
        self.assertEqual(payload["first_value"], "A")
        self.assertEqual(
            payload["first_error"],
            "first script failed" if failed_first else None,
        )
        self.assertEqual(
            payload["after_first"],
            {
                "parent_same": True,
                "external_same": True,
                "external_attr_same": True,
                "ordinary_same": True,
                "local_cached": False,
                "stale_local_attr": False,
            },
        )
        self.assertEqual(payload["second_value"], "B")
        self.assertEqual(
            Path(payload["second_local_file"]).parent.parent.parent.name,
            "B",
        )
        for field in (
            "second_parent_same",
            "second_external_same",
            "second_external_attr_same",
            "second_ordinary_same",
            "helper_preserved",
            "stdlib_preserved",
        ):
            with self.subTest(field=field):
                self.assertTrue(payload[field])
        self.assertFalse(payload["after_second_local_cached"])
        self.assertFalse(payload["after_second_stale_local_attr"])

    def test_helper_is_never_evicted_even_under_the_script_root(self):
        executor = _import_executor()
        name = "resources.vapoursynth.python.assetmaker_vs.synthetic_test"
        module = types.ModuleType(name)
        module.__file__ = str(HELPER_ROOT / "assetmaker_vs" / "synthetic.py")
        sys.modules[name] = module
        self.addCleanup(sys.modules.pop, name, None)

        removed = executor.evict_modules_under(HELPER_ROOT.parent)

        self.assertNotIn(name, removed)
        self.assertIs(sys.modules[name], module)

    def test_module_search_order_is_deterministic(self):
        executor = _import_executor()
        project = Path(tempfile.gettempdir()).resolve() / "素材" / "黍"
        third_party = project / "third_party"

        actual = executor.build_module_search_paths(
            script_path=project / "pipeline.vpy",
            runtime_dirs=[third_party],
        )

        self.assertEqual(
            actual,
            (
                project,
                project / "modules",
                third_party,
                HELPER_ROOT,
            ),
        )

    def test_module_search_paths_deduplicate_without_reordering(self):
        executor = _import_executor()
        project = Path(tempfile.gettempdir()).resolve() / "assetmaker-dedupe"

        actual = executor.build_module_search_paths(
            script_path=project / "pipeline.vpy",
            runtime_dirs=[project / "modules", project, project / "extra"],
        )

        self.assertEqual(
            actual,
            (project, project / "modules", project / "extra", HELPER_ROOT),
        )

    def test_runtime_python_dirs_use_dedicated_json_environment(self):
        executor = _import_executor()
        first = str((Path(tempfile.gettempdir()) / "python-a").resolve())
        second = str((Path(tempfile.gettempdir()) / "python-b").resolve())
        with mock.patch.dict(
            os.environ,
            {"ASSETMAKER_VS_PYTHON_DIRS_JSON": json.dumps([first, second])},
            clear=False,
        ):
            self.assertEqual(
                executor.runtime_python_dirs_from_env(),
                (Path(first), Path(second)),
            )

        with mock.patch.dict(
            os.environ,
            {"ASSETMAKER_VS_PYTHON_DIRS_JSON": json.dumps("not-a-list")},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                executor.runtime_python_dirs_from_env()

    def test_reload_evicts_only_modules_under_script_root(self):
        executor = _import_executor()
        root_name = f"assetmaker_reload_{uuid.uuid4().hex}"
        external_name = f"assetmaker_external_{uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir).resolve()
            script_root = base / "script"
            external_root = base / "external"
            script_root.mkdir()
            external_root.mkdir()
            module_path = script_root / f"{root_name}.py"
            external_path = external_root / f"{external_name}.py"
            module_path.write_text('VALUE = "first"\n', encoding="utf-8")
            external_path.write_text('VALUE = "kept"\n', encoding="utf-8")
            sys.path[:0] = [str(script_root), str(external_root)]
            self.addCleanup(lambda: sys.path.remove(str(script_root)))
            self.addCleanup(lambda: sys.path.remove(str(external_root)))
            first = importlib.import_module(root_name)
            external = importlib.import_module(external_name)
            self.addCleanup(sys.modules.pop, root_name, None)
            self.addCleanup(sys.modules.pop, external_name, None)
            self.assertEqual(first.VALUE, "first")

            module_path.write_text(
                'VALUE = "second-and-different-size"\n', encoding="utf-8"
            )
            future = time.time() + 2
            os.utime(module_path, (future, future))
            for cache_file in script_root.glob("__pycache__/*.pyc"):
                cache_file.unlink()
            executor.evict_modules_under(script_root)
            importlib.invalidate_caches()
            second = importlib.import_module(root_name)

            self.assertEqual(second.VALUE, "second-and-different-size")
            self.assertIs(sys.modules[external_name], external)
            self.assertIn("assetmaker_vs.executor", sys.modules)

    def test_evict_modules_under_removes_namespace_package_path(self):
        executor = _import_executor()
        package_name = f"assetmaker_namespace_{uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            modules = root / "modules"
            package = modules / package_name
            package.mkdir(parents=True)
            (package / "child.py").write_text("VALUE = 1\n", encoding="utf-8")
            sys.path.insert(0, str(modules))
            self.addCleanup(sys.path.remove, str(modules))
            namespace = importlib.import_module(package_name)
            self.addCleanup(sys.modules.pop, package_name, None)
            self.assertIsNone(namespace.__file__)

            removed = executor.evict_modules_under(root)

        self.assertIn(package_name, removed)
        self.assertNotIn(package_name, sys.modules)

    def test_deferred_frame_callback_keeps_import_path_and_stdout_isolated(self):
        result = _run_child("executor_deferred")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload["logs"],
            [
                "hello from script",
                "lazy module imported",
                "hello from callback",
            ],
        )
        self.assertTrue(payload["active_during_render"])
        self.assertFalse(payload["active_after_close"])

    def test_real_cross_root_same_name_module_reloads_after_graph_retirement(self):
        result = _run_child("executor_cross_root")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["first_value"], "from-a")
        self.assertEqual(payload["second_value"], "from-b")
        self.assertEqual(
            Path(payload["active_module_path"]).parent.parent.name, "A"
        )
        self.assertEqual(
            Path(payload["second_module_path"]).parent.parent.name, "B"
        )
        self.assertTrue(payload["retired_after_close"])
        self.assertTrue(payload["retired_second_after_close"])
        self.assertTrue(payload["helper_preserved"])
        self.assertTrue(payload["stdlib_preserved"])

    def test_mixed_namespace_with_preexisting_third_party_reloads_local_child(self):
        result = _run_child("executor_mixed_namespace_sys_path")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self._assert_mixed_namespace_payload(
            json.loads(result.stdout), failed_first=False
        )

    def test_mixed_namespace_from_runtime_dirs_preserves_external_identity(self):
        result = _run_child("executor_mixed_namespace_runtime_dirs")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self._assert_mixed_namespace_payload(
            json.loads(result.stdout), failed_first=False
        )

    def test_failed_script_retires_half_loaded_mixed_namespace(self):
        result = _run_child("executor_mixed_namespace_failure")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self._assert_mixed_namespace_payload(
            json.loads(result.stdout), failed_first=True
        )

    def test_second_graph_is_rejected_until_active_graph_closes(self):
        result = _run_child("executor_graph_overlap")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["accepted_while_active"])
        self.assertEqual(payload["error_type"], "GraphLifecycleError")
        self.assertEqual(payload["error_code"], "executor.graph_active")
        self.assertIn("executor.graph_active", payload["error_message"])
        self.assertEqual(payload["clear_calls_while_rejected"], 1)
        self.assertTrue(payload["active_path_unchanged"])
        self.assertEqual(payload["after_close_value"], "B")

    def test_graph_lease_is_reusable_after_close_and_script_failure(self):
        result = _run_child("executor_graph_reuse")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "after_double_close_value": "B",
                "failure_error": "script failed",
                "after_failure_value": "C",
            },
        )

    def test_graph_close_clears_vapoursynth_output_registry(self):
        executor = _import_executor()
        fake_vs = _fake_vapoursynth()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            script, job = _write_executor_script(
                root,
                "import vapoursynth as vs\n"
                "clip = vs.make_clip()\n"
                "clip.set_output(0)\n",
            )
            with mock.patch.dict(sys.modules, {"vapoursynth": fake_vs}):
                graph = executor.execute_user_script(
                    script_path=script,
                    job_path=job,
                    api_version="1",
                    mode="raw",
                )
                self.assertEqual(set(fake_vs.get_outputs()), {0})

                graph.close()

                self.assertEqual(fake_vs.get_outputs(), {})

    def test_failed_script_clears_vapoursynth_output_registry(self):
        executor = _import_executor()
        fake_vs = _fake_vapoursynth()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            script, job = _write_executor_script(
                root,
                "import vapoursynth as vs\n"
                "clip = vs.make_clip()\n"
                "clip.set_output(0)\n"
                'raise RuntimeError("script failed")\n',
            )
            with mock.patch.dict(sys.modules, {"vapoursynth": fake_vs}):
                with self.assertRaisesRegex(RuntimeError, "script failed"):
                    executor.execute_user_script(
                        script_path=script,
                        job_path=job,
                        api_version="1",
                        mode="raw",
                    )

                self.assertEqual(fake_vs.get_outputs(), {})

    def test_clear_outputs_failure_is_wrapped_and_other_cleanup_continues(self):
        executor = _import_executor()
        fake_vs = _fake_vapoursynth(fail_clear_call=2)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            script, job = _write_executor_script(
                root,
                "import vapoursynth as vs\n"
                "clip = vs.make_clip()\n"
                "clip.set_output(0)\n",
            )
            with mock.patch.dict(sys.modules, {"vapoursynth": fake_vs}):
                graph = executor.execute_user_script(
                    script_path=script,
                    job_path=job,
                    api_version="1",
                    mode="raw",
                )
                with mock.patch.object(
                    executor.importlib, "invalidate_caches"
                ) as invalidate_caches:
                    with self.assertRaises(
                        executor.GraphLifecycleError
                    ) as raised:
                        graph.close()

                self.assertEqual(
                    raised.exception.code, "executor.retirement_failed"
                )
                self.assertFalse(graph.environment.active)
                invalidate_caches.assert_called_once_with()

    def test_environment_close_failure_is_wrapped_and_cleanup_continues(self):
        executor = _import_executor()
        fake_vs = _fake_vapoursynth()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            script, job = _write_executor_script(
                root,
                "import vapoursynth as vs\n"
                "clip = vs.make_clip()\n"
                "clip.set_output(0)\n",
            )
            with mock.patch.dict(sys.modules, {"vapoursynth": fake_vs}):
                graph = executor.execute_user_script(
                    script_path=script,
                    job_path=job,
                    api_version="1",
                    mode="raw",
                )
                original_close = graph.environment.close

                def close_then_fail() -> None:
                    original_close()
                    raise RuntimeError("environment close failed")

                graph.environment.close = close_then_fail
                with mock.patch.object(
                    executor.importlib, "invalidate_caches"
                ) as invalidate_caches:
                    with self.assertRaises(
                        executor.GraphLifecycleError
                    ) as raised:
                        graph.close()

                self.assertEqual(
                    raised.exception.code, "executor.retirement_failed"
                )
                self.assertEqual(fake_vs.get_outputs(), {})
                invalidate_caches.assert_called_once_with()

                graph.close()
                next_script = root / "next.vpy"
                next_script.write_text(
                    "import vapoursynth as vs\n"
                    "clip = vs.make_clip()\n"
                    "clip.set_output(0)\n",
                    encoding="utf-8",
                )
                next_graph = executor.execute_user_script(
                    script_path=next_script,
                    job_path=job,
                    api_version="1",
                    mode="raw",
                )
                next_graph.close()

    def test_cache_failure_is_wrapped_after_other_cleanup_completes(self):
        executor = _import_executor()
        fake_vs = _fake_vapoursynth()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            script, job = _write_executor_script(
                root,
                "import vapoursynth as vs\n"
                "clip = vs.make_clip()\n"
                "clip.set_output(0)\n",
            )
            with mock.patch.dict(sys.modules, {"vapoursynth": fake_vs}):
                graph = executor.execute_user_script(
                    script_path=script,
                    job_path=job,
                    api_version="1",
                    mode="raw",
                )
                with mock.patch.object(
                    executor.importlib,
                    "invalidate_caches",
                    side_effect=RuntimeError("cache invalidation failed"),
                ):
                    with self.assertRaises(
                        executor.GraphLifecycleError
                    ) as raised:
                        graph.close()

                self.assertEqual(
                    raised.exception.code, "executor.retirement_failed"
                )
                self.assertEqual(fake_vs.get_outputs(), {})
                self.assertFalse(graph.environment.active)

    def test_cleanup_failure_does_not_mask_script_failure(self):
        executor = _import_executor()
        fake_vs = _fake_vapoursynth(fail_clear_call=2)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            script, job = _write_executor_script(
                root,
                "import vapoursynth as vs\n"
                "clip = vs.make_clip()\n"
                "clip.set_output(0)\n"
                'raise RuntimeError("script failed")\n',
            )
            original_path = list(sys.path)
            with mock.patch.dict(sys.modules, {"vapoursynth": fake_vs}):
                with mock.patch.object(
                    executor.importlib,
                    "invalidate_caches",
                    wraps=executor.importlib.invalidate_caches,
                ) as invalidate_caches:
                    with self.assertRaisesRegex(
                        RuntimeError, "script failed"
                    ) as raised:
                        executor.execute_user_script(
                            script_path=script,
                            job_path=job,
                            api_version="1",
                            mode="raw",
                        )

                self.assertEqual(sys.path, original_path)
                self.assertEqual(invalidate_caches.call_count, 2)
                self.assertTrue(
                    any(
                        "executor.retirement_failed" in note
                        for note in getattr(raised.exception, "__notes__", ())
                    )
                )

    def test_concurrent_graph_load_has_exactly_one_winner(self):
        result = _run_child("executor_graph_race")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["alive"], [False, False])
        self.assertEqual(payload["clear_calls_during_race"], 1)
        self.assertEqual(payload["after_race_value"], "C")
        graphs = [
            outcome
            for outcome in payload["outcomes"]
            if outcome["kind"] == "graph"
        ]
        errors = [
            outcome
            for outcome in payload["outcomes"]
            if outcome["kind"] == "error"
        ]
        self.assertEqual(len(graphs), 1)
        self.assertIn(graphs[0]["value"], {"A", "B"})
        self.assertEqual(
            errors,
            [
                {
                    "kind": "error",
                    "type": "GraphLifecycleError",
                    "code": "executor.graph_active",
                }
            ],
        )

    def test_poison_package_metadata_does_not_abort_normal_retirement(self):
        result = _run_child("executor_poison_close")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "first_value": "A",
                "close_error": None,
                "poison_preserved": True,
                "a_marker_cached": False,
                "second_value": "B",
                "second_retired": True,
                "helper_preserved": True,
                "stdlib_preserved": True,
            },
        )

    def test_poison_file_metadata_does_not_mask_script_failure(self):
        result = _run_child("executor_poison_failure")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "execution_error": {
                    "type": "RuntimeError",
                    "message": "script failure",
                },
                "poison_preserved": True,
                "a_marker_cached": False,
                "second_value": "B",
                "second_retired": True,
                "helper_preserved": True,
                "stdlib_preserved": True,
            },
        )

    def test_parent_unbind_failure_does_not_abort_remaining_retirement(self):
        result = _run_child("executor_retirement_unbind_close")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["close_error"]["type"], "GraphLifecycleError")
        self.assertEqual(
            payload["close_error"]["code"], "executor.retirement_failed"
        )
        self.assertIn(
            "executor.retirement_failed", payload["close_error"]["message"]
        )
        self.assertEqual(
            payload["state"],
            {
                "parent_preserved": True,
                "bad_module_removed": True,
                "bad_attr_remains": True,
                "replacement_module_preserved": True,
                "replacement_attr_preserved": True,
                "later_module_removed": True,
                "later_attr_removed": True,
                "read_parent_preserved": True,
                "read_child_removed": True,
            },
        )
        self.assertFalse(payload["a_marker_cached"])
        self.assertEqual(payload["second_value"], "B")
        self.assertTrue(payload["second_retired"])

    def test_retirement_failure_is_not_primary_over_script_failure(self):
        result = _run_child("executor_retirement_unbind_failure")

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["execution_error"]["type"], "RuntimeError")
        self.assertEqual(
            payload["execution_error"]["message"], "script failure"
        )
        self.assertTrue(
            any(
                "executor.retirement_failed" in note
                for note in payload["execution_error"]["notes"]
            )
        )
        self.assertEqual(
            payload["state"],
            {
                "parent_preserved": True,
                "bad_module_removed": True,
                "bad_attr_remains": True,
                "replacement_module_preserved": True,
                "replacement_attr_preserved": True,
                "later_module_removed": True,
                "later_attr_removed": True,
                "read_parent_preserved": True,
                "read_child_removed": True,
            },
        )
        self.assertFalse(payload["a_marker_cached"])
        self.assertEqual(payload["second_value"], "B")
        self.assertTrue(payload["second_retired"])

    def test_stdout_sink_is_thread_safe_and_chunks_below_protocol_limit(self):
        executor = _import_executor()
        lines: list[str] = []
        original = sys.stdout
        writer = executor.install_python_stdout(lines.append)
        try:
            payload = "黍" * 1_500_000
            writer.write(payload + "\n")
            threads = [
                threading.Thread(
                    target=lambda index=index: writer.write(f"thread-{index}\n")
                )
                for index in range(8)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
            writer.flush()
        finally:
            sys.stdout = original

        payload_chunks = lines[:-8]
        self.assertEqual("".join(payload_chunks), payload)
        self.assertTrue(payload_chunks)
        self.assertTrue(
            all(len(chunk.encode("utf-8")) < 4 * 1024 * 1024 for chunk in lines)
        )
        self.assertEqual(set(lines[-8:]), {f"thread-{i}" for i in range(8)})


class SharedPortableAdapterTests(unittest.TestCase):
    @staticmethod
    def _signature(callable_):
        try:
            callable_()
        except ValueError as exc:
            return (
                getattr(exc, "code", None),
                getattr(exc, "field", None),
                getattr(exc, "path", None),
            )
        raise AssertionError("非法 fixture 未被拒绝")

    def test_header_helper_and_core_adapter_share_error_identity(self):
        if str(HELPER_ROOT) not in sys.path:
            sys.path.insert(0, str(HELPER_ROOT))
        try:
            helper = importlib.import_module("assetmaker_vs.script_header")
        except ModuleNotFoundError as exc:
            self.fail(f"portable script_header 尚未实现: {exc}")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir).resolve() / "invalid.vpy"
            path.write_text(
                "# assetmaker-api: 1\n"
                "# assetmaker-mode: magic\n"
                "# assetmaker-capabilities: source\n"
                "# assetmaker-requires:\n"
                "# assetmaker-editor-output: 0\n",
                encoding="utf-8",
            )

            helper_error = self._signature(lambda: helper.parse_script_header(path))
            core_error = self._signature(lambda: parse_script_header(path))

        self.assertEqual(helper_error, core_error)
        self.assertEqual(helper_error[0], "header.mode")
        self.assertEqual(helper_error[1], "mode")

    def test_job_helper_and_core_adapter_share_error_identity(self):
        if str(HELPER_ROOT) not in sys.path:
            sys.path.insert(0, str(HELPER_ROOT))
        try:
            helper = importlib.import_module("assetmaker_vs.job_api")
        except ModuleNotFoundError as exc:
            self.fail(f"portable job_api 尚未实现: {exc}")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            path = root / "invalid-job.json"
            _write_job(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["timeline"]["start_frame"] = 3
            payload["timeline"]["end_frame"] = 3
            path.write_text(json.dumps(payload), encoding="utf-8")

            helper_error = self._signature(lambda: helper.load_job(path))
            core_error = self._signature(lambda: load_render_job(path))

        self.assertEqual(helper_error, core_error)
        self.assertEqual(helper_error[0], "job.timeline.order")
        self.assertEqual(helper_error[1], "timeline.end_frame")

    def test_portable_modules_import_without_project_or_qt_dependencies(self):
        script = """
import builtins
import json
real_import = builtins.__import__
blocked = {"core", "config", "PyQt6"}
def guarded(name, *args, **kwargs):
    if name.split(".", 1)[0] in blocked:
        raise RuntimeError("forbidden import: " + name)
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded
import assetmaker_vs.contract
import assetmaker_vs.display
import assetmaker_vs.executor
import assetmaker_vs.job_api
import assetmaker_vs.script_header
print(json.dumps({"ok": True}))
"""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(HELPER_ROOT)
        with tempfile.TemporaryDirectory() as temp_dir:
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=temp_dir,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"ok": True})


class TrustedRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        expected = ROOT / "tools" / "media" / "VSPipe.exe"
        available = bool(TOOLCHAIN.vspipe_path) and Path(
            TOOLCHAIN.vspipe_path
        ).is_file()
        self.assertTrue(
            available,
            "绑定真实 VSPipe 验收不可跳过："
            f"VSPipe missing；discover_root={ROOT}；"
            f"expected={expected}；{TOOLCHAIN.describe()}",
        )

    def test_chinese_script_root_imports_modules_and_keeps_stdout_off_y4m(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir).resolve() / "素材" / "黍"
            modules = project / "modules"
            modules.mkdir(parents=True)
            script = project / "pipeline.vpy"
            shutil.copyfile(FIXTURES / "prints_and_imports.vpy", script)
            (modules / "marker.py").write_text(
                'VALUE = "marker-from-script-root"\n', encoding="utf-8"
            )
            (modules / "lazy_marker.py").write_text(
                'print("lazy module imported")\nVALUE = "lazy-marker"\n',
                encoding="utf-8",
            )
            job = project / "job.json"
            _write_job(job)

            result = _run_vspipe(script=script, job=job, mode="raw")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Width: 384", result.stdout)
        self.assertNotIn("hello from script", result.stdout)
        self.assertIn("hello from script", result.stderr)
        self.assertIn("lazy module imported", result.stderr)

    def test_real_y4m_stdout_contains_only_stream_and_python_logs_use_stderr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir).resolve() / "素材" / "黍"
            modules = project / "modules"
            modules.mkdir(parents=True)
            script = project / "pipeline.vpy"
            shutil.copyfile(FIXTURES / "prints_and_imports.vpy", script)
            (modules / "marker.py").write_text(
                'VALUE = "marker-from-script-root"\n', encoding="utf-8"
            )
            (modules / "lazy_marker.py").write_text(
                'print("lazy module imported")\nVALUE = "lazy-marker"\n',
                encoding="utf-8",
            )
            job = project / "job.json"
            _write_job(job)

            result = _run_vspipe_y4m(script=script, job=job, mode="raw")

        self.assertEqual(
            result.returncode,
            0,
            result.stderr.decode("utf-8", errors="replace"),
        )
        self.assertTrue(result.stdout.startswith(b"YUV4MPEG2"))
        for text in (
            b"hello from script",
            b"lazy module imported",
            b"hello from callback",
        ):
            with self.subTest(text=text):
                self.assertNotIn(text, result.stdout)
                self.assertIn(text, result.stderr)

    def test_header_mode_mismatch_fails_before_user_script(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir).resolve() / "素材" / "黍"
            project.mkdir(parents=True)
            script = project / "pipeline.vpy"
            shutil.copyfile(FIXTURES / "raw_valid.vpy", script)
            job = project / "job.json"
            _write_job(job)

            result = _run_vspipe(script=script, job=job, mode="compatible")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invocation.mode", result.stderr)

    def test_wrong_job_hash_fails_before_user_script_side_effect(self):
        """runner 必须先验证冻结 job 内容，不能先执行用户 Python。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir).resolve() / "素材" / "黍"
            project.mkdir(parents=True)
            sentinel = project / "user-script-ran.txt"
            script = project / "pipeline.vpy"
            script.write_text(
                "# assetmaker-api: 1\n"
                "# assetmaker-mode: raw\n"
                "# assetmaker-capabilities: source\n"
                "# assetmaker-requires:\n"
                "# assetmaker-editor-output: 0\n\n"
                "from pathlib import Path\n"
                "import vapoursynth as vs\n"
                f"Path({str(sentinel)!r}).write_text('ran', encoding='utf-8')\n"
                "clip = vs.core.std.BlankClip(width=384, height=640, length=3, "
                "fpsnum=30, fpsden=1, format=vs.YUV420P8, color=[16, 128, 128])\n"
                "clip = vs.core.std.SetFrameProps(clip, _Matrix=6, _Transfer=6, "
                "_Primaries=6, _ColorRange=1)\n"
                "clip.set_output(0)\n",
                encoding="utf-8",
            )
            job = project / "job.json"
            _write_job(job)

            result = _run_vspipe(
                script=script,
                job=job,
                mode="raw",
                expected_job_sha256="0" * 64,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("job", result.stderr.lower())
        self.assertFalse(sentinel.exists())

    def test_bad_output_contract_makes_real_vspipe_fail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir).resolve() / "素材" / "黍"
            project.mkdir(parents=True)
            script = project / "bad-output.vpy"
            shutil.copyfile(FIXTURES / "compatible_bad_output.vpy", script)
            job = project / "job.json"
            _write_job(job)

            result = _run_vspipe(script=script, job=job, mode="compatible")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contract.pixel_format", result.stderr)

    def test_convertible_noninteger_props_fail_through_fixed_runner(self):
        cases = (
            ("_Matrix", "6.5", "matrix"),
            ("_Transfer", "6.0", "transfer"),
            ("_Primaries", "b'6'", "primaries"),
            ("_Range", "0.0", "range"),
            ("_ColorRange", "1.0", "range"),
            ("_Matrix", "b'not-an-int'", "matrix"),
        )
        for prop, value_expression, field in cases:
            with self.subTest(prop=prop, value=value_expression):
                with tempfile.TemporaryDirectory() as temp_dir:
                    project = Path(temp_dir).resolve() / "素材" / "黍"
                    project.mkdir(parents=True)
                    script = project / "invalid-prop.vpy"
                    _write_invalid_prop_script(
                        script,
                        prop=prop,
                        value_expression=value_expression,
                    )
                    job = project / "job.json"
                    _write_job(job)

                    result = _run_vspipe(
                        script=script, job=job, mode="raw"
                    )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("ASSETMAKER_VS_ERROR", result.stderr)
                self.assertIn(f'"field":"{field}"', result.stderr)

    def test_real_y4m_sequential_consumption_stops_on_second_frame_drift(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir).resolve() / "素材" / "黍"
            project.mkdir(parents=True)
            script = project / "late-drift.vpy"
            shutil.copyfile(FIXTURES / "late_drift.vpy", script)
            job = project / "job.json"
            _write_job(job, frame_count=5)

            result = _run_vspipe_y4m(script=script, job=job, mode="raw")

        stderr = result.stderr.decode("utf-8", errors="replace")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ASSETMAKER_VS_ERROR", stderr)
        self.assertIn('"field":"matrix"', stderr)


class RunnerBindingPolicyTests(unittest.TestCase):
    def test_bound_runner_suite_cannot_be_hidden_by_missing_tool_skip(self):
        self.assertNotIn(
            "__unittest_skip__",
            TrustedRunnerTests.__dict__,
            "绑定真实 VSPipe 验收不得通过类级 skip 被包装成绿色",
        )

    def test_missing_vspipe_is_an_explicit_failure_with_diagnostics(self):
        case = TrustedRunnerTests(
            "test_header_mode_mismatch_fails_before_user_script"
        )
        with mock.patch(
            f"{__name__}.TOOLCHAIN", MediaToolchain()
        ), self.assertRaises(AssertionError) as raised:
            case.setUp()

        message = str(raised.exception)
        self.assertIn("VSPipe", message)
        self.assertIn("missing", message)


if __name__ == "__main__":
    unittest.main()
