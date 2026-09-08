# 14 — 预览 worker、事务、epoch 与恢复

## 架构边界

GUI 父进程不导入 VapourSynth。源码开发时启动 `python -B vs_worker.py`，冻结包启动
`vs_worker.exe`；该子进程独占 binding、core、native plugin、用户脚本和 VS frame。

worker **不执行固定 runner 文件**。load 时它直接调用共享
`assetmaker_vs.executor.execute_user_script()`，再用共享 contract 校验 output。
`assetmaker_runner.vpy` 只属于 fresh VSPipe 导出路径。

## load 与冻结身份

一次 `RenderSession` 的 load message 携带：

```text
request_id, api_version, track, epoch, mode,
canonical script_path, job_path, job_sha256,
script bundle hash, runtime fingerprint
```

worker generation 有自己的受控 staging 根。每次 load 把 job 字节复制为只读
`snapshot-*/job.json`，脚本仍从 canonical 原路径执行，以保持 `__file__` 和相邻资源
一致。worker 在读取 job、解析 header、执行代码前后复核 job、bundle 与 runtime
身份；退休时删除该 snapshot。

控制通道是带长度前缀的 JSON，帧像素走 Windows named mmap：

```text
Qt → load(epoch, job/script/runtime identity)
Qt → request_frame(epoch, index, editor|final, viewport/zoom/pan, slot)
worker → frame_ready|frame_discarded|request_error
Qt → cancel_epoch(epoch) → worker ACK
Qt → unload → worker ACK
```

## F1/F2 crop 事务边界

鼠标 crop 手势分为 draft 与正式状态：

1. `begin_crop_edit()` 冻结正式 cropbox、建立 draft，并停止现有 100 ms job debounce。
2. move 只更新 draft、信息文本和 `QLabel.update()`；不写正式 crop、不建 job、不
   请求新 frame。当前已显示的画面仍可重绘草稿框。
3. 有效 commit 才把 draft 提升为正式 crop，发出一次正式 crop 信号/undo 单元，并
   由 debounce 创建一个新 epoch；重复 release/focus-out/ungrab 不重复提交。
4. cancel 或纯 crop no-op（未移动、钳制后等于起点、移出又返回）不发正式 crop
   信号、不新增 crop undo、也不因 crop 自身 load job。

第 4 条不是“无条件清空 pending”。若手势前或手势中已有 trim/rotation 等非 crop
修改令 `_job_dirty=True`，cancel/no-op 收口会重新调度该 pending，并最终 load 新 job；
`tests.test_crop_interaction_transaction::test_i7_noop_gestures_do_not_commit_crop_but_preserve_non_crop_pending`
锁定了这一边界。

保存/导出/模式切换通过 `ensure_render_state_committed()` 收口有效 draft。自动备份可
读取 `get_draft_cropbox()` 生成副本，但不把 live 正式 crop 或 undo 栈提前改掉。

## frame、ACK 与三重代际

- `epoch`：图/job 代际；新正式编辑创建新 epoch，旧 epoch 的 frame terminal 不得
  覆盖新画面。
- worker generation：子进程代际；崩溃/restart 后旧协议线程和 staging 不得冒充
  新进程。
- slot generation：named mmap 槽代际；terminal 必须与 host 保留的 slot name、
  capacity 和 generation 精确一致。

Future callback 在 worker 内持有条件锁完成“检查 epoch→写 mmap→发送 terminal”。
`cancel_epoch` 在同一把锁下记录取消并发送 ACK，所以 ACK 是线性化点：其后尚未终态
的该 epoch 请求不再产生 `frame_ready`。每个 frame request 最终只接受一个 terminal，
host 随后回收 slot。

## RuntimeSnapshot 与 restart

RuntimeSnapshot 包含规范化配置、app/media 根、Python 目录环境和完整 fingerprint。

- 初次创建 client 时冻结 snapshot；已启动 client 不能原地更换环境。
- 普通 worker crash/restart 是同一 client 的新 generation，继续使用原 snapshot。
- 显式“应用运行配置”或新 context 可以重新读取配置；GUI 必须先取消/退休旧 client、
  切断回调并清 staging，再创建携带新 snapshot 的 client。
- 每次用户脚本前恢复首次 core 基线或 snapshot 中的非零资源限制，防止前一个脚本
  修改 `num_threads`/cache 后污染下一个图。

因此“改 JSON 后重启当前 worker”不等于配置已经切换；配置刷新、transport restart
和 graph reload 是三个不同操作。

## 故障与恢复

| 响应/事件 | 含义 |
|---|---|
| `requirement_error` | 脚本头依赖不存在、不可调用或来源不符 |
| `script_error` | 用户代码编译/执行失败 |
| `contract_error` | output 0/1 不满足合同 |
| `request_error` | 请求 shape、身份、frame 或 runtime 错误 |
| `frame_discarded` | coalesce 或 epoch 取消后的正常淘汰 |
| `worker_crashed` | 进程退出；客户端终结 pending，按有界策略恢复或停止 |

graph 退休按顺序排空 Future、清 VS outputs、驱逐脚本根模块、恢复 import 环境并关闭
snapshot。退休不干净属于致命代际错误，不能在同一污染进程里继续加载下一脚本。

## 验证

```powershell
uv run python -m unittest -v `
  tests.test_crop_interaction_transaction `
  tests.test_runtime_snapshot `
  tests.test_preview_runtime_snapshot `
  tests.test_vs_worker_process `
  tests.test_preview_worker_integration `
  tests.test_vs_frame_probe
```

真实媒体类若没有实际工具会 skip；报告必须区分纯协议、替身 worker 与真实 R73。

## 相关

- [07 帧生命周期](07-frame-lifetime-threading.md)
- [13 用户 VPY ABI](13-user-vpy-abi.md)
- [15 输出契约](15-output-contract.md)
- [16 脚本信任](16-script-trust.md)
