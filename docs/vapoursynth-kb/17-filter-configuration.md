# 17 · 默认滤镜参数与扩展插件

滤镜图仍由用户、项目或内置 `.vpy` 编写；本次不新增 GUI 滤镜面板，也不恢复
旧 `vsconfig.json` 图配置。设备输出尺寸、帧数、帧率和颜色合同仍来自 RenderJob。

## 内置处理链

`resources/vapoursynth/default_pipeline.vpy` 顶部集中缓存、未标记源的解释、
尺寸缩放核和设备格式转换参数。默认使用 Lanczos，尺寸缩放不传 `format=vs.YUV420P8`，
保留源格式和位深。修改 `FINAL_RESIZER` / `FINAL_RESIZE_OPTIONS` 可选择其他 resize 核。
缩放结束后单独执行设备所需的格式/颜色转换；`OUTPUT_CONVERSION_OPTIONS` 保留原有
`dither_type="error_diffusion"`，显示端的 RGB24 转换也保留该抖动设置。

顺序为：取源 → 补充未标记源的颜色信息 → 旋转 → 图片循环 / 可选 RIFE → output 1 →
时间裁切 → 空间裁剪 → `process_source()` → 保留源格式的尺寸缩放 → 设备格式/色彩转换 → 补边/设备旋转 →
output 0。`process_source()` 默认原样返回，供按实际素材加入去噪、去色带等处理。
启用第三方调用时，同时在脚本头的 `assetmaker-requires` 中声明具体函数。

output 1 保留完整编辑画布与时间轴；output 0 固定为设备需要的 YUV420P8。
中间处理可以使用高位深或 float，但必须在输出前回到该合同。
不要把教程中的固定 1920×1080、10-bit 输出或 IVTC 帧率直接用于 compatible 脚本。

输入已有颜色标签时保留标签；缺少标签时，YUV 按原始源高度推断 709/170m，
RGB 图片默认 sRGB/709/full。默认图实际执行 matrix、transfer、primaries、range
转换，不仅重写最终标签。显示支路转换到 sRGB/709/full；适应窗口使用 Lanczos，
像素放大保留 Point。画面比较应在相同显示色彩空间中进行。
设备合同的 transfer `170m` 传入 resize 时使用 `601`，两者数值代码均为 6；
matrix 与 primaries 的名称仍为 `170m`。

## 可选 RIFE 插帧到 60 fps

RIFE 由脚本参数控制，默认关闭。启用后仅将低于 60 fps、至少两帧的视频
插到 60 fps；图片、单帧视频以及原本达到或超过 60 fps 的素材保持原帧率。
使用 [VapourSynth-RIFE-ncnn-Vulkan](https://github.com/styler00dollar/VapourSynth-RIFE-ncnn-Vulkan)
的 `r9_mod_v33` Windows DLL 和 `rife-v4.6_ensembleFalse` 模型。
模型文件及 MIT 许可与 DLL 一起分发并校验，不依赖开发机目录。

需要具有 Vulkan 支持的 GPU/驱动，以及
[Microsoft Visual C++ v14 x64 Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)。
本次实测 DLL 的导入表包含 `VCOMP140.DLL`（OpenMP）和 MSVC v14 运行库。
基础 R79 媒体包没有 `VCOMP140.DLL`，因此启用 RIFE 的电脑应安装官方运行库；
没有 Vulkan 的电脑可以保持 RIFE 关闭。该包设置 `autoload=false`，避免默认运行
因可选 GPU 插件依赖而失败。

源码根目录执行，只安装 RIFE 及其依赖，不需要下载全部扩展：

```powershell
uv run --no-sync python plugin_distribution.py sync --app-dir . --package rife
uv run --no-sync python plugin_distribution.py verify --app-dir . --package rife
```

把当前应用的 `tools/vs-plugins/rife` 绝对目录加入本机运行配置覆盖，而不是
写入随源码分发的 `config/vs_runtime.json`。下面命令在 Windows 源码根执行，
合并已有目录并保留其他配置；路径由当前应用位置生成，不使用开发者路径：

```powershell
uv run --no-sync python -c 'from pathlib import Path; from config.vs_runtime import load_vs_runtime, save_vs_runtime_override, default_vs_runtime_user_path; folder = str((Path.cwd() / "tools" / "vs-plugins" / "rife").resolve()); dirs = list(dict.fromkeys([*load_vs_runtime().plugins.native_plugin_dirs, folder])); save_vs_runtime_override(default_vs_runtime_user_path(), {"plugins": {"native_plugin_dirs": dirs}})'
```

然后在所选 `.vpy` 脚本参数区设置：

```python
RIFE_ENABLED = True
RIFE_TARGET_FPS = 60
```

内置脚本的模型路径基于 canonical 脚本所在应用目录生成，指向
`tools/vs-plugins/rife/models/rife-v4.6_ensembleFalse`。如果把脚本复制到全局或
项目脚本目录，须同时将 `RIFE_MODEL_PATH` 设置为当前应用的该模型目录。
选择“重载”或重新打开素材后生效。明确启用却缺少插件、模型或可用 GPU 时会报告
原因，不把原视频冒充为成功插帧结果。

插帧发生在 `output 1` 和 trim 之前，编辑时间轴、预览及导出采用同一帧率。
输出帧数按 `floor(源帧数 × 60 / 源帧率)` 取整，尾部时长误差小于一个 60 fps 帧。
例如 25 fps、24000/1001 和 30000/1001 fps 都是真正插到 60，而非一律乘二。
旧项目仅存 trim 帧编号：请在开始裁剪前确定 RIFE 开关；切换已有项目时重新核对
入出点，避免把旧帧编号当作 60 fps 时间轴中的同一时间。

关闭抖动不等于删除设备要求的 8-bit YUV420P8 转换，也不修改 x264 编码质量参数。
真实 Vulkan GPU 插值尚未在本次 Linux 环境执行；模型安装/校验与模拟时间轴测试
不能替代 Windows 上启用 RIFE 的真实视频预览、导出和回读。

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
