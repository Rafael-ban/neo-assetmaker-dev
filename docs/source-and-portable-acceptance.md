# 源码克隆与便携分发：测试结果和解决方案

核对时间：2026-10-13。基线为 master `25b6e4127f2dd5257aaf3bb67d97cd68a4b11525`。
本次执行环境是 Linux / Python 3.12.14。项目目标仍是 Windows 10/11；本报告区分实际文件/GUI验证、模拟媒体边界的单元测试，以及尚待 Windows 执行的验收。

## 已确认的原因

### 1. 干净克隆不包含媒体二进制

`tools/` 被 Git 忽略。`uv sync` 安装 Python 依赖，不下载 VapourSynth R79、VSPipe、x264 或 muxer。
原 README 快速启动先列出 `main.py` 启动，再在后文描述媒体目录要求，且 clone URL 指向上游而不是本仓库。
因此仅按原快速启动命令操作的人能安装 Python 包、甚至打开界面，但无法正常预览/导出。

本次依赖安装使用 `uv sync --locked --group dev --no-install-project`，成功安装 47 个包，锁文件未修改。
README 已修正为本仓库地址，并把媒体 ZIP 下载、可信 SHA-256、条件解压和部署树校验放在启动之前。
重复部署复用有效目录；损坏或旧布局给出明确诊断，不将 R73 和 R79 混合覆盖。

### 2. 预览 job 与上次运行的缓存撞名

MainWindow 将缓存根固定为 `<project>/.assetmaker-vs/preview/<track>`；widget 每次重新建立后 epoch 从零递增；job 按 `job-{epoch}.json` 写入且不能覆盖。
异常退出残留、删除失败或并行打开同一个项目，使后续运行遇到相同 job 名。
导出收集阶段会 flush 预览 job，所以错误也可能发生在点击导出时。

本次修复：每个 widget 在缓存根内建立唯一 `session-*` 临时目录，job 与脚本索引放在该目录中。
job 仍保留不覆盖约束。旧 job 在 unload/worker stopped 确认后退休；最后一个 job 退休后清除该会话的脚本缓存。
runtime reload、正常退出、无 worker 的退出和写 job 失败也清理自身目录。
新会话不读取历史残留，且不删除其他 widget/进程的缓存。

关闭所有程序实例与其 worker 后，可删除项目 `.assetmaker-vs/preview`，或调用 `core.vs_runtime.preview_cache.clear_preview_cache(root)` 做离线清理；不要传入项目根目录。
异常强杀仍可能留下目录，但这些残留不再影响新会话。

### 3. 日志中的 seal 后 job 读取来自旧实现

给定日志的 `export_service.py` 行号和调用顺序与 `0af4536` 修复前完全一致：删除 `.assetmaker-work` → seal → 再读其中的 job。
2026-09-08 的 `0af4536b957c8b8eaef9dd46fb67a456af27c60c` 已把完整身份检查移到清理前；seal 后仅检查 script/runtime。
本次没有重复重写这段修复，运行了对应四个回归用例。

如果用户仍收到同样 traceback，需要更新实际构建产物，而不是继续修改已经修好的源码。
不得仅因为程序显示同一个版本号，就推定它包含相同提交；当前构建尚未嵌入 commit，启动诊断明确记录 commit=unknown。

### 4. 两种旧路径不能混为一谈

traceback 的 `D:\a\...`、`C:\hostedtoolcache\...` 是构建时源文件的位置标记，可以留在代码对象中；不表示访问开发者电脑。
截图错误字段 `actual` 的 `D:\Visual Studio\...\tools\media` 则是实际 runtime 根，明确不正确。
当前 cx_Freeze 7.2.10 的目录算法符合其 frozen 契约：`sys.frozen` 时采用 `sys.executable` 的父目录，源码时采用模块所在源码根。
本次没有证据支持把当前算法随意替换为 cwd、任意搜索 DLL 或开发目录回退。

