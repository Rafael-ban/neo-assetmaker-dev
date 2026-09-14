# 04 · Trim、Loop 与零长度 clip

**结论：VapourSynth 不允许 0 帧 clip；项目不是在脚本里临时把非法时间轴改成
一帧，而是在 RenderJob 边界拒绝 `end_frame <= start_frame`。图片先按
`virtual_frame_count` 建立完整编辑时间轴，再对 output 0 做半开区间 trim。**

## R73 历史来源

以下固定版本来源保留用于迁移对照；当前源码要求 R79，其部署与验收范围见
[08](08-version-upgrade-notes.md)，不将本节旧版本引用当作 R79 实测。

固定 tag：

- `https://github.com/vapoursynth/vapoursynth/blob/R73/doc/functions/video/trim.rst`
- `https://github.com/vapoursynth/vapoursynth/blob/R73/doc/functions/video/loop.rst`

`std.Trim(first=3, last=3)` 的 `last` 是包含端点，因此返回一帧；Python 切片
`clip[3:3]` 仍是半开区间并会得到零帧，而 VapourSynth 禁止零帧 clip。
`Loop(times<1)` 表示循环到最大 clip 长度，不是“一次”或“不循环”。

## 非法时间轴在进入脚本前失败

`assetmaker_vs.job_api` 对已解析 timeline 要求：

```text
end_frame is None，或 end_frame > start_frame
```

导出作业还要求 `end_frame` 和 fps 都已解析。图片额外要求：

```text
0 <= start_frame < end_frame <= virtual_frame_count
virtual_frame_count >= 1
```

因此当前默认脚本不使用旧式 `max(start + 1, end)` 悄悄改变用户时间轴；非法 job
在加载阶段以结构化错误退出。预览 bootstrap 视频允许 `end_frame=None`，此时不 trim。

## output 1 与 output 0 的图片时间轴

`resources/vapoursynth/default_pipeline.vpy` 对图片执行：

```python
clip = core.imwri.Read(source["path"])[:1]
clip = core.std.AssumeFPS(clip, ...)
clip = rotate(clip, rotation)
clip = core.std.Loop(clip, times=virtual_count)[:virtual_count]
clip.set_output(1)
clip = clip[start_frame:end_frame]
```

这里 `virtual_count` 已经由 job 合同保证至少为 1，所以不会触发 `times<1` 的
“循环到最大长度”。紧随 Loop 的切片把长度钉死为 `virtual_count`，而 output 1
保留这条完整、已旋转的编辑时间轴；output 0 才应用 `[start:end)`。

视频不做 Loop：读取源、旋转后直接建立 output 1；当 timeline 已解析时，output 0
同样用半开切片。crop 在 trim 后执行，不能把空间裁剪与时间轴裁剪混为一谈。

## 定向验证入口

```powershell
uv run python -m unittest -v `
  tests.test_vs_job_contract `
  tests.test_default_vpy_pipeline
```

其中 `test_image_timeline_is_always_resolved_and_within_virtual_count` 锁定 job 边界，
`test_image_loops_full_editor_timeline_before_nonzero_trim` 锁定 Loop/output 1/trim 顺序。

## 相关

- [03 几何](03-geometry-filters.md) — 空间图的完整顺序
- [13 用户 VPY ABI](13-user-vpy-abi.md) — `load_job()` 与正式 job 合同
- [15 输出契约](15-output-contract.md) — output 0/1 的职责
