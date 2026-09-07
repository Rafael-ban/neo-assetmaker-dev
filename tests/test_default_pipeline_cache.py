"""内置 default pipeline 的 VapourSynth 缓存阈值契约。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from config.vs_runtime import load_vs_runtime
from core.media_pipeline import VSPipeRenderRequest, build_vspipe_render_env
from core.media_tools import MediaToolchain
from core.vs_runtime.session import compute_script_bundle_hash
from core.vs_runtime.vs_loader import compute_runtime_fingerprint
from core.vs_runtime.worker_process import SyncVSWorkerProcess, WorkerRequestError
from tests.helpers.m5_render_fixture import build_default_render_session


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PIPELINE = ROOT / "resources" / "vapoursynth" / "default_pipeline.vpy"
RUNNER = ROOT / "resources" / "vapoursynth" / "assetmaker_runner.vpy"
TOOLCHAIN = MediaToolchain.discover(str(ROOT))
REAL_VS_READY = not TOOLCHAIN.missing_for_export()
MARKER = "DEFAULT_PIPELINE_CACHE_SIZE_16000"


class DefaultPipelineCacheTests(unittest.TestCase):
    def test_default_pipeline_sets_explicit_16000_mb_cache_before_job_load(self):
        """执行脚本到 job 加载边界，观测缓存阈值已覆写。"""
        source = DEFAULT_PIPELINE.read_text(encoding="utf-8")
        fake_vs = types.ModuleType("vapoursynth")
        fake_vs.core = types.SimpleNamespace(max_cache_size=37)
        job_api = types.ModuleType("assetmaker_vs.job_api")

        class JobBoundaryReached(Exception):
            pass

        def observe_cache(*args, **kwargs):
            self.assertEqual(fake_vs.core.max_cache_size, 16000)
            raise JobBoundaryReached

        job_api.load_job = observe_cache
        with mock.patch.dict(sys.modules, {
            "vapoursynth": fake_vs,
            "assetmaker_vs": types.ModuleType("assetmaker_vs"),
            "assetmaker_vs.job_api": job_api,
        }), self.assertRaises(JobBoundaryReached):
            exec(compile(source, str(DEFAULT_PIPELINE), "exec"), {
                "assetmaker_job": "unused.json",
            })

    @unittest.skipUnless(REAL_VS_READY, "bundled VSPipe/x264/muxer unavailable")
    def test_worker_and_vspipe_keep_default_script_cache_at_16000(self):
        """worker 与 runner 的预设 runtime 值都必须被默认脚本覆写。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session_root = root / "session"
            appdata = session_root / "appdata"
            override = (
                appdata
                / "ArknightsPassMaker"
                / "vapoursynth"
                / "vs_runtime.user.json"
            )
            override.parent.mkdir(parents=True)
            override.write_text(
                json.dumps({"core": {"max_cache_size_mb": 37}}),
                encoding="utf-8",
            )
            source_path = root / "source.png"
            self.assertTrue(
                cv2.imwrite(str(source_path), np.full((16, 16, 3), 127, np.uint8))
            )
            session = build_default_render_session(
                session_root,
                source_path=source_path,
                source_kind="image",
                end_frame=1,
            )
            script_path = Path(session.selection.script_path)
            source = script_path.read_text(encoding="utf-8")
            instrumented = source.replace(
                "core = vs.core",
                "assert vs.core.max_cache_size == 37\ncore = vs.core",
                1,
            )
            self.assertNotEqual(instrumented, source)
            script_path.write_text(
                instrumented
                + "\nassert core.max_cache_size == 16000, core.max_cache_size\n"
                + f"print({MARKER!r})\n"
                + "_cache_probe_clip = vs.get_output(0).clip\n"
                + "def _cache_probe_frame(n):\n"
                + "    assert core.max_cache_size == 16000, core.max_cache_size\n"
                + "    return _cache_probe_clip\n"
                + "core.std.FrameEval(\n"
                + "    _cache_probe_clip, eval=_cache_probe_frame\n"
                + ").set_output(0)\n",
                encoding="utf-8",
            )
            session = replace(
                session,
                selection=replace(
                    session.selection,
                    bundle_hash=compute_script_bundle_hash(script_path),
                ),
            )
            runtime = load_vs_runtime(ROOT / "config" / "vs_runtime.json", override)
            self.assertEqual(runtime.core.max_cache_size_mb, 37)
            self.assertEqual(
                session.runtime_fingerprint,
                compute_runtime_fingerprint(ROOT, runtime),
            )

            with self.subTest(backend="preview worker"):
                worker = SyncVSWorkerProcess(
                    app_dir=ROOT,
                    env={**os.environ, "APPDATA": str(appdata)},
                )
                events = []
                worker.transport.add_listener(events.append)
                try:
                    worker.start(timeout_ms=15_000)
                    metadata = worker.load(session, timeout_ms=30_000)
                    self.assertEqual(metadata.output0.num_frames, 1)
                    # load 完成后再请求一帧，确保后续执行未重置 cache。
                    digests = worker.request_plane_digest(
                        epoch=session.epoch, index=0, surface="final"
                    )
                    self.assertTrue(digests)
                    self.assertIn(MARKER, str(events))
                except WorkerRequestError as exc:
                    self.fail(
                        "默认脚本未将 worker cache 覆写为 16000: "
                        f"{exc.response}"
                    )
                finally:
                    worker.close()

            request = VSPipeRenderRequest(
                runner_path=str(RUNNER),
                script_path=str(script_path),
                job_path=session.job_path,
                expected_job_sha256=hashlib.sha256(
                    Path(session.job_path).read_bytes()
                ).hexdigest(),
                api_version=session.selection.api_version,
                mode=session.selection.mode,
                app_dir=str(ROOT),
                runtime=runtime,
                runtime_fingerprint=session.runtime_fingerprint,
            )
            command = [
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
                    runtime=runtime,
                    expected_fingerprint=request.runtime_fingerprint,
                ),
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            with self.subTest(backend="VSPipe --info"):
                result = subprocess.run(command, **kwargs)
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                self.assertIn(MARKER, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
