# 08 · R79 运行时、R73 历史基线与验收边界

**当前源码要求 VapourSynth R79 / API R4.2，迁移代码已合入 master 的
`cc4c586`。媒体二进制不纳入 Git，源码提交、工作目录实际部署、冻结产物和
已安装应用必须分别核对。**

## 当前运行时合同

- `assetmaker_vs.runtime_layout.resolve_runtime_layout()` 从应用根唯一解析
  `tools/media/runtime/`，要求 `vapoursynth-79.dist-info/METADATA` 为版本 79。
- binding/core/VSPipe 位于 `runtime/Lib/site-packages/vapoursynth/`，分别为
  `vapoursynth.pyd`、`libvapoursynth.dll`、`vspipe.exe`；VSScript 与嵌入式 Python
  保持完整层级，不能只复制 DLL 或 EXE。
- 内置插件在包的 `plugins/`；随包 native 插件在
  `runtime/native-plugins/01-lsmas/` 与 `02-imwri/`，由共享策略显式加载。
- R73 平铺文件及新旧混合布局会被拒绝；`portable.vs` 不再是当前部署依据。
- Python 下限仍为 3.12；worker 与 VSPipe 共享 helper、runtime 配置和文件身份，
  GUI 父进程不加载 VapourSynth。
- 默认脚本写 `_Range`，合同有界接受 `vs.Range` 与普通整数；物理旧键和映射
  风险见 [01 色彩范围](01-colour-range-props.md)。

## 已有证据及实际部署（2026-09-15 核对）

| 对象 | 已确认的状态 | 边界 |
|---|---|---|
| master 源码 | 已包含 R79 布局、Range、插件及分发改动，迁移提交为 `cc4c586` | 不是二进制部署证据 |
| 保留工作树 `.codex_tmp/preview-crop-r79` | 已部署 R79，metadata 为 79；此前 VSPipe 报 Core R79 / API R4.2 / R3.6 | 该目录的证据不自动覆盖主目录 |
| 主目录 `tools/media` | 本次核对仍有 `vapoursynth-73.dist-info`，缺少 R79 metadata | 合并只更新源码，尚须单独部署 R79 |
| 分发与构建定向测试 | 上一轮 71 项通过 | 不是全量 R79 回归或 GUI 验收 |
| 临时 cx_Freeze 媒体树 | 120 文件、343,699,939 字节按清单校验通过，包含此前缺失的 20 个 VC 运行库文件 | 临时产物已清理；未完成冻结版真实预览/导出 smoke |

迁移提交可定位为：`ae1654c`（运行时布局与双端身份）、`521c538`（Range）、
`2ca3195`（插件与帧探测夹具）、`cc4c586`（分发与冻结媒体树）。这些提交及
定向探针不能被概括为“所有 R79 产品场景均已验收”。

## 分发与后续验收

当前包名为 `media-tools-r79-v1.zip`，替代 `media-tools-v1.0.7z`；旧包可用于
历史回滚，但不是当前构建输入。完整媒体包由
`resources/packaging/media-tools-r79-v1.json` 固定路径、大小、SHA-256。
`media_distribution.py` 支持创建、验证、解压归档及验证部署树；
`build.py` 在冻结前后校验媒体树，并显式包含 VC 运行库以避免 cx_Freeze
默认排除规则漏件。

CI 需要媒体包 URL 与 ZIP SHA-256：通过 `media_tools_url/media_tools_sha256`
输入或 `MEDIA_TOOLS_R79_URL/MEDIA_TOOLS_R79_SHA256` 仓库变量提供。代码已配置
此入口，不代表远程资产及变量已部署。本次未核验远程配置。

后续验收仍需：在目标目录部署完整 R79 包、生成保留的冻结产物、运行 frozen
worker/VSPipe，并用真实素材验证预览、导出、编码回读及便携包解压后的行为。
已安装的应用版本需独立核对，不能由源码合并推定。

## 当前复核命令

以下命令在已部署 R79 的应用根执行；主目录未部署前会失败，这是预期的诊断：

```powershell
Get-Content tools\media\runtime\Lib\site-packages\vapoursynth-79.dist-info\METADATA
tools\media\runtime\Lib\site-packages\vapoursynth\vspipe.exe --version
uv run python media_distribution.py verify-tree --app-dir . --manifest resources/packaging/media-tools-r79-v1.json
uv run python -m unittest tests.test_media_distribution tests.test_build_safety tests.test_media_packaging
```

真实媒体回归须记录实际工具根、版本、素材和 skip 计数；按变更风险选择测试，
不要把跳过真实媒体的绿色结果算作验收。

## R73 历史基线（2026-09-09，非当前验收）

产品 BASE `351025b3223ca8a55270b79ca2245e5a5b54d123` 当时的 R73 / API R4.1
报告记录了 compileall 成功和 719 项完整测试（0 failure / 0 error / 0 skipped）。
这些数量仅属于该旧提交和旧运行时，不迁移为 R79 测试数量。

旧 F4 样本测量“鼠标输入到 QLabel Paint 开始分派”，播放/暂停 P95 分别为
0.294075 / 0.320925 ms；不是绘制完成、物理呈现或解码 FPS，也不是 R79 性能结论。
其余章节保留的 R73 固定版本来源和探针用于历史对照。

## 相关

- [01 色彩范围](01-colour-range-props.md)
- [05 便携插件](05-plugin-autoload-portable.md)
- [10 研究方法](10-research-method.md)
- [14 worker 协议](14-worker-protocol.md)
- [15 输出契约](15-output-contract.md)
