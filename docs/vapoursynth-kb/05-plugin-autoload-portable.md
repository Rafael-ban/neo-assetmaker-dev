# 05 · 便携运行时与 native/Python 插件目录

**结论：R73 自带插件仍由 `portable.vs` 锚定的便携布局提供；用户配置的多个
native 目录则在 worker/runner 导入 binding 后按顺序逐目录调用
`LoadAllPlugins`。Python module 目录只参与用户脚本 import，绝不能与 native DLL
目录混用。**

## R73 `portable.vs` 的版本化事实

当前 `tools/media` 是 R73 便携包：`vapoursynth.dll` 在自身目录识别零字节
`portable.vs` 标记，并从相邻 `vs-plugins/` 自动加载 `lsmas`、`imwri` 等插件。
这是当前二进制与实际探针证实的 R73 行为，不应泛化成所有 VapourSynth 版本的
永久合同；R79 必须重新验证分发目录、CPU 变体 manifest 与 autoload 行为。

R73 固定 tag 的 `std.LoadAllPlugins` 文档：
`https://github.com/vapoursynth/vapoursynth/blob/R73/doc/functions/general/loadallplugins.rst`。
它会尝试加载目录中的插件，但失败项可被静默跳过，因此“调用没有抛异常”不能证明
每个 DLL 已注册。

当前 CI 与冻结产物必须包含：

```text
tools/media/vapoursynth.pyd
tools/media/vapoursynth.dll
tools/media/portable.vs
tools/media/vs-plugins/LSMASHSource.dll
tools/media/vs-plugins/libimwri.dll
```

缺少 marker 或插件不能靠启动成功来放行；`.github/workflows/build-app.yml` 对源工具
和冻结树分别做硬校验。

## binding 只在 worker/VSPipe 进程加载

`core.vs_runtime.vs_loader` 的 worker 路径：

1. 要求 Python 3.12+ 与 `tools/media/vapoursynth.pyd`；
2. 清空 `VAPOURSYNTH_EXTRA_PLUGIN_PATH`，避免未冻结的隐式 native autoload；
3. 用 `os.add_dll_directory(media_dir)` 与 `spec_from_file_location()` 显式加载 binding；
4. 记录 R73 便携目录已加载的 plugin source，并处理配置的额外 native 目录；
5. 捕获首次 core 资源基线，供每次脚本执行前恢复。

GUI 父进程不导入 VapourSynth，也不存在父进程 prewarm。不要把 `tools/media` 插入
`sys.path`：该目录同时携带嵌入式 Python 扩展，可能遮蔽宿主模块。也不要把 R73
wheel 随意装入 venv 后仍期待相邻的 `portable.vs`/`vs-plugins` 自动生效。

## 多目录 native 策略

`assetmaker_vs.native_plugins` 对 `plugins.native_plugin_dirs`：

- 每个目录先解析为存在的绝对目录，按 Windows 路径语义保序去重；
- 按配置顺序逐目录 `core.std.LoadAllPlugins(path=...)`；
- 临时安装 R73 log handler，识别候选 DLL 加载失败、API 不兼容、plugin identity
  或 namespace 冲突，并给出候选/既有来源；
- 用 `core.plugins()`、callable 和 `plugin_path` 核对脚本头的每项
  `assetmaker-requires` 是否真实可用且来自内置或已配置根。

空目录或只含依赖 DLL 的目录可以合法存在；最终 requirement 校验才决定脚本能否
运行。内置 `vs-coreplugins/` 在旧包里可以不存在；若存在，会成为许可 source root
并纳入 runtime fingerprint。

## Python module 目录是另一条链

`plugins.python_module_dirs` 通过专用 JSON 环境传输给 executor，只在受控的脚本
import 搜索上下文中使用。它不会传给 `LoadAllPlugins`。native 与 Python 目录都会
进入 runtime fingerprint，但加载方式、错误类别和信任边界不同。

worker 与固定 VSPipe runner 共享上述 native policy；VSPipe 环境同样要求
`VAPOURSYNTH_EXTRA_PLUGIN_PATH=""`，额外 native 目录由 runner 显式加载。

## 相关

- [08 版本升级](08-version-upgrade-notes.md) — R79 分发/插件候选门禁
- [09 插件生态](09-plugin-ecosystem.md) — 生产 requirement 与 namespace
- [13 用户 VPY ABI](13-user-vpy-abi.md) — 脚本头和两类插件目录
- [16 脚本信任](16-script-trust.md) — `portable.vs` 不是完整信任证明
