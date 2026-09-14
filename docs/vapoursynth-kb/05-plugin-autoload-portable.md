# 05 · 便携运行时与 native/Python 插件目录

**结论：当前 R79 使用 `tools/media/runtime/` 完整布局；随包及用户配置的多个
native 目录在 worker/runner 导入 binding 后按顺序逐目录调用
`LoadAllPlugins`。Python module 目录只参与用户脚本 import，绝不能与 native DLL
目录混用。**

## 当前 R79 分发布局

`assetmaker_vs.runtime_layout` 统一解析包、core filters、VSScript、嵌入式 Python
与插件路径。包内 `plugins/` 和随包 native 目录职责不同；后者与用户目录由
`assetmaker_vs.native_plugins` 统一加载和诊断。

历史 R73 平铺布局依赖 `portable.vs` 与 `vs-plugins/`。当前代码拒绝这些旧
文件与 R79 混用；marker 存在不再是当前运行时可用的判断依据。

历史 R73 固定 tag 的 `std.LoadAllPlugins` 文档：
`https://github.com/vapoursynth/vapoursynth/blob/R73/doc/functions/general/loadallplugins.rst`。
它会尝试加载目录中的插件，但失败项可被静默跳过，因此“调用没有抛异常”不能证明
每个 DLL 已注册。

当前 CI 与冻结产物必须包含：

```text
tools/media/runtime/Lib/site-packages/vapoursynth/vapoursynth.pyd
tools/media/runtime/Lib/site-packages/vapoursynth/libvapoursynth.dll
tools/media/runtime/Lib/site-packages/vapoursynth/vspipe.exe
tools/media/runtime/Lib/site-packages/vapoursynth/plugins/avscompat.dll
tools/media/runtime/native-plugins/01-lsmas/LSMASHSource.dll
tools/media/runtime/native-plugins/02-imwri/libimwri.dll
```

以上仅列关键文件。`resources/packaging/media-tools-r79-v1.json` 固定完整
120 个文件的路径、大小与 SHA-256；构建在冻结前后都校验媒体树。

## binding 只在 worker/VSPipe 进程加载

`core.vs_runtime.vs_loader` 的 worker 路径：

1. 要求 Python 3.12+，解析并完整预检 R79 runtime 布局；
2. 清空 `VAPOURSYNTH_EXTRA_PLUGIN_PATH`，避免未冻结的隐式 native autoload；
3. 为 VS 包、runtime 和 native 目录注册 DLL 搜索路径，从包 `__init__.py` 显式加载 binding；
4. 按随包 `01-lsmas`、`02-imwri`、用户配置目录的顺序处理 native 插件，记录 plugin source；
5. 捕获首次 core 资源基线，供每次脚本执行前恢复。

GUI 父进程不导入 VapourSynth，也不存在父进程 prewarm。不要把 `tools/media` 插入
`sys.path`，也不要通过随意在 venv 安装 wheel 绕开固定布局、metadata 和文件身份
校验。运行时部署说明见 [08](08-version-upgrade-notes.md)。

## 多目录 native 策略

`assetmaker_vs.native_plugins` 对 `plugins.native_plugin_dirs`：

- 每个目录先解析为存在的绝对目录，按 Windows 路径语义保序去重；
- 按配置顺序逐目录 `core.std.LoadAllPlugins(path=...)`；
- 临时安装 core log handler，识别候选 DLL 加载失败、API 不兼容、plugin identity
  或 namespace 冲突，并给出候选/既有来源；
- 用 `core.plugins()`、callable 和 `plugin_path` 核对脚本头的每项
  `assetmaker-requires` 是否真实可用且来自内置或已配置根。

空目录或只含依赖 DLL 的目录可以合法存在；最终 requirement 校验才决定脚本能否
运行。当前内置 source root 是 VS 包的 `plugins/`；旧包的 `vs-coreplugins/`
不属于当前 R79 支持布局。runtime fingerprint 覆盖规范运行时文件及配置身份。

## Python module 目录是另一条链

`plugins.python_module_dirs` 通过专用 JSON 环境传输给 executor，只在受控的脚本
import 搜索上下文中使用。它不会传给 `LoadAllPlugins`。native 与 Python 目录都会
进入 runtime fingerprint，但加载方式、错误类别和信任边界不同。

worker 与固定 VSPipe runner 共享上述 native policy；VSPipe 环境同样要求
`VAPOURSYNTH_EXTRA_PLUGIN_PATH=""`，额外 native 目录由 runner 显式加载。

## 相关

- [08 版本升级](08-version-upgrade-notes.md) — R79 分发与验收边界
- [09 插件生态](09-plugin-ecosystem.md) — 生产 requirement 与 namespace
- [13 用户 VPY ABI](13-user-vpy-abi.md) — 脚本头和两类插件目录
- [16 脚本信任](16-script-trust.md) — 文件身份不等于安全沙箱
