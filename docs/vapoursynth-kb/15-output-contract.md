# 15 — output 0/1 与设备编码契约

## 两个输出的职责

用户脚本可以自由构图，但宿主只消费明确注册的 `VideoOutputTuple`：

| 输出 | 使用者 | 合同 |
|---|---|---|
| output 0 | final 预览、导出预检、VSPipe→x264 | 必需的设备编码 clip |
| output 1 | compatible 编辑器的 editor surface | 仅 `assetmaker-editor-output: 1` 时必需 |

历史 R73 官方 `set_output()`/`get_output()` 参考（保留用于对照）：
`https://github.com/vapoursynth/vapoursynth/blob/R73/doc/pythonreference.rst`。
当前 binding 的 `get_output()` 返回 `VideoOutputTuple`；项目拒绝非 `VideoNode`、带
alpha 或非零 `alt_output`，不会从脚本全局变量猜哪个 clip 应导出。

`compatible` 脚本若声明 trim/crop/rotation 能力，output 1 的语义是旋转后、正式
trim/crop 前的完整编辑时间轴。`raw` 必须声明 editor output 0，只提供 output 0。

## output 0 静态合同

`assetmaker_vs.contract.validate_outputs()` 要求：

- format 精确为 `YUV420P8`，且 clip format 固定；
- width/height 精确等于 job 的 `coded_width/coded_height`，并满足实际 chroma
  subsampling；
- `num_frames > 0`，已解析 timeline 时等于 `end_frame - start_frame`；
- fps 为正有理数，已解析 job fps 时按交叉乘法精确相等；
- 首/中/末 sentinel frame 的尺寸、format、matrix、transfer、primaries、range
  符合 job 且彼此一致。

profile 几何示例：

| profile | 内容画布 | output 0 编码画布 |
|---|---:|---:|
| `360x640` | 360×640 | 384×640 |
| `720x1080` | 720×1080 | 720×1080 |

360 profile 的右侧 24 像素由默认脚本在 resize 后 AddBorders；contract 只接受最终
384×640。看到 384 而不是 360 不是参数错误。

## 当前 R79 色彩与 VUI

当前 profile 要求 `matrix/transfer/primaries=170m`、`range=limited`。当前 contract：

- `_Matrix/_Transfer/_Primaries` 必须是受支持的普通整数 code；
- 默认脚本写 `_Range`：普通整数 `0` limited、`1` full；
- 有界接受精确 `vs.Range` 类型的 `RANGE_LIMITED/RANGE_FULL`；拒绝其他枚举、bool、float；
- 真实 `vs.FrameProps` 中若存在物理 `_ColorRange`，无论是否与 `_Range` 并存都拒绝；
- 普通映射的兼容 `_ColorRange` 整数为 `1` limited、`0` full，双键须语义一致。

sentinel 校验产出 `X264Vui`，导出命令据此明确传
`--colormatrix/--colorprim/--transfer/--range`。因此像素转换、frame props 与码流
VUI 是三层一致性，不可只修其中一层。

R79 旧键兼容访问可能遮蔽物理属性，不能证明旧值正确。具体拒绝条件和
验证入口见 [01 色彩范围](01-colour-range-props.md)，不以 `int()` 吞掉未知类型。

## 逐帧 guard

首/中/末 sentinel 只用于预检签名。通过后，contract 还用 `std.ModifyFrame` 包装
output 0；每一帧真正被 worker 或 VSPipe 请求时都会再次验证 format、尺寸和色彩
props。这样脚本若在第 2 帧或后续帧漂移，fresh VSPipe 顺序消费会在编码期间失败，
而不是仅靠第 0 帧误放行。

worker 的 final surface 和 VSPipe runner 都消费 `guarded_clip`。VSPipe runner 校验后
把 guarded clip 重新注册为 output 0，x264 不会看到未经合同保护的原始输出。

## output 1 合同与边界

若脚本头声明 editor output 1，contract 要求：

- 正尺寸、正帧数、固定 format 与正 fps；
- 已解析 `end_frame` 时，完整编辑时间轴至少覆盖该帧；
- fps 与 job 一致；
- 图片的帧数精确等于 `virtual_frame_count`。

这些是可机械验证的结构。contract 不能从像素自动证明“rotation 已应用且 trim/crop
未应用”；该语义由脚本头承诺、默认脚本顺序测试与真实 editor/final parity 共同
保证。不要把 metadata 校验夸大成对任意用户滤镜语义的证明。

## 常见失败

- 只写 `clip` 变量但没 `set_output(0)`；
- 输出 RGB、10-bit、可变 format、alpha 或错误 coded size；
- 按内容宽 360 注册 output 0，忘记 384 编码补边；
- timeline 帧数或有理 fps 不一致；
- 只写标签但像素未转换，或像素已转换但 props/VUI 不一致；
- compatible 脚本声明 editor output 1 却未注册，或图片 output 1 已被 trim。

宿主不会在 output 0 后偷偷补 resize 来“修好”用户脚本，因为那会让用户脚本、
预览与导出看到不同图。修复应回到 `.vpy`。

## 验证

```powershell
uv run python -m unittest -v `
  tests.test_vs_output_contract `
  tests.test_default_vpy_pipeline `
  tests.test_preview_export_parity `
  tests.test_export_color_roundtrip `
  tests.test_vs_runner
```

真实 parity/编码测试必须报告是否 skip。

## 相关

- [01 色彩范围](01-colour-range-props.md)
- [02 Resize](02-resize-semantics.md)
- [13 用户 VPY ABI](13-user-vpy-abi.md)
- [14 worker 协议](14-worker-protocol.md)
