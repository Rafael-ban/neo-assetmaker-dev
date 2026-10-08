"""C2：真实 worker 与 VSPipe 必须从同一 canonical 用户脚本根渲染。"""

from __future__ import annotations

import json
import os
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from config.vs_runtime import load_vs_runtime
from core.media_pipeline import (
    VSPipeRenderRequest,
    build_vspipe_command,
    build_vspipe_render_env,
)
from core.media_tools import MediaToolchain
from core.vs_runtime.script_header import parse_script_header
from core.vs_runtime.session import (
    RenderSession,
    ScriptSelection,
    compute_job_sha256,
    compute_script_bundle_hash,
)
from core.vs_runtime.vs_loader import compute_runtime_fingerprint
from core.vs_runtime.worker_process import SyncVSWorkerProcess


ROOT = Path(__file__).resolve().parents[1]
TOOLCHAIN = MediaToolchain.discover(str(ROOT))
RUNNER = ROOT / "resources" / "vapoursynth" / "assetmaker_runner.vpy"


def _write_image_job(path: Path, *, epoch: int) -> None:
    """写入完整 image job；用户脚本只负责 C2 的确定性输出探针。"""
    root = path.parent.resolve()
    source = root / "source.png"
    source.write_bytes(b"C2 image fixture is present for the frozen image job\n")
    path.write_text(
        json.dumps(
            {
                "api_version": 1,
                "epoch": epoch,
                "track": "loop",
                "project_root": str(root),
                "source": {
                    "path": str(source),
                    "kind": "image",
                    "virtual_frame_count": 3,
                },
                "timeline": {
                    "start_frame": 0,
                    "end_frame": 3,
                    "fps": {"numerator": 30_000, "denominator": 1_001},
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
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_identity_script(script: Path, *, mode: str) -> None:
    editor_output = "\neditor = core.resize.Bicubic(base, format=vs.RGB24)\neditor.set_output(1)" if mode == "compatible" else ""
    script.write_text(
        "# assetmaker-api: 1\n"
        f"# assetmaker-mode: {mode}\n"
        "# assetmaker-capabilities: source\n"
        "# assetmaker-requires:\n"
        f"# assetmaker-editor-output: {1 if mode == 'compatible' else 0}\n\n"
        "import json\n"
        "from pathlib import Path\n\n"
        "import vapoursynth as vs\n\n"
        "identity = json.loads(\n"
        "    Path(__file__).with_name('grade.json').read_text(encoding='utf-8')\n"
        ")\n"
        "script = Path(__file__).resolve()\n"
        "if str(script) != assetmaker_script:\n"
        "    raise RuntimeError('__file__ and assetmaker_script diverged')\n"
        "if str(script) != identity['script_path']:\n"
        "    raise RuntimeError('__file__ is not the canonical user script')\n"
        "if str(script.parent) != identity['script_root']:\n"
        "    raise RuntimeError('__file__ root is not the canonical script root')\n"
        "if str(Path(assetmaker_script).resolve().parent) != identity['script_root']:\n"
        "    raise RuntimeError('assetmaker_script root diverged')\n\n"
        "core = vs.core\n"
        "clips = []\n"
        "for yuv in identity['frames']:\n"
        "    frame = core.std.BlankClip(\n"
        "        width=360, height=640, length=1, fpsnum=30000, fpsden=1001,\n"
        "        format=vs.YUV420P8, color=yuv,\n"
        "    )\n"
        "    clips.append(core.std.SetFrameProps(\n"
        "        frame, _Matrix=6, _Transfer=6, _Primaries=6, _ColorRange=1,\n"
        "    ))\n"
        "base = core.std.Splice(clips)\n"
        "base.set_output(0)"
        + editor_output
        + "\n",
        encoding="utf-8",
    )


def _read_y4m_plane_digests(payload: bytes, *, frame_count: int) -> list[dict[str, str]]:
    """解析 Y4M 的有效平面，明确不把行 padding 纳入 digest。"""
    header_end = payload.find(b"\n")
    if header_end < 0:
        raise AssertionError("VSPipe Y4M 缺少 stream header")
    tokens = payload[:header_end].split()
    if not tokens or tokens[0] != b"YUV4MPEG2":
        raise AssertionError(f"VSPipe stdout 不是 Y4M: {payload[:80]!r}")
    dimensions = {
        token[:1]: int(token[1:])
        for token in tokens[1:]
        if token[:1] in (b"W", b"H")
    }
    if set(dimensions) != {b"W", b"H"}:
        raise AssertionError(f"Y4M header 缺少 W/H: {payload[:header_end]!r}")
    width, height = dimensions[b"W"], dimensions[b"H"]
    if width % 2 or height % 2:
        raise AssertionError(f"YUV420 Y4M 尺寸必须为偶数: {width}x{height}")

    offset = header_end + 1
    plane_sizes = (("Y", width * height), ("U", width * height // 4), ("V", width * height // 4))
    digests: list[dict[str, str]] = []
    for index in range(frame_count):
        frame_end = payload.find(b"\n", offset)
        if frame_end < 0 or not payload[offset:frame_end].startswith(b"FRAME"):
            raise AssertionError(f"Y4M 缺少第 {index} 帧头")
        offset = frame_end + 1
        frame_digests: dict[str, str] = {}
        for label, size in plane_sizes:
            plane_end = offset + size
            if plane_end > len(payload):
                raise AssertionError(f"Y4M 第 {index} 帧 {label} 平面不完整")
            frame_digests[label] = hashlib.sha256(payload[offset:plane_end]).hexdigest()
            offset = plane_end
        digests.append(frame_digests)
    if offset != len(payload):
        raise AssertionError("Y4M 包含未预期的额外帧或尾部字节")
    return digests


def _render_vspipe_plane_digests(*, session: RenderSession, runtime) -> list[dict[str, str]]:
    """用 worker 已加载的同一 frozen session 驱动固定 runner。"""
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
    kwargs: dict[str, object] = {
        "cwd": ROOT,
        "capture_output": True,
        "timeout": 45,
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
    result = subprocess.run(build_vspipe_command(TOOLCHAIN.vspipe_path, request), **kwargs)
    if result.returncode != 0:
        raise AssertionError(
            "VSPipe 执行 canonical 用户脚本失败: "
            + result.stderr.decode("utf-8", errors="replace")
        )
    return _read_y4m_plane_digests(result.stdout, frame_count=3)


class WorkerVSPipeParityTests(unittest.TestCase):
    """不冻结 JSON：它只是验证两端实际 script root 的相邻资源探针。"""

    def test_canonical_user_script_and_adjacent_grade_have_plane_digest_parity(self):
        """复制脚本根或读取另一份 grade.json 时，任一端必须无法通过。"""
        expected_vspipe = ROOT / "tools" / "media" / "VSPipe.exe"
        self.assertTrue(
            bool(TOOLCHAIN.vspipe_path) and Path(TOOLCHAIN.vspipe_path).is_file(),
            "C2 真实 parity 需要 bundled VSPipe；"
            f"expected={expected_vspipe}；{TOOLCHAIN.describe()}",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve() / "中文 空格 & '单引号'"
            root.mkdir()
            job = root / "frozen job.json"
            _write_image_job(job, epoch=61)

            runtime = load_vs_runtime(
                ROOT / "config" / "vs_runtime.json",
                root / "appdata" / "ArknightsPassMaker" / "vapoursynth" / "vs_runtime.user.json",
            )
            runtime_fingerprint = compute_runtime_fingerprint(ROOT, runtime)

            for mode in ("compatible", "raw"):
                with self.subTest(mode=mode):
                    script = root / f"用户 {mode} & '脚本'.vpy"
                    _write_identity_script(script, mode=mode)
                    canonical_script = script.resolve()
                    (root / "grade.json").write_text(
                        json.dumps(
                            {
                                "script_path": str(canonical_script),
                                "script_root": str(canonical_script.parent),
                                "frames": [[73, 101, 193], [74, 102, 194], [75, 103, 195]],
                            },
                            ensure_ascii=False,
                        ),
                        encoding="utf-8",
                    )
                    session = RenderSession(
                        epoch=61,
                        track="loop",
                        selection=ScriptSelection.from_header(
                            canonical_script,
                            parse_script_header(canonical_script),
                            compute_script_bundle_hash(canonical_script),
                        ),
                        job_path=str(job.resolve()),
                        job_sha256=compute_job_sha256(job),
                        runtime_fingerprint=runtime_fingerprint,
                    )

                    worker = SyncVSWorkerProcess(
                        app_dir=ROOT,
                        env={**os.environ, "APPDATA": str(root / "appdata")},
                    )
                    self.addCleanup(worker.close)
                    worker.start(timeout_ms=15_000)
                    metadata = worker.load(session, timeout_ms=30_000)
                    self.assertEqual(session.selection.script_path, str(canonical_script))
                    self.assertEqual(metadata.mode, mode)
                    worker_digests = [
                        worker.request_plane_digest(
                            epoch=session.epoch, index=index, timeout_ms=15_000
                        )
                        for index in range(3)
                    ]

                    vspipe_digests = _render_vspipe_plane_digests(
                        session=session,
                        runtime=runtime,
                    )
                    self.assertEqual(vspipe_digests, worker_digests)
