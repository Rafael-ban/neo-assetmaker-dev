# 导出末尾闪退、界面响应与日志完整性修改方案

状态：2026-09-07 用户审核通过；2026-09-08 源码实施与自动验证完成，用户手测导出成功后授权日志复核及本地提交。下文草稿状态为送审时快照；最终结果见 [验收记录](2026-09-07-export-crash-acceptance.md)。构建、推送和发布仍需另行授权。

## 1. 审核范围与当前状态

本方案覆盖用户提出的四项需求：

1. 导出末尾失败并闪退。
2. 编码期间界面鼠标移动卡顿。
3. 日志不完整，无法充分分析故障。
4. 内置 `resources/vapoursynth/default_pipeline.vpy` 显式设置 `core.max_cache_size = 16000`。

“旧网络栈服务”按用户最新意见暂缓，不删除、不迁移、不扩大调查。

先前已按“GPT-6 分析、Terra 写修复”产生部分未提交草稿；新增审核要求后已暂停。现有草稿保留，不自动回滚。**这不是已完成或可以直接发布的版本。**

| 子项 | 已有草稿 | 暂停时的验证与缺口 |
|---|---|---|
| 导出事务与线程生命周期 | 已修改核心及测试 | Terra 定向 49 项、0 skip 通过；GPT-6 独立验收 48 项、0 skip。另有集成测试超时清理意见未闭环 |
| GUI 暂停、取消、光标 | 已修改 GUI 及测试 | Terra 最后定向 18 项通过；独立审查留下取消信号同步重入问题；真实控件测试原生退出待定位 |
| 默认脚本 cache=16000 | 赋值和测试草稿已写 | 静态检查通过；真实 worker 已执行 16000 断言，随后测试误用 metadata 字段失败；VSPipe 部分未执行 |
| 诊断日志与完整导出 | 只有两份红测草稿 | `main.py`、`utils/logger.py` 未修改；诊断模块尚不存在，测试含预期失败和导入错误 |

上述测试次数不是整套测试的累计通过数，不能相加冒充完整验收。当前新增日志测试仍为红灯，缓存测试未全部通过；全套测试和新绿色包均未验收。

## 2. 已确认的故障与证据边界

### 2.1 导出失败：删除了还要校验的 job

现场日志最后的异常为：`seal 后最终身份检查：冻结 job 无法读取`，缺失文件位于 staging 的 `.assetmaker-work/jobs/`。

旧代码顺序为：

```text
编码完成 → 删除私有工作目录 → seal → 再读取 job 校验 → commit
```

