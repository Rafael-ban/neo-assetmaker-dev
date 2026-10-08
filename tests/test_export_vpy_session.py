"""M5 第二批：导出冻结 preview session，并以同一 session 做 worker 预检。"""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import tempfile
import unittest
import inspect
import shutil
from pathlib import Path
from unittest import mock

from core.media_tools import MediaToolchain


ROOT = Path(__file__).resolve().parents[1]
REAL_TOOLCHAIN = MediaToolchain.discover(str(ROOT))
REAL_EXPORT_READY = not REAL_TOOLCHAIN.missing_for_export()
REAL_VSPIPE_READY = bool(REAL_TOOLCHAIN.vspipe_path) and Path(
    REAL_TOOLCHAIN.vspipe_path
).is_file()


def _session(root: Path, *, bundle_hash: str | None = None, runtime: str = "b" * 64):
    from core.vs_runtime.session import (
        RenderSession,
        ScriptSelection,
        compute_script_bundle_hash,
    )

    script = root / "pipeline.vpy"
    job = root / "job-7.json"
    script.write_text("# test", encoding="utf-8")
    job.write_text("{}", encoding="utf-8")
    bundle_hash = bundle_hash or compute_script_bundle_hash(script)
    return RenderSession(
        epoch=7,
        track="loop",
        selection=ScriptSelection(
            script_path=str(script), mode="compatible", bundle_hash=bundle_hash
        ),
        job_path=str(job),
        job_sha256=hashlib.sha256(job.read_bytes()).hexdigest(),
        runtime_fingerprint=runtime,
    )


