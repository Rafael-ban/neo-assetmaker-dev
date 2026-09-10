"""无 Qt、无父进程 in-process VS 污染的 M5 真实 runner 子进程。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _y4m_plane_digests(payload: bytes) -> dict[str, str]:
    """从单帧 Y4M 提取 output 0 的有效 Y/U/V 行，不读取 stride padding。"""
    header, remainder = payload.split(b"\n", 1)
    if not header.startswith(b"YUV4MPEG2 "):
        raise AssertionError(f"unexpected Y4M header: {header[:100]!r}")
    frame_header, planes = remainder.split(b"\n", 1)
    if not frame_header.startswith(b"FRAME"):
        raise AssertionError(f"missing Y4M frame marker: {frame_header!r}")

    fields = dict(
        (field[:1], field[1:])
        for field in header.split()[1:]
        if field[:1] in {b"W", b"H"}
    )
    width, height = int(fields[b"W"]), int(fields[b"H"])
    y_size = width * height
    chroma_size = (width // 2) * (height // 2)
    expected_size = y_size + 2 * chroma_size
    if len(planes) != expected_size:
        raise AssertionError(
            f"unexpected Y4M plane byte count: {len(planes)} != {expected_size}"
        )
    return {
        "Y": hashlib.sha256(planes[:y_size]).hexdigest(),
        "U": hashlib.sha256(planes[y_size:y_size + chroma_size]).hexdigest(),
        "V": hashlib.sha256(planes[y_size + chroma_size:]).hexdigest(),
    }


def _vspipe_plane_digests(toolchain, request, index: int) -> dict[str, str]:
    from core.media_pipeline import build_vspipe_command, build_vspipe_render_env

    command = build_vspipe_command(toolchain.vspipe_path, request)
    command[-2:-2] = ["-s", str(index), "-e", str(index)]
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        timeout=30,
        check=False,
        env=build_vspipe_render_env(
            toolchain.vspipe_path,
            app_dir=request.app_dir,
            runtime=request.runtime,
            expected_fingerprint=request.runtime_fingerprint,
        ),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise AssertionError(
            "VSPipe output 0 failed: "
            + result.stderr.decode("utf-8", errors="replace")[-1_000:]
        )
    return _y4m_plane_digests(result.stdout)


def _preview_export_contract() -> dict[str, object]:
    import cv2
    import numpy as np
    from PIL import Image

    from core.media_pipeline import MediaEncoder
    from core.media_tools import MediaToolchain
    from core.vs_runtime.worker_process import SyncVSWorkerProcess
    from tests.helpers.m5_render_fixture import (
        build_default_render_session,
        preflight_encode_request,
    )

    toolchain = MediaToolchain.discover(str(ROOT))
    if toolchain.missing_for_export():
        raise RuntimeError(f"M5 real encode unavailable: {toolchain.describe()}")
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir) / "中文 空格 & '单引号'"
        root.mkdir()
        source = root / "source.png"
        image = np.zeros((480, 320, 3), np.uint8)
        image[:240, :160] = (255, 0, 0)
        image[:240, 160:] = (0, 255, 0)
        image[240:, :160] = (0, 0, 255)
        image[240:, 160:] = (255, 255, 0)
        Image.fromarray(image[:, :, ::-1]).save(source)
        session = build_default_render_session(
            root / "session",
            source_path=source,
            source_kind="image",
            end_frame=4,
            crop=(20, 40, 180, 320),
            rotation=90,
        )
        worker = SyncVSWorkerProcess(
            app_dir=ROOT,
            env={**os.environ, "APPDATA": str(root / "appdata")},
        )
        try:
            worker.start(timeout_ms=15_000)
            worker.load(session, timeout_ms=30_000)
            preview = worker.request_frame(
                epoch=session.epoch,
                index=0,
                surface="final",
                viewport=(384, 640),
                zoom_factor=1.0,
                pan=(0.5, 0.5),
                timeout_ms=30_000,
            )
            worker_plane_digests = {
                str(index): worker.request_plane_digest(
                    epoch=session.epoch, index=index, timeout_ms=30_000
                )
                for index in (0, 1, 3)
            }
        finally:
            worker.close()
        request, fps, vui = preflight_encode_request(session)
        plane_digests = {
            index: {
                "worker": worker_plane_digests[index],
                "vspipe": _vspipe_plane_digests(toolchain, request, int(index)),
            }
            for index in worker_plane_digests
        }
        for index, digests in plane_digests.items():
            if digests["worker"] != digests["vspipe"]:
                raise AssertionError(
                    f"output 0 plane digest mismatch at frame {index}: {digests}"
                )
        output = root / "encoded.mp4"
        MediaEncoder(toolchain).encode_vpy_to_mp4(request, str(output), fps, vui=vui)
        capture = cv2.VideoCapture(str(output))
        try:
            ok, encoded = capture.read()
        finally:
            capture.release()
        if not ok or encoded is None:
            raise RuntimeError("cannot decode fixed-runner MP4")
        if preview.shape != encoded.shape:
            raise AssertionError(f"geometry mismatch: {preview.shape} != {encoded.shape}")
        difference = np.abs(preview.astype(np.int16) - encoded.astype(np.int16))
        if difference.mean() >= 3.0:
            raise AssertionError(f"mean BGR difference too large: {difference.mean():.3f}")
        if (difference.max(axis=2) > 30).mean() >= 0.02:
            raise AssertionError("too many BGR pixels exceed the bounded colour error")
        probe = subprocess.run(
            [
                sys.executable,
                str(ROOT / "tests" / "helpers" / "run_vs_contract_case.py"),
                "encoded_vui",
                str(output),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if probe.returncode != 0:
            raise AssertionError(
                f"encoded VUI probe failed: {probe.stderr or probe.stdout}"
            )
        encoded_props = json.loads(probe.stdout.splitlines()[-1])
        if encoded_props != {
            "_Matrix": 6,
            "_Transfer": 6,
            "_Primaries": 6,
            "_Range": 0,
        }:
            raise AssertionError(f"encoded VUI props mismatch: {encoded_props}")
    return {
        "status": "ok",
        "plane_digests": plane_digests,
        "encoded_props": encoded_props,
    }


def _main() -> dict[str, object]:
    if len(sys.argv) != 2 or sys.argv[1] != "preview_export_contract":
        raise SystemExit("usage: run_m5_render_case.py preview_export_contract")
    return _preview_export_contract()


if __name__ == "__main__":
    try:
        print(json.dumps(_main(), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False))
        raise
