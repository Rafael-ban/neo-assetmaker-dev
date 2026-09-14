# 06 · VSPipe 命令行与导出 ABI

**结论：当前项目明确使用 `--arg`，而且每个 `key=value` 都是独立 argv 元素；
导出链是 VSPipe 的 Y4M stdout → x264-7mod stdin → 外部 MP4 muxer。它与 worker
是两条执行路径，但共享冻结 runtime、用户脚本、job API 和输出合同。**

## R73 历史 CLI 来源

本节为旧版本来源；当前项目要求 R79，实际命令构造和运行路径见后两节。

固定 tag：
`https://github.com/vapoursynth/vapoursynth/blob/R73/doc/output.rst`。

R73 支持 `--arg key=value` 向脚本注入字符串变量，`-` 作为输出位置表示 stdout，
`-c y4m` 输出 Y4M，`-p` 把逐帧进度写到 stderr。`--` 是求值帧但不输出的 CLI
能力；它目前不是产品导出前置步骤，不能在本文把可能用途写成已实现功能。

## 当前命令是参数数组，不经 shell 拼接

`core.media_pipeline.build_vspipe_command()` 返回：

```text
VSPipe.exe -c y4m -p
  --arg assetmaker_job=<冻结 job 路径>
  --arg expected_job_sha256=<job SHA-256>
  --arg assetmaker_script=<用户脚本路径>
  --arg assetmaker_api=1
  --arg assetmaker_mode=<compatible|raw>
  assetmaker_runner.vpy -
```

上面为便于阅读换行；实际每个 token 都是 list 中的独立元素，并以
`subprocess.Popen(list, shell=False)` 启动。中文、空格、`&` 和单引号都不会再被
shell 拆分；`tests.test_vspipe_render_request` 对这些路径字符有定向断言。

五个 `--arg` 中，`expected_job_sha256` 是固定 runner 的私有完整性参数。runner
验证 job 后，通过共享 executor 给用户脚本注入的正式 ABI 只有四个字符串：
`assetmaker_job`、`assetmaker_api`、`assetmaker_script`、`assetmaker_mode`。业务参数
从 `load_job(assetmaker_job)` 读取，不散落为额外全局变量。

## stdout/stderr 所有权

1. 固定 runner 在导入 helper 前执行 `sys.stdout = sys.stderr`，让 Python
   `print()` 与 Python 日志避开 Y4M stdout。
2. VSPipe C 层把 Y4M 写入 stdout；父进程将它直接接到 x264-7mod stdin。
3. VSPipe 与 x264 的 stderr 均由后台线程持续排空；VSPipe `-p` 进度从 stderr
   解析，避免管道填满而死锁。
4. x264 把 H.264 写到临时文件，随后由 MP4Box 或 lsmash-muxer 封装为 MP4。

`sys.stdout` 重定向只覆盖 Python 文件对象。任意 native 插件若直接写进 OS fd 1，
仍可能污染 Y4M；项目不承诺不受信任 native 插件的 fd1 输出一定安全。

## 两端一致性与边界

- VSPipe 环境只接受 `app_dir/tools/media/runtime/Lib/site-packages/vapoursynth/vspipe.exe`，并携带冻结 runtime JSON、
  fingerprint、Python module 目录和空的隐式 native autoload 环境。
- runner 在读取 job 前后、执行用户脚本前后复核 job SHA-256，并在输出注册前运行
  相同的 header/requirement/output contract。
- worker/VSPipe plane parity 的测试入口是 `tests.test_worker_vspipe_parity`；报告须
  标记实际运行时和 skip 数，旧 R73 结果不能直接作为 R79 验收。两进程各建 VS core。

## 相关

- [05 便携插件](05-plugin-autoload-portable.md) — VSPipe runtime 环境
- [13 用户 VPY ABI](13-user-vpy-abi.md) — 四个宿主变量与 job API
- [15 输出契约](15-output-contract.md) — VSPipe 只消费通过校验的 output 0
- [16 脚本信任](16-script-trust.md) — stdout 与本机代码执行边界
