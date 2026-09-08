# 02 · `resize` 语义：输入解释、输出转换与帧属性

**结论：`*_in` 参数解释输入，`matrix_s`/`range_s` 等参数决定输出转换；输出
frame props 是对结果的元数据描述。写属性不等于转换像素，做了像素转换也不保证
编码器会自动写出相同标签。**

## R73 官方语义

固定 tag：
`https://github.com/vapoursynth/vapoursynth/blob/R73/doc/functions/video/resize.rst`。

- 转到 YUV 时必须指定输出 matrix；项目通过 `matrix_s` 明确给出。
- `matrix_in`、`transfer_in`、`primaries_in`、`range_in` 的 `_in` 形式只在对应
  输入 frame prop 未设置时作为兜底；已有输入属性优先。
- Bicubic 的 `filter_param_a`/`filter_param_b` 分别是 b/c；项目未覆盖默认值。
- `dither_type="error_diffusion"` 不保证确定性；当前脚本不显式选择它。

R73 探针用 `Y=16` 的 YUV420P8 单帧验证了输入优先级：

```text
_ColorRange=0 + range_in_s=limited -> RGB R=16
_ColorRange=1 + range_in_s=full    -> RGB R=0
```

冲突时 frame prop 获胜。因此 `*_in` 不是“强制覆盖源标签”的开关；若源标签错误，
必须先有意识地修正属性或删除错误属性，再转换，而不是假设传参必然覆盖。

## 当前默认脚本的真实顺序

`resources/vapoursynth/default_pipeline.vpy` 在 trim/crop 后执行：

```python
if source["kind"] == "video":
    source_matrix = first_frame.props.get("_Matrix", 2)
    if source_matrix == 2:
        clip = core.std.SetFrameProps(clip, _Matrix=heuristic)

clip = core.resize.Bicubic(
    clip,
    width=output["display_width"],
    height=output["display_height"],
    format=vs.YUV420P8,
    matrix_s=output["matrix"],
    range_s=output["range"],
)
```

- 视频仅在 `_Matrix` 缺失/unspecified（代码 2）时按源高补 709 或 170m；已有
  matrix 不覆盖。图片由 `imwri.Read` 以 RGB 输入。
- Bicubic 把实际裁剪结果转换到内容画布和 YUV420P8，输出 matrix/range 来自
  profile；当前 360×640 profile 为 `170m`、`limited`。
- 补边与可选最终 180° 完成后，脚本再写 `_Matrix`、`_Transfer`、
  `_Primaries`、`_ColorRange`。这是输出标签，不会再次改变像素。
- VSPipe 的 Y4M 接 x264；编码器参数还需与这些输出标签一致。当前 x264 使用
  `smpte170m` 与 `--range tv`。

## 受保护的输出合同

profile 由 `assetmaker_vs.job_api` 固定，而不是可随意拼接的旧全局配置：

| profile | 内容画布 | 编码画布 | format | matrix/range |
|---|---:|---:|---|---|
| `360x640` | 360×640 | 384×640 | YUV420P8 | 170m / limited |
| `720x1080` | 720×1080 | 720×1080 | YUV420P8 | 170m / limited |

修改 kernel、Bicubic b/c、dither、输出格式或色彩参数会改变实际像素或编码合同，
必须同时验证默认脚本、output contract、worker/VSPipe parity 和真实导出回读；不能
只改文档或一端参数。

## 相关

- [01 色彩范围](01-colour-range-props.md) — R73 两个 range 键的相反编码
- [03 几何](03-geometry-filters.md) — crop、resize、AddBorders 的实际顺序
- [15 输出契约](15-output-contract.md) — output 0 的几何/色彩验收
