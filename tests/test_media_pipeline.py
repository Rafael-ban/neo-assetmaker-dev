import os
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock



def _render_request():
    from config.vs_runtime import VSRuntimeConfig
    from core.media_pipeline import VSPipeRenderRequest

    return VSPipeRenderRequest(
        runner_path="runner.vpy",
        script_path="script.vpy",
        job_path="job.json",
        expected_job_sha256="b" * 64,
        api_version=1,
        mode="compatible",
        app_dir="D:/AssetMaker",
        runtime=VSRuntimeConfig(),
        runtime_fingerprint="a" * 64,
    )


def _vui():
    from core.vs_runtime.output_contract import X264Vui

    return X264Vui(
        colormatrix="smpte170m",
        colorprim="smpte170m",
        transfer="smpte170m",
        range_="tv",
    )


class MediaToolchainTests(unittest.TestCase):
    def test_discovers_bundled_media_tools_without_ffmpeg(self):
        from core.media_tools import MediaToolchain

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            from tests.test_vs_runtime_layout import _build_r79_fixture

            _build_r79_fixture(root)
            media_dir = root / "tools" / "media"
            for name in ("x264-7mod.exe", "MP4Box.exe"):
                (media_dir / name).write_text("", encoding="utf-8")
            (root / "ffmpeg.exe").write_text("", encoding="utf-8")
            (root / "ffprobe.exe").write_text("", encoding="utf-8")

            toolchain = MediaToolchain.discover(root)

        self.assertEqual(Path(toolchain.vspipe_path).name, "vspipe.exe")
        self.assertEqual(Path(toolchain.x264_path).name, "x264-7mod.exe")
        self.assertEqual(Path(toolchain.muxer_path).name, "MP4Box.exe")
        self.assertNotIn("ffmpeg", toolchain.describe().lower())
        self.assertNotIn("ffprobe", toolchain.describe().lower())

    def test_encoder_commands_use_vspipe_y4m_and_x264_stdin(self):
        from config.vs_runtime import VSRuntimeConfig
        from core.media_pipeline import (
            VSPipeRenderRequest,
            build_vspipe_command,
            build_x264_command,
        )
        from core.vs_runtime.output_contract import X264Vui

        request = VSPipeRenderRequest(
            runner_path="runner.vpy",
            script_path="script.vpy",
            job_path="job.json",
            expected_job_sha256="b" * 64,
            api_version=1,
            mode="compatible",
            app_dir="D:/AssetMaker",
            runtime=VSRuntimeConfig(),
            runtime_fingerprint="a" * 64,
        )
        vspipe = build_vspipe_command("VSPipe.exe", request)
        x264 = build_x264_command(
            "x264-7mod.exe",
            "out.mp4",
            crf=26,
            preset="veryslow",
            vui=X264Vui(
                colormatrix="smpte170m",
                colorprim="smpte170m",
                transfer="smpte170m",
                range_="tv",
            ),
        )

        # -p enables VSPipe per-frame progress on stderr (drives the dialog).
        self.assertEqual(
            vspipe,
            [
                "VSPipe.exe",
                "-c",
                "y4m",
                "-p",
                "--arg",
                "assetmaker_job=job.json",
                "--arg",
                "expected_job_sha256=" + "b" * 64,
                "--arg",
                "assetmaker_script=script.vpy",
                "--arg",
                "assetmaker_api=1",
                "--arg",
                "assetmaker_mode=compatible",
                "runner.vpy",
                "-",
            ],
        )
        self.assertIn("--demuxer", x264)
        self.assertIn("y4m", x264)
        self.assertIn("--output", x264)
        self.assertIn("out.mp4", x264)
        self.assertIn("--partitions", x264)
        self.assertNotIn("--x264-params", x264)
        self.assertEqual(x264[-1], "-")
        self.assertNotIn("ffmpeg", " ".join(vspipe + x264).lower())

    def test_x264_signals_smpte170m_colour_in_vui(self):
        # x264-7mod defaults are --colorprim/--transfer/--colormatrix "undef" and
        # --range "auto" (x264-7mod --fullhelp); an untagged sub-HD stream is
        # decoded as BT.601 by convention (H.273), so the pipeline must tag what
        # it actually converted to.
        from core.media_pipeline import build_x264_command
        from core.vs_runtime.output_contract import X264Vui

        x264 = build_x264_command(
            "x264-7mod.exe",
            "out.264",
            vui=X264Vui(
                colormatrix="smpte170m",
                colorprim="smpte170m",
                transfer="smpte170m",
                range_="tv",
            ),
        )
        for flag, value in (
            ("--colormatrix", "smpte170m"),
            ("--colorprim", "smpte170m"),
            ("--transfer", "smpte170m"),
            ("--range", "tv"),
        ):
            self.assertIn(flag, x264)
            self.assertEqual(x264[x264.index(flag) + 1], value)

    def test_discovers_lsmash_muxer_when_mp4box_is_absent(self):
        from core.media_tools import MediaToolchain

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            from tests.test_vs_runtime_layout import _build_r79_fixture

            _build_r79_fixture(root)
            media_dir = root / "tools" / "media"
            for name in ("x264-7mod.exe", "lsmash-muxer.exe"):
                (media_dir / name).write_text("", encoding="utf-8")

            toolchain = MediaToolchain.discover(root)

        self.assertEqual(Path(toolchain.muxer_path).name, "lsmash-muxer.exe")

    def test_export_requires_external_mp4_muxer(self):
        from core.media_tools import MediaToolchain

        toolchain = MediaToolchain(
            vspipe_path="VSPipe.exe",
            x264_path="x264-7mod.exe",
        )

        self.assertEqual(["MP4Box or lsmash-muxer"], toolchain.missing_for_export())

    def test_export_eligibility_checks_only_the_three_executables(self):
        from core import media_tools
        from core.media_tools import MediaToolchain

        with tempfile.TemporaryDirectory() as temp_dir:
            vspipe = Path(temp_dir) / "VSPipe.exe"
            vspipe.touch()
            toolchain = MediaToolchain(
                vspipe_path=str(vspipe),
                x264_path="x264-7mod.exe",
                muxer_path="MP4Box.exe",
            )

            # 插件和脚本 callable 的资格只由 frozen RenderSession 的 worker
            # preflight + runner contract 判断；此 gate 只读取 executable 字段。
            with mock.patch.object(
                media_tools,
                "build_media_subprocess_env",
                side_effect=AssertionError("export gate must not build an env"),
            ):
                missing = toolchain.missing_for_export()

        self.assertEqual([], missing)

    def test_export_eligibility_reports_each_missing_executable(self):
        from core.media_tools import MediaToolchain

        cases = (
            (MediaToolchain(), ["VSPipe", "x264-7mod", "MP4Box or lsmash-muxer"]),
            (
                MediaToolchain(vspipe_path="VSPipe.exe", muxer_path="MP4Box.exe"),
                ["x264-7mod"],
            ),
            (
                MediaToolchain(vspipe_path="VSPipe.exe", x264_path="x264-7mod.exe"),
                ["MP4Box or lsmash-muxer"],
            ),
        )
        for toolchain, expected in cases:
            with self.subTest(toolchain=toolchain):
                self.assertEqual(toolchain.missing_for_export(), expected)

    def test_muxer_commands_use_rational_fps(self):
        # MP4Box accepts "num/den" (mp4box -h import: "-fps ... as TS/inc").
        # The muxer boundary accepts only the frozen job ratio, never a float
        # captured from metadata and reconstructed heuristically later.
        from core.media_pipeline import (
            build_lsmash_mux_command,
            build_mp4box_mux_command,
        )
        from core.vs_runtime.job import RationalFPS

        self.assertEqual(
            build_mp4box_mux_command(
                "MP4Box.exe", "video.264", "out.mp4", RationalFPS(30_000, 1_001)
            ),
            ["MP4Box.exe", "-add", "video.264:fps=30000/1001", "-new", "out.mp4"],
        )
        # Whole rates stay bare integers, not "30/1".
        self.assertEqual(
            build_lsmash_mux_command(
                "lsmash-muxer.exe", "video.264", "out.mp4", RationalFPS(30, 1)
            ),
            ["lsmash-muxer.exe", "-i", "video.264", "--fps", "30", "-o", "out.mp4"],
        )

    def test_muxer_commands_preserve_non_common_rational_fps_exactly(self):
        """Muxer must receive the frozen job ratio, never a float reconstruction."""
        from core.media_pipeline import build_mux_command
        from core.vs_runtime.job import RationalFPS

        fps = RationalFPS(123_457, 4_003)
        expected = {
            "MP4Box.exe": [
                "MP4Box.exe",
                "-add",
                "video.264:fps=123457/4003",
                "-new",
                "out.mp4",
            ],
            "lsmash-muxer.exe": [
                "lsmash-muxer.exe",
                "-i",
                "video.264",
                "--fps",
                "123457/4003",
                "-o",
                "out.mp4",
            ],
        }

        for muxer_path, command in expected.items():
            with self.subTest(muxer_path=muxer_path):
                try:
                    actual = build_mux_command(
                        muxer_path, "video.264", "out.mp4", fps
                    )
                except TypeError as exc:
                    self.fail(
                        "mux command must accept RationalFPS without converting it "
                        f"to float: {exc}"
                    )
                self.assertEqual(actual, command)


