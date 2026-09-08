# 09 · 插件生态：生产调用、namespace 与替代评估

**结论：当前内置脚本的外部 native requirement 只有
`lsmas.LWLibavSource` 与 `imwri.Read`；`std` 和 `resize` 是 VapourSynth 自带
namespace。可安装、可枚举、存在同名 namespace 和脚本实际声明可调用，是四个
不同问题。**

## 当前生产调用表

事实源是 `resources/vapoursynth/default_pipeline.vpy` 的脚本头与函数调用：

| namespace | 当前调用 | 类型 | 用途 |
|---|---|---|---|
| `lsmas` | `LWLibavSource` | 外部 native plugin | 视频解码与 `.lwi` 索引 |
| `imwri` | `Read` | 外部 native plugin | 静态图片读取 |
| `std` | `AssumeFPS`、`Transpose`、`FlipHorizontal`、`FlipVertical`、`Turn180`、`Loop`、`CropAbs`、`SetFrameProps`、`AddBorders` | core/标准 plugin | 时间、几何、属性 |
| `resize` | `Bicubic` | core resize plugin | RGB/YUV、内容尺寸与 range 转换 |

脚本头明确写：

```text
# assetmaker-requires: lsmas.LWLibavSource,imwri.Read
```

即便一次素材只走视频或图片分支，默认 compatible 脚本仍声明完整 requirement；
worker/runner 在执行前验证 namespace、属性可调用性与 `plugin_path` 来源。

## 内置 namespace 与外部 DLL 分开看

`core.plugins()` 是当前 core 已注册 plugin 的枚举入口。对外部 requirement，项目
不仅检查 `hasattr(core, namespace)`，还检查：

1. `core.<namespace>.<function>` 确实 callable；
2. plugin 的 `plugin_path` 位于便携内置 `vs-plugins`、可选 `vs-coreplugins` 或
   runtime 明确配置的 native 根；
3. 多目录加载期间没有可证明的 identity/namespace 冲突或候选加载失败。

真正的内置 plugin 可能没有文件路径，此时允许 `plugin_path=None`。不要用“没有
plugin_path”推导插件缺失，也不要把 Python module 目录当成 native 来源根。

## 用户脚本如何声明额外插件

用户 `.vpy` 若调用额外 native 函数，必须把完整 `namespace.function` 写入
`assetmaker-requires`，并把 DLL 所在目录加入 `plugins.native_plugin_dirs`。若调用
相邻 Python 模块，则其根加入 `plugins.python_module_dirs`；两类目录加载机制不同。

例如以下只是概念示例，不是内置生产依赖：

```text
# assetmaker-requires: fmtc.resample
```

是否采用 `fmtc`、BestSource、ffms2、PNG writer、ML 插件或脚本包，必须逐项验证
目标 VS API、CPU/GPU 依赖、DLL 的相邻依赖、namespace、输出格式、许可证、冻结
打包和 worker/VSPipe parity。第三方数据库未收录或多年未更新，都不能证明“不存在”
或“兼容”。

## 不要从“可替代”跳到“应替代”

- `lsmas` 当前真实视频、索引和编码链在 R73 验收通过；替换 source plugin 会改变
  解码帧、时间轴、色彩 props 和缓存行为，必须有具体产品收益。
- `imwri` 当前负责图片首帧；候选必须覆盖当前格式、色彩、异常语义和
  `virtual_frame_count` 流程。视频 source 的候选不能自动视为图片 source 替代。
- `std.Merge`、`MaskedMerge`、`Expr` 等虽然可用于合成，但默认脚本当前没有调用。
  它们只能作为明确标注的可选示例，不能写成生产依赖或已采用方案。
- AVFS 是把脚本暴露给外部应用的独立组件，不是 source plugin，也不替代这里的
  `lsmas`/`imwri` requirement。

## 相关

- [05 便携插件](05-plugin-autoload-portable.md) — 多目录 native policy
- [08 版本升级](08-version-upgrade-notes.md) — R79 插件/分发门禁
- [10 研究方法](10-research-method.md) — 第三方候选的证据等级
- [13 用户 VPY ABI](13-user-vpy-abi.md) — `assetmaker-requires`
