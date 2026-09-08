# 07 · 帧生命周期、mmap 与线程边界

**结论：VapourSynth `VideoFrame` 只活在 `vs_worker.exe` 内。Future 完成后，worker
按 stride 把 RGB24 有效像素复制并重排为连续 BGR24，再写入具名 mmap；宿主核对
request/epoch/slot generation 后复制成 numpy，最后由 Qt queued signal 交给 GUI。**

## R73 API 边界

固定 tag：

- `https://github.com/vapoursynth/vapoursynth/blob/R73/doc/pythonreference.rst`
- `https://github.com/vapoursynth/vapoursynth/blob/R73/src/cython/vapoursynth.pyx`

`clip.get_frame_async(n)` 返回 Future；`future.result()` 才取得 `VideoFrame`。
`frame[plane]` 暴露 VS 所有的缓冲视图，`frame.close()` 后 frame 与 props 不再可用。
plane 可能带 stride padding，不能假设 `stride == width`，也不能把未复制的 view
跨线程或跨进程保存。

## 当前帧链

```text
Qt 请求 frame
  → WorkerProcess 创建 (name, slot generation, capacity) 具名 mmap
  → worker 对 editor/final surface 建 RGB24 display clip
  → get_frame_async(index) 返回 Future
  → Future callback 在 worker 内按 stride 复制 R/G/B 有效行
  → 重排为 packed BGR24 并写 mmap
  → 发送 frame_ready 身份与尺寸
  → host 校验 worker generation + request + epoch + slot name/generation
  → 从 mmap 复制 numpy frame，关闭 slot
  → Qt signal 排队到 VideoPreviewWidget
```

`core.vs_runtime.shared_frame.FrameSlot.write_vs_rgb()` 明确读取 planar RGB24，按
`(2, 1, 0)` 写成 BGR，并只复制每行 `:width` 的有效区域。worker 发送 terminal 后
在 `finally` 中关闭 `VideoFrame` 和自己打开的 slot；host 的 `read_bgr()` 返回副本，
不会把 mmap 生命周期泄漏给 QLabel。

## 过时帧与所有权

- 每个 frame request 必须得到且只得到一个 terminal：`frame_ready`、
  `frame_discarded` 或 `request_error`。
- host 最多允许 3 个 in-flight frame；可合并请求只保留最新 sequence，避免拖动或
  播放时无限排队。
- epoch 标识图/job 代际；worker generation 标识子进程代际；slot generation
  防止复用的映射名称/描述符被迟到消息冒充。
- `cancel_epoch` 的 ACK 是线性化点。worker 的 mmap commit、frame terminal 与 ACK
  共用条件锁，因此 ACK 后尚未终态的旧请求不能再发送 `frame_ready`。
- GUI 仍会核对当前 request owner、epoch、surface 和 index；迟到或已替换的画面
  不进入当前 QLabel。

## Qt 与 VS 的线程分工

VapourSynth Future callback 不操作 Qt 对象；它只复制 frame、写 mmap、发送协议
消息。宿主协议 reader 也不直接绘制，`VSWorkerClient` 把事件转成 Qt signal，由
queued connection 在 GUI 线程执行。播放时钟仍是 `VideoPreviewWidget` 的 QTimer，
seek 是单帧请求，不使用 `clip.frames()` 顺序迭代器。

GUI 父进程不加载 VS binding、不创建 core，也不存在“必须在 PyQt6 前 prewarm”这条
当前架构约束。VS import、core、用户脚本和 native 插件都隔离在 worker 或 VSPipe
子进程；这解决进程/线程所有权问题，但不把用户脚本变成安全沙箱。

## 相关

- [11 预览缩放](11-preview-zoom.md) — worker 内 RGB24 display clip
- [14 worker 协议](14-worker-protocol.md) — epoch、取消、退休与恢复
- [16 脚本信任](16-script-trust.md) — 进程隔离不等于安全执行
