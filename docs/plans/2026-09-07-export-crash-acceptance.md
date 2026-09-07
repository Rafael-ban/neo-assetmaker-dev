# 导出闪退修复验收记录

状态：2026-09-08 源码实施、自动验收及一次用户导出手测完成；用户已授权检查日志后提交，未构建或发布。对应已审核方案：`2026-09-07-export-crash-diagnostics-plan.md`。

## 本轮实施范围

- 导出事务：在删除私有 job 前完整核对冻结身份；seal 后只核对仍有效的 script/runtime 身份。
- 线程生命周期：等待真正的 QThread.finished 后才回收和向 GUI 通知终态；拒收旧 worker 的进度与终态，允许终态回调立即重入。
- GUI：导出期间暂停原先播放的预览；按媒体身份安全恢复；取消/Esc 等待真正收尾；同步完成时不覆盖终态按钮；去掉同一区域的重复光标设置。
- 日志：早期、独立、非覆盖会话诊断；Qt fatal 直接落盘并尽力输出 Python 全线程栈；实际打开失败时回退；搜索与导出保留完整异常链。
- 默认脚本：`core.max_cache_size = 16000`，仅影响内置脚本和此后新复制的脚本，不改用户已有脚本或全局配置。

实现由 Terra 完成，GPT-6 独立复核；主代理负责真实媒体矩阵和统一验收。本轮不包含网络栈修改。

## 独立真实媒体矩阵

本地补充探针命令（探针及运行日志不纳入本次提交；持久回归见 `tests/test_export_package_integration.py` 等测试）：

```powershell
uv run python -X utf8 .planning/2026-09-07-export-crash/probe_real_acceptance.py
```

自建临时素材：20 秒、600 帧的有色彩标签视频，以及 12 帧图片 intro；输入与输出含中文路径。使用真实 VS worker、VSPipe、x264、muxer、ExportService/QThread 和 VideoPreviewWidget；未向 GUI 父进程导入 VapourSynth。

| 场景 | 第二次独立运行 | 结果 |
|---|---:|---|
| 视频 loop＋图片 intro 首发 | 8.862 秒 | 单次成功通知；两段产物可解码，帧数分别 600/12 |
| 20 秒视频替换，预览保持播放 | 5.997 秒 | 导出成功；182 帧预览；心跳最大间隔 8.294ms |
| 20 秒视频替换，预览暂停 | 5.701 秒 | 导出成功；0 帧预览；心跳最大间隔 7.580ms |
| 真实取消 | 1.680 秒 | 单次失败通知；旧包逐文件哈希不变 |
| 真实解码预检失败 | 2.366 秒 | 单次失败通知；旧包逐文件哈希不变 |

每次终态 service._worker 均已清空，无 staging/backup/lock 遗留。最后同步关闭真实预览 worker、控件并正常退出。第一次完整矩阵也通过。

心跳使用 Windows 高分辨率 `perf_counter()`，不是低分辨率的 monotonic 采样；计时为本机单次样本，不是编码加速保证。短素材与这次 20 秒素材均未复现现场严重 AppHang，新构建的实际项目仍需用户手动验证。

## R3 原生测试退出的独立对照

原失败测试夹具强制设置 worker_ready_for_frames，虽已有 raw selection，却缺失 _session_metadata；父级最小负对照省略了两者，均触发同一 guard。首次计时器取帧从 `_frame_request_target` 抛出 `RuntimeError: 预览 session 尚未加载`。

- 用诊断性 sys.excepthook 捕获，得到明确 Python 调用栈，进程正常退出。
- 保持默认 sys.excepthook，不完整夹具以 `0xc0000409` 退出，stderr 为空。
- 仅补齐元数据并正确拆除替身，同一真实控件在 150ms 内取帧 4 次，exit0。
- 真实 worker 对照：模态播放 1.5 秒得到 48 帧；暂停并排空在途响应后，无新增帧，正常关闭。
- 持久回归 `tests/test_export_preview_native.py` 使用完整 FakeWorkerClient、真实 context/load/metadata 流程，在独立进程中保留默认 hook，覆盖连续取帧、暂停后零新增请求、恢复与正常关闭。

