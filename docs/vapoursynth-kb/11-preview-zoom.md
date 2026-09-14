# 11 · 预览缩放：worker 内的 RGB24 视口

**结论：缩放是每次 frame request 的显示支路，不写入 RenderJob，也不改变 output
0/1。worker 先把选定 surface 转为 RGB24；放大时先裁一个源窗口，再用 Point 放到
fit 尺寸，避免创建随倍率平方增长的巨帧。**

## 当前入口与参数合同

实现位于
`resources/vapoursynth/python/assetmaker_vs/display.py::to_display_clip()`，由 worker
在处理 `request_frame` 时调用。参数必须满足：

```text
viewport.width/height > 0
0.01 <= zoom_factor <= 100.0     # UI 为 1% 到 10000%
0.0 <= pan.x/pan.y <= 1.0
```

无论请求 `editor` 还是 `final` surface，先执行：

```python
rgb = core.resize.Bicubic(clip, format=vs.RGB24)
fit = min(viewport_width / rgb.width, viewport_height / rgb.height)
```

因此 mmap 始终接收 RGB24→BGR24 的显示帧，而不是把 YUV plane 或 VS frame 暴露给
Qt。

## 1%–100%：完整画面缩放

当 `zoom_factor <= 1.0`，实现按 fit 尺寸乘倍率，再用 Bicubic 输出：

```python
output_width = round(fit_width * zoom_factor)
output_height = round(fit_height * zoom_factor)
```

100% 指“完整画面 fit 到 viewport”，不是保证输出像素等于源尺寸，也不是返回原始
clip 对象。1% 至少保留 1×1。pan 在这一段不参与裁剪。

## 100% 以上：先裁 RGB 窗口再 Point

放大时：

```python
window_width = ceil(rgb.width / zoom_factor)
window_height = ceil(rgb.height / zoom_factor)
window = core.std.CropAbs(rgb, width=..., height=..., left=..., top=...)
display = core.resize.Point(window, width=fit_width, height=fit_height)
```

倍率越高，进入 Point 的源窗口越小；输出始终不超过 fit/viewport。Point 保持像素
边缘，适合检查 crop 边界。整帧先放大 100 倍再由 Qt 截去绝大多数像素是历史反例，
不是当前实现，也不应复用历史机器上的毫秒数字作为当前性能结论。

`pan` 是归一化窗口中心，left/top 会夹在有效范围。由于 CropAbs 发生在 RGB24，
宽高和偏移不做无条件偶数对齐；奇数 RGB 中心与一像素窗口的历史证据包含真实 R73 子进程
用例，不能为“将来或许改回 YUV”而人为偏移当前视口。

## 与 crop 和导出的边界

- zoom/pan 只存在于 frame request 的 `display` 字段，不进入冻结 job、bundle hash
  或 VSPipe 命令。
- 放大后显示坐标不再与源 crop 坐标一一对应，所以 GUI 在
  `zoom_factor > 1.0` 时锁定裁剪框绘制、鼠标和键盘编辑。
- `editor` surface 来自 output 1，`final` surface 来自通过合同的 output 0；两者
  各自在 worker 内加显示支路。Point 只影响观察结果，不改变编码像素。

## 定向验证

```powershell
uv run python -m unittest -v tests.test_vs_display tests.test_preview_zoom
```

`test_vs_display` 覆盖 1%、fit、2×、100×、超大 viewport 上限、奇数 RGB 中心和
一像素窗口；`test_preview_zoom` 覆盖真实 widget 请求、内容、范围与编辑锁定。若真实
媒体工具缺失，必须报告 skip，而不是只引用纯参数测试。

## 相关

- [03 几何](03-geometry-filters.md) — 正式 source crop 与 RGB 视口的区别
- [07 帧生命周期](07-frame-lifetime-threading.md) — display frame 的 mmap 所有权
- [14 worker 协议](14-worker-protocol.md) — request/epoch/slot 身份
