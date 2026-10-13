# 17 · 默认滤镜参数与扩展插件

滤镜图仍由用户、项目或内置 `.vpy` 编写；本次不新增 GUI 滤镜面板，也不恢复
旧 `vsconfig.json` 图配置。设备输出尺寸、帧数、帧率和颜色合同仍来自 RenderJob。

## 内置处理链

`resources/vapoursynth/default_pipeline.vpy` 顶部集中缓存、未标记源的解释、
尺寸缩放核和设备格式转换参数。默认使用 Lanczos，尺寸缩放不传 `format=vs.YUV420P8`，
保留源格式和位深。修改 `FINAL_RESIZER` / `FINAL_RESIZE_OPTIONS` 可选择其他 resize 核。
缩放结束后单独执行设备所需的格式/颜色转换；`OUTPUT_CONVERSION_OPTIONS` 保留原有
`dither_type="error_diffusion"`，显示端的 RGB24 转换也保留该抖动设置。

顺序为：取源 → 补充未标记源的颜色信息 → 旋转 → 图片循环 → output 1 →
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

`default_pipeline.vpy` 不包含 RIFE。需要插帧时选择独立的
`resources/vapoursynth/rife_pipeline.vpy`，默认目标为 60 fps；仅处理低于60 fps、
至少两帧的视频。图片、单帧和≥60 fps视频保持原帧率。
独立模板仍保留完整编辑画布、trim/crop、旋转、色彩和设备输出合同。

依赖使用 [VapourSynth-RIFE-ncnn-Vulkan](https://github.com/styler00dollar/VapourSynth-RIFE-ncnn-Vulkan)
的 `r9_mod_v33` DLL 和 `rife-v4.6_ensembleFalse` 模型。启用电脑需要 Vulkan GPU/驱动
及 [Microsoft VC++ v14 x64运行库](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)。
DLL导入包含VCOMP140.DLL和MSVC v14运行库；基础R79包不包含VCOMP140.DLL。
这些要求只适用于选用RIFE的电脑。

### 安装 DLC 或从源码安装

RIFE DLC ZIP包含独立脚本、DLL、模型、MIT许可及其清单；不包含主程序和基础媒体包。
把 ZIP内容解压到包含 `ArknightsPassMaker.exe` 的实际应用目录，保持resources/tools层级。
基础目录和DLC版本分别安装；默认内置脚本继续使用基础图。

源码目录可直接安装（或修复）RIFE：

```powershell
uv run python plugin_distribution.py sync --app-dir . --manifest resources/packaging/vs-rife.json
uv run python plugin_distribution.py verify --app-dir . --manifest resources/packaging/vs-rife.json
```

RIFE管理根为 `tools/vs-rife/`；通用扩展根为 `tools/vs-plugins/`。
同步、修复及清理只影响相应根，不会删除另一组扩展。安装到已有便携包时，从源码
目录运行同一工具，将 `--app-dir` 指向该应用目录，`--manifest`仍指向源码中的独立清单。

### 配置插件路径与选择脚本

把实际应用的 `tools/vs-rife/rife` 绝对目录加入本机运行覆盖的
`plugins.native_plugin_dirs`，不写入随源码分发的config/vs_runtime.json。
以前配置过 `tools/vs-plugins/rife` 时，用新目录替换，避免重复加载同一个插件。
下列命令在源码根执行，保留其他本机配置并生成当前应用路径：

```powershell
uv run python -c 'from pathlib import Path; from config.vs_runtime import load_vs_runtime, save_vs_runtime_override, default_vs_runtime_user_path; folder = str((Path.cwd() / "tools" / "vs-rife" / "rife").resolve()); old = (Path.cwd() / "tools" / "vs-plugins" / "rife").resolve(); dirs = list(dict.fromkeys([*[d for d in load_vs_runtime().plugins.native_plugin_dirs if Path(d).resolve() != old], folder])); save_vs_runtime_override(default_vs_runtime_user_path(), {"plugins": {"native_plugin_dirs": dirs}})'
```

在“VapourSynth脚本 → 来源”选择“全局本机脚本”，选中独立的rife_pipeline.vpy；
或复制到项目内，选择“项目脚本”，按现有流程确认信任。“内置脚本”始终使用基础图。
独立模板的参数默认为 `RIFE_ENABLED=True`、`RIFE_TARGET_FPS=60`。
选择“重载”或重新打开素材后生效。模板头声明rife.RIFE，所以选用它前须安装并配置
RIFE，即使当前素材会跳过插帧；需要基础处理时直接选择内置脚本。

模型默认位于当前RenderSession实际应用根的
`tools/vs-rife/rife/models/rife-v4.6_ensembleFalse`；复制脚本到项目或外部目录不会
改变模型根。模型另存于外部目录时，可显式设置RIFE_MODEL_PATH。
插件、模型或GPU失败会报告原因，不把原视频冒充为成功插帧结果。

插帧位于output 1与trim之前；预览、编辑和导出使用同一时间轴。
帧数按floor(源帧数×60/源帧率)取整，尾部误差小于一个60 fps帧。
旧项目只保存trim帧编号，切换脚本后需要重新核对入出点。
尺寸缩放不指定YUV420P8；设备输出前单独转换格式，保留原有抖动和x264设置。
DLC打包和模拟测试不代表已完成Vulkan GPU推理验收。

### 制作与清理 DLC

完成RIFE资源安装后运行：

```powershell
uv run python rife_dlc.py --app-dir . --output dist/RIFE-DLC-r9_mod_v33.zip
```

打包前校验锁定文件，输出已存在时要求选择新文件名，不覆盖已有包。
**Build RIFE DLC**工作流可独立生成下载产物，无需构建主程序。

清理RIFE独立资源（脚本模板保留）：

```powershell
uv run python plugin_distribution.py clean --app-dir . --manifest resources/packaging/vs-rife.json
```

## 通用扩展的安装、修复与清理

通用扩展清单为 `resources/packaging/vs-plugins.json`，不是基础构建要求。
需要时运行：

```powershell
uv run python plugin_distribution.py sync --app-dir .
uv run python plugin_distribution.py verify --app-dir .
uv run python plugin_distribution.py clean --app-dir .
```

构建只校验R79基础媒体树，不校验或收集扩展；CI不再下载整套插件/模型。
安装后的插件目录、依赖和模型仍通过现有runtime配置与身份校验供worker/VSPipe共用。
无需改变现有R79下载变量，也不需要重新生成基础媒体工具包。

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