class ExportSessionTests(unittest.TestCase):
    def test_video_export_task_requires_immutable_render_session(self):
        from core.export_service import ExportTask, ExportType, VideoExportParams

        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(Path(temp_dir))
            task = ExportTask(ExportType.LOOP_VIDEO, "loop.mp4", session)

        self.assertIs(task.session, session)
        with self.assertRaises(TypeError):
            ExportTask(ExportType.LOOP_VIDEO, "loop.mp4", object())
        with self.assertRaises(TypeError):
            ExportTask(
                ExportType.LOOP_VIDEO,
                "loop.mp4",
                VideoExportParams("legacy.mp4", (0, 0, 1, 1), 0, 1, 30.0),
            )

    def test_preflight_output_vui_is_required_before_encoding(self):
        from core.export_service import ExportWorker
        from core.vs_runtime.session import NodeMetadata, SessionMetadata

        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(Path(temp_dir))
            worker = ExportWorker()
            worker._media_toolchain = mock.Mock(missing_for_export=mock.Mock(return_value=[]))
            metadata = SessionMetadata(
                epoch=session.epoch,
                mode=session.selection.mode,
                capabilities=frozenset(),
                output0=NodeMetadata(
                    width=360, height=640, num_frames=30,
                    fps_num=30000, fps_den=1001, pixel_format="YUV420P8",
                    matrix="170m", transfer="170m", primaries="170m",
                    range="limited",
                ),
                editor=None,
            )
            with mock.patch(
                "core.export_service.SyncVSWorkerProcess"
            ) as process_type, mock.patch(
                "core.export_service.MediaEncoder"
            ) as encoder_type, mock.patch.object(
                ExportWorker,
                "_runtime_fingerprint_for_export",
                return_value=session.runtime_fingerprint,
            ):
                process_type.return_value.load.return_value = metadata
                worker._export_video(str(Path(temp_dir) / "loop.mp4"), session, 0)

        encoder_type.return_value.encode_vpy_to_mp4.assert_called_once()
        self.assertEqual(
            encoder_type.return_value.encode_vpy_to_mp4.call_args.kwargs["vui"].to_dict(),
            {
                "colormatrix": "smpte170m",
                "colorprim": "smpte170m",
                "transfer": "smpte170m",
                "range": "tv",
            },
        )

    def test_export_video_preflights_frozen_session_before_encoding(self):
        """资格门只检查 executable；worker preflight 验证 frozen session。"""
        from core.export_service import ExportWorker
        from core.vs_runtime.session import NodeMetadata, SessionMetadata

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = _session(root)
            vspipe = root / "VSPipe.exe"
            vspipe.touch()
            worker = ExportWorker()
            worker._media_toolchain = MediaToolchain(
                vspipe_path=str(vspipe),
                x264_path="x264-7mod.exe",
                muxer_path="MP4Box.exe",
            )
            metadata = SessionMetadata(
                epoch=session.epoch,
                mode=session.selection.mode,
                capabilities=frozenset(),
                output0=NodeMetadata(
                    width=360, height=640, num_frames=30,
                    fps_num=30000, fps_den=1001, pixel_format="YUV420P8",
                    matrix="170m", transfer="170m", primaries="170m",
                    range="limited",
                ),
                editor=None,
            )
            with mock.patch("core.export_service.SyncVSWorkerProcess") as process_type, mock.patch(
                "core.export_service.MediaEncoder"
            ) as encoder_type, mock.patch.object(
                ExportWorker,
                "_runtime_fingerprint_for_export",
                return_value=session.runtime_fingerprint,
            ):
                process_type.return_value.load.return_value = metadata
                worker._export_video(str(root / "loop.mp4"), session, 0)

        encoder_type.return_value.encode_vpy_to_mp4.assert_called_once()

    def test_preflight_identity_mismatch_fails_before_encoding(self):
        from core.export_service import ExportWorker

        with tempfile.TemporaryDirectory() as temp_dir:
            session = _session(Path(temp_dir))
            worker = ExportWorker()
            worker._media_toolchain = mock.Mock(missing_for_export=mock.Mock(return_value=[]))
            with mock.patch(
                "core.export_service.SyncVSWorkerProcess"
            ) as process_type:
                with mock.patch(
                    "core.export_service.compute_script_bundle_hash",
                    return_value="c" * 64,
                ), mock.patch("core.export_service.MediaEncoder") as encoder_type:
                    with self.assertRaisesRegex(RuntimeError, "预检前"):
                        worker._export_video(
                            str(Path(temp_dir) / "loop.mp4"), session, 0
                        )

        encoder_type.return_value.encode_vpy_to_mp4.assert_not_called()

    def test_encode_time_script_change_rejects_staging_output(self):
        from core.export_service import ExportWorker
        from core.vs_runtime.session import NodeMetadata, SessionMetadata

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = _session(root)
            output = root / "staging" / "loop.mp4"
            output.parent.mkdir()
            worker = ExportWorker()
            worker._media_toolchain = mock.Mock(missing_for_export=mock.Mock(return_value=[]))
            metadata = SessionMetadata(
                epoch=session.epoch,
                mode=session.selection.mode,
                capabilities=frozenset(),
                output0=NodeMetadata(
                    width=360, height=640, num_frames=30,
                    fps_num=30000, fps_den=1001, pixel_format="YUV420P8",
                    matrix="170m", transfer="170m", primaries="170m",
                    range="limited",
                ),
                editor=None,
            )

            def mutate_script(*_args, **_kwargs):
                output.write_bytes(b"partial")
                Path(session.selection.script_path).write_text("# changed", encoding="utf-8")

            with mock.patch("core.export_service.SyncVSWorkerProcess") as process_type, mock.patch(
                "core.export_service.MediaEncoder"
            ) as encoder_type, mock.patch.object(
                ExportWorker,
                "_runtime_fingerprint_for_export",
                return_value=session.runtime_fingerprint,
            ):
                process_type.return_value.load.return_value = metadata
                encoder_type.return_value.encode_vpy_to_mp4.side_effect = mutate_script
                with self.assertRaisesRegex(RuntimeError, "编码后"):
                    worker._export_video(str(output), session, 0)

            self.assertFalse(output.exists())

    def test_export_video_consumes_callers_export_owned_job_before_runner(self):
        from core.export_service import ExportWorker
        from core.vs_runtime.session import NodeMetadata, SessionMetadata

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = _session(root)
            worker = ExportWorker()
            worker._staging_dir = str(root / "staging")
            Path(worker._staging_dir).mkdir(exist_ok=True)
            worker._media_toolchain = mock.Mock(missing_for_export=mock.Mock(return_value=[]))
            metadata = SessionMetadata(
                epoch=session.epoch, mode=session.selection.mode, capabilities=frozenset(),
                output0=NodeMetadata(360, 640, 30, 30000, 1001, "YUV420P8", "170m", "170m", "170m", "limited"),
                editor=None,
            )
            with mock.patch("core.export_service.SyncVSWorkerProcess") as process_type, mock.patch(
                "core.export_service.MediaEncoder"
            ) as encoder_type, mock.patch.object(
                ExportWorker,
                "_runtime_fingerprint_for_export",
                return_value=session.runtime_fingerprint,
            ):
                process_type.return_value.load.return_value = metadata
                worker._export_video(str(root / "staging" / "loop.mp4"), session, 0)

        request = encoder_type.return_value.encode_vpy_to_mp4.call_args.args[0]
        self.assertEqual(request.job_path, session.job_path)

    def test_preflight_and_runner_share_one_snapshot_when_original_job_changes(self):
        """预检与 VSPipe 必须读取同一个冻结 job，而不是先后读原文件/副本。"""
        from core.export_service import ExportWorker
        from core.vs_runtime.session import NodeMetadata, SessionMetadata

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = _session(root)
            original_job = Path(session.job_path)
            export_job = root / "staging" / ".assetmaker-work" / "jobs" / "loop-7.json"
            export_job.parent.mkdir(parents=True)
            export_job.write_bytes(original_job.read_bytes())
            from dataclasses import replace
            export_session = replace(
                session,
                job_path=str(export_job),
                job_sha256=hashlib.sha256(export_job.read_bytes()).hexdigest(),
            )
            worker = ExportWorker()
            worker._staging_dir = str(root / "staging")
            Path(worker._staging_dir).mkdir(exist_ok=True)
            worker._media_toolchain = mock.Mock(missing_for_export=mock.Mock(return_value=[]))
            metadata = SessionMetadata(
                epoch=session.epoch, mode=session.selection.mode, capabilities=frozenset(),
                output0=NodeMetadata(360, 640, 30, 30000, 1001, "YUV420P8", "170m", "170m", "170m", "limited"),
                editor=None,
            )
            seen = {}

            def load(frozen_session):
                seen["worker_path"] = frozen_session.job_path
                seen["worker_payload"] = Path(frozen_session.job_path).read_bytes()
                # 模拟 UI/文件系统在 snapshot 后改写原 job；本次导出不得受影响。
                original_job.write_text('{"changed": true}', encoding="utf-8")
                return metadata

            def encode(request, *_args, **_kwargs):
                seen["runner_path"] = request.job_path
                seen["runner_payload"] = Path(request.job_path).read_bytes()

            with mock.patch("core.export_service.SyncVSWorkerProcess") as process_type, mock.patch(
                "core.export_service.MediaEncoder"
            ) as encoder_type, mock.patch.object(
                ExportWorker,
                "_runtime_fingerprint_for_export",
                return_value=session.runtime_fingerprint,
            ):
                process_type.return_value.load.side_effect = load
                encoder_type.return_value.encode_vpy_to_mp4.side_effect = encode
                worker._export_video(
                    str(root / "staging" / "loop.mp4"), export_session, 0
                )

            self.assertNotEqual(seen["worker_path"], session.job_path)
            self.assertEqual(seen["worker_path"], export_session.job_path)
            self.assertEqual(seen["worker_path"], seen["runner_path"])
            self.assertEqual(seen["worker_payload"], b"{}")
            self.assertEqual(seen["runner_payload"], b"{}")
            self.assertEqual(original_job.read_text(encoding="utf-8"), '{"changed": true}')

    def test_preflight_rejects_export_job_replacement_before_encoding(self):
        """预检后同路径替换 job 时，VSPipe 绝不能消费新内容。"""
        from core.export_service import ExportWorker
        from core.vs_runtime.session import NodeMetadata, SessionMetadata
        from dataclasses import replace

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            session = _session(root)
            export_job = root / "staging" / ".assetmaker-work" / "jobs" / "loop-7.json"
            export_job.parent.mkdir(parents=True)
            export_job.write_bytes(Path(session.job_path).read_bytes())
            export_session = replace(
                session,
                job_path=str(export_job),
                job_sha256=hashlib.sha256(export_job.read_bytes()).hexdigest(),
            )
            worker = ExportWorker()
            worker._media_toolchain = mock.Mock(
                missing_for_export=mock.Mock(return_value=[])
            )
            metadata = SessionMetadata(
                epoch=export_session.epoch,
                mode=export_session.selection.mode,
                capabilities=frozenset(),
                output0=NodeMetadata(
                    360, 640, 30, 30000, 1001, "YUV420P8",
                    "170m", "170m", "170m", "limited",
                ),
                editor=None,
            )

            def replace_job_after_preflight(_session):
                export_job.write_text('{"replaced": true}', encoding="utf-8")
                return metadata

            with mock.patch("core.export_service.SyncVSWorkerProcess") as process_type, mock.patch(
                "core.export_service.MediaEncoder"
            ) as encoder_type, mock.patch.object(
                ExportWorker,
                "_runtime_fingerprint_for_export",
                return_value=export_session.runtime_fingerprint,
            ):
                process_type.return_value.load.side_effect = replace_job_after_preflight
                with self.assertRaisesRegex(RuntimeError, "预检后：.*job"):
                    worker._export_video(
                        str(root / "staging" / "loop.mp4"), export_session, 0
                    )

            encoder_type.return_value.encode_vpy_to_mp4.assert_not_called()