这与 PyQt 默认异常处理的 fatal 行为一致，不是现场 native 调用栈的替代证据；不能将“同一退出码”解释为已证明现场只有这一条原因。[PyQt 维护者说明](https://riverbankcomputing.com/pipermail/pyqt/2014-October/034892.html)

验收夹具的另外两处问题已修正：OpenCV 中文文件写入使用 imencode/tofile；OpenCV mp4v 缺 VUI 的源先经真实工具链规范化。没有因此扩大生产解码代码修改范围。

## 原事故日志验收

仅只读用户提供的 `app_20260907.log`，导出至自有临时目录：4298 字节完全一致，原有 2 段 traceback 保留 2 段，FileNotFoundError 搜索命中 1 条完整记录。原日志未修改。旧实现此前导出时丢失全部 traceback。

## 测试与审查状态

- 核心五模块父级定向：53 项通过，0 skip；早于最后一项旧 worker 进度过滤补充。
- GUI/日志/缓存六模块父级定向：61 项通过，0 skip。
- 全范围 compileall 与 git diff --check：通过。
- 第一次全量 unittest：636 项通过，300.004 秒，0 skip；该次早于最后两项日志兜底修复，最终版本必须重跑。
- 核心 GPT-6 复核：无未关闭 P1/P2；R1 与清理测试无工具环境兼容性均已关闭。空工具链模拟清理测试通过，真实媒体测试正常 skip；本机真实媒体测试全部执行。
- GUI GPT-6 复核：无未关闭 P1/P2，独立 33 项 GUI＋1 项 native 测试通过、无 skip。
- 日志 GPT-6 复核：无未关闭 P1/P2，两项兜底问题均关闭。无 fd 时不替换 Qt handler，缺失标准流各自尝试所有候选目录；真实子进程验证原 fatal 文本保留和单文件拒写回退。日志定向 27 项通过、0 skip；GPT-6 独立重跑对应 3 项通过、0 skip、2.213 秒，核对修改文件哈希不变。
- 最终全量：**638 项通过，292.879 秒，0 失败、0 skip，进程 exit0**。在全部实施停止后运行；16 份代码/测试文件 SHA-256 测试前后一致。最终 compileall 与 git diff --check 通过。

最终验收命令：

```powershell
uv run python -X utf8 -m compileall -q main.py config core gui utils _mext build.py tests
uv run python -X utf8 -m unittest discover -v -s tests -p "test_*.py"
git diff --check
```

全量明细保存在 `.planning/2026-09-07-export-crash/full-suite-final.log`。其中预期故障日志、模拟 PyArmor 输出和临时空测试 ZIP 属于测试夹具，不是实际应用构建或发布。

不同批次存在重叠，不能相加作为全套数量。真实媒体分支跳过数必须单独检查。

R1–R5 的代码或测试缺口均已关闭；R6 已补两轮真实 20 秒视频采样，但本机仍未复现原项目的严重 AppHang。该现场问题的最终体感和 frozen 包验证保留在用户手动测试范围，不声称已彻底消除所有卡顿。

## 用户手动测试清单

### 2026-09-08 已完成的日志复核

本次只读检查项目 `logs/app_20260908.log` 及同会话 diagnostic 日志：

- 00:07:02：明确记录导出成功。
- 00:08:53：后续自动保存正常，应用未在导出结束时退出。
- 00:09:16：应用主动收尾并记录退出码 0。
- 没有 Python traceback、导出失败、Qt critical/fatal 或 worker 崩溃记录。
- 诊断入口记录 file/faulthandler/python hooks/Qt handler 启用，应用版本随后补记为 2.2.0。
- 留有 7 次 `QFont::setPointSize(-1)` 警告。它们在本次会话中没有阻断导出或正常退出，但不等于已定位或修复字体问题，后续可独立排查；本轮不改字体逻辑。

提交前再次核对 16 份源码/测试文件与 638 项全量通过时的 SHA-256 一致；新跑 12 个定向测试模块共 117 项通过、0 skip、28.831 秒，compileall 通过。日志、`.recovery/`、临时计划/探针、用户素材及设置均排除在提交之外。

### 后续建议

当前桌面绿色包不会因源码修改自动更新。可以先在本源码目录运行 `uv run python main.py`；如需绿色包手测，另行构建独立测试包，不覆盖旧包或项目。

1. 使用原先触发问题的项目，导出 loop＋intro；导出结束确认成功、打开产物、连续再导出一次，然后关闭程序。
2. 导出前播放预览；导出期间观察鼠标移动与窗口响应，确认预览暂停。分别测试成功、取消按钮及 Esc 后的恢复行为；原先暂停的预览不应自动播放。
3. 对已有导出包执行取消或人为可控的失败，确认旧包仍可用；不要用唯一副本进行破坏性验证。
4. 查看实际日志目录中的 `app_YYYYMMDD.log` 与 `diagnostic-会话标识.log`。无控制台环境另有 `stdout-会话标识.log`、`stderr-会话标识.log`；重启后旧会话文件应保留。异常反馈时一并提供，分享前检查路径等私人信息。

日志目录按实际可打开结果回退：程序 logs → LOCALAPPDATA/ArknightsPassMaker/logs → TEMP/ArknightsPassMaker_logs；普通日志会记录独立诊断的实际位置。

## 交付边界

本轮没有构建真实 frozen GUI，没有推送、发布、变更版本或覆盖桌面应用；本地提交按用户后续授权执行。模拟 frozen 分支不等于真实包验收。Python 线程栈不等于 C++ native 栈；所有路径不可写时只能安全降级；单条 Qt 消息超过 262144 字符时显式标记截断。

16000 是每个 VS core 的帧缓存 MB 阈值，不是预分配量或进程内存硬上限，多个进程会叠加。本轮不将提高缓存作为解决闪退或卡顿的证据。
