# 13 — 用户 `.vpy` ABI 与默认模板

## 范围

`.vpy` 是滤镜图的事实源。宿主提供冻结 RenderJob、脚本头、受控 import 环境、
worker/VSPipe 执行器和 output contract；宿主不会把每个裁剪、旋转或时间参数拼成
Python 源码，也不会在 GUI 父进程执行 VapourSynth。

历史 R73 官方构图模型是 Python 创建 `VideoNode` 并用 `set_output()` 注册输出；固定
版本参考：
`https://github.com/vapoursynth/vapoursynth/blob/R73/doc/pythonreference.rst`。

此来源保留作历史对照。当前源码要求 R79，脚本以随当前版本分发的默认模板和
contract 为准，尤其是 `_Range` 的写法；见 [01](01-colour-range-props.md)。

## 从内置模板开始

复制 `resources/vapoursynth/default_pipeline.vpy` 是最稳定的起点。它已经实现：

```text
source → 图片首帧/AssumeFPS → rotation → 图片 Loop
       → output 1 → [start:end) → crop → matrix 补标
       → Bicubic/YUV420P8 → AddBorders → 可选 180°
       → color props → output 0
```

模板不是固定 runner。预览 worker 通过共享 executor **直接执行用户脚本**；VSPipe
才先执行 `assetmaker_runner.vpy`，由 runner 校验并调用同一 executor。

## 宿主注入的四个字符串

`assetmaker_vs.executor.execute_user_script()` 只注入：

| 变量 | 值 |
|---|---|
| `assetmaker_job` | 本次执行的 UTF-8 job snapshot 绝对路径 |
| `assetmaker_api` | 脚本 API 字符串，当前为 `"1"` |
| `assetmaker_script` | canonical 主 `.vpy` 绝对路径 |
| `assetmaker_mode` | `"compatible"` 或 `"raw"` |

读取业务参数：

```python
from assetmaker_vs.job_api import load_job

job = load_job(assetmaker_job)
source = job["source"]
timeline = job["timeline"]
transform = job["transform"]
output = job["output"]
```

`assetmaker_job` 是宿主管理的临时 snapshot；worker 与导出 staging 的父目录可以
不同，也会在退休/导出收尾后删除。不要用 `Path(assetmaker_job).parent` 当资源根。
脚本相邻资源应以 `Path(assetmaker_script).parent` 或 `__file__` 定位。

mpv/PlayKit 的 `video_in`、`container_fps`、`display_fps`、`vf=...` 不属于 VS
标准 API，也不会被本项目注入。裁剪、旋转、timeline、output profile 都从 job 读。

## 必需脚本头

声明必须位于第一个 Python statement 前，且解析窗口上限为 8 KiB：

```python
# assetmaker-api: 1
# assetmaker-mode: compatible
# assetmaker-capabilities: source,trim,crop,rotation,resolution,image_loop
# assetmaker-requires: lsmas.LWLibavSource,imwri.Read
# assetmaker-editor-output: 1
```

- `assetmaker-api`：当前只接受严格版本 1。
- `assetmaker-mode`：`compatible` 或 `raw`。
- `assetmaker-capabilities`：脚本明确接受宿主哪些编辑能力；未知 token 拒绝。
- `assetmaker-requires`：逗号分隔的 `namespace.Function`；可为空，但声明项必须实际
  callable，并符合 native source policy。
- `assetmaker-editor-output`：0 或 1。

`compatible` 若声明 trim/crop/rotation 中任一编辑能力，必须声明 editor output 1；
output 1 应为旋转后、trim/crop 前的完整编辑时间轴。`raw` 必须声明 editor output 0，
编辑器只消费 output 0，不假装支持交互 crop/trim。

## import 搜索与模块退休

executor 按以下顺序保序去重：

```text
主 .vpy 所在目录
主 .vpy 所在目录/modules
runtime.plugins.python_module_dirs
portable assetmaker_vs helper 根
其余原 sys.path
```

`plugins.python_module_dirs` 只放 Python 模块根；native DLL 根写入
`plugins.native_plugin_dirs`。执行前清空 VS output registry、驱逐上一个图从脚本根
加载的模块并 invalidate import cache。搜索环境保持到图及延迟回调彻底退休，再恢复
原 `sys.path`；这样相邻模块不会在第二次加载时悄悄复用旧代码，也不会驱逐
`assetmaker_vs` helper 或无关第三方模块。

一个脚本根同一时刻只有一个 graph lease。旧图未关闭时第二次执行会被拒绝；失败
路径也必须清 outputs、退休模块和释放 lease。

## core 资源与插件

worker/runner 在每次用户脚本前先恢复 core 基线或冻结的非零配置。配置值 0 表示
原生基线；用户脚本仍可显式覆盖，例如内置模板设置 `max_cache_size=16000` MB。该值
不是 RSS 硬上限。

native 目录按顺序逐个 `LoadAllPlugins`，Python 目录进入上述 import 搜索。脚本头
requirement 在代码执行前按 namespace/callable/source 校验；缺失时应明确失败，不能
静默换另一个滤镜。

## 最小 compatible 骨架

```python
# assetmaker-api: 1
# assetmaker-mode: compatible
# assetmaker-capabilities: source
# assetmaker-requires:
# assetmaker-editor-output: 0

import vapoursynth as vs
from assetmaker_vs.job_api import load_job

job = load_job(assetmaker_job)
clip = vs.core.std.BlankClip(width=384, height=640, format=vs.YUV420P8)
# 这里只是结构示例；真实 output 0 仍须匹配 job 的帧数、fps 与色彩 props。
clip.set_output(0)
```

概念骨架不是可直接通过所有 output contract 的完整脚本；生产修改应从默认模板复制，
并通过真实 worker 与 VSPipe 验证。

## 验证

```powershell
uv run python -m unittest -v `
  tests.test_vs_script_header `
  tests.test_vs_job_contract `
  tests.test_default_vpy_pipeline `
  tests.test_vs_runner
```

## 相关

- [05 便携插件](05-plugin-autoload-portable.md)
- [14 worker 协议](14-worker-protocol.md)
- [15 输出契约](15-output-contract.md)
- [16 脚本信任](16-script-trust.md)
