# 明日方舟通行证素材工具箱

Arknights Pass Material Toolbox 是一个面向明日方舟电子通行证 2.1 素材的 Windows 图形化制作工具。它将项目配置、素材编辑、VapourSynth 渲染、x264 编码、设备模拟和素材管理整合到同一套工作流中。

> 本仓库是**源代码**。用户实际运行的是由 `build.py` 生成的 `ArknightsPassMaker` 构建目录或安装包；修改源代码后，必须重新构建或发布，变更才会进入可运行程序。

## 核心能力

- **素材制作**：编辑 `epconfig.json`、循环/入场素材、过渡、图标和叠加层。
- **统一视频渲染**：预览与导出共用同一份用户 `.vpy` 脚本和同一份冻结渲染作业；“导出预览”展示的就是导出图。
- **可交互编辑**：时间轴逐帧定位、播放/暂停、裁剪、四向旋转、预览缩放，以及视频帧截取为图标。
- **自定义 VapourSynth**：可选择内置、当前 Windows 用户全局或项目内 `.vpy` 脚本；用户可在明确 ABI 和输出契约下编写自己的滤镜图。
- **可靠导出**：`VSPipe → x264-7mod → MP4Box/lsmash-muxer` 生成设备素材；导出前校验颜色、帧率、几何和配置。
- **设备模拟**：Rust/egui 模拟器按当前项目参数播放入场、循环、过渡和叠加层。
- **素材生态**：素材论坛、下载管理、OAuth/FIDO2 登录、USB/MTP 与 EPass RNDIS 远程管理。
- **项目保护**：自动保存、崩溃恢复、临时项目、更新检查和操作日志。

## 渲染架构

```text
素材文件 + EPConfig + 用户 .vpy
            │
            ├── 预览：Qt → vs_worker.exe → VapourSynth → BGR 帧共享内存 → Qt
            │
            └── 导出：VSPipe → x264-7mod → MP4Box / lsmash-muxer
```

预览 worker 和 VSPipe 运行的是同一份主 `.vpy`、同一份 job、同一份输出契约。主 GUI 进程不直接加载 VapourSynth DLL，避免 Qt/DLL 生命周期冲突；worker 隔离的是渲染运行时，并不是运行不可信 Python 的安全沙箱。

## 环境要求

- Windows 10/11（项目当前仅支持 Windows）
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Rust stable（只在编译 `simulator/` 或完整打包时需要）
- Inno Setup（只在本地生成安装包时需要；`build.py` 可尝试下载便携版）
- 媒体工具包：VapourSynth、VSPipe、x264-7mod、MP4Box/lsmash-muxer 及所需插件

## 快速开始

```powershell
git clone https://github.com/rhodesepass/neo-assetmaker.git
cd neo-assetmaker
uv sync --no-install-project
uv run python main.py
```

首次运行前，请将媒体工具包解压到 `tools/media/`。至少应包含：

```text
tools/media/
├── VSPipe.exe
├── x264-7mod.exe
├── mp4box.exe 或 lsmash-muxer.exe
├── vapoursynth.pyd / vapoursynth.dll / portable.vs
└── vs-plugins/
    ├── LSMASHSource.dll
    └── libimwri.dll
```

`portable.vs` 与 `vs-plugins/` 不是可选装饰文件：VapourSynth 便携运行时通过它们定位插件，缺失时插件加载可能退化为“没有可用插件”。GitHub Actions 会在构建前检查这些文件。

## 基本使用

1. 选择“新建项目”或“打开项目”，项目主配置为 `epconfig.json`。
2. 在统一配置页的“视频配置”中导入循环视频/图片，并可按需启用和导入入场素材。
3. 用时间轴、裁剪框和旋转控制调整素材；“导出预览”用于核对实际设备输出画面。
4. 在配置面板填写名称、UUID、屏幕规格、过渡和叠加层；保存项目。
5. 运行导出。输出目录包含 `epconfig.json`、`loop.mp4`、可选 `intro.mp4`、图标与叠加资源。
6. 可启动模拟器核对最终播放流程，再上传到设备或通过 RNDIS 远程管理同步。

