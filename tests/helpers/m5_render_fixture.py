"""M5 真实编码测试共用的 frozen-session fixture。

这里故意不调用 ``write_vpy_script``：测试必须和生产一样，让 fixed runner
消费 canonical user VPy、RenderJob 与 worker 预检得到的 output 0 metadata。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Literal

from config.vs_runtime import load_vs_runtime
from core.export_service import ExportWorker
from core.media_pipeline import MediaEncoder, VSPipeRenderRequest
from core.vs_runtime.job import RationalFPS, RenderJob, write_render_job
from core.vs_runtime.script_header import parse_script_header
from core.vs_runtime.session import (
    RenderSession,
    ScriptSelection,
    compute_job_sha256,
    compute_script_bundle_hash,
)
from core.vs_runtime.vs_loader import compute_runtime_fingerprint
from core.vs_runtime.worker_process import SyncVSWorkerProcess


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "resources" / "vapoursynth" / "assetmaker_runner.vpy"
DEFAULT_PIPELINE = ROOT / "resources" / "vapoursynth" / "default_pipeline.vpy"


def _runtime_for(root: Path):
    return load_vs_runtime(
        ROOT / "config" / "vs_runtime.json",
        root / "appdata" / "ArknightsPassMaker" / "vapoursynth" / "vs_runtime.user.json",
    )


def _output_spec(profile: str) -> dict[str, object]:
    if profile != "360x640":
        raise ValueError(f"unsupported M5 fixture profile: {profile}")
    return {
        "profile": profile,
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
    }


def build_default_render_session(
    root: Path,
    *,
    source_path: Path,
    source_kind: Literal["image", "video"] = "video",
    end_frame: int = 15,
    crop: tuple[int, int, int, int] = (0, 0, 0, 0),
    rotation: int = 0,
    fps: RationalFPS | None = None,
    profile: str = "360x640",
    epoch: int = 1,
) -> RenderSession:
    """构造 default user VPy 的冻结 session，供固定 runner 真实执行。"""
    if source_kind not in ("image", "video"):
        raise ValueError(f"unsupported source kind: {source_kind}")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    source_path = source_path.resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    script = root / "用户脚本" / "default_pipeline.vpy"
    script.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEFAULT_PIPELINE, script)
    fps = RationalFPS(30, 1) if fps is None else fps
    job = RenderJob.from_dict(
        {
            "api_version": 1,
            "epoch": epoch,
            "track": "loop",
            "project_root": str(root),
            "source": {
                "path": str(source_path),
                "kind": source_kind,
                "virtual_frame_count": end_frame if source_kind == "image" else None,
            },
            "timeline": {
                "start_frame": 0,
                "end_frame": end_frame,
                "fps": fps.to_dict(),
            },
            "transform": {
                "rotation": rotation,
                "crop": {
                    "coordinate_space": "post_rotation_source_pixels",
                    "x": crop[0],
                    "y": crop[1],
                    "width": crop[2],
                    "height": crop[3],
                },
            },
            "output": _output_spec(profile),
            "paths": {"cache_dir": str(root / "cache")},
        }
    )
    job_path = write_render_job(job)
    runtime = _runtime_for(root)
    return RenderSession(
        epoch=epoch,
        track="loop",
        selection=ScriptSelection.from_header(
            script, parse_script_header(script), compute_script_bundle_hash(script)
        ),
        job_path=str(job_path),
        job_sha256=compute_job_sha256(job_path),
        runtime_fingerprint=compute_runtime_fingerprint(ROOT, runtime),
    )


def preflight_encode_request(
    session: RenderSession,
) -> tuple[VSPipeRenderRequest, RationalFPS, object]:
    """以同一 session 完成真实 worker 预检，并返回生产编码所需的唯一输入。"""
    fixture_root = Path(session.job_path).resolve().parents[1]
    runtime = _runtime_for(fixture_root)
    worker = SyncVSWorkerProcess(
        app_dir=ROOT,
        env={**os.environ, "APPDATA": str(fixture_root / "appdata")},
    )
    try:
        worker.start(timeout_ms=15_000)
        metadata = worker.load(session, timeout_ms=30_000)
    finally:
        worker.close()
    request = VSPipeRenderRequest(
        runner_path=str(RUNNER.resolve()),
        script_path=session.selection.script_path,
        job_path=session.job_path,
        expected_job_sha256=session.job_sha256,
        api_version=session.selection.api_version,
        mode=session.selection.mode,
        app_dir=str(ROOT.resolve()),
        runtime=runtime,
        runtime_fingerprint=session.runtime_fingerprint,
    )
    return (
        request,
        RationalFPS(metadata.output0.fps_num, metadata.output0.fps_den),
        ExportWorker._vui_from_preflight(metadata),
    )


def encode_render_session(
    toolchain, session: RenderSession, output_path: Path
) -> None:
    """只接受 frozen session，走真实 preflight 后的严格生产编码入口。"""
    request, fps, vui = preflight_encode_request(session)
    MediaEncoder(toolchain).encode_vpy_to_mp4(
        request, str(output_path), fps, vui=vui
    )
