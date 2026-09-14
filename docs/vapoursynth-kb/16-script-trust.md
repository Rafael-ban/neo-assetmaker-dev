# 16 — 脚本来源、bundle hash 与本机信任

## 信任边界

`.vpy` 是完整 Python 程序。它能读取/写入当前用户可访问的文件、导入模块、联网或
启动子进程；native plugin 更是在 worker/VSPipe 进程中执行 DLL 代码。

worker 提供 VS/Qt 生命周期和崩溃隔离，不是权限沙箱。脚本头、output contract、
bundle hash、job hash、runtime fingerprint 与 trust prompt 解决的是接口、身份和
用户确认，不是恶意代码隔离。

历史 `portable.vs` 只选择 R73 的便携 plugin 布局。当前 R79 由固定 runtime
布局、分发清单和 fingerprint 核对文件身份；这些机制也不代表 CPU 兼容或用户
脚本安全。部署状态见 [08](08-version-upgrade-notes.md)。

## 三种脚本来源

| source | 项目中保存什么 | 路径与信任 |
|---|---|---|
| `builtin` | source token，不保存路径 | 固定解析为应用资源中的 `default_pipeline.vpy` |
| `global` | source token，不保存本机路径 | 绝对路径只来自本机 `vs_runtime` override |
| `project` | 规范 project-relative POSIX `.vpy` 路径 | resolve 后必须仍在 project root；bundle 首次/变化后需本机确认 |

project 路径拒绝绝对路径、反斜杠、空/`.`/`..` 分段；canonicalize 后再次做根包含
检查，防止文件链接或 reparse 路径逃逸。全局脚本绝对路径不进入 `epconfig.json`，
共享项目不会泄露个人磁盘布局。

## bundle hash 精确覆盖什么

`compute_script_bundle_hash()` 递归读取主 `.vpy` 所在根内的所有 `.vpy` 与 `.py`：

- 相对 POSIX 路径按 UTF-8 排序；
- hash 同时覆盖相对路径、字节长度和文件字节；
- Windows 大小写碰撞拒绝；
- 目录链接/reparse 点以及参与代码身份的链接拒绝；
- 非代码资源文件不进入 bundle hash。

因此新增、删除、改名或修改任意 `.vpy/.py` 会改变 hash；图片、模型或其他数据文件
变化不会。若脚本行为依赖非代码资源，作者仍须自行做资源版本/摘要校验，不能把
bundle hash 误写成整个目录内容 hash。

project trust store 只记录本机
`(canonical script root, bundle hash)`，schema 为 1，位于用户应用数据目录。确认不
随项目导出，也不会扩散到另一 canonical 根；trust 记录损坏会明确失败。

## worker 的一致性保护

`RenderSession.selection` 携带 canonical script path、mode、API 与 bundle hash。
worker load message直接携带 bundle hash；worker 创建只读 job snapshot，并在用户
代码执行前后复核脚本 bundle、job SHA-256 和 runtime fingerprint。主脚本不复制到
staging，仍从 canonical 路径执行，以保持 `__file__` 和相邻 import/资源语义。

worker 退出或 graph 退休时清 VS outputs、驱逐脚本根模块并关闭 import 环境；下一次
load 不应复用上一版本的相邻 `.py`。

## VSPipe 当前保护与剩余窗口

导出先把 session job 复制到导出 staging 的只读 snapshot，并用短生命周期 worker
预检同一 session。`ExportService` 在预检前后、VSPipe 前、编码轮询期间、编码后和
最终封装边界重算 script bundle/job/runtime 身份；变化会终止导出并删除未成功输出。

当前 VSPipe argv/固定 runner 自身只携带并复核 job SHA-256 与 runtime fingerprint，
没有把 `bundle_hash` 作为 runner 参数。也就是说，脚本在 VSPipe 启动与下一次 host
轮询之间变化时，fresh VSPipe 可能短暂执行变化后的代码；host 会阻止结果发布，但
不能撤销用户脚本已经产生的副作用。这是当前一致性缺口，不应写成“VSPipe 内已完全
冻结代码”。若后续加固，应把 bundle hash 纳入 `VSPipeRenderRequest`、`--arg` 与
runner 执行前后校验，并重跑真实 runner/parity/信任测试。

## stdout 不是信任屏障

runner/worker 把 Python stdout 导向 stderr 或结构化日志，避免普通 `print()` 污染
Y4M/IPC。但 native DLL 直接写 OS fd1 不受 `sys.stdout` 替换约束；不可信 native
插件仍须审查和隔离。

## 用户检查清单

- 阅读主 `.vpy` 及根内所有 `.py`，不要只看脚本头。
- 核对 `assetmaker-requires` 对应 DLL/Python 模块来源和许可证。
- 核对脚本读取的非代码资源；这些文件不进入 bundle hash。
- 代码 bundle 变化后重新确认，不复用旧截图或旧 hash 判断。
- 不把 trust 记录、worker 进程或 `portable.vs` 当权限沙箱。

## 验证

```powershell
uv run python -m unittest -v `
  tests.test_vs_script_trust `
  tests.test_vs_project_compatibility `
  tests.test_vs_script_panel `
  tests.test_vs_runner `
  tests.test_vspipe_render_request
```

## 相关

- [05 便携插件](05-plugin-autoload-portable.md)
- [06 VSPipe](06-vspipe-cli.md)
- [13 用户 VPY ABI](13-user-vpy-abi.md)
- [14 worker 协议](14-worker-protocol.md)
