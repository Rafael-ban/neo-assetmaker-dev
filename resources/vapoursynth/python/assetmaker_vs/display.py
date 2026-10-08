"""从严格 output 生成 RGB24、viewport 封顶的独立显示支路。"""

from __future__ import annotations

import math
from typing import Any


DISPLAY_DITHER = "error_diffusion"  # YUV/高位深转为 Qt 的 RGB24 时使用


def _positive_dimension(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} 必须是正整数")
    return value


def to_display_clip(
    clip: Any,
    *,
    viewport: tuple[int, int],
    zoom_factor: float,
    pan: tuple[float, float],
) -> Any:
    """转换为 RGB24，并实现 1% 到 10000% 的固定缩放语义。"""
    if not isinstance(viewport, (tuple, list)) or len(viewport) != 2:
        raise ValueError("viewport 必须是 (width, height)")
    viewport_width = _positive_dimension(viewport[0], "viewport.width")
    viewport_height = _positive_dimension(viewport[1], "viewport.height")
    if (
        isinstance(zoom_factor, bool)
        or not isinstance(zoom_factor, (int, float))
        or not math.isfinite(zoom_factor)
        or not 0.01 <= zoom_factor <= 100.0
    ):
        raise ValueError("zoom_factor 必须满足 0.01 <= value <= 100.0")
    if not isinstance(pan, (tuple, list)) or len(pan) != 2:
        raise ValueError("pan 必须是 (x, y)")
    pan_values = tuple(float(value) for value in pan)
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in pan_values):
        raise ValueError("pan.x/pan.y 必须满足 0.0 <= value <= 1.0")

    import vapoursynth as vs

    core = vs.core
    # 已标记的输入属性优先；空白 RGB 测试帧等无标签源按 sRGB 解释。
    input_transfer = "srgb" if clip.format.color_family == vs.RGB else "709"
    colour = dict(
        format=vs.RGB24,
        transfer_in_s=input_transfer,
        primaries_in_s="709",
        transfer_s="srgb",
        primaries_s="709",
        range_s="full",
        dither_type=DISPLAY_DITHER,
    )
    fit = min(viewport_width / clip.width, viewport_height / clip.height)
    fit_width = max(1, min(viewport_width, round(clip.width * fit)))
    fit_height = max(1, min(viewport_height, round(clip.height * fit)))
    if zoom_factor <= 1.0:
        output_width = max(1, round(fit_width * zoom_factor))
        output_height = max(1, round(fit_height * zoom_factor))
        return core.resize.Spline36(
            clip,
            width=output_width,
            height=output_height,
            **colour,
        )

    # Pixel inspection retains conversion before the nearest-neighbour zoom.
    rgb = core.resize.Bicubic(clip, **colour)
    window_width = max(1, min(rgb.width, math.ceil(rgb.width / zoom_factor)))
    window_height = max(1, min(rgb.height, math.ceil(rgb.height / zoom_factor)))
    left = min(
        max(0, round(pan_values[0] * rgb.width - window_width / 2)),
        rgb.width - window_width,
    )
    top = min(
        max(0, round(pan_values[1] * rgb.height - window_height / 2)),
        rgb.height - window_height,
    )
    window = core.std.CropAbs(
        rgb,
        width=window_width,
        height=window_height,
        left=left,
        top=top,
    )
    # 放大时保留像素检查所需的最近邻行为。
    return core.resize.Point(window, width=fit_width, height=fit_height)


__all__ = ["to_display_clip"]
