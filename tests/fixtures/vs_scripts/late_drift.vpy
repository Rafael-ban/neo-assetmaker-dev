# assetmaker-api: 1
# assetmaker-mode: raw
# assetmaker-capabilities: source
# assetmaker-requires:
# assetmaker-editor-output: 0

import vapoursynth as vs


base = vs.core.std.BlankClip(
    width=360,
    height=640,
    length=5,
    fpsnum=30000,
    fpsden=1001,
    format=vs.YUV420P8,
    color=[16, 128, 128],
)
base = vs.core.std.SetFrameProps(
    base,
    _Matrix=6,
    _Transfer=6,
    _Primaries=6,
    _ColorRange=1,
)


def drift_on_second_frame(n, f):
    if n != 1:
        return f
    changed = f.copy()
    changed.props["_Matrix"] = 1
    return changed


vs.core.std.ModifyFrame(
    clip=base,
    clips=base,
    selector=drift_on_second_frame,
).set_output(0)