所有配置中的时间值均使用**微秒**：`1 秒 = 1_000_000`。

## 自定义 VapourSynth `.vpy`

`.vpy` 是实际的 Python/VapourSynth 脚本；`epconfig.json` 是项目和设备配置，二者用途不同。

脚本来源有三种：

| 来源 | 保存位置 | 适用场景 |
|---|---|---|
| 内置 | `resources/vapoursynth/default_pipeline.vpy` | 默认工作流或复制为模板 |
| 全局 | 当前 Windows 用户的运行时覆盖配置 | 同一台电脑上复用个人脚本，不写入项目 |
| 项目 | 项目目录内的相对路径 | 与项目一起保存和分享 |

自定义脚本必须显式调用 `clip.set_output(0)`。`compatible` 模式若声明编辑能力，还需要以 `output 1` 提供可交互裁剪画布；`raw` 模式则完全由脚本自行构图，只输出 `output 0`。

项目脚本第一次运行或其目录内容发生变化后，需要由本机用户重新确认。确认不是安全沙箱：只应运行你已经审查过的 `.vpy` 及其 Python 模块。

完整接口、脚本头、注入变量、输出约束、插件声明和信任规则请阅读：

- [用户 `.vpy` 脚本接口](docs/vapoursynth-kb/13-user-vpy-abi.md)
- [预览 worker 与帧传输](docs/vapoursynth-kb/14-worker-protocol.md)
- [output 0/1 与设备编码契约](docs/vapoursynth-kb/15-output-contract.md)
- [脚本来源与本机信任](docs/vapoursynth-kb/16-script-trust.md)
- [VapourSynth 知识库索引](docs/vapoursynth-kb/INDEX.md)

## 配置与目录

```text
neo-assetmaker/
├── main.py                         # Qt 应用入口
├── build.py                         # cx_Freeze、安装包与发布构建入口
├── installer.iss                    # Inno Setup 安装包定义
├── config/
│   ├── epconfig.py                  # 项目/设备配置数据模型
│   ├── constants.py                 # 设备规格、默认值、版本
│   ├── vs_runtime.py                # worker、核心、插件路径的运行时配置校验
│   └── vs_runtime.json              # 运行时默认值；不定义滤镜链
├── core/
│   ├── export_service.py            # 导出编排
│   ├── media_pipeline.py            # VSPipe、x264 与复用器管线
│   ├── media_tools.py               # 媒体工具发现与能力检查
│   ├── vs_runtime/                  # job、协议、worker、信任与迁移逻辑
│   └── validator.py                 # EPConfig 校验
├── gui/
│   ├── main_window.py               # 主窗口与项目工作流
│   ├── widgets/video_preview.py     # 预览、时间轴、裁剪与 worker 客户端协作
│   └── widgets/vs_script_panel.py   # `.vpy` 来源选择界面
├── resources/vapoursynth/
│   ├── default_pipeline.vpy          # 内置 compatible 脚本模板
│   ├── assetmaker_runner.vpy         # worker/VSPipe 的脚本启动器
│   └── python/assetmaker_vs/         # 用户脚本 ABI 与输出校验辅助模块
├── simulator/                        # Rust/egui 设备模拟器
├── _mext/                            # 素材论坛、下载、认证和 USB/MTP 扩展
├── docs/                             # 用户手册、知识库、变更日志
└── .github/workflows/                # CI、构建与发布工作流
```

`config/vs_runtime.json` 仅控制 worker 超时、VS core 资源上限、插件目录和全局脚本位置；滤镜顺序、裁剪、颜色与输出由内置或用户 `.vpy` 明确编写，不能把这两类职责混在一起。

## 测试

