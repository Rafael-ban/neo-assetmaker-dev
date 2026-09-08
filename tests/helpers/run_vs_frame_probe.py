"""Clean-process probes for the portable VapourSynth worker runtime.

Do not import PyQt here. The parent test module intentionally stays VS-free so
it can run beside Qt preview worker tests without triggering the bundled
runtime's PyQt-before-VS crash.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# A shared virtual environment can already expose ROOT after a competing
# project.  File-entry imports must always resolve this helper's own checkout.
sys.path.insert(0, str(ROOT))

import numpy as np


def _import_origin() -> dict[str, str]:
    """Expose file-entry imports so the parent can reject foreign roots."""
    from config import vs_runtime
    from core.vs_runtime import vs_loader
    from resources.vapoursynth.python.assetmaker_vs import runtime_fingerprint

    return {
        "root": str(ROOT.resolve()),
        "config": str(Path(vs_runtime.__file__).resolve()),
        "loader": str(Path(vs_loader.__file__).resolve()),
        "portable": str(Path(runtime_fingerprint.__file__).resolve()),
    }


def _load_vs():
    from config.vs_runtime import load_vs_runtime
    from core.vs_runtime.vs_loader import load_vapoursynth

    runtime = load_vs_runtime(ROOT / "config" / "vs_runtime.json")
    return load_vapoursynth(ROOT, runtime)


def _frame_to_bgr(frame) -> np.ndarray:
    """Read real planar RGB24 valid rows/columns and convert them to BGR."""
    if frame.format.name != "RGB24" or frame.format.num_planes != 3:
        raise ValueError("probe expects a planar RGB24 frame")
    planes = []
    for plane in range(3):
        array = np.asarray(frame[plane])
        if array.ndim != 2 or array.shape[0] < frame.height:
            raise AssertionError("RGB24 plane is shorter than its valid rows")
        if array.shape[1] < frame.width or array.dtype != np.uint8:
            raise AssertionError("RGB24 plane is shorter than its valid columns")
        planes.append(np.ascontiguousarray(array[: frame.height, : frame.width]))
    return np.ascontiguousarray(np.stack((planes[2], planes[1], planes[0]), axis=-1))


def _frame_contract() -> dict[str, object]:
    sys.path.insert(0, str(ROOT / "resources" / "vapoursynth" / "python"))
    vs = _load_vs()
    from assetmaker_vs.display import to_display_clip

    core = vs.core

    red = core.std.BlankClip(
        width=64, height=32, length=1, format=vs.RGB24, color=[255, 0, 0]
    )
    red_frame = red.get_frame(0)
    try:
        red_bgr = _frame_to_bgr(red_frame)
    finally:
        red_frame.close()
    assert red_bgr.shape == (32, 64, 3)
    assert red_bgr.dtype == np.uint8
    assert tuple(int(v) for v in red_bgr[0, 0]) == (0, 0, 255)

    blue = core.std.BlankClip(
        width=8, height=8, length=1, format=vs.RGB24, color=[0, 0, 255]
    )
    blue_frame = blue.get_frame(0)
    try:
        blue_bgr = _frame_to_bgr(blue_frame)
    finally:
        blue_frame.close()
    assert tuple(int(v) for v in blue_bgr[0, 0]) == (255, 0, 0)

    # Width 360 has a measured 384-byte plane stride in the bundled core.
    green = core.std.BlankClip(
        width=360, height=16, length=1, format=vs.RGB24, color=[0, 255, 0]
    )
    green_frame = green.get_frame(0)
    try:
        green_bgr = _frame_to_bgr(green_frame)
    finally:
        green_frame.close()
    assert green_bgr.shape == (16, 360, 3)
    assert bool((green_bgr[:, :, 1] == 255).all())
    assert bool((green_bgr[:, :, 0] == 0).all())

    owned = core.std.BlankClip(
        width=32, height=16, length=1, format=vs.RGB24, color=[10, 20, 30]
    ).get_frame(0)
    try:
        owned_bgr = _frame_to_bgr(owned)
    finally:
        owned.close()
    assert owned_bgr is not None
    assert tuple(int(v) for v in owned_bgr[0, 0]) == (30, 20, 10)
    assert int(owned_bgr.sum()) == 32 * 16 * 60

    gray = core.std.BlankClip(width=16, height=16, length=1, format=vs.GRAY8)
    gray_frame = gray.get_frame(0)
    try:
        try:
            _frame_to_bgr(gray_frame)
        except ValueError:
            pass
        else:
            raise AssertionError("non-RGB frame unexpectedly accepted")
    finally:
        gray_frame.close()

    yuv = core.std.SetFrameProps(
        core.std.BlankClip(
            width=48, height=32, length=1, format=vs.YUV420P8
        ),
        _Matrix=6,
        _Transfer=6,
        _Primaries=6,
        _ColorRange=1,
    )
    rgb = to_display_clip(
        yuv,
        viewport=(48, 32),
        zoom_factor=1.0,
        pan=(0.5, 0.5),
    )
    assert rgb.format.id == vs.RGB24
    rgb_frame = rgb.get_frame(0)
    try:
        assert _frame_to_bgr(rgb_frame).shape == (32, 48, 3)
    finally:
        rgb_frame.close()
    return {"status": "ok"}


def _real_frame(path: Path) -> dict[str, object]:
    sys.path.insert(0, str(ROOT / "resources" / "vapoursynth" / "python"))
    vs = _load_vs()
    from assetmaker_vs.display import to_display_clip

    source = vs.core.lsmas.LWLibavSource(str(path))
    clip = to_display_clip(
        source,
        viewport=(384, 640),
        zoom_factor=1.0,
        pan=(0.5, 0.5),
    )
    source_frame = source.get_frame(10)
    source_frame.close()
    rgb_frame = clip.get_frame(10)
    try:
        frame = _frame_to_bgr(rgb_frame)
    finally:
        rgb_frame.close()
    assert frame.ndim == 3
    assert frame.shape[2] == 3
    return {"shape": list(frame.shape), "mean": int(frame.mean())}


def _main() -> dict[str, object]:
    if len(sys.argv) < 2:
        raise ValueError("missing probe case")
    case = sys.argv[1]
    if case == "frame_contract":
        return _frame_contract()
    if case == "import_origin":
        return _import_origin()
    if case == "real_frame":
        return _real_frame(Path(sys.argv[2]).resolve())
    raise ValueError(f"unknown probe case: {case}")


if __name__ == "__main__":
    try:
        result = _main()
    except BaseException as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        raise
    print(json.dumps(result))
