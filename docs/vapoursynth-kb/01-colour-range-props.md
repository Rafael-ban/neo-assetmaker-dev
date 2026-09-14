# 01 · 色彩范围帧属性：`_Range` 与 `_ColorRange`

**当前 R79 默认脚本写 `_Range`；输出合同有界接受当前 binding 的 `vs.Range`
和普通整数。旧 `_ColorRange` 的整数编码方向相反，必须区分物理键与 binding
提供的兼容访问，不能用宽泛 `int()` 吞掉错误类型。**

## 当前源码合同

依据 `resources/vapoursynth/default_pipeline.vpy` 及
`assetmaker_vs.contract._range_name()` / `_range_value()`：

| 输入 | limited | full | 类型限制 |
|---|---|---|---|
| `_Range` 普通整数 | `0` | `1` | 精确 int，拒绝 bool、float、未知 code |
| 兼容映射中的 `_ColorRange` 普通整数 | `1` | `0` | 精确 int，并非旧键可无条件进入真实帧 |
| 当前 `vs.Range` 枚举 | `RANGE_LIMITED` | `RANGE_FULL` | 类型必须精确为该 binding 的 vs.Range，拒绝其他枚举 |

合同先枚举 `props.keys()` 中的物理键。对于真实 `vs.FrameProps`，只要物理
`_ColorRange` 仍存在就拒绝：单独存在时旧值不可可靠读取；与 `_Range` 共存时
旧值会被别名访问遮蔽，无法证明两者一致。不能借兼容读取把旧物理键当作已校验。
普通映射可用于独立合同输入/测试；若提供两个键，解析后语义必须一致。

默认脚本先通过 `resize.Bicubic(..., range_s=...)` 转换像素，再用
`SetFrameProps(..., _Range=range_codes[output["range"]])` 写标签。当前设备
profile 为 limited，编码器对应 `--range tv`。frame props 只是元数据，
不能替代像素转换；详见 [15 输出契约](15-output-contract.md)。

## R79 验证入口

`tests/test_vs_output_contract.py` 覆盖枚举、普通整数、非法类型、旧物理键与
冲突；`tests/test_default_vpy_pipeline.py` 覆盖默认脚本输出。
此前保留工作树的 U2 Range、limited/full 端点和漂移探针位于
`artifacts/u2-r79/`，这些本地证据未纳入 Git。完整验收范围与部署位置见
[08](08-version-upgrade-notes.md)，不将单个探针成功扩大为所有媒体通过。

## R73 历史对照

迁移前默认脚本写 `_ColorRange=1`，R73 / API R4.1 的旧 MP4 回读样本含
`_ColorRange=1`、`_Matrix/_Primaries/_Transfer=6`，未含 `_Range`。
旧消费探针用 `Y=16` 的 YUV420P8 单帧转 RGB24：`_ColorRange=1` 得到黑
`R=0`，`_ColorRange=0` 得到暗灰 `R=16`。这些是 R73 样本，不是 R79 回读结果。

当时的固定版本来源保留用于对照：

- `https://github.com/vapoursynth/vapoursynth/blob/R73/include/VSConstants4.h`
- `https://github.com/vapoursynth/vapoursynth/blob/R73/doc/apireference.rst`
- `https://github.com/vapoursynth/vapoursynth/blob/R73/doc/pythonreference.rst`

## 相关

- [02 Resize 语义](02-resize-semantics.md)
- [08 R79 运行时与验收边界](08-version-upgrade-notes.md)
- [10 研究方法](10-research-method.md)
- [15 输出契约](15-output-contract.md)