class ProductionVpyPathTests(unittest.TestCase):
    def test_export_and_simulator_bake_do_not_depend_on_generated_filter_vpy(self):
        from core.export_service import ExportWorker
        from gui.main_window import MainWindow

        self.assertNotIn("write_vpy_script", inspect.getsource(ExportWorker._export_video))
        self.assertNotIn(
            "write_vpy_script",
            inspect.getsource(MainWindow._bake_loop_image_for_simulator),
        )

    def test_production_export_no_longer_collects_legacy_vpy_inputs(self):
        """M5 收束后，生产调用点只有冻结 session/runner 这一条入口。"""
        from core.export_service import ExportWorker
        from gui.main_window import MainWindow

        self.assertNotIn(
            "VideoExportParams", inspect.getsource(MainWindow._collect_export_data)
        )
        self.assertNotIn(
            "loop_video_params", inspect.getsource(MainWindow._on_export)
        )
        self.assertNotIn("write_vpy_script", inspect.getsource(ExportWorker._export_video))
        self.assertNotIn("write_vpy_script", inspect.getsource(MainWindow._on_export))


@unittest.skipUnless(REAL_EXPORT_READY, "bundled VSPipe/x264/muxer unavailable")
class RealRunnerExportTests(unittest.TestCase):
    def test_real_runner_exports_frozen_image_session_with_special_paths(self):
        """真实 VSPipe→x264→muxer 必须消费 staging job，而非临时滤镜脚本。"""
        from PIL import Image

        from core.export_service import ExportWorker
        from core.vs_runtime.job import RenderJob, write_render_job
        from core.vs_runtime.session import (
            RenderSession,
            ScriptSelection,
            compute_script_bundle_hash,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "中文 空格 & '单引号'"
            root.mkdir()
            source = root / "静态 素材 & '一'.png"
            Image.new("RGB", (8, 6), (220, 40, 20)).save(source)
            script = root / "用户 脚本 & '一'" / "pipeline.vpy"
            script.parent.mkdir()
            shutil.copyfile(
                ROOT / "resources" / "vapoursynth" / "default_pipeline.vpy", script
            )
            cache_dir = root / "cache"
            job = RenderJob.from_dict(
                {
                    "api_version": 1,
                    "epoch": 41,
                    "track": "loop",
                    "project_root": str(root.resolve()),
                    "source": {
                        "path": str(source.resolve()),
                        "kind": "image",
                        "virtual_frame_count": 3,
                    },
                    "timeline": {
                        "start_frame": 0,
                        "end_frame": 3,
                        "fps": {"numerator": 30, "denominator": 1},
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
                        "coded_width": 360,
                        "coded_height": 640,
                        "pixel_format": "YUV420P8",
                        "matrix": "170m",
                        "transfer": "170m",
                        "primaries": "170m",
                        "range": "limited",
                        "final_rotate_180": False,
                    },
                    "paths": {"cache_dir": str(cache_dir.resolve())},
                }
            )
            job_path = write_render_job(job)
            worker = ExportWorker()
            worker._media_toolchain = REAL_TOOLCHAIN
            staging = root / "staging"
            staging.mkdir()
            worker._staging_dir = str(staging)
            session = RenderSession(
                epoch=41,
                track="loop",
                selection=ScriptSelection(
                    script_path=str(script),
                    mode="compatible",
                    bundle_hash=compute_script_bundle_hash(script),
                ),
                job_path=str(job_path),
                job_sha256=hashlib.sha256(job_path.read_bytes()).hexdigest(),
                runtime_fingerprint=worker._runtime_fingerprint_for_export(str(ROOT)),
            )
            output = staging / "loop.mp4"

            worker._export_video(str(output), session, 0)

            self.assertGreater(output.stat().st_size, 0)
            self.assertFalse(list(staging.glob("job-loop-41.json")))
            self.assertFalse(list(staging.glob("*.tmp.264")))
            self.assertFalse(list(staging.glob("*.tmp.mp4")))


@unittest.skipUnless(REAL_VSPIPE_READY, "bundled VSPipe unavailable")
class RealRunnerRuntimeIdentityTests(unittest.TestCase):
    """C1：真实 runner 必须在执行用户脚本前拒绝 runtime TOCTOU。"""

    def test_real_runner_rejects_runtime_mutation_before_user_script(self):
        from config.vs_runtime import load_vs_runtime
        from core.media_pipeline import (
            VSPipeRenderRequest,
            build_vspipe_command,
            build_vspipe_render_env,
        )
        from core.vs_runtime.session import (
            RenderSession,
            ScriptSelection,
            compute_script_bundle_hash,
        )
        from core.vs_runtime.vs_loader import compute_runtime_fingerprint
        from core.vs_runtime.worker_process import SyncVSWorkerProcess
        from core.vs_runtime.script_header import (
            parse_script_header,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve()
            appdata = root / "appdata"
            runtime_dir = root / "runtime-plugin"
            runtime_dir.mkdir()
            runtime_file = runtime_dir / "runtime_marker.py"
            original_runtime_source = "MARKER = 'before-preflight'\n"
            runtime_file.write_text(original_runtime_source, encoding="utf-8")
            override = (
                appdata
                / "ArknightsPassMaker"
                / "vapoursynth"
                / "vs_runtime.user.json"
            )
            override.parent.mkdir(parents=True)
            override.write_text(
                json.dumps(
                    {
                        "plugins": {
                            "python_module_dirs": [str(runtime_dir)],
                        },
                        "core": {
                            "num_threads": 1,
                            "max_cache_size_mb": 16,
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            runtime = load_vs_runtime(ROOT / "config" / "vs_runtime.json", override)
            fingerprint = compute_runtime_fingerprint(ROOT, runtime)
            sentinel = root / "user-script-ran.sentinel"
            script = root / "pipeline.vpy"
            fixture = (
                ROOT / "tests" / "fixtures" / "vs_scripts" / "raw_valid.vpy"
            ).read_text(encoding="utf-8")
            script.write_text(
                fixture.replace(
                    "\n\nimport vapoursynth as vs",
                    f"\n\nopen({str(sentinel)!r}, 'wb').close()\n"
                    "import vapoursynth as vs",
                    1,
                ),
                encoding="utf-8",
            )
            job = root / "job.json"
            job.write_text(
                json.dumps(
                    {
                        "api_version": 1,
                        "epoch": 43,
                        "track": "loop",
                        "project_root": str(root),
                        "source": {
                            "path": str(root / "source.mp4"),
                            "kind": "video",
                            "virtual_frame_count": None,
                        },
                        "timeline": {
                            "start_frame": 0,
                            "end_frame": 3,
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
                            "coded_width": 360,
                            "coded_height": 640,
                            "pixel_format": "YUV420P8",
                            "matrix": "170m",
                            "transfer": "170m",
                            "primaries": "170m",
                            "range": "limited",
                            "final_rotate_180": False,
                        },
                        "paths": {"cache_dir": str(root / "cache")},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            header = parse_script_header(script)
            session = RenderSession(
                epoch=43,
                track="loop",
                selection=ScriptSelection.from_header(
                    script, header, compute_script_bundle_hash(script)
                ),
                job_path=str(job),
                job_sha256=hashlib.sha256(job.read_bytes()).hexdigest(),
                runtime_fingerprint=fingerprint,
            )
            worker = SyncVSWorkerProcess(
                app_dir=ROOT,
                env={**os.environ, "APPDATA": str(appdata)},
            )
            self.addCleanup(worker.close)
            worker.start(timeout_ms=15_000)
            worker.load(session, timeout_ms=20_000)
            worker.close()
            self.assertTrue(sentinel.exists(), "preflight 未执行 sentinel 脚本")
            sentinel.unlink()
            request = VSPipeRenderRequest(
                runner_path=str(ROOT / "resources" / "vapoursynth" / "assetmaker_runner.vpy"),
                script_path=str(script),
                job_path=str(job),
                expected_job_sha256=hashlib.sha256(job.read_bytes()).hexdigest(),
                api_version=1,
                mode="raw",
                app_dir=str(ROOT),
                runtime=runtime,
                runtime_fingerprint=fingerprint,
            )
            command = build_vspipe_command(REAL_TOOLCHAIN.vspipe_path, request)

            for scenario in ("runtime_file_changed", "runtime_config_env_changed"):
                with self.subTest(scenario=scenario):
                    runtime_file.write_text(
                        "MARKER = 'changed-after-preflight'\n",
                        encoding="utf-8",
                    )
                    env = build_vspipe_render_env(
                        REAL_TOOLCHAIN.vspipe_path,
                        app_dir=str(ROOT),
                        runtime=runtime,
                        expected_fingerprint=fingerprint,
                    )
                    self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
                    if scenario == "runtime_config_env_changed":
                        runtime_file.write_text(original_runtime_source, encoding="utf-8")
                        changed_runtime = json.loads(
                            env["ASSETMAKER_VS_RUNTIME_CONFIG_JSON"]
                        )
                        changed_runtime["core"]["num_threads"] = 2
                        env["ASSETMAKER_VS_RUNTIME_CONFIG_JSON"] = json.dumps(
                            changed_runtime,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                    result = subprocess.run(
                        command,
                        cwd=ROOT,
                        env=env,
                        capture_output=True,
                        shell=False,
                        check=False,
                        timeout=30,
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, b"")
                    marker = b"ASSETMAKER_VS_ERROR:"
                    start = result.stderr.find(marker)
                    self.assertGreaterEqual(start, 0, result.stderr.decode("utf-8", "replace"))
                    payload = json.loads(
                        result.stderr[start + len(marker):].splitlines()[0]
                    )
                    self.assertEqual(payload["code"], "runtime.fingerprint_mismatch")
                    self.assertEqual(payload["field"], "ASSETMAKER_VS_RUNTIME_FINGERPRINT")
                    self.assertEqual(payload["expected"], fingerprint)
                    self.assertNotEqual(payload["actual"], fingerprint)
                    self.assertTrue(payload["hint"])
                    self.assertTrue(payload["message"])
                    self.assertFalse(sentinel.exists())


class MainWindowExportSessionTests(unittest.TestCase):
    def test_collect_export_data_flushes_loop_before_building_export_payload(self):
        from config.epconfig import EPConfig
        from gui.main_window import MainWindow

        window = MainWindow.__new__(MainWindow)
        window.video_preview = mock.Mock()
        window.intro_preview = mock.Mock()
        window._base_dir = tempfile.gettempdir()
        window._config = EPConfig()
        window._config.loop.file = "loop.mp4"
        window._snapshot_active_timeline_state = mock.Mock()
        window._collect_preview_media_state = mock.Mock(return_value=None)

        payload = MainWindow._collect_export_data(window)

        window.video_preview.flush_render_job.assert_called_once_with()
        window.intro_preview.flush_render_job.assert_not_called()
        self.assertIs(
            payload["loop_render_session"],
            window.video_preview.flush_render_job.return_value,
        )


if __name__ == "__main__":
    unittest.main()