新增启动诊断记录版本、frozen 状态、executable、file_utils 模块来源、resolved_app_dir 和 cwd。
Windows 缺失 runtime 目录时记录一次部署提示，保留 GUI 启动。完整性与真实执行由部署校验和媒体验收负责。
定位截图问题仍需取得那一份实际出错的 EXE/启动诊断；本次没有取得用户当时运行的包。

### 5. 克隆传播了本机驱动安装标志

原 `config/config.json` 被 Git 跟踪，内容为 `driver_installed=true`。
刷机流程据此跳过驱动安装，不能用开发者的安装状态推定其他电脑已安装驱动。
本次移除此文件的版本跟踪，并加入 `.gitignore`；缺失时由已有刷机初始化逻辑建立本机状态。
未安装或执行刷机驱动，没有连接用户设备。

## 本次实际验证

| 检查 | 结果与边界 |
|---|---|
| 锁定 Python 依赖安装 | 成功；47 个包，Python 3.12.14，未修改 uv.lock |
| 正式 R79 ZIP 下载 | 成功；127,806,386 字节 |
| ZIP SHA-256 与成员校验 | 成功；`50f86a516fbfc97812e2c6ea500ba1a06937d4cda5e2bc211c071309a6ce7dd4` |
| 解压及部署树 verify-tree | 成功；120 文件、343,699,939 字节全部匹配 |
| 源码 Qt 主窗口初始化 | 成功；offscreen 下跳过首次公告、自动更新与恢复交互，创建临时项目并正常清理；不代表 Windows 人工 GUI 验收 |
| 预览缓存及相关生命周期 | 44 测试运行：42 通过、2 跳过；假 worker；Linux 测试局部适配 Windows 路径谓词和无默认 runtime 快照，不修改生产校验 |
| 新增启动路径/诊断 | 10 通过；模拟源码/frozen 属性，非实际 Windows EXE |
| 既有崩溃诊断 | 21 通过；使用已安装 Qt 的 .venv，未跳过 |
| 导出清理/身份检查回归 | 4 通过；真实 ExportService 包事务，编码边界 mock；Linux 测试局部适配 Windows 路径谓词 |
| 分发/打包/layout 定向测试 | 68 父测试：59 通过、9 因 Windows 文件属性/重解析点/DLL API 不兼容 Linux而受阻；子测试合计 12 errors、2 failures，未宣称全通过 |
| 既有真实导出集成测试 | 3 项尝试均受 Windows 路径 ABI 限制，未执行真实编码；部署 EXE 文件存在不代表 Linux 可运行它们 |
| 新增冻结验收入口单元测试 | 10 通过；生产模型/会话序列化与真实 Qt 信号，媒体执行边界 mock；Linux 实际 CLI 非零退出并写出 platform 阶段报告 |
| Windows 真实 worker 帧、VSPipe 编码、MP4 回读 | 当前执行环境未完成；需按下节在 Windows 跑 |

原直接启动 main.py 的无界面超时已定位在首次使用公告的 `dialog.exec()`，属于等待交互，不应报告为主窗口崩溃。
测试中只跳过该交互；正式产品公告逻辑保留。

## Windows 端验收流程

1. 在全新目录准备本仓库源码，按 README 完成依赖与 R79 基础媒体包部署。
2. 普通默认处理仅依赖基础包。要构建与仓库清单相符的完整分发，另执行 `uv run --no-sync python plugin_distribution.py sync --app-dir .` 和 `verify --app-dir .`。
3. 运行 `uv run --no-sync python main.py --smoke-test --report source-smoke.json`。
4. 生成新 Windows 构建；修改源码不会修改已经发出去的 EXE。构建目录与 dist 应是新的/已按 build.py --clean 清理的输出，不复用旧便携 ZIP。
5. 解压真正的便携 ZIP 到中文/空格路径，切换到另一个工作目录，以完整 EXE 路径运行 `--smoke-test --report <绝对报告路径>`。
6. 验收报告必须 success=true、frozen=true、app_dir/executable 指向解压后的目录；worker 取帧、12 帧 360×640 导出、MP4 解码回读与工作目录清理必须通过。
7. 最后人工用一个实际 MP4 项目检查连续播放、暂停、裁剪、旋转、循环/入场切换、导出及重开同一项目；目标设备显示仍属于独立验收。

