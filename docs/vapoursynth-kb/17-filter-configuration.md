# 17 · 默认滤镜参数与扩展插件

滤镜图仍由用户、项目或内置 `.vpy` 编写；本次不新增 GUI 滤镜面板，也不恢复
旧 `vsconfig.json` 图配置。设备输出尺寸、帧数、帧率和颜色合同仍来自 RenderJob。

## 内置处理链

`resources/vapoursynth/default_pipeline.vpy` 顶部集中缓存、未标记源的解释、
最终缩放核和抖动参数。默认使用 Spline36 与 error diffusion；修改脚本中的
`FINAL_RESIZER` / `FINAL_RESIZE_OPTIONS` 即可选择其他 VapourSynth resize 核。

顺序为：取源 → 补充未标记源的颜色信息 → 旋转 → 图片循环 → output 1 →
时间裁切 → 空间裁剪 → `process_source()` → 缩放与色彩转换 → 补边/设备旋转 →
output 0。`process_source()` 默认原样返回，供按实际素材加入去噪、去色带等处理。
启用第三方调用时，同时在脚本头的 `assetmaker-requires` 中声明具体函数。

output 1 保留完整编辑画布与时间轴；output 0 固定为设备需要的 YUV420P8。
中间处理可以使用高位深或 float，但必须在输出前回到该合同。
不要把教程中的固定 1920×1080、10-bit 输出或 IVTC 帧率直接用于 compatible 脚本。

输入已有颜色标签时保留标签；缺少标签时，YUV 按原始源高度推断 709/170m，
RGB 图片默认 sRGB/709/full。默认图实际执行 matrix、transfer、primaries、range
转换，不仅重写最终标签。显示支路转换到 sRGB/709/full；适应窗口使用 Spline36，
像素放大保留 Point。画面比较应在相同显示色彩空间中进行。
设备合同的 transfer `170m` 传入 resize 时使用 `601`，两者数值代码均为 6；
matrix 与 primaries 的名称仍为 `170m`。

## 安装、修复与清理

基础 R79 媒体包仍使用 `media-tools-r79-v1.json`。扩展插件独立位于
`tools/vs-plugins/<package>/`，由 `resources/packaging/vs-plugins.json` 记录上游
版本、下载地址、归档成员和文件 SHA-256；它不描述滤镜图。

先按 README 部署基础媒体包，然后执行：

```powershell
uv run python plugin_distribution.py sync --app-dir .
uv run python plugin_distribution.py verify --app-dir .
```

`sync` 是安装和更新共用的操作：已有正确文件不重复下载；缺失、损坏或含旧文件的
包按包替换，失败后可重复执行。中断保留已安装包，临时下载自动清理；锁清单删除的
包会从此专用目录移除。用户自己的插件请使用 runtime 配置的 `native_plugin_dirs`，
不要手工放入工具管理的目录。彻底移除扩展包：

```powershell
uv run python plugin_distribution.py clean --app-dir .
```

构建前、冻结后和便携包解压后都检查扩展清单；CI 会在基础媒体包解压后安装扩展。
worker 和 VSPipe 共用相同插件目录及依赖搜索路径，模型/权重与 DLL 一起参与运行时
身份校验。没有更改已发布基础包的内容，也不需要替换现有基础包下载变量。

## 教程来源与验证边界

用户提供的 `vs_plugins.md`、`vs_plugins_dump.txt`、`vs_plugins_signatures.txt`
记录了另一套 R79 / Python 3.13 环境，但没有其完整版本锁。本项目从
[官方 VSRepo](https://github.com/vapoursynth/vsrepo) 的 Win64 包及
[mvutensils 上游](https://github.com/myrsloik/mvutensils) 重建相应接口库存。
现有 R79 ABI 可在项目 Python 3.12 上加载这些 native DLL，因此不为枚举插件而
更换宿主 Python。插件版本以本项目新清单为准，不能称为原环境的二进制副本。

2026-10-08 本地 R79 实际加载得到 63 个命名空间、308 个函数；逐项匹配教程的
62 个命名空间、306 个函数，无缺项，多出的两项为图片读写 `imwri.Read/Write`。
这个结果证明加载及接口存在，不证明每个参数组合、GPU 算子或模型的实际运算效果。
默认链不自动开启 GPU、AI、去隔行、降噪和去色带。

### 本次本地验证（2026-10-08）

- 最小测试首轮 15 项，8 通过、6 失败、1 错误；仅对未通过的 7 项复测一次，
  4 通过、3 失败。后续补齐 runner 的 DLL 搜索目录并调整旧测试的像素比较口径，
  按项目测试轮次限制没有继续运行 unittest，因此不能报告 15 项全通过。
- cx_Freeze 和本地便携 ZIP 构建成功。解压后的基础媒体 120 文件与扩展 140 文件
  均通过清单校验。此本地媒体验证包沿用开发版本号，不是新发布版本；未构建
  Rust 模拟器、刷机工具和 Inno 安装程序。
- 在解压目录实际启动 `vs_worker.exe`，请求显示帧，并与随包 VSPipe 比较第
  0/1/3 帧的 Y/U/V 平面摘要，全部一致。中文及空格路径图片经旋转、裁剪、循环
  编码后可回读，帧率 30 fps，VUI 为 matrix/transfer/primaries=6、Range=0。
- 以上不包含完整 GUI 操作、所有 GPU/AI 插件运算或目标设备人工验收。

已核实的教程修正：

- Bicubic 的 B=C=1/3 是 Mitchell–Netravali；Catmull–Rom 为 B=0、C=1/2。
  resize 参数名为 `filter_param_a` / `filter_param_b`，不是教程简写的 b/c。
  参见 [VapourSynth Resize](https://www.vapoursynth.com/doc/functions/video/resize.html)。
- `_FieldBased` 为 1=BFF、2=TFF，教程反写了场序。
  参见 [SetFieldBased](https://www.vapoursynth.com/doc/functions/video/setfieldbased.html)。
- BM3DCPU 的 `chroma=True` 需要 YUV444PS，不能仅将 YUV420 提升位深。
  参见 [BM3DCPU/BM3DCUDA 参数](https://github.com/WolframRhodium/VapourSynth-BM3DCUDA#parameters)。
- Yadifmod 的 `edeint` 必须与源同尺寸；`znedi3(dh=True)` 倍高的结果不能直接传入。
  参见 [Yadifmod](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-Yadifmod#usage)
  和 [znedi3](https://github.com/sekrit-twc/znedi3)。

不能仅凭插件名把任意 HDR、AI 或插帧链当作默认画质增强。此类脚本需明确源格式、
目标格式和时间轴语义，使用单个实际样本验证后再用于项目。
