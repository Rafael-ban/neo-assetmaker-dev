# assetmaker-api: 1
# assetmaker-mode: compatible
# assetmaker-capabilities: source,trim,crop,rotation,resolution,image_loop
# assetmaker-requires: lsmas.LWLibavSource,imwri.Read
# assetmaker-editor-output: 1

"""AssetMaker 内置 compatible pipeline。

output 1 是旋转后、trim/crop 前的完整编辑画布；output 0 严格按 RenderJob
生成设备编码帧。脚本有意保持自包含和可读，便于用户复制后定制。
"""

from pathlib import Path

import vapoursynth as vs

from assetmaker_vs.job_api import load_job


core = vs.core
# 默认脚本显式设置帧缓存阈值（MB），不依赖进程预设或默认值。
core.max_cache_size = 16000
job = load_job(assetmaker_job, for_export=False)


def rotate(clip, degrees):
    if degrees == 90:
        return core.std.FlipHorizontal(core.std.Transpose(clip))
    if degrees == 180:
        return core.std.Turn180(clip)
    if degrees == 270:
        return core.std.FlipVertical(core.std.Transpose(clip))
    return clip


def crop_safely(clip, crop, target_width, target_height):
    requested_width = crop["width"]
    requested_height = crop["height"]
    if requested_width <= 0 or requested_height <= 0:
        return clip

    x_step = 1 << clip.format.subsampling_w
    y_step = 1 << clip.format.subsampling_h
    left_limit = max(0, clip.width - x_step)
    top_limit = max(0, clip.height - y_step)
    left = min(max(0, crop["x"]), left_limit)
    top = min(max(0, crop["y"]), top_limit)
    left = left // x_step * x_step
    top = top // y_step * y_step
    width = min(requested_width, clip.width - left)
    height = min(requested_height, clip.height - top)

    target_aspect = target_width / target_height
    scale = min(
        width / float(requested_width),
        height / float(requested_height),
    )
    width = int(min(width, round(requested_width * scale)))
    height = int(min(height, round(width / target_aspect)))
    width = int(min(width, round(height * target_aspect)))
    width = width // x_step * x_step
    height = height // y_step * y_step
    if width < x_step or height < y_step:
        return clip
    return core.std.CropAbs(
        clip,
        width=width,
        height=height,
        left=left,
        top=top,
    )


source = job["source"]
timeline = job["timeline"]
transform = job["transform"]
output = job["output"]

if source["kind"] == "video":
    cache_dir = Path(job["paths"]["cache_dir"])
    cache_dir.mkdir(parents=True, exist_ok=True)
    clip = core.lsmas.LWLibavSource(
        source["path"],
        cachefile=str(cache_dir / f"source-{job['epoch']}.lwi"),
    )
    source_height = clip.height
else:
    clip = core.imwri.Read(source["path"])
    clip = clip[:1]
    fps = timeline["fps"]
    clip = core.std.AssumeFPS(
        clip,
        fpsnum=fps["numerator"],
        fpsden=fps["denominator"],
    )

clip = rotate(clip, transform["rotation"])

if source["kind"] == "image":
    virtual_count = source["virtual_frame_count"]
    clip = core.std.Loop(clip, times=virtual_count)[:virtual_count]

# output 1 永远保留完整编辑时间轴，且坐标空间已经应用 rotation。
clip.set_output(1)

if timeline["end_frame"] is not None:
    clip = clip[timeline["start_frame"] : timeline["end_frame"]]

clip = crop_safely(
    clip,
    transform["crop"],
    output["display_width"],
    output["display_height"],
)

if source["kind"] == "video":
    first_frame = clip.get_frame(0)
    source_matrix = int(first_frame.props.get("_Matrix", 2))
    first_frame.close()
    if source_matrix == 2:
        source_matrix = 1 if source_height >= 720 else 6
        clip = core.std.SetFrameProps(clip, _Matrix=source_matrix)

clip = core.resize.Bicubic(
    clip,
    width=output["display_width"],
    height=output["display_height"],
    format=vs.YUV420P8,
    matrix_s=output["matrix"],
    range_s=output["range"],
)

right_border = output["coded_width"] - output["display_width"]
bottom_border = output["coded_height"] - output["display_height"]
if right_border or bottom_border:
    clip = core.std.AddBorders(
        clip,
        right=right_border,
        bottom=bottom_border,
    )
if output["final_rotate_180"]:
    clip = core.std.Turn180(clip)

colour_codes = {"709": 1, "170m": 6}
range_codes = {"limited": 1, "full": 0}
clip = core.std.SetFrameProps(
    clip,
    _Matrix=colour_codes[output["matrix"]],
    _Transfer=colour_codes[output["transfer"]],
    _Primaries=colour_codes[output["primaries"]],
    _ColorRange=range_codes[output["range"]],
)
clip.set_output(0)
