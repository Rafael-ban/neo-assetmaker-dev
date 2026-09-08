# VapourSynth 知识库索引

**先从本页按问题定位，再只读取命中的文章。** 本库服务于本项目当前的用户
`.vpy` 架构，不是完整的 VapourSynth 滤镜百科，也不是把某一版上游文档直接抄成
项目合同。

## 当前合同快照

- 当前产品基线使用 **VapourSynth R73 / API R4.1**；内置入口是
  `resources/vapoursynth/default_pipeline.vpy`。
- worker 直接执行已确认的用户脚本；预览与导出共享 `executor`、脚本头、
  `job_api` 和 `contract`。只有 VSPipe 导出端执行固定的
  `assetmaker_runner.vpy`。
- `output 1` 是旋转后、trim/crop 前的完整编辑时间轴；`output 0` 是经过
  trim、crop、色彩转换、补边和最终旋转的设备编码输出。
- 当前 360×640 profile 的内容画布是 360×640，编码画布是 384×640。
  二者不同是输出合同，不是参数丢失。
- R79 兼容性仍属于后续候选验证。本库若提到 R79 固定 tag，只表示静态源码
  边界；在候选运行时、插件、打包与真实媒体探针完成前，不代表本项目已兼容。

## 快速路由

| 你要解决的问题 | 先读 |
|---|---|
| 写第一份项目脚本、导入视频/图片、添加滤镜 | [13 用户 VPY ABI](13-user-vpy-abi.md) |
| 脚本为何能预览、如何避免过时帧覆盖新画面 | [07 帧生命周期](07-frame-lifetime-threading.md)、[14 worker 协议](14-worker-protocol.md) |
| `set_output()`、尺寸/颜色/帧率为何导出失败 | [15 输出契约](15-output-contract.md) |
| 项目脚本是否安全、为何修改后要再次确认 | [13 用户 VPY ABI](13-user-vpy-abi.md)、[16 脚本信任](16-script-trust.md) |
| 旋转、裁剪、补边与 YUV 子采样 | [03 几何](03-geometry-filters.md)、[04 Trim/Loop](04-trim-loop-zero-length.md) |
| resize、矩阵、范围与色彩标签 | [01 色彩](01-colour-range-props.md)、[02 Resize](02-resize-semantics.md)、[15 输出契约](15-output-contract.md) |
| 插件如何被便携运行时加载 | [05 便携插件](05-plugin-autoload-portable.md)、[09 插件生态](09-plugin-ecosystem.md)、[13 用户 VPY ABI](13-user-vpy-abi.md) |
| VSPipe、编码和导出排错 | [06 VSPipe](06-vspipe-cli.md)、[13 用户 VPY ABI](13-user-vpy-abi.md)、[15 输出契约](15-output-contract.md) |
| 预览缩放、帧生命周期或线程 | [07 帧生命周期](07-frame-lifetime-threading.md)、[11 预览缩放](11-preview-zoom.md)、[14 worker 协议](14-worker-protocol.md) |
| R73→R79 到底要验证什么 | [01 色彩](01-colour-range-props.md)、[05 便携插件](05-plugin-autoload-portable.md)、[08 升级](08-version-upgrade-notes.md)、[10 研究方法](10-research-method.md) |

## 文章目录

| # | 文件 | 主题 |
|---|---|---|
| 01 | [colour-range-props.md](01-colour-range-props.md) | 色彩范围与帧属性 |
| 02 | [resize-semantics.md](02-resize-semantics.md) | Resize 的矩阵、范围和核参数 |
| 03 | [geometry-filters.md](03-geometry-filters.md) | 旋转、裁剪与子采样约束 |
| 04 | [trim-loop-zero-length.md](04-trim-loop-zero-length.md) | Trim、Loop 和零长度 clip |
| 05 | [plugin-autoload-portable.md](05-plugin-autoload-portable.md) | 便携插件自动加载 |
| 06 | [vspipe-cli.md](06-vspipe-cli.md) | VSPipe 与编码管道 |
| 07 | [frame-lifetime-threading.md](07-frame-lifetime-threading.md) | 帧内存和线程边界 |
| 08 | [version-upgrade-notes.md](08-version-upgrade-notes.md) | 版本与升级风险 |
| 09 | [plugin-ecosystem.md](09-plugin-ecosystem.md) | 当前插件依赖与替代方案 |
| 10 | [research-method.md](10-research-method.md) | 来源分级与复核方式 |
| 11 | [preview-zoom.md](11-preview-zoom.md) | 高倍率预览缩放 |
| 12 | [field-hazards.md](12-field-hazards.md) | 常见运行时陷阱 |
| 13 | [user-vpy-abi.md](13-user-vpy-abi.md) | 用户脚本接口、模板与外部教程映射 |
| 14 | [worker-protocol.md](14-worker-protocol.md) | 预览 worker、epoch、mmap 与恢复 |
| 15 | [output-contract.md](15-output-contract.md) | output 0/1 与设备编码约束 |
| 16 | [script-trust.md](16-script-trust.md) | 来源、bundle hash 与本机信任 |

## 证据等级

- **固定版本官方文档/源码**：必须标 R73、R79 等 tag；滚动网页不能替代版本证据。
- **项目直接源码**：当前默认脚本、共享 executor/header/contract/job API、worker、
  runner 与导出管道。
- **运行时实测**：标明运行时版本、日期、环境和输入；真实 worker、VSPipe 或
  编码回归不能由 mock 代替。
- **项目测试**：证明被断言的合同；跳过真实媒体用例的绿色结果不等于真实链路已跑。
- **第三方经验**：只用于可读性、教程结构或性能启发，不能单独改变输出契约。

升级 VapourSynth、替换便携插件或改变 runner 时，应先复核 01、05、06、08、
13、14、15、16，再按 [10 研究方法](10-research-method.md) 分层验证。索引中没有
列出的滤镜不应被默认加入内置脚本；用户脚本应显式声明依赖和处理顺序。
