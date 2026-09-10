"""VapourSynth and x264-7mod export pipeline."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

from config.vs_runtime import VSRuntimeConfig
from core.media_tools import MediaToolchain, build_media_subprocess_env
from core.video_processor import X264_CLI_ARGS
from core.vs_runtime.output_contract import X264Vui
from core.vs_runtime.job import RationalFPS
from resources.vapoursynth.python.assetmaker_vs.runtime_fingerprint import (
    RUNTIME_MEDIA_ROOT_ENV,
    canonical_runtime_json_bytes,
)
from resources.vapoursynth.python.assetmaker_vs.runtime_layout import (
    RuntimeLayoutError,
    resolve_runtime_layout,
    sanitize_runtime_process_environment,
)

# VSPipe -p prints "Frame: <done>/<total>" lines (\r-refreshed) to stderr.
_VSPIPE_PROGRESS_RE = re.compile(rb"Frame:\s*(\d+)\s*/\s*(\d+)")


@dataclass(frozen=True)
class VSPipeRenderRequest:
    """固定 runner 执行一次已冻结用户脚本的参数数组。"""

    runner_path: str
    script_path: str
    job_path: str
    expected_job_sha256: str
    api_version: int
    mode: Literal["compatible", "raw"]
    app_dir: str
    runtime: VSRuntimeConfig
    runtime_fingerprint: str

    def __post_init__(self) -> None:
        if type(self.api_version) is not int or self.api_version != 1:
            raise ValueError("api_version 必须是严格整数 1")
        if self.mode not in ("compatible", "raw"):
            raise ValueError("mode 必须是 compatible/raw")
        if not isinstance(self.runtime, VSRuntimeConfig):
            raise TypeError("runtime 必须是 VSRuntimeConfig")
        for field in ("expected_job_sha256", "runtime_fingerprint"):
            value = getattr(self, field)
            if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"{field} 必须是小写 SHA-256")


def build_vspipe_render_env(
    vspipe_path: str,
    *,
    app_dir: str,
    runtime: VSRuntimeConfig,
    expected_fingerprint: str,
) -> dict[str, str]:
    """构建唯一由冻结 VSRuntimeConfig 派生的 VSPipe 环境。

    不能复用 legacy ``build_media_subprocess_env``：它服务 x264/muxer 的
    PATH/PYTHONPATH，不能表达 VSPipe 的 frozen runtime 身份。
    """
    if not isinstance(runtime, VSRuntimeConfig):
        raise TypeError("runtime 必须是 VSRuntimeConfig")
    if len(expected_fingerprint) != 64:
        raise ValueError("expected_fingerprint 必须是 SHA-256")
    root = Path(app_dir).resolve()
    try:
        layout = resolve_runtime_layout(root)
    except RuntimeLayoutError as exc:
        raise ValueError(str(exc)) from exc
    expected_vspipe = layout.vspipe_executable
    actual_vspipe = Path(vspipe_path).resolve()
    if actual_vspipe != expected_vspipe.resolve():
        raise ValueError(
            "VSPipe 必须来自已验证的 R79 runtime layout，"
            f"实际为: {actual_vspipe}"
        )
    env = sanitize_runtime_process_environment(os.environ, layout)
    python_dirs = [str(Path(path)) for path in runtime.plugins.python_module_dirs]
    # R79 把该值解释为一个自动加载目录；native 目录保留在冻结 runtime JSON，
    # 由 runner 在 import 后逐目录显式 LoadAllPlugins。
    env["ASSETMAKER_VS_PYTHON_DIRS_JSON"] = json.dumps(
        python_dirs, ensure_ascii=False, separators=(",", ":")
    )
    env["ASSETMAKER_VS_APP_DIR"] = str(root)
    env["ASSETMAKER_VS_RUNTIME_CONFIG_JSON"] = canonical_runtime_json_bytes(
        runtime.to_dict()
    ).decode("utf-8")
    env["ASSETMAKER_VS_RUNTIME_FINGERPRINT"] = expected_fingerprint
    env[RUNTIME_MEDIA_ROOT_ENV] = str(layout.media_root)
    # clean install 只携带源码时，runner import helper 生成的 __pycache__ 不得
    # 反过来改变本次预检已固定的 runtime fingerprint。
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def build_vspipe_command(
    vspipe_path: str, request: VSPipeRenderRequest
) -> list[str]:
    """Build a VSPipe command that emits Y4M to stdout.

    ``-p`` enables per-frame progress on stderr (VSPipe R73 --help:
    "-p, --progress   Print progress to stderr") so the export dialog can
    show real progress instead of freezing at a fixed percentage.  VSPipe's
    ``--arg key=value`` ABI receives every path as one argv element, rather
    than through a shell-quoted command string.
    """
    if not isinstance(request, VSPipeRenderRequest):
        raise TypeError("request 必须是 VSPipeRenderRequest")
    return [
        vspipe_path,
        "-c",
        "y4m",
        "-p",
        "--arg",
        f"assetmaker_job={request.job_path}",
        "--arg",
        f"expected_job_sha256={request.expected_job_sha256}",
        "--arg",
        f"assetmaker_script={request.script_path}",
        "--arg",
        f"assetmaker_api={request.api_version}",
        "--arg",
        f"assetmaker_mode={request.mode}",
        request.runner_path,
        "-",
    ]


def build_x264_command(
    x264_path: str,
    output_path: str,
    *,
    crf: int = 26,
    preset: str = "veryslow",
    vui: X264Vui,
) -> list[str]:
    """Build an x264-7mod command that consumes Y4M from stdin.

    ``vui`` is deliberately mandatory. x264-7mod defaults to undefined
    matrix/primaries/transfer and automatic range; the values must instead
    come from the same worker-validated output 0 that VSPipe consumes.
    """
    if not isinstance(vui, X264Vui):
        raise TypeError("vui 必须是 output 0 派生的 X264Vui")
    return [
        x264_path,
        "--demuxer",
        "y4m",
        "--preset",
        preset,
        "--crf",
        str(crf),
        "--profile",
        "high",
        "--output-csp",
        "i420",
        "--colormatrix",
        vui.colormatrix,
        "--colorprim",
        vui.colorprim,
        "--transfer",
        vui.transfer,
        "--range",
        vui.range_,
        *X264_CLI_ARGS,
        "--output",
        output_path,
        "-",
    ]


def _format_fps(fps: RationalFPS) -> str:
    """Format a frozen job frame rate for the muxers without reconstruction.

    MP4Box documents ``-fps`` as "expressed as a number, as TS-inc or TS/inc"
    (mp4box -h import), so ``30000/1001`` is valid. The export boundary must
    carry the worker-approved numerator and denominator directly: converting to
    float and guessing a fraction again can change valid non-common rates.
    """
    if not isinstance(fps, RationalFPS):
        raise TypeError("fps 必须是冻结 RenderJob 的 RationalFPS")
    if fps.denominator == 1:
        return str(fps.numerator)
    return f"{fps.numerator}/{fps.denominator}"


def build_mp4box_mux_command(
    muxer_path: str,
    raw_h264_path: str,
    output_path: str,
    fps: RationalFPS,
) -> list[str]:
    """Build an MP4Box command for wrapping raw H.264 into MP4."""
    return [
        muxer_path,
        "-add",
        f"{raw_h264_path}:fps={_format_fps(fps)}",
        "-new",
        output_path,
    ]


def build_lsmash_mux_command(
    muxer_path: str,
    raw_h264_path: str,
    output_path: str,
    fps: RationalFPS,
) -> list[str]:
    """Build an lsmash-muxer command for wrapping raw H.264 into MP4."""
    return [
        muxer_path,
        "-i",
        raw_h264_path,
        "--fps",
        _format_fps(fps),
        "-o",
        output_path,
    ]


def build_mux_command(
    muxer_path: str,
    raw_h264_path: str,
    output_path: str,
    fps: RationalFPS,
) -> list[str]:
    """Build the configured MP4 muxer command."""
    muxer_name = Path(muxer_path).name.lower()
    if "mp4box" in muxer_name:
        return build_mp4box_mux_command(muxer_path, raw_h264_path, output_path, fps)
    return build_lsmash_mux_command(muxer_path, raw_h264_path, output_path, fps)


class MediaEncoder:
    """Run VSPipe and x264-7mod as a cancellable encode pipeline."""

    def __init__(self, toolchain: MediaToolchain):
        self.toolchain = toolchain
        self.active_processes: list[subprocess.Popen] = []

    def terminate_active_processes(self) -> None:
        processes = list(self.active_processes)
        for process in processes:
            try:
                is_running = process.poll() is None
            except Exception:
                # A partially started child can fail even while its Popen state is
                # queried. Its state is unknown, so still attempt bounded cleanup.
                is_running = True
            if is_running:
                try:
                    process.terminate()
                except Exception:
                    pass
        for process in processes:
            try:
                is_running = process.poll() is None
            except Exception:
                is_running = True
            if is_running:
                try:
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
        self.active_processes.clear()

    def encode_vpy_to_mp4(
        self,
        request: VSPipeRenderRequest,
        output_path: str,
        fps: RationalFPS,
        *,
        vui: X264Vui,
        is_cancelled: Optional[Callable[[], bool]] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        if not isinstance(fps, RationalFPS):
            raise TypeError("fps 必须是冻结 RenderJob 的 RationalFPS")
        if is_cancelled and is_cancelled():
            self.terminate_active_processes()
            raise InterruptedError("Export cancelled")

        missing = self.toolchain.missing_for_export()
        if missing:
            raise RuntimeError("Missing media tools: " + ", ".join(missing))
        if not self.toolchain.muxer_path:
            raise RuntimeError(
                "Missing MP4 muxer: MP4Box or lsmash-muxer is required because "
                "x264-7mod writes raw H.264 before MP4 packaging"
            )

        output_root, output_ext = os.path.splitext(output_path)
        temp_output = f"{output_root}.tmp{output_ext or '.mp4'}"
        temp_raw = f"{output_root}.tmp.264"
        if os.path.exists(temp_output):
            os.remove(temp_output)
        if os.path.exists(temp_raw):
            os.remove(temp_raw)

        try:
            result = self._run_encode_pipeline(
                request, temp_raw, vui, is_cancelled, progress_cb
            )
            if result["vspipe_returncode"] != 0:
                details = str(result["stderr"])[-1000:].strip()
                message = f"VSPipe failed with code {result['vspipe_returncode']}"
                if details:
                    message = f"{message}: {details}"
                raise RuntimeError(message)
            if result["x264_returncode"] != 0:
                raise RuntimeError(
                    f"x264-7mod failed with code {result['x264_returncode']}: "
                    + result["stderr"][-500:]
                )

            self._run_muxer(temp_raw, temp_output, fps)
            os.replace(temp_output, output_path)
        finally:
            # A failed/cancelled encode used to litter the export dir with
            # .tmp.264 / .tmp.mp4 leftovers — clean up on every exit path.
            for leftover in (temp_raw, temp_output):
                try:
                    if os.path.exists(leftover):
                        os.remove(leftover)
                except OSError:
                    pass

    def _run_encode_pipeline(
        self,
        request: VSPipeRenderRequest,
        output_path: str,
        vui: X264Vui,
        is_cancelled: Optional[Callable[[], bool]] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> dict[str, object]:
        vspipe_cmd = build_vspipe_command(self.toolchain.vspipe_path, request)
        x264_cmd = build_x264_command(
            self.toolchain.x264_path, output_path, vui=vui
        )
        env = build_vspipe_render_env(
            self.toolchain.vspipe_path,
            app_dir=request.app_dir,
            runtime=request.runtime,
            expected_fingerprint=request.runtime_fingerprint,
        )
        popen_kwargs = {
            "env": env,
            "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        }
        if not popen_kwargs["creationflags"]:
            popen_kwargs.pop("creationflags")

        with _suppress_windows_error_dialogs():
            vspipe = subprocess.Popen(
                vspipe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_kwargs
            )
        # Register VSPipe before attempting x264: its startup can fail after
        # VSPipe owns both pipe handles, and the exception path must use the
        # same bounded terminate -> wait -> kill cleanup as cancellation.
        self.active_processes = [vspipe]
        x264: subprocess.Popen | None = None
        try:
            with _suppress_windows_error_dialogs():
                x264 = subprocess.Popen(
                    x264_cmd,
                    stdin=vspipe.stdout,
                    # x264 writes the H.264 bitstream to its --output file; its stdout is
                    # unused. Piping it to an unread PIPE was pure deadlock surface -> DEVNULL.
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    **popen_kwargs,
                )
        finally:
            if x264 is None:
                for stream in (vspipe.stdout, vspipe.stderr):
                    try:
                        if stream is not None:
                            stream.close()
                    except Exception:
                        pass
                self.terminate_active_processes()
        if vspipe.stdout is not None:
            vspipe.stdout.close()
        self.active_processes = [vspipe, x264]

        # Drain both children's stderr concurrently in background threads. vspipe emits
        # per-frame progress and x264 (veryslow) prints periodic progress to stderr; on a
        # long encode a full OS pipe buffer blocks the writing child, which stalls the
        # pipeline and hangs the poll loop below. The Python subprocess docs warn that a
        # poll/wait loop with unread PIPEs deadlocks — reader threads are the fix.
        stderr_bufs: dict[str, bytes] = {}

        def _drain(proc, key):
            try:
                stderr_bufs[key] = proc.stderr.read() if proc.stderr is not None else b""
            except Exception:
                stderr_bufs[key] = b""
            finally:
                try:
                    if proc.stderr is not None:
                        proc.stderr.close()
                except Exception:
                    pass

        def _drain_vspipe_with_progress(proc, key):
            """Drain vspipe stderr AND surface `Frame: n/total` progress.

            The plain _drain above swallowed the -p output, leaving the export
            dialog frozen at a fixed percentage for the whole encode. VSPipe
            refreshes the progress line with \r, so split on [\r\n].
            """
            chunks: list[bytes] = []
            pending = b""
            try:
                stream = proc.stderr
                if stream is None:
                    return
                while True:
                    chunk = stream.read1(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    pending += chunk
                    *complete, pending = re.split(rb"[\r\n]", pending)
                    for line in complete:
                        match = _VSPIPE_PROGRESS_RE.search(line)
                        if match:
                            try:
                                progress_cb(int(match.group(1)), int(match.group(2)))
                            except Exception:
                                pass
            except Exception:
                pass
            finally:
                stderr_bufs[key] = b"".join(chunks)
                try:
                    if proc.stderr is not None:
                        proc.stderr.close()
                except Exception:
                    pass

        vspipe_drain = (
            _drain_vspipe_with_progress if progress_cb is not None else _drain
        )
        readers = [
            threading.Thread(target=vspipe_drain, args=(vspipe, "vspipe"), daemon=True),
            threading.Thread(target=_drain, args=(x264, "x264"), daemon=True),
        ]
        for reader in readers:
            reader.start()

        try:
            while x264.poll() is None:
                if is_cancelled and is_cancelled():
                    self.terminate_active_processes()
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    raise InterruptedError("Export cancelled")
                time.sleep(0.1)
            vspipe.wait(timeout=5)
        finally:
            self.terminate_active_processes()

        for reader in readers:
            reader.join(timeout=5)

        vspipe_stderr = stderr_bufs.get("vspipe", b"")
        x264_stderr = stderr_bufs.get("x264", b"")
        return {
            "vspipe_returncode": int(vspipe.returncode or 0),
            "x264_returncode": int(x264.returncode or 0),
            "stderr": (
                x264_stderr.decode("utf-8", errors="replace")
                + vspipe_stderr.decode("utf-8", errors="replace")
            ),
        }

    def _run_muxer(
        self, raw_h264_path: str, output_path: str, fps: RationalFPS
    ) -> None:
        cmd = build_mux_command(self.toolchain.muxer_path, raw_h264_path, output_path, fps)
        run_kwargs = {
            "capture_output": True,
            "env": build_media_subprocess_env(self.toolchain.muxer_path),
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": 120,
        }
        if sys.platform == "win32":
            run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        with _suppress_windows_error_dialogs():
            result = subprocess.run(cmd, **run_kwargs)
        if result.returncode != 0:
            raise RuntimeError(f"MP4 muxer failed: {result.stderr[-500:]}")


@contextmanager
def _suppress_windows_error_dialogs():
    if sys.platform != "win32":
        yield
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32
    old_mode = kernel32.SetErrorMode(
        0x0001 | 0x0002 | 0x8000
    )
    try:
        yield
    finally:
        kernel32.SetErrorMode(old_mode)


__all__ = [
    "MediaEncoder",
    "MediaToolchain",
    "VSPipeRenderRequest",
    "X264Vui",
    "build_vspipe_command",
    "build_x264_command",
    "build_mp4box_mux_command",
    "build_lsmash_mux_command",
    "build_mux_command",
]