class EncoderRunTests(unittest.TestCase):
    def test_muxer_failure_decodes_utf8_and_invalid_stderr_without_reader_thread_error(self):
        """muxer 非零退出必须保留 UTF-8 原因，不能由 Windows locale 覆盖。"""
        from core.media_pipeline import MediaEncoder, MediaToolchain

        toolchain = MediaToolchain(
            vspipe_path="VSPipe.exe",
            x264_path="x264-7mod.exe",
            muxer_path="MP4Box.exe",
        )
        encoder = MediaEncoder(toolchain)
        stderr = io.StringIO()
        program = (
            "import sys; "
            "sys.stderr.buffer.write('编码失败：中文'.encode('utf-8') + b'\\xff'); "
            "raise SystemExit(7)"
        )

        with redirect_stderr(stderr), mock.patch(
            "core.media_pipeline.build_mux_command",
            return_value=[sys.executable, "-c", program],
        ), self.assertRaisesRegex(RuntimeError, "MP4 muxer failed: 编码失败：中文\\ufffd"):
            from core.vs_runtime.job import RationalFPS

            encoder._run_muxer("input.264", "output.mp4", RationalFPS(30, 1))

        self.assertNotIn("UnicodeDecodeError", stderr.getvalue())

    def test_x264_start_failure_releases_registered_vspipe_and_its_pipes(self):
        """A failed x264 launch must not orphan the already-started VSPipe."""
        from core.media_pipeline import MediaEncoder, MediaToolchain

        class FakePipe:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class FakeVSPipe:
            def __init__(self):
                self.stdout = FakePipe()
                self.stderr = FakePipe()
                self.returncode = None
                self.terminate_calls = 0
                self.wait_timeouts = []
                self.kill_calls = 0

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminate_calls += 1

            def wait(self, timeout=None):
                self.wait_timeouts.append(timeout)
                self.returncode = -15
                return self.returncode

            def kill(self):
                self.kill_calls += 1
                self.returncode = -9

        vspipe = FakeVSPipe()
        launches = 0

        def fake_popen(cmd, **kwargs):
            nonlocal launches
            launches += 1
            if launches == 1:
                return vspipe
            raise OSError("x264 launch failed")

        encoder = MediaEncoder(
            MediaToolchain(
                vspipe_path="VSPipe.exe",
                x264_path="x264-7mod.exe",
                muxer_path="MP4Box.exe",
            )
        )

        with mock.patch("core.media_pipeline.subprocess.Popen", fake_popen), mock.patch(
            "core.media_pipeline.build_vspipe_render_env", return_value={}
        ), self.assertRaisesRegex(OSError, "x264 launch failed"):
            encoder._run_encode_pipeline(_render_request(), "out.tmp.264", _vui())

        self.assertEqual(launches, 2)
        self.assertEqual(vspipe.terminate_calls, 1)
        self.assertEqual(vspipe.wait_timeouts, [2])
        self.assertEqual(vspipe.kill_calls, 0)
        self.assertTrue(vspipe.stdout.closed)
        self.assertTrue(vspipe.stderr.closed)
        self.assertEqual(encoder.active_processes, [])

    def test_x264_start_failure_survives_vspipe_poll_cleanup_error(self):
        """Cleanup must not replace x264's launch error when VSPipe poll fails."""
        from core.media_pipeline import MediaEncoder, MediaToolchain

        class FakePipe:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class FakeVSPipe:
            def __init__(self):
                self.stdout = FakePipe()
                self.stderr = FakePipe()
                self.terminate_calls = 0
                self.wait_timeouts = []
                self.kill_calls = 0

            def poll(self):
                raise RuntimeError("cleanup poll failed")

            def terminate(self):
                self.terminate_calls += 1

            def wait(self, timeout=None):
                self.wait_timeouts.append(timeout)
                raise subprocess.TimeoutExpired("VSPipe.exe", timeout)

            def kill(self):
                self.kill_calls += 1

        vspipe = FakeVSPipe()
        launches = 0

        def fake_popen(cmd, **kwargs):
            nonlocal launches
            launches += 1
            if launches == 1:
                return vspipe
            raise OSError("x264 launch failed")

        encoder = MediaEncoder(
            MediaToolchain(
                vspipe_path="VSPipe.exe",
                x264_path="x264-7mod.exe",
                muxer_path="MP4Box.exe",
            )
        )

        with mock.patch("core.media_pipeline.subprocess.Popen", fake_popen), mock.patch(
            "core.media_pipeline.build_vspipe_render_env", return_value={}
        ), self.assertRaisesRegex(OSError, "x264 launch failed"):
            encoder._run_encode_pipeline(_render_request(), "out.tmp.264", _vui())

        self.assertEqual(launches, 2)
        self.assertEqual(vspipe.terminate_calls, 1)
        self.assertEqual(vspipe.wait_timeouts, [2])
        self.assertEqual(vspipe.kill_calls, 1)
        self.assertTrue(vspipe.stdout.closed)
        self.assertTrue(vspipe.stderr.closed)
        self.assertEqual(encoder.active_processes, [])

    def test_run_encoder_terminates_pipeline_on_cancellation(self):
        from core.media_pipeline import MediaEncoder, MediaToolchain
        from core.vs_runtime.job import RationalFPS

        toolchain = MediaToolchain(
            vspipe_path="VSPipe.exe",
            x264_path="x264-7mod.exe",
            muxer_path="MP4Box.exe",
        )
        encoder = MediaEncoder(toolchain)
        cancelled = mock.Mock(return_value=True)

        with self.assertRaises(InterruptedError):
            encoder.encode_vpy_to_mp4(
                _render_request(),
                "out.mp4",
                RationalFPS(30, 1),
                vui=_vui(),
                is_cancelled=cancelled,
            )

        self.assertEqual(encoder.active_processes, [])

    def test_encoder_uses_external_muxer_without_trying_x264_mp4_output(self):
        from core.media_pipeline import MediaEncoder, MediaToolchain
        from core.vs_runtime.job import RationalFPS

        class FakePipe:
            def close(self):
                pass

        class FakePopen:
            calls = []

            def __init__(self, cmd, **kwargs):
                self.cmd = cmd
                self.kwargs = kwargs
                self.stdout = FakePipe()
                self.returncode = 0
                self.stderr_bytes = b""
                FakePopen.calls.append(cmd)
                if Path(cmd[0]).name == "x264-7mod.exe":
                    output_path = cmd[cmd.index("--output") + 1]
                    if output_path.endswith(".mp4"):
                        raise AssertionError("x264 must not write MP4 directly")
                    Path(output_path).write_bytes(b"raw")

            def poll(self):
                return self.returncode

            def communicate(self, timeout=None):
                return b"", self.stderr_bytes

            def wait(self, timeout=None):
                return self.returncode

            def terminate(self):
                self.returncode = 0

            def kill(self):
                self.returncode = 0

        mux_calls = []

        def fake_run(cmd, **kwargs):
            mux_calls.append(cmd)
            Path(cmd[-1]).write_bytes(b"mp4")
            return mock.Mock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "out.mp4"
            toolchain = MediaToolchain(
                    vspipe_path="VSPipe.exe",
                x264_path="x264-7mod.exe",
                muxer_path="MP4Box.exe",
            )
            encoder = MediaEncoder(toolchain)

            with mock.patch("core.media_pipeline.subprocess.Popen", FakePopen):
                with mock.patch("core.media_pipeline.subprocess.run", fake_run), mock.patch(
                    "core.media_pipeline.build_vspipe_render_env", return_value={}
                ):
                    encoder.encode_vpy_to_mp4(
                        _render_request(),
                        str(output_path),
                        RationalFPS(30, 1),
                        vui=_vui(),
                    )

            self.assertTrue(output_path.exists())

        x264_outputs = [
            call[call.index("--output") + 1]
            for call in FakePopen.calls
            if Path(call[0]).name == "x264-7mod.exe"
        ]
        self.assertEqual(1, len(x264_outputs))
        self.assertTrue(x264_outputs[0].endswith(".tmp.264"))
        self.assertEqual(
            mux_calls[0][0:3],
            ["MP4Box.exe", "-add", str(output_path.with_suffix(".tmp.264")) + ":fps=30"],
        )

    def test_vspipe_failure_includes_stderr_details(self):
        from core.media_pipeline import MediaEncoder, MediaToolchain
        from core.vs_runtime.job import RationalFPS

        class FakePipe:
            def close(self):
                pass

            def read(self):
                return b"Script evaluation failed: missing lsmas plugin"

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                self.cmd = cmd
                self.kwargs = kwargs
                self.stdout = FakePipe()
                self.stderr = FakePipe()
                self.returncode = 1 if Path(cmd[0]).name == "VSPipe.exe" else 0

            def poll(self):
                return self.returncode

            def communicate(self, timeout=None):
                return b"", b""

            def wait(self, timeout=None):
                return self.returncode

            def terminate(self):
                pass

            def kill(self):
                pass

        toolchain = MediaToolchain(
            vspipe_path="VSPipe.exe",
            x264_path="x264-7mod.exe",
            muxer_path="MP4Box.exe",
        )
        encoder = MediaEncoder(toolchain)

        with mock.patch("core.media_pipeline.subprocess.Popen", FakePopen):
            with mock.patch(
                "core.media_pipeline.build_vspipe_render_env", return_value={}
            ), self.assertRaisesRegex(RuntimeError, "missing lsmas plugin"):
                encoder.encode_vpy_to_mp4(
                    _render_request(),
                    "out.mp4",
                    RationalFPS(30, 1),
                    vui=_vui(),
                )


if __name__ == "__main__":
    unittest.main()
