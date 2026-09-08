# VapourSynth 用户脚本架构

## 结论

项目不再由 Python 代码拼接滤镜链。用户、项目或内置的 `.vpy` 是唯一的
滤镜图来源；预览由 `vs_worker.exe` 执行该脚本，导出由 `VSPipe` 执行同一
脚本和同一份冻结 job。因此滤镜、插件调用与处理顺序只写一次。

这解决了旧的“双份图”问题：预览与导出曾各自实现裁剪、旋转、颜色和补边，
即使两边语法都正确，也无法证明像素结果一致。现在两条路径共用
`assetmaker_vs.executor`、脚本头、`job_api` 和输出 `contract`：worker 直接执行
所选 `.vpy`，只有导出路径由 VSPipe 执行固定的
`resources/vapoursynth/assetmaker_runner.vpy`。runner 不是 worker 的入口。
`tests/test_preview_export_parity.py` 与真实编解码测试验证共享合同下的结果。

## 运行边界

```text
编辑器 UI
  └─ RenderJob（UTF-8 JSON，按 epoch 冻结）
       ├─ vs_worker.exe → 预览帧 → named mmap → Qt
       └─ VSPipe → Y4M → x264-7mod → MP4Box / lsmash
                         ↑
              同一份用户或内置 .vpy
```

- 主界面进程不直接载入 VapourSynth；它只管理 worker、job、帧槽和 Qt UI。
  这样避免便携 VS 与 Qt 的 DLL 初始化顺序风险。
- `vs_worker.exe` 是预览唯一的 VS 进程；帧经命名 mmap 以连续 BGR24 传输，
  控制消息使用有长度前缀的 JSON。
- `VSPipe` 只在导出时运行；x264 没有 Python 绑定，因此 Y4M 管道仍是必要的
  编码接口，而不是第二套滤镜实现。

## 配置与脚本的职责

`config/vs_runtime.json` 只描述运行环境：worker 超时、VS core 的资源请求初值、
插件目录和本机全局脚本位置。它不再包含重采样核、像素格式、颜色矩阵或滤镜
顺序。每个 worker/VSPipe context 在启动边界冻结该配置和 runtime fingerprint；
执行每个用户脚本前恢复 core 资源。`num_threads` / `max_cache_size_mb` 的 `0`
表示首次捕获的 VapourSynth 原生值，非零值是脚本执行前的请求初值；脚本仍可
显式覆盖（内置脚本固定设置 `core.max_cache_size = 16000`）。cache 不是整个进程
RSS 的硬上限。

这些图像语义属于 `.vpy`：脚本作者可显式选择滤镜和参数；编码输出仍必须满足
固定 output 0 契约。这样新增滤镜或插件不会要求在宿主 Python 代码中增加
字符串分支，也不会让预览和导出分叉。

## 三种脚本来源与信任

| 来源 | 保存位置 | 适用场景 |
|---|---|---|
| 内置 | 随应用分发的 `default_pipeline.vpy` | 默认兼容工作流与可复制模板 |
| 全局 | 当前 Windows 用户的本机运行时覆盖 | 同一台电脑上多个项目复用 |
| 项目 | 项目目录内的相对 `.vpy` | 与项目一同版本管理和共享 |

项目只保存来源与相对路径；全局绝对路径不写入项目。项目脚本首次使用、或脚本
根目录任一文件变化后，需要由本机用户重新信任递归 bundle hash。信任不是沙箱：
`.vpy` 是 Python，能够执行其权限范围内的任意代码。

## 用户入口与验证

- 脚本语法、兼容模式、raw 模式和输入变量：
  [知识库 13](vapoursynth-kb/13-user-vpy-abi.md)
- worker 通信、epoch 和帧传输：[知识库 14](vapoursynth-kb/14-worker-protocol.md)
- output 0/1、颜色和编码契约：[知识库 15](vapoursynth-kb/15-output-contract.md)
- 脚本信任模型：[知识库 16](vapoursynth-kb/16-script-trust.md)

```powershell
uv run python -m unittest tests.test_default_vpy_pipeline tests.test_preview_export_parity tests.test_vs_architecture_boundaries -v
```

上述测试包括真实 VSPipe、编码和 worker 路径；机器缺少 `tools/media` 时，需先按
README 的媒体工具说明补齐便携运行时，不能把跳过的媒体测试当作验证通过。
