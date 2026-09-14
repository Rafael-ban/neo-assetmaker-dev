# 03 · 几何滤镜：旋转、裁剪、resize 与补边

**结论：当前默认图只有一份脚本真相。生产顺序是 source → 图片首帧/FPS →
rotation → 图片 Loop → output 1 → timeline trim → crop → matrix 补标 →
resize/YUV420P8 → AddBorders → 可选最终 180° → frame props → output 0。**

## R73 历史来源

本节保留迁移前的固定版本依据；当前默认图以下文源码为准，R79 的实际验收
范围见 [08](08-version-upgrade-notes.md)。旧版本引用不是 R79 运行时证据。

固定 tag 文档：

- `https://github.com/vapoursynth/vapoursynth/blob/R73/doc/functions/video/transpose.rst`
- `https://github.com/vapoursynth/vapoursynth/blob/R73/doc/functions/video/crop_cropabs.rst`
- `https://github.com/vapoursynth/vapoursynth/blob/R73/doc/functions/video/addborders.rst`

`Transpose` 是矩阵转置，不是独立的 90° 旋转；左右旋要与
`FlipHorizontal`/`FlipVertical` 组合。Crop/CropAbs 和 AddBorders 都要求尺寸与
边界满足当前格式的子采样约束，越界、裁掉整幅或违反约束会报错。

R73 的 `AddBorders` 默认 `color=<black>`。默认黑是滤镜按当前格式生成的黑色，
不能误写成“未显式传 color 就会得到全零绿边”。项目当前不传 `color`，这是合法的
默认黑；若未来显式传数组，才需要按格式、位深和各 plane 中性值验证。

## 当前默认脚本

`resources/vapoursynth/default_pipeline.vpy` 的旋转定义为：

```python
if degrees == 90:
    clip = core.std.FlipHorizontal(core.std.Transpose(clip))
elif degrees == 180:
    clip = core.std.Turn180(clip)
elif degrees == 270:
    clip = core.std.FlipVertical(core.std.Transpose(clip))
```

`crop_safely()` 使用实际 `clip.format.subsampling_w/h` 推导 x/y 步长：

```python
x_step = 1 << clip.format.subsampling_w
y_step = 1 << clip.format.subsampling_h
```

它先把 left/top 向下对齐，再在画面边界与目标比例内等比收缩 width/height，最后
按各自步长对齐；不足一个采样步长时保留原 clip，不向 CropAbs 发送非法尺寸。
这比无条件“取偶数”准确：YUV420P8 通常两个步长都是 2，但 RGB 或 4:4:4 可为 1，
其他格式也必须服从自己的 subsampling。

`CropAbs` 后，Bicubic 先生成 profile 的内容画布。例如 `360x640` profile 先得到
360×640 YUV420P8，再由 `AddBorders(right=24)` 得到 384×640 编码画布。补边发生
在颜色转换之后，因此其约束按 YUV420P8 计算。

## 编辑框与 RGB 视口不是同一个裁剪层

- 正式 crop 坐标属于 `post_rotation_source_pixels`，写入 job 后由默认脚本处理；
  它必须满足源 clip 的实际子采样约束。
- 高倍率预览由 `assetmaker_vs.display` 把 surface 转为 RGB24 后取视口。RGB24 无
  chroma 子采样，因此视口的 x/y/width/height 不应被统一强制为偶数。
- `output 1` 在正式 timeline trim/crop 之前建立，供编辑器查看完整旋转后画面；
  `output 0` 才是设备输出。不要把 RGB 视口几何反向当作编码 crop 合同。

## 相关

- [02 Resize 语义](02-resize-semantics.md) — 像素转换与 frame props 的边界
- [04 Trim/Loop](04-trim-loop-zero-length.md) — output 1 和半开时间轴
- [11 预览缩放](11-preview-zoom.md) — RGB24 视口链
- [15 输出契约](15-output-contract.md) — 内容画布与编码画布
