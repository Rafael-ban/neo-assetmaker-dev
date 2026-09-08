# 08 · R73 基线与 R79 升级门禁

**结论：当前已验收的是 R73 源码工作树，不是 R79，也不是已安装应用或冻结包。
R79 升级必须被当作 runtime、binding、插件、分发、色彩合同和双执行路径的迁移，
不能只替换 `vapoursynth.dll` 或 `VSPipe.exe`。**

## 当前 R73 身份

- `tools/media/vapoursynth-73.dist-info/METADATA`：`Version: 73`。
- `tools/media/VSPipe.exe --version`：Core R73、API R4.1，并保留 API R3.6。
- binding wheel 为 `cp312-abi3`，所以项目的 Python 下限是 3.12；`abi3` 表示从
  cp312 起向前兼容，不表示能在 Python 3.11 向后运行。
- 默认入口是 `resources/vapoursynth/default_pipeline.vpy`；worker 与 VSPipe 各自
  建 core，但共享 executor/header/job API/output contract。
- runtime 配置中的 core 值 `0` 表示保留进程首次捕获的 VapourSynth 原生值；
  内置脚本随后显式设置 `core.max_cache_size=16000` MB。cache 阈值不是进程 RSS
  硬上限，也不是所有用户脚本都必然采用 16000。

## 当前验收边界（2026-09-09）

产品 BASE `351025b3223ca8a55270b79ca2245e5a5b54d123` 的 R73 证据为：

- compileall exit 0；完整测试 719 项，0 failure / 0 error / 0 skipped，OK；
- 真实 worker 生命周期、worker/VSPipe output 0 plane parity、多目录 native 插件、
  图片循环、旋转/crop、实际编码与色彩 VUI 均有非 skip 证据；
- F4 性能样本只测“鼠标输入到 QLabel Paint 开始分派”，不代表绘制完成、物理
  显示或 VapourSynth 解码 FPS。新侧连续拖动 n=24/场景，P95 为播放
  0.294075 ms、暂停 0.320925 ms；完整分布与限制见 F4 session3 报告。

这些结果证明当前源树的 R73 验收，不证明 cx_Freeze 产物已构建，也不更新用户正在
运行的 `ArknightsPassMaker`。R79 候选验证尚未开始。

## 为什么 R79 不是 DLL 单换

固定 tag 静态入口：

- `https://github.com/vapoursynth/vapoursynth/blob/R79/include/VSConstants4.h`
- `https://github.com/vapoursynth/vapoursynth/blob/R79/src/cython/vsconstants.pxd`
- `https://github.com/vapoursynth/vapoursynth/blob/R79/src/cython/vapoursynth.pyx`
- `https://github.com/vapoursynth/vapoursynth/blob/R79/src/core/vsapi.cpp`

静态源码已经提示 API 4.2 `_Range`、Python `Range(IntEnum)`、旧键 remap、autoload
与分发布局等迁移面，但真实候选行为仍需运行时证据。AVFS 从 R74 起是独立组件；
R79 不会把它重新变成核心内置功能。AVFS 面向把 `.vpy` 暴露给外部应用，不替代
本项目的按帧 worker→mmap→Qt 预览链。

## U 阶段最低验收门

1. **候选身份**：记录 binding/core/API/VSPipe 版本、Python tag、全部 DLL/插件
   SHA-256；证明 worker 与 VSPipe 使用同一候选根。
2. **分发与插件**：实测 `portable.vs`、`vs-plugins`、可选 `vs-coreplugins`、CPU
   变体/manifest、多个配置 native 目录的加载顺序、冲突诊断和 plugin source。
3. **Range**：分别探测 `_Range`/`_ColorRange` 的普通整数或枚举类型、数值语义、
   旧键读写映射、双键冲突、lsmas/imwri/resize 实际行为；再决定是否修改合同。
4. **图与像素**：默认脚本顺序、output 0/1、Bicubic、AddBorders、viewport、
   frame props 与真实编码回读逐项对照 R73，不把 golden 漂移当作可直接重抓的噪声。
5. **资源与生命周期**：验证 core 默认基线、0 语义、脚本覆盖、Future/frame close、
   cancel ACK、worker restart/退休以及 VSPipe stderr/stdout。
6. **构建**：完成 source tests 后再运行 cx_Freeze，核对 frozen worker、VSPipe、
   helper、marker/plugin 文件和真实便携包 smoke；源码绿色不能代替冻结产物。

若候选没有解决明确产品需求，或任一门禁无法稳定通过，应保留 R73，而不是因版本号
更新降低现有合同。

## 当前可运行的 R73 基线命令

```powershell
uv run python -m compileall main.py config core gui utils _mext build.py tests `
  resources/vapoursynth/python
uv run python -m unittest discover -s tests -p "test_*.py"
tools\media\VSPipe.exe --version
```

完整测试可能包含真实媒体；必须同时记录 skip 计数与工具身份。打包只在明确进入
冻结验收时运行 `uv run python build.py ...`，不要把它混入 K1 文档冻结。

## 相关

- [01 色彩范围](01-colour-range-props.md)
- [05 便携插件](05-plugin-autoload-portable.md)
- [10 研究方法](10-research-method.md)
- [14 worker 协议](14-worker-protocol.md)
- [15 输出契约](15-output-contract.md)
