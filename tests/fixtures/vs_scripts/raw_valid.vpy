# assetmaker-api: 1
# assetmaker-mode: raw
# assetmaker-capabilities: source
# assetmaker-requires:
# assetmaker-editor-output: 0

import vapoursynth as vs

core = vs.core
clip = core.std.BlankClip(
    width=360,
    height=640,
    length=3,
    fpsnum=30000,
    fpsden=1001,
    format=vs.YUV420P8,
    color=[16, 128, 128],
)
clip = core.std.SetFrameProps(
    clip,
    _Matrix=6,
    _Transfer=6,
    _Primaries=6,
    _ColorRange=1,
)
clip.set_output(0)
