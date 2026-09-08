# 01 · 色彩范围帧属性：`_Range` 与 `_ColorRange`

**当前结论：R73 下两个键表达同一业务概念，但编码方向相反。项目输出写
`_ColorRange=1` 表示 limited；兼容读取 `_Range=0` 也表示 limited。不要凭键名
猜数值，更不要用宽泛 `int()` 把类型变化悄悄吞掉。**

## 先分清三层合同

| 层次 | 当前已证实的含义 | 证据 |
|---|---|---|
| 项目 R73 输出 | `_ColorRange`: `0=full`、`1=limited` | `default_pipeline.vpy`、真实导出回读 |
| 项目 R73 兼容输入 | `_Range`: `0=limited`、`1=full` | R73 固定 tag 常量与 `contract.py` |
| R79 候选 | 静态源码出现 API 4.2 `_Range`、`Range(IntEnum)` 和旧键重映射 | 只作为 U 阶段待验证边界 |

当前输出合同由
`resources/vapoursynth/python/assetmaker_vs/contract.py::_range_value()` 严格实现：

```python
if "_Range" in props:
    code = props["_Range"]
    if type(code) is not int:
        reject()
    value = {0: "limited", 1: "full"}[code]
if "_ColorRange" in props:
    code = props["_ColorRange"]
    if type(code) is not int:
        reject()
    value = {1: "limited", 0: "full"}[code]
```

若两个键同时存在，必须映射为同一语义，否则合同拒绝输出。这里刻意要求普通
`int`：K1 冻结的是当前 R73 合同，不提前把 R79 可能返回的枚举当作已兼容。

## R73 固定版本依据与运行时证据

固定 tag 入口：

- `https://github.com/vapoursynth/vapoursynth/blob/R73/include/VSConstants4.h`
- `https://github.com/vapoursynth/vapoursynth/blob/R73/doc/apireference.rst`
- `https://github.com/vapoursynth/vapoursynth/blob/R73/doc/pythonreference.rst`

本项目 R73 / API R4.1 对真实导出 MP4 经 `lsmas` 读回，第 0 帧包含：

```text
_ColorRange = 1
_Matrix = 6
_Primaries = 6
_Transfer = 6
_Range present? False
```

`default_pipeline.vpy` 先用 `resize.Bicubic(..., range_s="limited")` 完成像素转换，
再用 `SetFrameProps(..., _ColorRange=1)` 写输出标签；x264 同时使用 `--range tv`。
设置 frame props 只是写元数据，不会重新计算像素。

R73 的消费探针还证明：同一 YUV420P8 单帧取 `Y=16`，用 `resize.Point` 转
RGB24 时，`_ColorRange=1` 得到黑 `R=0`，`_ColorRange=0` 得到暗灰 `R=16`。
这与项目 limited 输出一致。单独写 `_Range` 不会在 R73 中自动生成
`_ColorRange`；`SetFrameProps` 不替调用者做键迁移。

## R79 只记录待验证边界

R79 固定 tag 的 C 常量、Cython binding 与 core 映射静态显示：API 4.2 使用
`_Range`，Python 暴露 `Range(IntEnum)`，并存在旧 `_ColorRange` 键的兼容映射。
但 K1 尚未运行真实 R79 候选，以下问题必须留给 U 阶段：

- `props["_Range"]` 的实际 Python 类型，以及严格普通整数合同是否需有界扩展；
- 新旧键的读、写、枚举别名和双键冲突行为；
- `lsmas`、`imwri` 与内置 resize 在候选包中实际发出、消费哪个键；
- 编码后回读是否仍与 `--range tv` 和像素探针一致。

在这些探针完成前，不做全局 `_ColorRange`→`_Range` 重命名，也不以
`int(value)` 掩盖未知枚举或错误类型。

## 相关

- [02 Resize 语义](02-resize-semantics.md) — 像素转换参数和输出标签的边界
- [08 版本升级](08-version-upgrade-notes.md) — R73→R79 的验收门
- [10 研究方法](10-research-method.md) — 固定 tag 与真实媒体探针
- [15 输出契约](15-output-contract.md) — output 0 的严格 range 校验