```powershell
# 完整 Python/Qt/协议/脚本契约测试
uv run python -m unittest discover -s tests -p "test_*.py"

# 导入与语法检查
uv run python -m compileall main.py config core gui utils _mext build.py tests resources/vapoursynth/python

# VPY 默认模板、输出与 worker 的重点验证
uv run python -m unittest `
  tests.test_default_vpy_pipeline `
  tests.test_vs_output_contract `
  tests.test_vs_worker_process `
  tests.test_preview_export_parity -v
```

真实编码、worker 与预览集成测试需要本地 `tools/media/` 可用；没有媒体工具时，相关测试会按测试条件跳过，因此绿色结果不等价于真实媒体路径已被覆盖。

## 构建与发布

### 本地构建

```powershell
# 完整构建：cx_Freeze 目录 + 绿色免安装 ZIP + Inno Setup 安装包
uv run python build.py

# 不生成安装程序，但仍生成可分发的绿色 ZIP
uv run python build.py --no-installer

# 仅供 CI 快速验证：只保留 cx_Freeze 目录，不生成 ZIP 或安装程序
uv run python build.py --no-installer --no-portable

# 清理构建目录后再构建
uv run python build.py --clean

# 不将本地 epass_flasher/bin 打入产物
uv run python build.py --skip-flasher

# 可选的本地混淆构建；大脚本需要已注册的 PyArmor 8 付费许可证
uv pip install "pyarmor>=8,<9"
pyarmor reg "C:\path\to\pyarmor-regfile-xxxx.zip"
uv run python build.py --obfuscate
```

构建结果：

| 产物 | 位置 | 使用方式 |
|---|---|---|
| 冻结目录 | `ArknightsPassMaker/` | 开发期检查 cx_Freeze 实际分发内容，不直接当作 Release 文件上传 |
| 绿色免安装包 | `dist/ArknightsPassMaker-v<版本>-windows-portable.zip` | 解压后保留 `ArknightsPassMaker/` 整个目录，运行其中的 `ArknightsPassMaker.exe`；不能只取出单个 EXE |
| 安装包 | `dist/*.exe` | 运行 Inno Setup 安装程序并按向导安装 |

绿色包与安装包使用相同的 cx_Freeze 内容，因此均包含 `vs_worker.exe`、`resources/`、`tools/media/` 与运行时配置。构建前需要先编译 Rust 模拟器：

```powershell
cd simulator
cargo build --release
cd ..
```

### GitHub Actions

- [`build.yml`](.github/workflows/build.yml)：push、Pull Request 或手动触发的 CI 入口。常规 CI 仍执行 Rust、全量 Python、cx_Freeze 与 worker 自测，但跳过 Inno Setup、绿色 ZIP 和 artifact 上传，以缩短反馈时间。
- 手动运行 `Build` 工作流时，默认会同时构建并上传**安装版 EXE 与绿色免安装 ZIP**；可取消“package artifacts”仅执行快速构建验证。
- [`build-app.yml`](.github/workflows/build-app.yml)：Windows 可复用构建流程。Release/手动产物构建会校验绿色 ZIP 可解压且包含主程序、worker、运行时配置、`portable.vs` 和内置 `.vpy`。
- [`release.yml`](.github/workflows/release.yml)：当 `docs/CHANGELOG.md` 顶部版本变化时创建发布，同时附加安装版、绿色免安装版和同时覆盖二者的 `SHA256SUMS`。CI 与 Release 不启用需要付费许可证的 PyArmor；`--obfuscate` 仅保留为本地可选构建参数。版本必须同时匹配 `pyproject.toml`、`config/constants.py`、`installer.iss` 与 `simulator/Cargo.toml`。

修改 `docs/CHANGELOG.md` 顶部版本后推送会触发自动 Release；未准备发布时不要这样做。

## 相关文档

- [用户手册](docs/USER_MANUAL.md)
- [VapourSynth 知识库索引](docs/vapoursynth-kb/INDEX.md)
- [VapourSynth 架构说明](docs/VS_DECOUPLING.md)
- [更新日志](docs/CHANGELOG.md)

## 许可证

本项目仅供学习和研究使用。
