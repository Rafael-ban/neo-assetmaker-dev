# 12 · 现场风险：stdout、索引、缓存与格式边界

**结论：当前架构已经把常见风险变成显式边界，但没有把 native 插件、用户脚本、
缓存或索引变成绝对安全。排错时先判断风险属于 Python、VapourSynth core、native
DLL、IPC、mmap 还是编码管道，不要用一个“VS 崩了”笼统归因。**

## 1. Python stdout 已隔离，native fd1 仍是边界

VSPipe 的 stdout 承载 Y4M。固定 `assetmaker_runner.vpy` 在导入 helper 和执行用户
脚本前执行：

```python
sys.stdout = sys.stderr
```

所以 Python `print()` 和 executor 管理的 Python 输出不会混入帧流；真实
`tests.test_vs_runner` 用例验证 Y4M stdout 只含视频流。worker 进程的 stdout 则是
结构化 IPC，也在入口处把普通 Python 输出导向 stderr。

这不拦截 native DLL 直接写 OS fd 1。此类插件仍可能污染 Y4M 或 worker 协议；
只有白名单、隔离实测或 OS 级 fd 重定向才能进一步收紧，当前项目没有承诺任意
第三方 native 输出都安全。

## 2. `.lwi` 不写源目录，但损坏/并发不是自动解决

默认视频 source 使用：

```python
cachefile = cache_dir / f"source-{job['epoch']}.lwi"
core.lsmas.LWLibavSource(source_path, cachefile=str(cachefile))
```

`cache_dir` 来自 RenderJob 的项目缓存上下文，不是素材父目录；只读素材、中文文件名
和用户目录污染因此不依赖 source sidecar。epoch 文件名也避免新旧 job 复用同一
索引名。

但以下仍需独立压力测试：进程被杀时的半写索引、同一源多进程并发、磁盘满、索引
与源文件变化的恢复，以及退休清理失败。看到 `.lwi` 相关异常时应保留 job/epoch、
源 hash、索引路径和 lsmas 日志，不能仅删除文件后宣布根因已修复。

## 3. `max_cache_size` 不是 RSS 硬上限

worker 首次建 core 后捕获 `num_threads`、`max_cache_size` 等原生基线。每次用户脚本
执行前：

- runtime 配置为 0：恢复原生基线；
- runtime 配置为非零：应用该明确值；
- 内置默认脚本随后显式设置 `core.max_cache_size=16000` MB。

该值控制 VS cache 策略，不等于进程总内存限额；frame、numpy/mmap、插件内部缓存、
索引和 Python 对象都可能位于其外。当前运行时对长周期缓存的实际行为须用进程级 RSS/
commit 与请求模式测量，不能把 16000 写成“最多占 16 GB”。

脚本可修改 core 资源，因此 graph 退休和下一次执行前的基线恢复是合同的一部分。
普通 worker restart 沿用启动时冻结的 RuntimeSnapshot；只有显式应用/新 context
退休旧 client 并创建新 client，才可切换配置身份。

## 4. 插件“加载过”不等于 requirement 可用

不能将 `LoadAllPlugins` 返回等同于所有插件可用。项目通过临时 log handler 捕获可识别的加载失败
和冲突，再用 `core.plugins()`、callable 与 `plugin_path` 验证脚本头 requirement。
即便如此，相邻依赖 DLL 没有 plugin entry point 时可能不产生同类 warning；完整
判断还需要真实调用和媒体读取。

历史 `portable.vs` 只选择 R73 便携布局。当前 R79 通过 runtime 布局和媒体清单
核对分发；文件完整性也不能单独证明 CPU 指令兼容、插件实际调用成功或脚本安全。

## 5. 子采样约束按当前 clip 格式计算

正式 source crop 使用 `1 << subsampling_w/h` 对齐。YUV420P8 常见步长为 2，但这
不是所有 clip 的统一规则；RGB/4:4:4 可为 1。AddBorders 发生在 YUV420P8 resize
之后，必须满足输出格式约束。

高倍率 viewport 在 RGB24 上 CropAbs，故允许奇数偏移、奇数尺寸和一像素窗口。
为了“统一”而给 RGB viewport 强制偶数，会制造可见中心偏移。相反，跳过正式
YUV crop 对齐会让 VapourSynth 直接拒绝图，不会可靠地静默取整。

## 6. frame props 不会替你转换像素

`SetFrameProps` 写标签；`resize` 完成像素空间/range 转换；x264 VUI 写码流标签。
三者必须一致但职责不同。只改 `_ColorRange` 或 `_Matrix` 不会修正已经按错误语义
生成的像素，只改 x264 flag 也不会重算 YUV。

R79 output contract 有界接受精确 `vs.Range` 类型和普通整数，拒绝真实 FrameProps
中的物理旧 `_ColorRange` 键；普通映射的双键须语义一致。详见
[01](01-colour-range-props.md)，不能用 `int(value)` 把未知类型强行放行。

## 7. worker 是故障隔离，不是安全沙箱

用户 `.vpy` 与相邻 Python 模块可执行任意本机 Python 代码，native 插件更可在
worker/VSPipe 进程内执行 DLL 代码。bundle hash、runtime fingerprint、job hash、
代际 staging、epoch/cancel 和输出合同用于身份、一致性与故障收束，不构成权限
隔离。信任提示与脚本变更后重确认仍必须保留。

## 快速排错路由

| 症状 | 先查 |
|---|---|
| `lsmas`/`imwri` 不存在 | portable 文件、native 目录诊断、plugin source、requirement |
| Y4M/x264 解析失败 | VSPipe stderr、runner Python stdout、native fd1、output 0 format |
| 旧帧覆盖新画面 | request/epoch/worker generation/slot generation、cancel ACK |
| 裁剪报 subsampling | crop 时 clip 实际 format 与步长，不要统一硬编码偶数 |
| 内存异常增长 | core baseline/脚本覆盖、RSS/commit、in-flight、插件与索引 |
| 预览与导出不同 | 同一 job/script/runtime hash、output surface、worker/VSPipe plane digest |

## 相关

- [05 便携插件](05-plugin-autoload-portable.md)
- [06 VSPipe](06-vspipe-cli.md)
- [07 帧生命周期](07-frame-lifetime-threading.md)
- [14 worker 协议](14-worker-protocol.md)
- [16 脚本信任](16-script-trust.md)
