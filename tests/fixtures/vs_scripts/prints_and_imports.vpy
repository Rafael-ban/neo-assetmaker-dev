# assetmaker-api: 1
# assetmaker-mode: raw
# assetmaker-capabilities: source
# assetmaker-requires:
# assetmaker-editor-output: 0

import vapoursynth as vs

from marker import VALUE

assert VALUE == "marker-from-script-root"
print("hello from script")
core = vs.core
base = core.std.BlankClip(
    width=360,
    height=640,
    length=3,
    fpsnum=30000,
    fpsden=1001,
    format=vs.YUV420P8,
    color=[16, 128, 128],
)
base = core.std.SetFrameProps(
    base,
    _Matrix=6,
    _Transfer=6,
    _Primaries=6,
    _ColorRange=1,
)


def deferred_callback(n, f):
    import lazy_marker

    assert lazy_marker.VALUE == "lazy-marker"
    print("hello from callback")
    return f


clip = core.std.ModifyFrame(
    clip=base,
    clips=base,
    selector=deferred_callback,
)
clip.set_output(0)