新增的 `--smoke-test` 入口运行实际生产 RenderJob/RenderSession、独立 worker IPC 与 ExportService，使用临时中文素材目录和隔离的 APPDATA；不会读取或改写用户 runtime 覆盖配置。
测试请求第 0/11 帧并验证输出元数据，再导出 12 帧 MP4，用 OpenCV 回读帧数、尺寸和非黑画面。超时和异常写阶段报告并非零退出。
该 PNG 样本覆盖 imwri 和编码链，不证明所有 MP4 解码器、所有滤镜、GPU/AI 算子或完整 GUI 工作流都已通过。
构建 CI 在上传产物的流程中加入迁移目录真实验收及 JSON 报告上传；它需要下一次 Windows 构建执行，不能把新增 CI 代码说成已成功运行。

## 发布及反馈约定

- 发布包含完整目录的绿色 ZIP 和安装版，避免只发送 EXE 或只复制 lib 的部分文件。
- 用户使用新目录解压；旧项目保留，关闭所有实例后可清理旧 preview 缓存。
- 反馈同时提供 `app_*.log` 与 `diagnostic-*.log`，以及便携包文件名和是否重新构建/解压；实际目录从 resolved_app_dir 看，不从 traceback 文件名猜。
- 版本号应随真正发布同步，后续可给构建产物加入实际 commit 信息；目前 unknown 的记录只用于诚实诊断。
- 本报告记录源码修改与本地验证结果；新增 Windows CI 验收仍需在 GitHub 上实际运行。本次未发布新的 Windows 二进制。

## 滤镜清单

[完整清单](filter-inventory.md) 给出当前默认实际调用、64 个扩展/依赖包的锁定版本、命名空间、加载策略和 GitHub 上游。
默认 `process_source()` 原样返回，采用 Lanczos，显式关闭抖动（none）；默认未启用 DFTTest、neo_f3kdb、插帧或 AI 放大。

## 追加：Lanczos、关闭抖动与可选 RIFE 60 fps

按用户最终选择：默认及预览缩放改为 Lanczos，明确使用 dither_type=none；8-bit设备合同和x264量化设置保留。RIFE先由脚本参数按需开启、默认关闭，只将低于60fps的多帧视频插到60；图片、单帧和>=60fps素材保留原帧率。没有新增GUI开关或迁移旧项目schema，旧trim帧号切换RIFE后必须重新核对。

- 滤镜/显示/时间轴相关13个定向模拟测试一次通过，包括25/23.976/29.97→60、跳过条件和错误诊断；没有执行真实GPU推理。
- 插件分发相关9个用例最终通过；新增raw模型下载、坏下载保留旧包、选择安装与依赖闭包、未知包诊断、禁止把局部clean当成全部删除。新增测试最初的fixture字段和hash错误已修正，仅复测失败项。
- 实际下载上游r9_mod_v33 DLL、v4.6_ensembleFalse模型及MIT许可并核对SHA-256；复用这些真实字节做安装/校验，7个最终文件（含邻近模型副本）、26,559,742字节通过，重复sync不下载。
- 同一sync操作新增--package rife，可只安装RIFE及其3项模型/许可依赖；完整打包仍沿用全部64包清单。
- DLL导入表实测包含VCOMP140.DLL，启用电脑须安装官方VC++v14 x64运行库及Vulkan驱动；默认不自动加载RIFE。

启用与验证步骤见 [RIFE配置](vapoursynth-kb/17-filter-configuration.md#可选-rife-插帧到-60-fps)。