`shutil.rmtree()` 删除目录树，之后再次打开其中的 job 不可能按原设计成功。这是调用顺序错误，不是 `read_bytes()` 或 `rmtree()` API 本身无效。[Python rmtree 文档](https://docs.python.org/3.12/library/shutil.html#shutil.rmtree)

现场包的导出模块字节码与本次修改前源码已核对一致。父代理用自建 12 帧素材、真实 VSPipe/x264/muxer 执行完整旧流程，在 3.238 秒后复现相同失败；没有发布最终目录。不是只靠 mock 推断。

### 2.2 闪退：线程提前回收存在确定缺陷，与现场 Qt fast-fail 吻合

现场 Windows 记录：22:47:29，Qt6Core.dll 6.10.2，异常码 `0xc0000409`。故障偏移指令与 Qt 的 Windows fast-fail 终止实现相符。

旧实现收到 worker 的业务成功/失败信号就 `deleteLater()` 并清空引用，但 `run()` 还会进入 `finally` 释放事务锁。Qt 6.10.2 源码明确：运行中的非 adopted QThread 被析构会 `qFatal()`；线程退出与业务结果信号不是同一时刻。[QThread 析构源码](https://github.com/qt/qtbase/blob/v6.10.2/src/corelib/thread/qthread.cpp#L409-L432)

安全闩锁探针实际截获：DeferredDelete 到达时 `running=True`、`finished=False`，线程仍在 finally。探针拦截危险事件，没有故意销毁用户应用。

因此线程提前回收是必须修复的确定缺陷，也是现场闪退的首要原因；但现场没有 minidump/完整 native 调用栈，不能称为唯一已证明的 native 调用源。[Qt Windows qAbort 实现](https://github.com/qt/qtbase/blob/v6.10.2/src/corelib/global/qassert.cpp#L21-L43)

### 2.3 卡顿：确认了额外负载，尚未复现现场全部挂起

- 模态窗口阻止用户输入，但 `exec()` 会运行嵌套事件循环，不会自动暂停预览计时器。[Qt 模态对话框文档](https://doc.qt.io/qt-6/qdialog.html#modal-dialogs)
- 当前导出入口没有暂停播放中的预览。独立探针在模态窗口 600ms 内仍收到 18 次取帧请求；显式暂停后为 0。
- 同一鼠标区域反复移动时重复调用 `setCursor()`；Qt 源码会为每次调用发送 CursorChange。旧探针 1000 次移动产生 1000 次该事件。[QWidget 源码](https://github.com/qt/qtbase/blob/v6.10.2/src/widgets/kernel/qwidget.cpp#L4627-L4640)
- 真实预览与真实编码 A/B：播放组收到 100 帧，暂停组 0 帧；两组 GUI 心跳最大间隔均约 8ms。该短素材探针未复现严重挂起，不能据此宣称 AppHang 已完全解决。
- 同步 prepare 确实在 GUI 线程，但真实小包准备约 6.6ms；注入慢等待只能证明阻塞机制，不等于现场耗时。本轮不据此直接重构整个准备阶段。

### 2.4 日志：原生通道缺失、重启覆盖与导出截断

`stderr.log` 与应用日志最新均为同次事故的 22:47:28，没有额外的 Qt fatal 原文。空 `crash.log` 不代表没有原生崩溃。

1. Python `sys.stderr` 重定向不会接管 Qt 的 OutputDebugString/C `fprintf(stderr)` 通道。[Qt Windows logging 源码](https://github.com/qt/qtbase/blob/v6.10.2/src/corelib/global/qlogging.cpp#L1823-L1896)
2. Windows fast-fail 不调用异常处理器；Qt 这里绕过常规 abort，不能依赖 faulthandler 自动捕获。[Microsoft fast-fail 文档](https://learn.microsoft.com/en-us/cpp/intrinsics/fastfail?view=msvc-170)
3. 当前 stdout/stderr/crash 文件用 `w` 打开，重启会截断旧文件。[Python open 文档](https://docs.python.org/3.12/library/functions.html#open)
4. 日志导出按单行日志头解析，丢掉 traceback 续行。父代理实际将用户原日志导出到自有临时目录：原日志 48 行、2 段 traceback；导出后 37 行、0 段 traceback，FileNotFoundError 也丢失。原始用户文件未修改。

## 3. 拟修改内容

### A. 修复导出事务收尾与线程生命周期

主要文件：`core/export_service.py`。

新的事务顺序：

```text
全部工件生成并校验
→ 全量核对所有视频的 script/job/runtime 身份
→ 删除私有工作目录
→ seal：校验工件清单、类型、配置引用
→ 再核对仍存在的 script/runtime 身份
→ 检查取消状态
→ 原子发布；必要时回滚
→ 释放事务锁
→ QThread.finished
→ service 清空 worker 并安排安全回收
→ 向 GUI 发布一次最终结果
```

具体约束：

- 保留完整 job 哈希检查，不吞 FileNotFoundError，不取消 seal 后全部检查。
- worker 的业务结果只暂存；service 使用真正的 `finished` 作为回收和对外通知边界。
- 槽在 service 所属 GUI 线程执行，拒收旧 worker 和重复结果。
- 允许终态回调立即开始下一次导出，旧 worker 后续事件不得污染新导出。
- 启动失败、准备失败各自清理，不等待不会发生的 finished。
- 不改变拒绝覆盖无关目录、事务锁、旧包回滚、人工恢复保护。
- 不使用 `terminate()` 杀死 Python QThread，不用 GUI 线程阻塞等待代替正确收尾。

验收：真实 QThread 退出闩锁；成功/失败/取消/启动异常；视频首次发布和替换；最后 job 篡改及 seal 后 script/runtime 变化均拒绝；旧包保留。集成测试本身必须在异常时取消、确认 worker 退出，再清理临时目录。

### B. 改善导出期间响应并收紧取消流程

主要文件：`gui/main_window.py`、`gui/dialogs/export_progress_dialog.py`。

- 确认导出目录后、冻结导出输入前，记录并暂停原先正在播放的预览。
- 保存独立的导出暂停状态，不破坏页面切换的暂停列表。
- 不 `clear()`、不关闭 VS worker、不丢弃 RenderSession；只停止连续播放。
- 原先未播放的保持暂停；媒体路径改变的不自动播放；同媒体重新冻结 session/epoch 允许恢复。
- service 仍持有 `_worker` 就视为尚未完成，即使 `isRunning()` 已为 false，也等待 queued finished 被 service 消费。
- 准备异常时安全恢复；运行或待收尾期间保持导出重入保护。
- Esc/取消只请求一次取消，不能提前退出模态窗口；完成后恢复“确定”按钮。
- 修复审查发现的同步重入：先设置取消中文案和按钮状态，再发取消信号。若槽同步完成，不允许 emit 后的旧代码覆盖最终界面。
- 非拖动分支仅在目标光标不同的时候设置光标，保留边角与拖动行为。

验收：真实导出入口接线和异常矩阵、同媒体新 epoch、换媒体、重复导出、按钮/Esc 同步完成、取消后关闭。暂停改善只以请求数和响应证据说明，不承诺编码必然更快。

真实控件待查：一次“真实 VideoPreviewWidget + 计数 worker 替身”的自动测试及其最小探针以 `-1073740791` 原生退出，没有 Python traceback。该替身只有 `request_frame()`，缺少 close/unload、signals 和完整生命周期接口；当前不能排除测试替身/控件收尾问题，不能直接归因生产 worker。稳定替身测试不算该问题闭环；审核后需隔离测试生命周期/替身接口与真实 worker，禁止仅移除不稳定测试后宣布通过。

### C. 补齐诊断日志与完整日志导出

主要文件：`main.py`、`utils/logger.py`，新增 `utils/crash_diagnostics.py`。

#### C1. 会话文件和可写目录

- 尽早建立独立会话诊断文件，文件名包含时间、PID 和唯一后缀，不覆盖前次现场。
- 候选顺序为程序 logs、LOCALAPPDATA 应用 logs、TEMP 应用 logs；实际打开文件成功才算可写。
- 普通日志优先复用选定目录；具体普通日志文件不可写时仍继续回退，并记录实际路径。
- 保持诊断 fd 长期有效，不与普通日志轮转共用生命周期。[faulthandler 文件描述符要求](https://docs.python.org/3.12/library/faulthandler.html#issue-with-file-descriptors)
- 无可写目录时明确降级但不阻断启动，避免 StringIO 无 fileno 引发第二次启动异常。
- 仅记录版本、PID/线程、frozen、路径、钩子启用结果；不主动转储环境变量、认证信息或完整项目配置。异常文本可能包含路径，分享前仍需检查。

#### C2. Qt/Python 异常入口

- 标准库诊断初始化早于 Qt；QtCore 可用后、QApplication 创建前安装 Qt 消息处理器。
- Qt fatal 首先直接写入已打开的紧急 fd，再尽力主动输出所有 Python 线程栈。
- fatal 路径不依赖普通 logging 锁、后台队列、GUI 事件循环，不弹窗，不等待业务线程。
- 回调独立容错并正常返回，不把 fatal 当普通可恢复错误吞掉。Qt 在消息处理器返回后继续终止。[Qt 消息处理器契约](https://doc.qt.io/qt-6/qtlogging.html#qInstallMessageHandler)
- 主入口、普通 threading、unraisable 异常统一关联会话并保留异常链，不长期持有 traceback/对象，不改变 SystemExit 语义。
- 不往 GUI 父进程重新引入 VapourSynth；不宣称 Python 栈就是 C++ native 栈。

#### C3. 搜索与导出

- 以“日志头 + 后续所有续行”组成记录，再筛选级别、时间和关键词。
- 关键词能匹配 traceback 中的异常类型、文件名和异常消息。
- 无过滤导出保留原始字节；过滤导出保留完整原始记录及换行，不重建为单行。
- 去掉导出端 100000 条静默截断；保留搜索界面有意义的分页/结果数限制。
- 第一轮不新增复杂打包上传功能；明确告知诊断文件与应用日志的实际位置，提交故障时一并提供。

验收：多层异常链及空行保留；目录存在但文件拒写；两次启动不覆盖；轮转后 fd 仍有效；独立隐藏子进程触发 qFatal，确认原文/栈先写出且进程仍异常退出；普通 logging 锁被占用时不阻塞 fatal。不开启系统 WER 注册表，不自动生成或上传内存 dump。

### D. 默认脚本显式设置 16000

主要文件：`resources/vapoursynth/default_pipeline.vpy`。

```python
core = vs.core
core.max_cache_size = 16000
```

位置在加载 job 与构建图之前，附一条说明单位和作用的中文注释。

VapourSynth 将此属性定义为帧缓存的 MB 阈值，不是预分配量，也不是进程 RSS 硬上限。[官方属性说明](https://www.vapoursynth.com/doc/pythonreference.html#vapoursynth.Core.max_cache_size)

作用范围：

- 内置默认脚本及以后从它复制的新脚本；不覆盖用户已有或定制 `.vpy`。
- 运行时配置先执行，脚本后执行；本方案明确让该默认脚本的 16000 覆盖先前缓存值。
- 不改 `config/vs_runtime.json`、用户 settings 或线程数。
- 多个 VS 进程的缓存不是共享总额度，仍可能叠加内存压力；不能把增大 cache 等同于修复闪退/卡顿。

验收：专属测试修正错误的 metadata 字段后，以预设 37MB 构造对照，真实 preview worker 和 VSPipe runner 执行脚本后均断言 16000；只读取属性和生成短帧，不分配 16000MB 进行测试。

## 4. 必须带入后续阶段的未完成事项

| 编号 | 问题 | 审核通过后的动作 |
|---|---|---|
| R1 | 真实完整导出测试在超时/异常路径没有确保 cancel/join | 保留确切 worker 引用，确认退出后再回收目录；注入超时验证 |
| R2 | 取消信号同步完成后被 emit 后的 UI 更新覆盖 | 将状态更新放到 emit 前；按钮和 Esc 两条回归 |
| R3 | 真实预览控件配测试替身出现 native 退出 | 用隔离进程和新增诊断定位，不以替身绿测代替闭环 |
| R4 | cache 测试误用 `output0.frame_count`，VSPipe 部分未跑 | 核对实际 metadata 字段，修测试后完成两后端验证 |
| R5 | 日志只有测试草稿，生产模块未实现 | 审核后实现；当前导入错误/红测不能视为通过 |
| R6 | 本机短素材未复现现场严重 AppHang | 保存 A/B 边界，补真实长素材和新构建采样后再决定是否扩大性能修复 |

## 5. 执行顺序与验收门槛

审核通过后仍由 Terra 写代码，GPT-6 做独立复核；每阶段给出变更差异、真实命令、结果和剩余问题，不把跳过测试当作通过。

1. 收束 A 与测试安全性 R1，维持事务保护和 no-VS-parent 边界。
2. 完成 C 的最小诊断入口与日志续行修复，先建立能解释 native 退出的证据通道。
3. 收束 B、修 R2，并用诊断隔离排查 R3。
4. 完成 D 的专属两后端验证。
5. 统一语法检查、定向回归、全量测试和完整真实导出矩阵；真实媒体验收要求工具存在且相关测试 0 skip。

计划命令（尚未按整套新版本执行）：

```powershell
uv run python -m compileall main.py config core gui utils _mext build.py tests
uv run python -m unittest tests.test_export_robustness tests.test_export_worker_lifecycle tests.test_export_package_integration tests.test_export_vpy_session tests.test_export_integrity
uv run python -m unittest tests.test_main_window_export_state tests.test_export_preview_pause tests.test_main_window_hover
uv run python -m unittest tests.test_logger tests.test_crash_diagnostics tests.test_default_pipeline_cache
uv run python -m unittest discover -s tests -p "test_*.py"
git diff --check
```

完整真实导出矩阵包括：首发/替换、loop+intro、图片循环/视频、中文路径、取消/失败不覆盖旧包、连续导出、完成后退出。性能检验记录 GUI 心跳和持续取帧请求，不用与机器绑定的苛刻毫秒阈值替代功能判断。

## 6. 构建与发布边界

源码树不是用户正在运行的绿色包。源码草稿或源码测试通过不会更新桌面的 ArknightsPassMaker。

本次方案审核默认只授权约定的源码实施与验证，不自动授权提交、推送、发布或覆盖用户目录。

如果用户另行同意本地构建：

- 先确认现有构建产物、子模块及媒体依赖，再按项目流程构建独立测试包。
- 不覆盖现有绿色包、不覆盖用户项目与用户脚本。
- 在真正 frozen GUI、无控制台条件下验证日志启动标记、导出完成、取消与退出。
- 用独立测试进程验证 fatal 留证，不主动使用户当前运行的程序崩溃。
- 通过新包验收后再单独确认是否发布；不自动修改 CHANGELOG 顶部版本，不推送触发 release。

## 7. 本轮不做

- 不清理旧网络栈、论坛 HTTP/OAuth 或设备服务。
- 不重构全部同步 prepare，不迁移 QWidget 操作到后台。
- 不擅自调低 x264 preset、修改进程优先级或修改编码画质参数。
- 不改全局运行时配置、线程数、已有用户 `.vpy`。
- 不改系统 WER 注册表，不收集/上传内存 dump。
- 不声称已找到所有故障或已消除现场全部卡顿。

审批记录：用户已确认按 A–D 范围、R1–R6 验收要求与上述边界实施，自动测试后由用户手动测试。
