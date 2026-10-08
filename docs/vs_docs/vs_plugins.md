# VapourSynth 已装插件详解

> 环境：VapourSynth R79 / Python 313
> 生成方式：本清单来自 `core.plugins()` 实际枚举——共 **62 个命名空间 / 306 个函数**，全部为当前机器真实可用，非泛指库。
> 签名参考文件：`vs_plugins_signatures.txt`（各函数参数的真实签名）。
> 说明：每个滤镜节含 **输入要求**（格式/位深/逐行前提）与 **参数推荐值**（常用起步参数）。示例脚本均可用 `core.std.BlankClip()` 造源验证。

---

## 目录

- [核心库](#1-核心库)
- [格式与色彩管理 fmtc](#2-格式与色彩管理-fmtc)
- [源加载 / 字幕 / 信息类](#3-源加载--字幕--信息类)
- [反缩放（descaling）家族](#4-反缩放descaling家族)
- [降噪 / 去块 / 去色带](#5-降噪--去块--去色带)
- [动补偿家族 mvtools / mvutensils](#6-动补偿家族-mvtools--mvutensils)
- [去隔行 / IVTC / 缩放家族](#7-去隔行--ivtc--缩放家族)
- [AI / 神经放大](#8-ai--神经放大)
- [锐化 / 边缘 / 增强 / 其它滤镜](#9-锐化--边缘--增强--其它滤镜)
- [质量评估 / 分析与杂项](#10-质量评估--分析与杂项)
- [典型处理链示例](#11-典型处理链示例)

---

## 1. 核心库

核心库属于 VapourSynth 内置，不来自任何插件，永远可用。

### 1.1 `std`（标准库，81 个函数）

最庞大的内置命名空间，提供剪辑基本操作、逐像素/逐帧编程、通道与格式处理。按用途分组：

**加载 / 启动**

| 函数 | 说明 |
|---|---|
| `std.LoadPlugin(path)` | 手动加载外部 DLL |
| `std.LoadAllPlugins()` | 自动扫描插件目录（启动时默认已做） |

**取帧 / 剪辑操作**

| 函数 | 说明 |
|---|---|
| `std.Trim(clip, first, last)` | 按帧号切片（含 last）。注意：**不是按时间**，是帧号 |
| `std.Trim(..., length)` / `std.Trim(..., end)` | 帧号 + 长度 / 帧号区间 `[first, end)` 两种语义 |
| `std.SelectEvery` | 按周期抽帧（如逐行补抽帧） |
| `std.DuplicateFrames` / `std.DeleteFrames` | 复制 / 删除指定帧 |
| `std.FreezeFrames` | 把某区间帧冻结成固定一帧 |
| `std.Reverse` | 倒放 |
| `std.Loop` | 循环 n 次或到指定长度 |
| `std.Interleave(clips)` | 交错合成（多源逐帧交替） |
| `std.Splice` | 首尾拼接（可带 transition） |
| `std.Transpose` / `std.Turn90/180/270` | 转置 / 旋转 |
| `std.FlipHorizontal` / `std.FlipVertical` | 水平 / 垂直镜像 |
| `std.Crop` / `std.CropAbs` / `std.CropRel` | 裁剪（左右上下像素）；CropRel 按“总宽减”语义 |
| `std.AddBorders` | 补边（加黑边到目标宽高） |

**逐像素 / 表达式（性能关键，常用）**

| 函数 | 说明 |
|---|---|
| `std.Expr(clip, expr, format)` | 用 RPN 表达式逐像素计算，可跨多剪辑（每输入给一组表达式），支持 `x y +` 等。现代 VS 写滤镜逻辑的头号工具 |
| `std.Lut` / `std.Lut2` | 单帧 / 双帧查表（8 位常用） |
| `std.Convolution` | 卷积核（如锐化、高斯） |
| `std.BoxBlur` | 盒式模糊 |
| `std.Median` | 中值滤波（去椒盐/斑点） |
| `std.Invert` | 反色 |
| `std.Levels` / `std.Binarize` | 色阶调整 / 二值化 |
| `std.Minimum` / `std.Maximum` / `std.Inflate` / `std.Deflate` | 形态学腐蚀膨胀 |
| `std.Sobel` / `std.Prewitt` | 边缘检测 |
| `std.MaskedMerge(clipa, clipb, mask)` | **掩码合成**——全站最重要的合成函数，mask 0→clipb、255→clipa |
| `std.Merge` / `std.MergeDiff` / `std.MakeDiff` 系列 | 两剪辑加权合并 / 差分 |
| `std.ShufflePlanes` / `std.SplitPlanes` / `std.Combine` | 平面拆/合，自由重组 YUV 平面（如 Y 和 UV 分开处理再拼回） |
| `std.ShuffleChannels` | 重排音频声道 |
| `std.StackHorizontal` / `std.StackVertical` | 拼接预览（左->右 / 上->下），调试对比神器 |
| `std.CopyFrameProps` | 复制帧属性（frameprops） |

**音频**

`std.AudioTrim`/`AudioLoop`/`AudioSplice`/`AudioMix`/`AudioGain`/`AudioResample`/`AudioReverse`/`BlankAudio`、`std.SetAudioCache`、`std.AssumeSampleRate`、`std.AudioGain` 等——完整音频剪辑控制。

**辅助**

| 函数 | 说明 |
|---|---|
| `std.AssumeFPS` | 改帧率标签（不改帧数） |
| `std.BlankClip` | 生成测试图（纯色/噪声），**脚本调试造源首选** |
| `std.SetFrameProp`/`SetFrameProps`/`RemoveFrameProps`/`GetFrameProps` | 帧属性读写 |
| `std.FrameEval` / `std.ModifyFrame` | 逐帧回调 Python 函数，做条件逻辑 |
| `std.PlaneStats` | 输出每帧统计属性（平均亮度等，可用于自适应阈值） |
| `std.Limiter` | 钳制像素到某范围（防越界） |

> 常用片段：
> ```python
> # 两剪辑用亮度掩码合成（edge 亮处取 sharp、否则取 den）
> merge = core.std.MaskedMerge(sharp, den, mask)
>
> # 逐像素表达式：y 与 128 之差的绝对值
> out = core.std.Expr(src, "x 128 - abs")
>
> # 预览左右对比
> side = core.std.StackHorizontal([a, b])
> ```

> **输入要求**：`std` 各函数均为通用工具，无固定格式前提，但同一条链里参与运算的多个 clip 通常要求**格式/位深/尺寸一致**（`MaskedMerge`、`Expr`、`Merge` 等会校验）。逐像素函数建议先在 8–16 bit 整数上跑，避免 float 边界行为差异。
> **参数推荐**：`MaskedMerge` 的 mask 建议与 clip 同尺寸、单平面（Y），值为 0–满幅；`Expr` 用 8-bit 时数字写 0–255、16-bit 写 0–65535，公式里同一表达式作用于所有平面时注意色度偏移；`StackHorizontal/StackVertical` 要求两 clip 的帧数/帧率一致，常用于 A/B 对比预览。

> **算法特点（std 里的几个核心工具）**：
> - **Expr**：用**逆波兰（RPN）**表达式做逐像素运算，整个滤镜被编译成字节码一次性执行（比反复组合 std 函数快一个量级）。它是 VS"自己写像素逻辑"的万能底层——mask 运算、条件选择、合并都可用 Expr 一行表达。
> - **MaskedMerge**：逐像素 `a·mask + b·(1−mask)`（mask 归一化加权）。所有"只处理局部"的高级用法（只锐化边缘、只去噪平坦区）最终都落到它。mask 常由 Canny/Sobel/差帧等生成。
> - **形态学（Minimum/Maximum/Inflate/Deflate）**：在邻域取极值，可扩张/收缩 mask、去孤立噪点、修 mask 孔洞。
> - **Sobel/Prewitt/Convolution**：卷积/梯度算子，本质是固定核的线性滤波。
> 这些"通用原语"的价值：让你用几个基本积木拼出任意自定义滤镜，而不必为每种想法找一个现成插件。

### 1.2 `resize`（内置 zimg 缩放，8 个函数）

由 zimg 提供，是纯缩放/转换路径，**不做色彩空间解释变更**。

| 函数 | 说明 |
|---|---|
| `resize.Bicubic(clip, w, h, format=..., b=1/3, c=1/3)` | 默认 b=1/3 c=1/3（Catmull-Rom）；调 b/c 可到 Mitchell 等 |
| `resize.Bilinear` | 双线性，模糊一点 |
| `resize.Spline16/36/64` | 高质量平滑缩放，动画/实拍通用 |
| `resize.Lanczos` | 默认 3 taps，可调 `taps` |
| `resize.Point` | 最近邻（点采样，无模糊） |
| `resize.Bob` | 场分离 + 垂直放大去隔行输出（每场成帧） |

> **输入要求**：接受 GRAY / YUV / RGB，8–16 bit 整数与 32 bit float。缩放不强制逐行；`Bob` 需**隔行源**。转成 4:2:0 时目标宽高需为偶数；4:2:2 时高度为偶数。
> **参数推荐**：动画放大常用 `Spline36`，实拍放大常用 `Spline36` 或 `Lanczos(taps=3)`；`Spline64` 更平滑但边际提升小；缩小怕摩尔纹/混叠用 `Area` 效果最好——但**注意本机未装 `resize.Area`**（zimg 无该核），缩小抗混叠可用 `fmtc.resample(kernel="box")` 或 `descale` 系替代。Bicubic 调 b/c：动画偏利落用 b≈0/c≈1，通用用默认。`Bob` 只是粗去隔行，正式处理用 §7 的 bwdif/yadifmod。

> **算法特点**：zimg 用**可分卷积**实现缩放（水平、垂直各做一次一维滤波），速度极快。核的本质是不同形状的一维插值窗函数：
> - **Bicubic**：用参数 b/c 控制的三次多项式逼近理想插值，构成一族核——b=0/c=1 很锐利、b=1/c=0 很平滑、b=0/c=0.5 即 Catmull-Rom（常见"平衡"点）。升格可微调但别指望质变。
> - **Spline16/36/64**：基于 Catmull-Rom 样条，taps 越多平滑性越好、对噪声越钝。Spline36 在"保高频 vs 少振铃"上很均衡，因此是压制默认宠儿。
> - **Lanczos**：取 sinc 窗（理想带限重采样核）截断，taps 越大越接近理想。理论最优，但**振铃/光边（ringing）最强**，放大高对比线条尤其明显。
> - **Point**：最近邻，不做任何插值（零成本、像素方块化），只在整数倍缩放/测试时用。
> - 要点：**放大不产生新信息**——放大倍率越大，固定核能恢复的细节越少（奈奎斯特以上频率已在采样时丢失），这时只能靠超分/AI 或 descale 补救；缩小则要防混叠，需足够"低通"的核。

关键点：
- `format=` 传 `vs.YUV420P8` 等可在缩放同时转像素格式；`matrix_s=` / `matrix_in_s=` 可设系数，但**原始色度位置默认 Full/MPEG 假设**——高清 4:2:0 素材通常要显式 `chromaloc_in_s` / `matrix_in_s`，或用下方 fmtc 做严谨转换。
- 内置 resize 只缩放 + 改 format，不修 primaries/transfer。
- Bob 用 `bwdif` / `yadifmod` 去隔行常更优。

> 片段：
> ```python
> up = core.resize.Spline36(src, 1920, 1080, format=vs.YUV420P8)
> down_aa = core.resize.Area?  # 注：Area 不在已装插件里，缩小抗混叠多走 fmtc.resample 或 descale
> ```

### 1.3 `text`（内置调试绘制，5 个函数）

| 函数 | 说明 |
|---|---|
| `text.ClipInfo(clip)` | 叠加 分辨率/格式/帧率/帧号 等一屏信息——**查输出是否如预期的最快方式** |
| `text.FrameProps` | 叠加每帧的帧属性 |
| `text.FrameNum` | 只显示帧号 |
| `text.Text` | 自定义文本（常配 `std.BlankClip` 打时间码/说明） |
| `text.CoreInfo` | 显示核心信息 |

> ```python
> preview = core.text.ClipInfo(out)
> ```

> **输入要求**：任意格式 clip；绘制的文字会叠加在画面（通常转成 RGB/YUV420 以便显示）。`scale` 仅在需要放大文本时用。
> **参数推荐**：调试阶段在输出前接 `text.ClipInfo` / `text.FrameProps` 验证尺寸/格式/帧率/帧属性；正式出片前记得摘掉。`alignment` 0–8 对应九宫格位置，文本 + `std.BlankClip` 可当临时水印。

---


## 2. 格式与色彩管理 fmtc

`fmtc`（fmtconv）是所有装好的插件里做**位深 / 色彩空间 / 矩阵 / 传递 / 缩放**最严谨的一个。核心思路是：每一步都显式声明"从哪来、到哪去"，避免内置 resize 里常见的隐式假设。

| 函数 | 说明 |
|---|---|
| `fmtc.resample(clip, w=, h=, kernel=..., taps=..., ...)` | 缩放 + 格式转换。核可用名（fmtconv 官方）：`point`、`rect`(box)、`linear`(=bilinear)、`cubic`(=bicubic)、`lanczos`、`blackman`、`blackmanminlobe`、`spline16/36/64`、`spline`、`gauss`(=gaussian)、`sinc`、`impulse`（自定义权重）；可水平/垂直给不同核。注意 `box/bilinear/bicubic/point/spline16/36/64` 等在 zimg 的旧名也能被部分版本接受，但官方列名以上为准 |
| `fmtc.bitdepth(clip, bits=, dmode=, ...)` | 位深转换。`dmode` 是**抖动/舍入算法编号**，官方定义 0–9：**0=Ordered(Bayer)、1=直接舍入(无抖动)、2=更快舍入、3=Sierra-2-4A 误差扩散=默认、4=Stucki、5=Atkinson、6=Floyd–Steinberg、7=Ostromoukhov、8=Void-and-Cluster、9=Quasi-random**。常用：升位深图省事可直接默认；降位深要最小色带用误差扩散(3) |
| `fmtc.matrix(clip, mat=..., col_fam=...)` | RGB↔YCbCr 矩阵转换。`mat/mats/matd` 预设名（fmtconv）：`601`(=BT.470-2/BG 即常说的 470bg)、`709`、`2020`、`2100`、`240`、`FCC`、`470-525`、`YCoCg`、`YDzDx`、`RGB`（RGB 直通）；primaries 预设才有 `470bg` 这个名字。别把 "470bg" 当 matrix 名写 |
| `fmtc.primaries(clip, prims=..., primd=...)` | 色彩原色转换（BT.709→BT.2020 等）。**要求输入为 planar RGB，且位深须 16-bit 整数或 32-bit float**（本机 DLL 硬性约束） |
| `fmtc.transfer(clip, transs=..., transd=...)` | 传递函数转换（gamma 2.2→PQ→HLG 等 HDR 相关） |
| `fmtc.nativetostack16` / `fmtc.stack16tonative` | 与 16bit "堆叠" 表示互转（LSB/MSB 上下两帧代表高位深的老办法） |
| `fmtc.matrix2020cl(clip, full=)` | 简化 BT.2020 constant-luminance 矩阵，HDR 10-bit 用。本机要求 planar 4:4:4，RGB 与 YUV 互转时深度≥16bit |
| `fmtc.histluma` | 输出亮度直方图 |

**缩放核上的 fh/fv 与 total**：`fh` 可给高频提亮参数（h 方向的预锐化系数，类似放大时轻微加强边缘）。

fmtc 的转换是"**一次一个属性**"的——矩阵、原色、传递要分开调。完整色彩流（尤其 SDR→HDR）要三步都做对：

```python
# RGB(709 limited) → YUV420P10 对应 BT.709，注意 csp/col_fam/bits 用 10 位 4:2:0
rgb10 = core.fmtc.bitdepth(rgb, bits=16)                     # 先升到高位避免中间损耗
yuv = core.fmtc.matrix(rgb10, mat="709", col_fam=vs.YUV)
yuv = core.fmtc.resample(yuv, csp=vs.YUV420P16)
out = core.fmtc.bitdepth(yuv, bits=10)
```

> fmtc 在压制流程里最典型的用法其实就是**两处**：① 把源统一成 16bit 高位深再处理，末尾再降回输出位深；② 严谨的 matrix/primaries/transfer 色彩流。它不追求快，追求准。

> **输入要求**：
> - `bitdepth`：整数 8–16 bit 互转，或整数↔32 bit float（`flt=True/False`）。RGB 常用 `flt=True` 过渡以避开色度限制。
> - `resample`：可与缩放同时转换，源/目标 4:2:0 宽高需 2 对齐，`csp` 用 `vs.YUV420P16` 等命名。
> - `matrix`：仅处理 RGB↔YCbCr，源/目标需声明 `mat/mats/matd` 与 `fulls/fulld`（limited/full range）。**range 默认随色彩空间**：R'G'B' 与 Y'CoCg' 假设 full，其余（YUV 等）假设 TV/limited——所以 YUV→RGB 时默认按 limited 算。混 range 会偏色。
> - `primaries`/`transfer`：输入输出应为 float 或足够高位，避免 8-bit 中间态损失。
> **参数推荐**：
> - 升位深（如 8→16）：`fmtc.bitdepth(clip, bits=16)`（默认 dmode=3 误差扩散，升位深不引入额外噪声）。降位深（16→8）若要最平滑渐变，可显式 `dmode=3`(误差扩散) 或 8(blue-noise 类)；不想引入任何抖动就 `dmode=1`（直接舍入，但会出色带）。
> - 缩放核：想高保真放大 `kernel="spline36"` 或 `"lanczos", taps=4`；做锐利放大可在 `resample` 前对 Y 用 `total`/`fh` 预提，但新手建议先不动。
> - 色彩转换三步别并成一步：先 `transfer`（若 HDR）再 `primaries` 再 `matrix`；每步 16bit/float 下做，最后才降位深。

> **算法特点**：
> - **bitdepth**：升位深只是"移位 + 可选抖动"；降位深才需抖动——fmtconv 的 `dmode` 提供一整套抖动/舍入算法（0=Ordered Bayer、1=直接舍入、2=更快舍入、3=Sierra-2-4A 误差扩散默认、6=Floyd–Steinberg、8=Void-and-Cluster…）。误差扩散/蓝噪类把量化误差散布到邻域，视觉上最平滑但会引入轻微噪声纹理；直接舍入则最"干净"却容易出色带。核心思想：**降位深时把量化误差转成不可察觉的高频噪声，而非可见色带**。
> - **resample**：缩放核与 zimg 同类（§1.2 讲的窗函数），但它允许水平/垂直给不同核，且支持**自定义 impulse（把自己的权重数组当成核）**——很多"仿某源缩放"的精确复刻都靠它。
> - **matrix/primaries/transfer**：本质都是 3×3 或一维查找的**色彩数学映射**。区别在作用域：matrix 管 RGB↔YCbCr（线性矩阵），primaries 管色域原色（RGB 三原色坐标换算，也近似线性矩阵），transfer 管"光↔电信号"的非线性曲线（gamma/PQ/HLG）。三者要连成完整链路才能正确做 HDR↔SDR。
> - 与内置 resize 的取舍：zimg 追求"一次调用顺手完成 + 快"，fmtc 追求"每步显式、可复现、不背地里假设"——代价是调用多、慢一点。严谨转换（尤其 range/矩阵组合错误会直接偏色）选 fmtc 更安全。

### 2.1 补充：`tonemap`（HDR→SDR 色调映射）

`fmtc` 做的是色彩空间数学转换（矩阵/原色/传输函数，保亮度范围）；当需要把 **HDR（PQ/HLG）映射进 SDR 亮度范围**时，靠的是色调映射（tone mapping），由 `tonemap` 提供。两者常搭配：先 fmtc 转传输函数/原色到 SDR 域，再用 tonemap 压动态范围。

> **输入要求（与官方 README 一致）**：本机这个插件（`com.ifb.tonemap`，r6.5a037ae）**只接受 planar 32-bit float 输入**——三个函数都是。因此必须先升成 float 再喂：`core.fmtc.bitdepth(x, bits=32, flt=True)`。它不是 16-bit 也能跑的通用工具，注意别拿整数 clip 直接调。

| 函数 | 参数（默认值） | 特点 |
|---|---|---|
| `tonemap.Hable(clip, exposure=2.0, a=0.15, b=0.50, c=0.10, d=0.20, e=0.02, f=0.30, w=11.2)` | **无 `peak`**；白点由 `w` 决定 | Filmic S 曲线（Uncharted 2 式），明暗细节保留好，代价是整体轻微变暗 |
| `tonemap.Reinhard(clip, exposure=1.5, contrast=0.5, peak=1.0)` | 有 `peak`（默认 1.0） | 全局 Reinhard 压缩，实现简单 |
| `tonemap.Mobius(clip, exposure=2.0, transition=0.3, peak=1.0)` | 有 `peak`（默认 1.0） | 线性段 + Mobius 变换，界内色彩最准 |

官方公式与语义：
- **Hable**：`hable(x)=((x·(a·x+c·b)+d·e)/(x·(a·x+b)+d·f))−e/f`，输出 `hable(exposure·x)/hable(w)`。a–f 控制曲线肩/趾形状，**`w`（默认 11.2）才是白点参考**。
- **Mobius**：低于 `transition`（默认 0.3）的值按 1:1 映射（保界内色彩），之上转 Mobius 曲线平滑压缩超出值。
- **peak** 是**归一化参考峰值**（默认 1.0），不是绝对 nits。

```python
# 概念：HDR float → 线性光 → 色调映射（本插件只收 32-bit float）
# 1) 先把 HDR 源升成 32-bit float 并转线性光
hf = core.fmtc.bitdepth(hdr, bits=32, flt=True)          # 源如为整数则先升 float
lin = core.fmtc.transfer(hf, transs="pq", transd="linear")  # PQ → 线性光（float 域）
# 2) 三种选一（注意 Hable 无 peak，Mobius/Reinhard 有 peak=）
sdr = core.tonemap.Hable(lin, exposure=2.0)              # 胶片感；想提亮可加大 exposure
# sdr = core.tonemap.Mobius(lin, transition=0.3, peak=1.0)     # 界内色彩最准
# sdr = core.tonemap.Reinhard(lin, exposure=1.5, peak=1.0)
# 3) 之后还要 transfer/matrix 落到 SDR gamma / BT.709 再降位深
```

> HDR→SDR 是全链路：transfer（PQ→线性）→ tonemap（压范围）→ transfer（线性→SDR gamma）→ matrix（2020→709）。`tonemap` 只是其中"压动态范围"这一环，别当成完整方案。

> **参数推荐**：偏色彩准确（转 SDR 看正片）用 `Mobius`（`peak` 拉到源参考峰值，可略大于 1 以保住高光）；想要"更有层次/胶片感"用 `Hable`（嫌整体偏暗就调大 `exposure`，白点想前移可调小 `w`）；`Reinhard` 简单但高光易发灰、界面色彩对比被压，不常用。

> **算法特点**：色调映射是**全局（每像素独立、同一条 S 曲线）**地把宽动态范围压窄到显示范围。三者的区别：
> - **Reinhard**：`L/(1+L)` 式的全局压缩，实现最简，但高光区被过度压低、发灰，暗部也提亮，观感平淡。
> - **Hable（Uncharted 2 filmic）**：用拟合的 S 形曲线（shadows 拉高、midtones 保对比、highlights 平滑滚降），是"胶片响应"风格的经典选择，画面更有层次，但整体略偏暗/偏风格化。
> - **Mobius**：低亮度段近似 1:1 线性（保界内色彩与对比），只在接近 `peak` 的高光区软拐弯压缩，因此**中间调和高光过渡最自然**，多数 SDR 转制更推荐。
> - 局限提醒：全局映射会把整个画面的动态范围一起压——亮暗对比同时被缩，无自动白平衡/局部提亮。真正的 HDR→SDR 还涉及色域与传输函数，务必全链路看，`tonemap` 只是其中一环。

---


## 3. 源加载 / 字幕 / 信息类

### 3.1 `lsmas`（LSMASHSource）

当前 VapourSynth 里**最主流**的解码器，libavformat 系，能吃几乎所有封装/编码。

| 函数 | 说明 |
|---|---|
| `lsmas.LWLibavSource(source, stream_index=0, threads=, fpsnum=, fpsden=, cache=, format=, decoder=, prefer_hw=, ...)` | 通用源。`cache`：0=关闭、1=读写 .lwi 索引缓存（默认，重开同一文件明显加速；首次建缓存略慢属正常）——注意部分旧文档提到 cache=2，以你实际 DLL 版本支持为准；`threads` 默认 0=自动；`format` 指定输出像素格式 |
| `lsmas.LibavSMASHSource(source, track=0, ...)` | 读 BDMV 蓝光更稳，能直接给正片音轨 mux；seek 表现通常比 LWLibav 好一点 |

参数速记：
- `cache`：0 关闭 / 1 读写 .lwi（默认）。想每次干净重读就设 0；要加速重开就留 1。
- `threads`：默认 0（ffmpeg 自动，最多 16）；遇到丢帧错帧可试 1（单线程更稳）。
- `seek_mode`：0=Normal（默认，重试多次最稳）、1=Unsafe（快）、2=Aggressive（最快但易错）。要稳用默认 0。
- `format`（如 `"YUV420P8"`）可让解码器直接出目标格式，避免二次转换（需 `variable=0` 时才强制）。

```python
clip = core.lsmas.LWLibavSource(r"C:\v\film.mkv", cache=1, threads=1)   # cache=1 读写 .lwi；threads 想稳用 1
# 若源是 10bit，直接要 P16 高位深再处理：
clip = core.lsmas.LWLibavSource(src, cache=1, format="YUV420P16")
```

> **输入要求**：路径为视频容器/流文件（mkv/mp4/ts/m2ts/avi…）；`LibavSMASHSource` 额外可给 BDMV 结构 `track=`。输出格式默认跟源一致（常见 YUV420P8/10），高位处理建议直接指定 `format`。
> **参数推荐**：`threads` 追求最稳给 1（重错误源更安全），默认 0（自动）通常也够。`cache` 留默认 1 即可（写 .lwi 加速重开）。`fpsnum/fpsden` 仅当源帧率元数据错误时才覆盖；正常不要动。`seek_mode` 用默认 0（Normal）求稳；只有明确要提速才改 1。

> **算法特点**：两者都把 libav（FFmpeg）解码器搬进 VS 帧模型。关键差异在**帧读取方式**：
> - `LWLibavSource`：按需 seek + 解码，建索引（.lwi）只是记录关键帧位置以便跳转；seek 误差理论上靠解码弥补。
> - `LibavSMASHSource`：先对整文件**精确扫描建立逐帧映射**，seek 是按帧精确定位，牺牲首访建索引时间换后续精确与稳定。因此对"画面坏帧/花屏/跳帧"敏感的老片、BDMV 正片，SMAHS 版更稳。
> - 共同局限：无论哪个，**遇到容器里带交错编辑、坏时间戳**的怪源都可能出帧错位——那类场景（尤其逐帧对应不了）就该考虑 d2v 或先 demux。无脑信 seek 会踩坑，遇"对不上帧/卡顿"先怀疑 seek 而非滤镜。

### 3.2 `ffms2`（FFmpegSource2）

资历更老、当年的事实标准。现在新项目大多转向 lsmas，但碰到 lsmas 解不动的老容器时 ffms2 仍是备选。

| 函数 | 说明 |
|---|---|
| `ffms2.Source(source, track=, fpsnum=, fpsden=, threads=, seekmode=, width=, height=, resizer=, format=, alpha=, cachefile=)` | 主入口。`seekmode`（-1/0/1/2）控制精确度，默认 1；`resizer` 控制解码端缩放器；`width/height` 解码时缩放（一般不用） |
| `ffms2.Index(source, cachefile=, errorhandling=)` | 显式建索引 |

> **输入要求**：同 lsmas 的通用容器；但 ffms2 对某些新封装（如部分 mkv 内嵌字体/附属流）支持较弱，遇到"装不上/索引慢"可换 lsmas。
> **参数推荐**：`seekmode` 默认 1 即可；出帧数不对（重复/丢帧）时试 `seekmode=-1`（精确 seek，较慢）或 `fpsnum/fpsden` 显式覆盖。首次务必先跑 `ffms2.Index` 生成 cachefile（如 `"x.ffindex"`），否则每次 Source 都重新索引。

> **算法特点**：ffms2 是 libavformat 的早期封装，建立索引后在**随机帧访问上较轻量**。它和 lsmas 同样是"seek+解码"模型，但实现更老：时间戳处理不够健壮、对含附属流/可变帧率的现代容器支持一般，遇到会掉帧。现在的新项目首选 lsmas，ffms2 更多作为 lsmas 解不动时的兜底。

### 3.3 `d2v`（d2vsource）

MPEG2 / DVD（.d2v 或直接 mpeg2 视频）专用解码器，处理老 DVD 去隔行/IVTC 时是首选。

| 函数 | 说明 |
|---|---|
| `d2v.Source(input, threads=, rff=)` | 读 mpeg2。`rff=1` 尊重 repeat field flags（对 23.976 转 29.97 的 DVD 正片很关键） |

> **输入要求**：输入为 MPEG-2 流文件，或已用 DGIndex/D2V 生成的 `.d2v` 索引。DVD 处理主流场景。
> **参数推荐**：DVD 正片（23.976 藏于 29.97）务必 `rff=1` 以获得正确场重复信息，再去 `vivtc/tivtc` 做 IVTC；`threads` 给 1–4 即可，过多对老片无益。

> **算法特点**：d2vsource 尊重 **repeat-field（RFF）旗标**——DVD 上 23.976 视频以 29.97 存储时靠 RFF 补重复场，d2v 能把这些旗标如实还原成原始场序，是后面做 TFM/场匹配的前提。通用源插件多忽略 RFF 把 DVD 当成真 29.97，就会让 IVTC 无从下手。因此处理 DVD/MPEG2 首选 d2v，而非 lsmas/ffms2。

### 3.4 `mpls`

直接读蓝光光盘结构的 MPLS 播放列表（与 BDMV 目录配合）。

| 函数 | 说明 |
|---|---|
| `mpls.Read(bd_path, playlist, angle=)` | 从碟目录加载指定 playitem 为剪辑 |

> **输入要求**：`bd_path` 指到含 `BDMV/` 的光盘或完整备份目录；`playlist` 为 mpls 编号（如正片常用 00800）。需 m2ts 可被解码（内部仍走 lsmas/ffms 链）。
> **参数推荐**：正片优先选含正确章节的 playitem 编号，而非第 0 个；`angle` 仅在多角度盘时设。多数压制场景更常用 `lsmas.LibavSMASHSource` 配 `.mpls` 直接定位，`mpls.Read` 属"按 playlist 整体入"场景。

> **算法特点**：mpls 只是**蓝光播放表的解析器**，本身不解码画面——它读 MPLS 里 playitem 指向的 .m2ts 片段并按时间线拼成一个剪辑（mux 出正片 = 多个 m2ts 首尾衔接），交给下游解码。好处是能自动处理 MPLS 的分段（章节/角度）与无缝拼接，不用手动把多段 m2ts 接起来。要的是"从碟目录直接出正片"时可省很多手活。

### 3.5 `avs`

| 函数 | 说明 |
|---|---|
| `avs.LoadPlugin(path)` | 加载 AviSynth 的插件 DLL 到 VS（很多老滤镜只有 avs 版，靠这个桥接；但 API 兼容有限，仅部分滤镜可用） |

> **输入要求**：需系统装 AviSynth+ 运行库（avs 命名空间由其提供桥接）；传入的 DLL 必须为 avs 插件而非 vs 插件。
> **参数推荐**：仅当目标滤镜**确实没有 VS 原生版**时使用，例如个别老字幕/老降噪滤镜。能用 VS 版就不用 avs.LoadPlugin，两者生态不互通，混用易出兼容问题。

### 3.6 字幕类：`vsfm`、`xyvsf`、`sub`

三类都是渲染字幕的，接口不同、能力相似。实际按"你装的是哪个"选用，用哪个就统一用哪个。

**vsfm（VSFilterMod 系）**

| 函数 | 说明 |
|---|---|
| `vsfm.TextSubMod(clip, file, fps=, charset=, vfr=)` | SRT/ASS/SSA 字幕渲染；支持 libass 风格化、`.ass` 特效 |
| `vsfm.VobSub(clip, file)` | 图形字幕 idx/sub |

**xyvsf（xy-VSFilter）**

| 函数 | 说明 |
|---|---|
| `xyvsf.TextSub(clip, file, fps=, vfr=)` | 同上的老牌 TextSub |
| `xyvsf.VobSub(clip, file)` | VobSub |

**sub（内置 Subtext 系）**

| 函数 | 说明 |
|---|---|
| `sub.Subtitle(clip, text, ...)` | 纯文本字幕/水印，可配样式、时间区间 |
| `sub.TextFile(clip, file, charset=, scale=, ...)` | 从 SRT/ASS 文件读，可 `fontdir` |
| `sub.ImageFile(clip, file)` | 把图片当水印叠上去（如台标 logo） |

字幕**渲染时机**注意：ass 特效依赖逐帧渲染，应在降噪/放大**之后**、但在最终输出格式转换前渲染，否则逐帧特效和缩放冲突。

```python
out = core.vsfm.TextSubMod(up, r"C:\v\sub.ass", fps=24000/1001)
```

> **输入要求**：字幕渲染吃 clip 尺寸/帧率。ASS/SSA 特效（逐字、位移、缩放）依赖帧率与帧序，输入应已确定最终分辨率与帧率（在放大/降噪**之后**加字幕）。`vsfm.TextSubMod` 需要字体系统可用（libass 自行加载字体，缺字体可 `fontdir`）。
> **参数推荐**：
> - 帧率：ASS 动画/卡拉OK 特效请显式 `fps=`（源 fpsnum/fpsden，如 24000/1001），否则特效时间轴会随帧率漂移。
> - 纯 .srt 无特效：`fps` 可省略。
> - VobSub（idx/sub）：`.idx` 与 `.sub` 需同目录同名，路径给 `.idx`。
> - 用哪个就统一用哪个（`vsfm`/`xyvsf` 二选一），别混。

> **算法特点**：文本类字幕（.ass/.srt）由 **libass**（或 VSFilter 系）解析 + 按字形轮廓光栅化渲染；特效（卡拉OK、位移、淡入）靠**逐帧计算**事件位置与样式，因此需要确定帧率才能精确。`vsfm.TextSubMod` 与 `xyvsf.TextSub` 都源自 VSFilter，能力几乎等同，`vsfm` 多为较新的维护版；`sub` 是 VS 内置简化实现，适合固定样式。图形字幕（idx/sub）是**预渲染的位图**，无特效可言、也不依赖帧率——只是按时间贴图。渲染都是纯光栅化，几乎不伤画质，但**会略微影响 PSNR 类指标**（若在评测链里记得先去掉字幕）。

### 3.7 信息 / 预览辅助：`text`、`hist`

- `text`：已见 §1.3。`text.FrameProps(clip)` 打印 _DurationNum/_SAR 等属性，`text.ClipInfo` 一屏看全，都是排查"我输出到底对不对"的头号工具。
- `hist`：各种直方图预览

| 函数 | 说明 |
|---|---|
| `hist.Luma(clip)` | 亮度直方图（波形图） |
| `hist.Classic` | 经典 YUV 分量波形 |
| `hist.Color` / `hist.Color2` | 彩色直方图（vectorscope 风格） |
| `hist.Levels(clip, factor=)` | 加大对比看分布，调试动态范围用 |

> **输入要求**：输入为要观察的 clip；`hist` 输出的是"波形/直方图画面"（尺寸被重排），仅用于预览，不出片。
> **参数推荐**：`hist.Luma` 看亮度波形、`hist.Color` 看色度矢量（检查色偏/色带）最常用；调曝光看 `hist.Levels`。都是观测工具，正式脚本可临时接入后移除。

> **算法特点**：histogram 系列把每帧像素的分布**重排成可视化图**：亮度波形（纵轴亮度、横轴时间/空间）能看到曝光与裁剪，vectorscope（色度平面散点）能看到色偏与肤色轴，直方图（单帧亮度分布）用于看动态范围。不是"滤镜"而是分析可视化——对判断"该不该去色带/压高光/提暗部"给直觉依据。

### 3.8 质量度量：`vmaf`

| 函数 | 说明 |
|---|---|
| `vmaf.VMAF(reference, distorted, log_path=, feature=, model=, ...)` | 计算 VMAF 质量分（逐帧写日志）；`feature` 可含 `psnr`、`ssim`、`ciede`、`float_ssim` 等 |
| `vmaf.Metric(reference, distorted, feature=)` | 直接输出 `psnr` 等指标到帧属性 |
| `vmaf.CAMBI(clip, log_path=, window_size=, topk=)` | 感知带宽（对比度灵敏度），与 VMAF 同作者、互补 |

> 用法：处理前后各留一份 `log_path`，用工具解析平均分对比。压片参数调优时最常用。

> **输入要求**：`VMAF(reference, distorted)` 两输入需**同分辨率/同格式/同帧数**（帧率不强制校验，但不同帧率跑分没意义）；建议都先转成相同格式（8-bit 足够跑分，用 SDR）。CAMBI 只需单个 clip。
> **参数推荐**：跑 VMAF 时 `log_path` 给路径、`log_format=1`（xml）或 2（csv）；要附 PSNR/SSIM 在 `feature` 里加 `"psnr"`、`"float_ssim"`。对比脚本/编码参数时，用同帧段（`std.Trim`）各取 60–120s 代表性片段即可，不必全片。

> **算法特点**：VMAF 不是单像素差，而是 **融合多个局部特征**（对比/细节/时域）经过训练回归出的主观分；它比 PSNR/SSIM 更接近人眼，但**并非所有退化都敏感**（对模糊/块状敏感，对色度偏移、胶片颗粒重、超分幻觉不敏感）。`CAMBI` 则是专门检测**色带**的感知指标，与 VMAF 互补。指标是参考工具，不是目标——压片更应以肉眼 + 指标结合为准。

---


## 4. 反缩放（descaling）家族

反缩放（descaling）与普通缩放**方向相反**：普通缩放从原始低分辨率"插值放大"到高分辨率；descale 则是假设素材曾经过一次缩放（常见于 DVD→720p 或 480p 拉成 1080p 的 Web-DL），去**反推**出缩放前的清晰原始像素，从而拿到"近似无损"的源，再配合锐化/超分重建。压制里用于提升"被劣质放大过"的源。

> 概念提醒：`resize.Spline36(小, 大)` 是插值放大；`descale.Spline36(大, 小)` 是**反**——从大图估算出"如果这个小图被 Spline36 放大过"应是怎样的小图。两者配合可做"resample+descale"逆变换链。

### 4.1 `descale`（核心函数）

| 函数 | 作用 |
|---|---|
| `descale.Bicubic/Bilinear/Lanczos/Spline16/36/64/Point` | 用对应核做反缩放（DescaleXxx 别名等价：`Debicubic`=`Bicubic` 反、`Delanczos`=反 Lanczos…）。参数与普通 resize 类似，额外支持 `src_left/src_top/src_width/src_height`（源窗口）、`blur`、`post_conv`（反卷积后处理核） |
| `descale.ScaleCustom(src, width, height, custom_kernel, taps, ...)` | 用自定义核反缩放 |
| `descale.Decustom(src, ...)` | 反缩放 + 自定义核（同 ScaleCustom） |

典型用法：源是"先压小再放大"的劣质 Web 源时，先按放大前分辨率 descale 拿到近乎干净的原始清晰像素，再自己用高质量核（或 AI）重新放大。

```python
# 假设 1080p 源是 480p(720x480) 用 Spline36 放大上来的：先反缩回 720x480
refined = core.descale.Spline36(src, 720, 480)      # 720x480 原清晰底
# 再用你真正想要的放大方式重建（例如 AI 超分或高质量 lanczos）
out = core.resize.Spline36(refined, 1920, 1080)
```

> 注意：descale 只在"源确实是被已知核放大"时才真正有效；原片是原生高清时不要乱反缩（会引入错误重采样）。先用 `core.std.Crop`+`text.ClipInfo` 确认源结构。

> **输入要求**：输入为"被放大过的"源 clip（需先与放大前原始分辨率对齐）；`width/height` 填**放大前的原始分辨率**（如 480p 源在 1080p 容器里 → 720×480）。官方未强制 4:2:0，通用常规模即可；若你的源是 4:2:0 而目标宽高为奇，转 4:4:4 或对采样位置谨慎处理更稳。`src_left/src_top` 用于存在裁剪/位移的退化源微调对齐。
> **参数推荐**：
> - 反缩放核：**必须先猜测**原放大核。DVD/Web 最常见 Spline36 或 Lanczos，先用 `descale.Spline36` 试；若残影/振铃明显再换 `Bicubic(b,c)` 或 `Lanczos`。
> - `blur`：默认 1.0；源放大时带轻微模糊可给 0.5–1 反卷积恢复；不确定就保持默认 1.0。
> - 用法顺序：descale 反到原分辨率 →（可选轻降噪）→ 用你真正要的放大方式（AI/高质核）重建。

> **算法特点**：descale 的数学本质是**反卷积/求逆插值**：正缩放 `小 = 核(大)` 是已知核把原始像素加权求和成新像素；反缩放则是给定核和缩放比，把"哪些原始像素能生成这一帧"当成线性方程组求解。因为输出像素数 < 未知数（欠定），结果依赖正则与核假设——**核猜对了**，能得到近乎无损的原生清晰底；**核猜错了**，会出现振铃、鬼影、错误重采样纹理。所以它工作的前提是"源确实由已知核放大过"（Web-DL/DVD upscale），对原生高清源无效且有害。真正的价值：把降质源的放大过程"逆向"后，用一个更聪明的重建（AI 超分）替代原来的廉价放大。

### 4.2 `dpid`（DPID 反缩放 / 去隔行源重建）

| 函数 | 说明 |
|---|---|
| `dpid.Dpid(clip, width=, height=, lambda=, src_left=, src_top=, ...)` | DPID（direct-pixel-inverse）反缩放，能处理"先降采样再上采样"的级联退化，反卷积式恢复清晰度；`lambda` 正则强度 |
| `dpid.DpidRaw` | 对两个不同退化源分别给参数做反缩放，输出可再 merge 互补 |

DPID 相比 descale 的优势是**能建模更复杂的退化**（例如隔行源 + 缩放），因此常用于 DVD 老动画/实拍"拉回原生清晰度"。输出分辨率给目标放大前的原始清晰分辨率，再由 AI/缩放重建。

```python
# DVD 480p 隔行 → 反缩到源原清晰底再重建（示意）
ref = core.dpid.Dpid(src, width=720, height=480, lambda=[1.0, 1.0])
```

> 选择建议：已知单次均匀核放大 → `descale`；源有隔行/更复杂的降质 → `dpid`。二者都假设源"确实被降质过"。

> **输入要求**：同 descale，需明确"放大前的原始分辨率/退化方式"；DPID 接受隔行/多层退化，但对宽高与采样格式同样要求与目标对齐（建议先转 4:4:4 或高位的 GRAY/YUV 以减少色度混叠误差）。
> **参数推荐**：`lambda` 是正则强度，越大越"平滑/抗噪"，越小越"追细节/易放大噪声"。实拍给 `[0.5~2, 0.5~2]`（Y、UV 可不同），动画可略高。与 descale 一样，正确估计退化核与原始分辨率是关键，参数微调是次要的；新手先从 `descale` 入手，确认源确需 DPID 再上。

> **算法特点**：DPID（Direct Pixel Inverse Decimation）比普通 descale 更"暴力直接"：它不假设单一均匀核，而是把**逐像素的反向映射 + 级联降质**（隔行、多次缩放、裁剪偏移）都建模进方程组直接解，再用 `lambda` 正则稳定。能处理 descale 搞不定的隔行源、多步退化；但因为求解的未知数更多，正则与初始核的依赖更强，参数敏感、也更慢。定位：descale 的加强版，处理"明显被反复加工过"的源。

---



## 5. 降噪 / 去块 / 去色带

> 降噪顺序经验：先去隔行/IVTC，再动补偿降噪；动补偿降噪要在高位深（≥16bit）下做。见 §11 完整链。

### 5.1 `bm3d` / `bm3dcpu` / `bm3dcuda` / `bm3dcuda_rtc`（BM3D 家族）

BM3D 是**目前质量天花板级的降噪**，代价是慢。本机同时装了四套实现，**接口与格式要求不同，别混用**：

- **`bm3d`（经典 CPU，mawen 系）**：走 `RGB2OPP/VBasic/VFinal/OPP2RGB` 流程，内部 OPP（亮度+色差）域处理，8–16 bit 整数可用。
- **`bm3dcpu` / `bm3dcuda` / `bm3dcuda_rtc`（WolframRhodium 系）**：单步 `BM3D`/`BM3Dv2`，**强制 32-bit float 输入**；`chroma=True` 时输入须为 **YUV444PS**（本机 DLL 硬性约束：`only constant format 32 bit float input supported` / `clip format must be YUV444 when "chroma" is true`）。这是与经典版最大的不同。

经典 CPU 流程（bm3d）两阶段：

1. `RGB2OPP(clip)`：RGB → 亮度+两色差（OPP，Opponent color）域，降噪在 OPP 做更准。
2. `VBasic(src, sigma=, radius=1, ps_num=2, ps_range=1, block_size=8, ...)`：第一阶段，产生"基础干净图"。
3. `VFinal(src, ref=basic, ...)`：第二阶段，用参考精修。
4. `VAggregate(basic, radius=)`：把多幅结果按重叠权重聚合（`radius` 对应 sigma 半径，用于递归/多参考模式）。
5. `OPP2RGB(...)`：转回 RGB。

- 变量版 `VBasic/VFinal/VAggregate` 支持多参考（多 sigma、去时域递归），能去时间性噪声，但**接口复杂、速度慢**。
- CPU 流程 1080p 单帧往往要数秒级起步，是压片管线里最吃时间的一环。

```python
# bm3d CPU 简易串联（sigma=估计的噪声强度，越高越狠）
b = core.bm3d.Basic(src, sigma=2.0)
f = core.bm3d.Final(src, ref=b, sigma=2.0)
```

**bm3dcpu** 提供 `bm3dcpu.BM3D(clip, sigma=, block_step=, bm_range=, radius=, ps_num=, ps_range=, chroma=)` ——单步完成（内部已做两阶段）。**必须喂 32-bit float**：

```python
# bm3dcpu：先升 32-bit float（chroma=True 时源要 YUV444）
srcf = core.fmtc.bitdepth(src, bits=32, flt=True)
den = core.bm3dcpu.BM3D(srcf, sigma=[3.0, 1.5], block_step=8, bm_range=6, radius=0,
                        ps_num=2, ps_range=1, chroma=True)   # 若要处理色度需 444PS 输入
# 降回输出位深
out = core.fmtc.bitdepth(den, bits=16)
```

**bm3dcuda**：参数几乎同 bm3dcpu（`device_id=` 选 GPU，N 卡专用），格式约束相同（float、chroma 需 YUV444）。实测 1080p 单 sigma 可做到几十~上百 fps。**注意 bm3dcuda 和 bm3dcuda_rtc 名字里都有 cuda，rtc 版是 runtime-compile（JIT）内核，速度接近但启动更耗。**

参数共同速记（BM3D 系）：
- `sigma`：噪声强度，通常 0.5~6。给数组代表"对多平面分别给"。
- `block_size/block_step`：块大小 / 步进（8/8 精细慢，16/16 快一点）。
- `bm_range`：块匹配搜索半径。
- `radius`：时间维度（0 只空间；1 向前向后各一帧，去时间噪声更彻底但会糊细节）。
- `ps_num/ps_range`：预滤波阶段参数，数值越多越精细。

> 一句话选型：**想绝对干净用 bm3d，想能用（接近实时）且 N 卡用 bm3dcuda**。源噪点特别大再上 bm3d VBasic/VFinal。

> **输入要求**：**因实现而异**——经典 `bm3d`（Basic/VFinal）走 OPP，输入 GRAY/YUV/RGB 整数 8–16 bit 可用；`bm3dcpu/bm3dcuda/bm3dcuda_rtc` 的 `BM3D` **只接受 32-bit float**，且 `chroma=True` 时必须 YUV444（GRAY 或 YUV444PS，非 444 会报 `must be YUV444 when chroma is true`）。噪声应为**加性高斯白噪声**近似（传感器/编码噪基本符合）。
> **参数推荐**：
> - `sigma`：最重要。噪声强估法：对**静止画面**拍两帧做差看标准差，或按经验；8-bit 源通常 1–8，动画偏低（0.5–2），实拍颗粒偏高（2–6）。给数组可分别控 Y/UV。
> - `radius`：0 = 纯空间（推荐起步）；需去时域闪烁再 1–2（去噪更彻底，代价糊细节与拖影）。
> - `block_step`：8 精细，16 快且略糊。
> - `bm_range`：越大越能找到相似块但也越慢，6–12 常用。
> - 实战建议：先小 `sigma`（欠降噪）起调，肉眼看是否达标，别一上来拉满糊掉细节。

> **算法特点**：BM3D 的"天花板质量"来自**块匹配 + 协同 3D 滤波**两个创新：
> 1. 把每个小块的**相似块**（在同一帧别处 + 前后帧）收集成一个 3D 组（block matching，这步很像运动补偿但更灵活）；
> 2. 对整组做 **3D 变换（DCT/Wiener）→ 频域收缩噪声 → 逆变换**，把噪声从"每个块独立滤"变成"一组相似块一起滤"，使信号在 3D 频谱上更集中、能被更有效地阈值。
> 它强在：把"相似结构的重复信息"都用上，去噪同时**最大限度保住真实纹理**，远胜逐像素/逐块独立法。代价是计算量大（尤其块匹配），所以快不了。局限：对**非高斯噪声**（色带、脉冲、压缩伪影）效果打折，那是 f3kdb/deblock 的活；且 `sigma` 估不准会欠滤或糊。
> **bm3d / bm3dcpu / bm3dcuda**：同一算法三套执行层——bm3d(CPU 原版)、bm3dcpu(SIMD/FFTW 加速)、bm3dcuda(N 卡 GPU)。结果质量一致，只是速度/依赖不同；rtc 变体是 JIT 编译 CUDA 核。GPU 够快可做时空（radius>0）不心疼，CPU 通常只敢做空间。

### 5.2 `knlm`（KNLMeansCL，GPU）

非局部均值降噪，去细节保留不错、参数直观。OpenCL 实现（N/A/I 卡皆可跑），快于 bm3d CPU。

| 参数 | 说明（含本机 DLL 硬性范围） |
|---|---|
| `d` | **时间半径**：向前向后各取 d 帧（时间维总长 2d+1）。本机须 `d>=0`（d=0 即纯空间） |
| `a` | 块半径。本机须 `a>=1`（一般 1~2，a=2 更均匀） |
| `s` | 步进。本机范围 **0–8**（0=自动，越小越细越慢；1 最细） |
| `h` | **主要力度**，本机须 `h>0`（越大越干净越糊，经验 1~4 起步试） |
| `channels` | 处理通道 `"YUV"/"Y"/"UV"/"RGB"`；YUV 且 channels=YUV 时须 **YV24(4:4:4)** 格式 |
| `device_type` | `"cpu"/"gpu"/"accelerator"/"auto"`（本机合法值）；`device_id>=0` |

```python
# d>0 = 时域（前后各 d 帧）+ 空域；d=0 才纯空间。例：去噪取前后各 2 帧
den = core.knlm.KNLMeansCL(src, d=2, a=2, s=1, h=2.0, device_type="GPU", device_id=0)
```

> **输入要求**：planar YUV、GRAY，以及 **RGB（RGB32/RGB64）都支持**（本机 DLL：`planar YUV, RGB32 and RGB64 are supported`）；8–16 bit 整数与 32-bit float 均可。需可用 OpenCL 设备（CPU/GPU 皆可）。GRAY8 时 `channels` 只能是 "Y"。
> **参数推荐**：想**时域+空域**一起去噪（推荐，压制常用）用 `d=2~3, a=2, s=1`；只想纯空间快速去噪 `d=0`。`h` 是主控（大致对应噪声强度），经验 8-bit 源 1–4 起步试。`s=2+` 提速但略糊。GPU 优先 `device_type="GPU"`。

> **算法特点**：NL-Means（非局部均值）的思路是**"同款像素不止一处"**：对每个像素，在搜索窗内找**外观相似的小块**，把相似块中心像素按相似度（权重）加权平均作为输出。与 BM3D 同属"非局部"，但 BM3D 走频域协同、NL-Means 直接在空间加权，纹理保留不如 BM3D 精细，但实现简单、参数直观、质量仍属上乘。`h` 就是这个"相似度→权重"的温度参数。**与很多人的直觉不同，`d` 不是"空域窗"而是时域半径**——KNLMeansCL 支持把前后帧也纳入"相似块"搜索做 3D 去噪（时间越长越干净但也越慢、易留拖影）。对高斯噪声效果好，对"重复纹理"能显著受益。局限：搜索窗大时慢、对非重复噪声（纯随机颗粒大）提升有限。

### 5.3 `dfttest` / `dfttest2_*`（DFTTest）

基于块傅里叶变换（Fourier domain）的降噪，去彩色噪声、细小颗粒效果好，但**极慢**。适合源噪声是随机色带/传感器噪的素材，不适合大噪点。

- `dfttest.DFTTest(clip, sigma=, tbsize=, tmode=, ...)`：老版。`sigma` 强度；`tbsize=1` 关闭时间维只做空间。
- 你的机器还装了 `dfttest2_cuda` / `dfttest2_nvrtc`：新版 GPU 实现（直接给 `kernel` 半径），比 CPU 版快几个数量级。

> **输入要求**：高位深（≥16bit）效果较好，时间维滤波需多帧连续素材；`dfttest` CPU 极慢，1080p 全片不现实，GPU 版（dfttest2_cuda / nvrtc）才适合实际用。
> **参数推荐**：CPU 版 `sigma` 经验 1–5（低噪 2 附近），`tbsize=1`（只空间）最快，开时间维成倍变慢。GPU 版 `kernel` 是"频域截止半径"式参数（数值越大保留越多细节/去噪越弱），从默认附近微调即可。DFTTest 擅长去**周期噪声/细颗粒**，不适合强噪点源——若只求通用去噪，先试 knlm / mv degrain。

> **算法特点**：dfttest 走 **频域收缩（frequency-domain shrinkage）**：把块做 2D/3D FFT，图像的自然结构在频域集中在低频，随机噪声均匀铺满全频段——于是可以给每个频率乘一个 0–1 之间的衰减因子，压掉"只有噪声贡献"的高频。`sigma` 越大衰减越狠。因为按频率处理，它对**特定频率的噪声（网纹、条纹、周期颗粒）特别有效**，对随机白噪声也有效，但逐块 FFT 开销巨大（CPU 版慢到难实用）。局限：频域"一刀切"容易把真正的细纹理（在频域也是中高频）一起削弱，造成"去噪但糊细节"的观感；适合精细调参去周期噪，不适合暴力通用去噪。

### 5.4 `fft3dfilter`（FFT3DFilter）

也是频域降噪，但用法偏"轻中度降噪 + 可选去网纹/去隔行残留"。CPU 但做了优化，中等速度。

| 常用参数 | 说明 |
|---|---|
| `sigma` | 强度（0~~10，常用 1~4） |
| `sigma2/3/4` | 第二~四平面的 sigma |
| `sharpen` | 锐化成分（负值=降噪同时轻微锐，防过度糊） |
| `degrid` | 去块格栅伪影 |
| `dehalo` | 去亮边光晕 |
| `interlaced` | 素材本身隔行的场合 |

> **输入要求**：高位深优先；`interlaced` 需明确告诉它源是否隔行（逐行源别设 1，否则出梳状）。时间滤波需连续帧。
> **参数推荐**：`sigma` 1–4（轻中度）；`sigma2/3/4` 让 UV 平面低于 Y（如 `sigma=[2,1,1]`）可防色度糊。想降噪+略锐防糊可加 `sharpen=0.3~0.6`；去光晕 `dehalo=0.5~1`。CPU 中等速度，可接受用于局部。多数情况下它是"够用但非最优"，追求质量仍推 bm3d/knlm。

> **算法特点**：FFT3DFilter 也是**频域收缩**（同 dfttest 一族），但优化了实现、能跑 3D（空间+时间）FFT，且带几个实用扩展：`sharpen`（频域里"抬高频"顺带补偿降噪造成的软化）、`degrid`（针对块效应在频谱里的规律分布）、`dehalo`（削弱边缘旁的亮/暗边）。它比 dfttest 更快、功能更全，因此更常被当"轻中度降噪 + 顺带修伪影"用。局限：本质仍是频率均匀衰减，纹理损失风险在；作为"够用"级工具可以，追求质量换 bm3d/knlm。

### 5.5 `hqdn3d` / `ttmpsm` / `median` / `flux` / `cas`

轻量级"日常去噪"和细节保留系：

**hqdn3d（Hqdn3d）**：低成本的时域+空域降噪，漫画/实拍"去一点灰噪、保线条"常用。**本机硬性约束**：输入必须是 **8-bit 且非 RGB**（DLL：`must be 8 bit, not RGB`）；四个参数 `lum_spac/chrom_spac/lum_tmp/chrom_tmp` 范围都是 **0–255**（默认 lum_spac=4.0）。数值越大降噪越强。

```python
# 注意：hqdn3d 只收 8bit 非 RGB；若源是 16bit 请先降到 8bit 或用别的降噪器
den = core.hqdn3d.Hqdn3d(src8, 4, 3, 4, 3)   # lum_spac=4, chrom_spac=3, lum_tmp=4, chrom_tmp=3 (0~255)
```

**ttmpsm（TTempSmooth）**：时间平滑，擅长去**时域闪烁/时间噪声**（如低码率源在暗处跳的噪）。参数 `maxr`（搜索半径）、`thresh`、`mdiff`。开大了会拖影。

**median**：`median.TemporalMedian(clip, radius=1)` 时间中值（radius 范围 **1–12**）；`median.Median(clipA, clipB, ...)` 是**多剪辑中值**（对 3–25 路输入逐像素取中值，用于多源融合），**不是**单剪辑的空间滤波——本插件不提供空间"中值模糊"。两者都收 8–16 bit 整数或 32-bit float。

```python
den = core.median.TemporalMedian(src, radius=1)   # 沿 3 帧取时间中值（去坏帧/闪烁）
```

**flux（FluxSmooth 系）**：`flux.SmoothT` 时间平滑 / `flux.SmoothST` 时空平滑，很便宜的去闪烁/降噪。参数名是 **`temporal_threshold` / `spatial_threshold`**（默认 7）；仅支持 8–16 bit 整数。

**cas（AMD CAS）**：Contrast Adaptive Sharpening，锐化（不是降噪）。`cas.CAS(clip, sharpness=)`，`sharpness` 范围 **0–1**（默认 0.5）。8–16 bit 整数与 32-bit float 均可。**配合轻度降噪链路尾端**用，比直接 sharpen 更注意保纹理。

> **输入要求**（本组轻量工具）：除上述各自硬性限制（hqdn3d 仅 8bit 非 RGB；flux 仅 8–16 int）外无苛刻前提。锐化工具 `cas` 应在**缩放/降噪完成后**使用（它按最终像素锐化）。
> **参数推荐**：
> - `hqdn3d`：四个值取 **0–255**（8bit 语义），日常轻降噪 `lum_spac=3~4, chrom_spac=2~3, lum_tmp=3~4, chrom_tmp=2~3` 即可，几乎不糊。
> - `ttmpsm`：`maxr=2~3`（时间半径），`thresh` 低一些只滤小变化、防止大运动拖影。优先滤"时间闪烁"而非强噪。
> - `median.TemporalMedian`：`radius=1`（3 帧中值）起步；专去坏点/脉冲噪很有效，别开大。
> - `flux`：默认 `temporal_threshold=7` 附近即可，当"预处理去小噪"。
> - `cas`：`sharpness` 默认 0.5，常用 0.4~0.7；过大容易出白边（halo）。想更克制可加个边缘 mask 只在细节区锐。

> **算法特点**（一组"轻量原语"，机制各异）：
> - **hqdn3d**：把画面当"亮度和色度两路"分别做**自适应时间平滑 + 少量空间平滑**；只在"时域/空间变化低于阈值"处平滑（保护边缘和运动），所以不糊细节。实现极简、CPU 极快，是"默认轻度去噪"的稳妥选择。
> - **ttmpsm（TTempSmooth）**：**纯时域**自适应平滑——对每个像素沿时间做加权平均，权重视该点在时间上是否"真的在动"（运动大则降低邻帧权重、保留本帧），因此能去时间闪烁/暗部噪而不产生明显拖影。局限：大运动区域几乎不滤（保持原样），对空间噪无效。
> - **median（中值）**：**排序滤波**。TemporalMedian 沿帧取中值（去单帧异常/坏帧线）；`Median` 则是多剪辑逐像素中值（合成多路输入）。中值比均值更"保住边缘与锐度"，但速度偏慢、对高斯细噪无效。
> - **flux（FluxSmooth）**：轻量时空平滑，阈值化避免糊边缘，CPU 开销极小，适合当"每帧必跑的 cheap 去闪烁"。
> - **cas（AMD CAS）**：**对比度自适应锐化**——在低对比区域基本不动、高对比边缘适度增强，靠"局部对比度调制"抑制了传统锐化的光边（halo），故比 unsharp 更"保纹理不脏"。本质仍是高通增强，量要克制。
> 本组共性是**快、参数少、适合作轻处理或前/后置**，不作主降噪。

### 5.6 去色带 `neo_f3kdb`（F3kdb 重写版）

去色带（banding）的头号工具：把平滑色阶处的 8bit 量化台阶打散成抖动噪声。

| 常用参数 | 说明（含官方默认/范围） |
|---|---|
| `range` | 检测色带邻域半径，默认 **15**（并非 16~20） |
| `y/cb/cr` | 各平面阈值，默认**都是 64**；值越小越不触发 |
| `grainy/grainc` | **补偿性加噪**，默认 64，可 0–较大值；越大重抖越明显 |
| `sample_mode` | 采样模式 **1 或 2**（1=2 参考像素、2=4 参考像素），默认 2 |
| `dither_algo` | 抖动算法 **1/2/3**（**1=无抖动、2=ordered、3=Floyd–Steinberg**），默认 3；输出 16bit 时忽略 |
| `preset` | 预设 `"low"/"medium"/"high"/"veryhigh"`（在其它参数前套用，可再覆盖） |

```python
# 官方默认即 range=15, y/cb/cr/grainy/grainc=64, sample_mode=2, dither_algo=3
deb = core.neo_f3kdb.Deband(src)                 # 全默认
# 更常见的"只去色带、留干净渐变"写法：
deb = core.neo_f3kdb.Deband(src, range=15, y=24, cb=24, cr=24, grainy=0, grainc=0, sample_mode=2)
```

> **重点：** 去色带不是"调大就好"。`y/cb/cr` 阈值别设太高，否则会伤真实渐变；觉得还有色带就降 `range`、别死升阈值。若用 **high bit depth 输出**（10bit 压制），通常几乎不需要 f3kdb——10bit 本身把色带消除了大半。

> **输入要求**：**仅 planar YUV**（8–16 bit 整数；DLL 也确认 `IsFamilyYUV` 与整数约束）。逐行源最佳；隔行源需先去隔行，否则时空阈值会误判。非 YUV（GRAY/RGB）不可直接喂。
> **参数推荐**：
> - 官方默认 `grainy=grainc=64` 会**主动加回较强颗粒**（那是给"去掉色带后仍保留胶片感"的默认）。想要"纯去色带不留噪"就把 `grainy=grainc=0`（靠后期 `grain.Add` 回质感）。
> - 阈值：想更保守（只处理明显的带）`y=24~48` 起步；色度带明显可让 `cb/cr` 略高。`range` 15 附近起调。
> - 高频细节区域 f3kdb 不触发（阈值内保护），所以阈值别乱拉满。

> **算法特点**：f3kdb 处理的是 **8-bit 量化产生的色带**——平滑渐变处相邻灰阶间隔过大，出现可见台阶。它检测局部"本应是缓变却被量化成几段"的区域，然后给这些区域**人为注入精细的抖动脉冲（dither）噪声**，把台阶重新"打散"成人眼不易察觉的微噪。关键点：
> - 它的"去色带"本质是**用噪声换渐变平滑**，不是真正重建更多灰阶——所以 `grainy/grainc`（补偿噪声量）是核心，设 0 只是"把带抹平留空"，会显得干净但也可能发闷。
> - 阈值逻辑：像素值与周围差异小于某值才算"缓变色带区"，差异大的真实边缘/纹理被保护不触发，因此调高阈值等于"更多区域被判为色带"→ 可能误伤渐变细节。
> - 定位：**8bit→8bit** 或 **8bit 转更高位**时用；若已是 10bit 源/10bit 输出，色带问题大幅减少，常常可省去。它不是万能去噪器，只管色带。

### 5.7 去块 `deblock` / `dctf`

**deblock（老 Deblock）**：去压缩块效应（低码率压缩的块状马赛克）。

| 参数 | 说明 |
|---|---|
| `quant` | 大致对应量化步长（QP 估算，越高去得越狠） |
| `aoffset` / `boffset` | 去块强弱调节 |

**dctf（DCTFilter）**：按 DCT 系数过滤——可以把"特定频率的块效应/网纹"压掉。给 `factors`（每频段权重）精确控制。

> **输入要求**：两者都以"有块状压缩伪影的源"为对象；对已非常干净/高码率源无效且可能伤细节。去块应在**放大/缩放前**做（块效应是源空间频率问题）。
> **参数推荐**：
> - `deblock`：`quant` 范围 0–60（默认 25），低码率源给 25–40 附近试，超高码率可忽略去块；`aoffset/boffset` 默认 0，必要时小幅调。
> - `dctf`：`factors` **必须恰好 8 个 float**（本机 DLL：`number of factors must be 8`），每个取值 0.0–1.0（低频建议 1.0 保留，越高频可越低）。想只压网格（去隔行格/网纹）就把对应频率 band 调低，不要整体砍高频（会糊）。支持 8–16 bit 整数与 32-bit float。
> - 优先考虑**源头改善**：去块治标，很多"块"其实来自过度压缩，配合轻降噪（如 hqdn3d）效果更好，别把 deblock 开满。

> **算法特点**：
> - **deblock**：去块靠检测**块边界处的"阶梯跳变"**（压缩让 8×8 块边界产生亮度台阶），对边界两侧像素做**平滑/裁剪**，量由 `quant` 控制。它是逐块边界处理的，块内部不管，因此能去块效应但"马赛克内部的振铃/污点"管不到。
> - **dctf（DCTFilter）**：在块内做 **DCT → 按频率给系数乘 0–1 权重 → 逆变换**。因为块效应/网纹在频域对应特定高频 band，可以"只压某个频率带"，比 deblock 更精准、能处理"块内也脏"的情况；代价是参数抽象（要懂频域），调错会把高频细节一起削掉。
> - 共局限：二者都假设"噪声来自压缩的块/频域伪影"，对真实胶片噪点无效；且**强力去块会糊细节**，应放在缩放前、配合轻降噪，而不是当万能滤波。

### 5.8 其它去噪小件

- `rgvs`（RemoveGrain / Repair）见 §9；`sangnom`（抗锯齿）见 §7。
- `Bilateral / Gaussian`：双边滤波（bilateral）保留边缘去噪；GPU 版在 `bilateralgpu`。**注意名字里带 gpu 的只有双边，不是高斯。** bilateral 适合做"边缘感知的轻度平滑"（处理 chroma 或做卡通感的过渡）。

> **算法特点（bilateral）**：高斯模糊是"空间越近权重越大"的固定低通，会连边缘一起糊掉；双边滤波在此基础上**再加一维权重**——"灰度越接近权重越大"，于是位于边缘两侧（灰度差异大）的像素互不平均，边缘被保留、平坦区被平滑。适合去"同色区域的细小噪点/色度噪"而不糊轮廓。局限：它没有"重复结构"的概念，无法像 NL-Means/BM3D 那样利用远处相似块，去噪强度有限；对大幅噪声不敌非局部法。
> **本机 bilateral 硬性要求**：只接受 **8–16 bit 整数**（`Bilateral` 与 `Gaussian` 皆然，DLL：`Only 8-16 bit int formats supported`）；`sigmaS`/`sigmaR` 为非负 float（sigmaR 作用于 [0,1] 归一化值域，默认 3.0/0.02）；有 `ref` 时需与输入同尺寸同格式。GPU 版 `bilateralgpu` 支持 8/16 int 或 32-bit float，注意它是 **CUDA（N 卡专用）** 实现，不是 OpenCL。

### 5.9 小结·怎么选降噪

| 目标 | 推荐 |
|---|---|
| 快速日常轻度降噪 | hqdn3d、flux |
| 想细节保留好、能等 | knlm（GPU 更快）、dfttest |
| 追求极限干净 | bm3d（CPU）/ bm3dcuda（N卡） |
| 专去时域闪烁/拖影噪 | ttmpsm、median.TemporalMedian |
| 去 8bit 色带 | neo_f3kdb |
| 去压缩块效应 | deblock、dctf |

---


## 6. 动补偿家族 mvtools / mvutensils

运动补偿（motion-compensation）是压制里**大杀器级**的技术：先用块匹配估计每帧的运动向量，再沿向量把邻帧"搬"过来做时域滤波（降噪、补帧、稳像）。你的机器同时装了 **mv（MVTools，API3 老版）** 和 **mvu（MVUtensils，API4 新版）**，两套各自独立、不能混用。

**共同核心管线**（两套一致）：
```
src ── Super（建多尺度参考金字塔，带 padding/pel）
   └── Analyse（块匹配，出前向/后向 motion vector）
          ├── Degrain1/2/3…（沿向量加权平均邻帧 → 时域降噪）
          ├── Compensate / FlowInter（把帧按向量扭曲）
          └── FlowFPS / DepanStabilise（变速 / 稳像）
```

**mv（老版）关键函数**：

| 函数 | 参数要点 |
|---|---|
| `Super(clip, pel=2, levels=0, sharp=2, rfilter=2, hpad=, vpad=)` | 建金字塔。`pel`（1=整像素/2=半/4=四分之一，越大越准越慢，默认 2）；`levels` 细化层级；`hpad/vpad` 一般由 Analyse 按 blksize 自动给 |
| `Analyse(super, blksize=8, isb=, delta=1, search=4, overlap=, dct=, ...)` | 出向量。`isb`：False 前向 / True 后向（**Degrain 需要一对正反向量**）；`search` **VS 版 0–7**：0=OneTime、1=NStep、2=Log、3=Exhaustive、**4=Hex2(默认)**、**5=UnevenMultiHex(UMH)**、6/7=Horiz/Vert——**注意 VS 版 UMH 是 5 不是 3**；`overlap`（越大补偿越平滑）、`dct=1` 有时更稳 |
| `Degrain1/2/3(clip, super, mvbw, mvfw, thsad=, limit=)` | 一/二/三参考降噪（参考数=用的帧数）；`thsad` 阈值越高降噪越狠（**默认 400**）；`limit` 限制每次最大改动防糊 |
| `Compensate` / `Flow` / `FlowInter` | 按向量做运动补偿（补帧、变速中间帧） |
| `FlowFPS(clip, super, mvbw, mvfw, num=, den=, blend=)` | 运动补偿帧率转换（如 24→60） |
| `DepanEstimate/Stabilise/Compensate` | 全局运动估计 + 稳像 |
| `Recalculate` | 在粗向量基础上用小块细化 |
| `SCDetection` | 场景切换检测（输出帧属性给降噪用，避免跨场景滤波） |

```python
# mv 的经典 Degrain3（SMDegrain 手写版的核心片段）
src = core.fmtc.bitdepth(src, bits=16)                       # 动补偿在 16bit 做
super = core.mv.Super(src, pel=2, sharp=1)                   # 高精度
mvbw1 = core.mv.Analyse(super, isb=True, delta=1, search=3)  # 后向1
mvfw1 = core.mv.Analyse(super, isb=False, delta=1, search=3) # 前向1
mvbw2 = core.mv.Analyse(super, isb=True, delta=2, search=3)  # 后向2
mvfw2 = core.mv.Analyse(super, isb=False, delta=2, search=3)
mvbw3 = core.mv.Analyse(super, isb=True, delta=3, search=3)  # 后向3
mvfw3 = core.mv.Analyse(super, isb=False, delta=3, search=3)
den = core.mv.Degrain3(src, super, mvbw1, mvfw1, mvbw2, mvfw2, mvbw3, mvfw3, thsad=150)
```

> **输入要求（mv 老版）**：
> - 动补偿建议在 **16-bit** 做：`src = core.fmtc.bitdepth(src, bits=16)`（8-bit 直接跑也可，但深度小、易出量化台阶）。
> - 需连续帧源（首尾帧会自动少参考）；隔行源**先做去隔行/IVTC**再喂，否则场序混乱导致向量错误。
> - `Super` 的 clip 应与降噪 clip 同格式/尺寸；`Analyse` 用的 `super` 必须是同一 Super 输出。
> **参数推荐（mv 老版）**：
> - `Super`：`pel=2`（好效果/速度均衡）或 1（更快略粗）；`sharp=1` 默认；rfilter 保持默认。
> - `Analyse`：`blksize=8`（细致）或 16（快）；`search` 选搜索策略（默认 4=Hex2；要 UMH 用 **5**，别把 VS 版 search=3(Exhaustive) 当 UMH）；`delta` 前向与后向各建（1、2、3…代表参考距离帧）。动画想省时可用 `blksize=16`。
> - `Degrain`：`thsad` 越大降噪越强也越可能糊，**动画 100–200、实拍 150–400 起步试**；`limit=255`（8bit 语义下的上限，可不设）防极端糊。降噪宁轻勿重——过度会把线条/纹理抹平。
> - 要 3–5 帧邻域就把前/后 `delta` 各做 1..N 并组进对应 DegrainN，但要理解每多一帧都更耗时、且远帧对"细节区域"贡献下降。

> **算法特点（动补偿家族的原理）**：这是理解整套 mvtools 的关键——
> - **Super**：把画面 padding +（可选 `pel`>1 时）插值出亚像素平面 + 建**多分辨率金字塔**。金字塔让运动估计先粗后细（大运动在低分辨率层先对准，再逐层细化），`pel` 提供 1/2、1/4 像素精度。向量精确度受 pel 上限约束。
> - **Analyse**：**块匹配（block matching）**——对每个块在邻帧搜索窗里找"最相似的块"，用 SAD（绝对差和）度量相似度，SAD 最小者即运动向量。`search` 选搜索策略（菱形/六边形/UMH…），是"全搜太慢、剪枝快但有陷阱"的折衷。场景切换处无匹配会得坏向量，靠 `thscd1/thscd2` 检测后跳过。
> - **Degrain（时域滤波）**：把当前块与按向量搬来的邻帧块**加权平均**，权重是 1/SAD——越像的帧信得越多。多参考（Degrain3）＝取前后多帧，时间越长去噪越彻底但对细节/运动的平均风险也越大。`thsad` 直接控制"SAD 高到多大就几乎不采信该帧"。
> - **本质权衡**：动补偿的假设是"相邻帧是同一场景的平移/近似"——对**平移运动**极准，对**缩放/旋转/遮挡/景深变化**（向量估计不准处）会出现鬼影或去噪失效，这是 Degrain 处理动作复杂镜头易糊/出伪影的根因。压制实践因此讲究"轻度 + 只在干净区域信任向量"。

**mvu（新版）**：由 Dogway 在 API4 下重写，命名空间 `mvu`。API 与 mv **精神兼容但非逐字一致**，关键差异：

| mv（MVTools） | mvu（MVUtensils） |
|---|---|
| `Super(clip, pel=2)`，blksize 隐含 | `Super(clip, blksize=8, overlap=4, pel=2)` —— **blksize / overlap 必填** |
| `hpad / vpad` | `pad=[h, v]` |
| `levels` | `onelevel=True/False`（默认多层级；只给 Degrain 用可 True 省内存） |
| `blksize / blksizev`、`overlap / overlapv` | `blksize=[h,v]`、`overlap=[h,v]`（单值双轴同用） |
| `Analyse(isb=False, delta=1)`=前向 | `Analyse(delta=-1)`=前向，**负=前向 / 正=后向**（无 isb 参数） |
| `dct=0/5` | `satd=False/True` |
| `lambda / global`（撞关键字） | `mvlambda / globalmv` |
| `Degrain1(clip, super, mvbw, mvfw)` | `Degrain(clip, super, [bw1, fw1, ...])` —— 向量以**列表**传，顺序 `[bw, fw, bw, fw, …]` |
| `thsad + thsadc` | `thsad=[luma, chroma]` |
| `limit + limitc` | `limit=[luma, chroma]`（float；`inf`=不限） |
| `Flow(mode)`、`BlockFPS`、`Finest`、`truemotion` 等 | 已移除 |
| `Mask(kind=0/1/2)` | 拆成 `VectorLengthMask` / `SADMask` / `OcclusionMask` 三函数 |

其亮点：
1. **`Degrain` 家族按向量数量自动定半径**：`Degrain(clip, super, core.mvu.AnalyseMany(super, radius=3))` 即 6 路向量（前后各 3 帧）；`Degrain1…Degrain25` 是等价显式写法。radius 1–25 对应 2–50 个向量。
2. `AnalyseMany`：一次生成 `[bw1,fw1,bw2,fw2,…]` 全列表，直接喂 Degrain / FlowFPS / FlowBlur。
3. 掩码三件套可直接输出运动掩码做 `std.MaskedMerge`。
4. 支持 GRAY/YUV 8–16 bit + **32-bit float**；向量以帧属性存储（`prefix` 可改），多套 MVUtensils 图可共存。
5. SAD 相关阈值按"8-bit 8×8 块"定义、自动按实际位深缩放，跨位深语义一致。
6. `thscd2` 从 mv 的 0–256 int（默认 130）改成 **0–100 百分比 float（默认 51）**，语义是"多少比例的块变化算场景切换"。

```python
# mvu 版双参考降噪（等价 mv 的 Degrain1）
src = core.fmtc.bitdepth(src, bits=16)
super = core.mvu.Super(src, blksize=8, overlap=4, pel=2)     # blksize/overlap 必填
bw = core.mvu.Analyse(super, delta=1)                         # 正 delta = 后向
fw = core.mvu.Analyse(super, delta=-1)                        # 负 delta = 前向
den = core.mvu.Degrain(src, super, [bw, fw], thsad=[400, 400])

# 更省事：AnalyseMany 自动生成 [bw1,fw1,bw2,fw2,bw3,fw3]
den = core.mvu.Degrain(src, super, core.mvu.AnalyseMany(super, radius=3), thsad=[400, 400])
```

> 注意：`mvu.Analyse` **没有** `isb`——正反向由 `delta` 正负决定（负=前向、正=后向）。且 `Degrain` 的向量列表顺序必须是 `[bw1, fw1, bw2, fw2, …]`（先全后向、再前向、按距离交替），别用 mv 的习惯硬套。

> **输入要求（mvu 新版）**：基本同 mv：16-bit 优选；`Super` 的 `blksize`/`overlap` **必填**，必须与你 `Analyse` 用的块参数一致（或干脆不传让 Analyse 沿用 super 的）。GRAY/YUV 8–16bit 或 float 皆可。
> **参数推荐（mvu 新版）**：
> - `Super(clip, blksize=8, overlap=4, pel=2)` 是又快又稳的起步；只做 Degrain（不做多层级 Analyse）可 `onelevel=True` 省内存。
> - `Analyse`：mvu 的 `search` 用 **自己的 0–5 编号**（与 mv 不同：mvu 3=UMH 质量均衡、5 是 vertical 等；MVUtensils 把旧 mode 0/1 去掉并整体移位）。别把 mv 的编号直接套到 mvu。`delta` 按 **正=后向/负=前向**；或直接用 `AnalyseMany(super, radius=2)` 一次得 `[bw1,fw1,bw2,fw2]`。
> - `Degrain`：`thsad=[400, 400]` 是默认（8-bit 8×8 块语义、自动缩放），提高增强、降低保真。想限制最大像素改动用 `limit=[luma, chroma]`（如 `[2000, 1500]` 在 16bit）。
> - `thscd1/thscd2` 用于场景切换自动跳过：一般保留默认（`thscd2` 0–100 百分比，默认 51）。
> - mvu 比 mv 新、可读性更好、支持 float；但老教程全是 mv。**两者混用会报错**，选一套贯穿到底。

> **算法特点（mvu 相对 mv 的改进）**：MVUtensils 不是新算法，而是**同一动补偿算法的现代化重写**——把"块几何"从一堆并列参数收敛成数组、把向量改为**帧属性存储**（`prefix` 管理多套图）、阈值按 8-bit 8×8 块语义自动缩放、并支持 float。核心引擎比 MVTools 更缓存友好 + SIMD 更宽，同参数通常更快、内存更低。mvu 提供的 `VectorLengthMask/SADMask/OcclusionMask` 让你能直接拿到"这块不可信/被遮挡"的空间信息去做掩码合成——这是老 mv 很难优雅表达的。算法本身假设不变（仍是平移优先的块匹配），选型层面 mvu 更新更适合新脚本。

### 与 SMDegrain / 压制实践的关系
实际压制（尤其动画 1080p→4K 超分前）的"标准降噪"就是 **动补偿 Degrain**：先给源做**轻度**降噪（过度会糊线条），重点去**时间闪烁**，再进超分。社区现成脚本 **SMDegrain**（havsfunc 里 `SMDegrain(clip, tr=2, ...)`）底层就是 mv/mvu + 递归，不建议手写绕，直接用现成封装调参更稳。

- 若你追求自己调，记住三件套：`thsad`（阈值）、`blksize`（块大小 8/16）、`limit`（改动上限防糊）。
- 两套都提供 Depan 稳像，但真正稳像更常用专门的 `vidstab`（需另装）而非 Depan。

---


## 7. 去隔行 / IVTC / 缩放家族

> 分类速记：**去隔行**（deinterlace）= 把 50i/60i 场合并为逐行帧；**IVTC** = 把 telecine（23.976 藏在 29.97 里）还原成原生 24fps。去隔行和 IVTC 是两件不同的事，先判断源类型再选工具。

### 7.1 去隔行

**`bwdif`（Bwdif）**：现代首选去隔行器，质量高、速度适中、参数简单。

| 参数 | 说明 |
|---|---|
| `field` | 本机 DLL 仅 **0–3**（无负值自动判序）：**0=保留底场(same rate)、1=保留顶场(same rate)、2=倍帧率先底场、3=倍帧率先顶场** |
| `edeint` | 可选传入一个边缘导向的"逐行化"clip（须与源同格式同尺寸）供它参考 |
| `opt` | 优化级别 0–4（本机 DLL），通常默认即可 |

```python
prog = core.bwdif.Bwdif(src, field=1)   # field 取决于源场序
```

> **输入要求**：输入为**隔行源**（YUV 常见；需帧率/场序正确）。Bwdif 是 bob 式去隔行：`field` 本机 DLL 仅 **0–3**（**0=保留底场(same rate)、1=保留顶场(same rate)、2/3=双倍率输出（2 先底场、3 先顶场）**；无负值自动判序）。顶场/底场、tff/bff 拿不准时先查源帧属性 `_FieldBased`（0=逐行、1=顶场优先 tff、2=底场优先 bff）——要让输出场序跟源一致，tff 源用 1/3、bff 源用 0/2。
> **参数推荐**：多数场景希望**倍帧率双率输出**(作 bobbing) 用 `field=2` 或 `3`（对应底/顶场先出，按源 order）；想保持原帧率单帧去隔行选 0/1。用错场序画面会垂直抖动——先用小段肉眼检查。

> **算法特点**：隔行源每帧只含一半扫描线（顶场或底场），去隔行要"补出另一半"。补法分两类，Bwdif 属于**时空插值**：
> - 静止/缓变区域：直接把上一帧的同位像素拿来（时间插值，最准）；
> - 运动区域：时间插值会"鬼影"，改为**在已知场里做空间插值**（用上下邻近行估计缺失行）。
> - **Bwdif（Bob Weaver 的改良）**：用运动自适应在"时间插值 vs 空间插值"间抉择，并把两者**加权混合**，过渡平滑、伪影少，且便宜。它是"场自适应空间插值"的当代代表：质量好、快，但不理解物体真实运动，快速运动仍会有轻微锯齿/虚边（没有运动向量）。
> 对比下面几类去隔行的"世界观"：bwdif/yadif = 时空插值；eedi/nnedi3 = 方向/神经边缘插值；真正运动补偿去隔行 = 用向量（更强但贵）。选哪类取决于源与预算。

**`yadifmod`（Yadifmod）**：Yadif + 可选的外部 deinterlacer。质量好、兼容性广，但现代脚本多用 bwdif 或 nnedi3。

| 参数 | 说明 |
|---|---|
| `order` | **0=底场优先(bff)、1=顶场优先(tff)**（本机 DLL：须为 0 或 1）——注意与直觉相反，**0 是 bff** |
| `edeint` | 外部高质量插值结果（如 nnedi3 逐行结果）；**形参第 2 位、可给 None**，Yadif 会用插值帧补细节 |
| `field` | **-1=同 order**、0/1=显式场（本机 DLL：-1/0/1） |
| `mode` | **0–3**（本机 DLL）：0=默认；1/2/3 = 是否单场输出等组合，通常用 0 |

```python
# yadifmod + nnedi3 增强（经典搭配，动画细节保留好）
nn = core.znedi3.nnedi3(src, field=1, dh=True)       # 注意：nnedi3 的 field 是"处理哪一场"，按源场序
prog = core.yadifmod.Yadifmod(src, nn, order=1)      # order=1 = 顶场优先(tff)
```

> **输入要求**：隔行源；`order` 必填且**0=bff / 1=tff**（按源实际场序；拿不准查源帧属性 `_FieldBased`：1=顶场优先 tff → order=1；2=底场优先 bff → order=0）。8–16 bit 整数与 32-bit float 均可；高度须 ≥4。`edeint`（须与源同格式同尺寸、已把缺失场补好）可为 None。
> **参数推荐**：只用 `order`（`edeint=None`）即纯 Yadif；要更高质量把 `edeint` 传 nnedi3 的垂直插值结果（Yadif 负责运动区域判断）。动画/线条去隔行常用 yadifmod+znedi3；实拍快速可用 bwdif。

> **算法特点（Yadif = "Yet Another Deinterlacing Filter"）**：与 bwdif 同属时空插值，核心是**逐像素运动判断**：若该点沿时间几乎不变（静止），用时间相邻场插值；若在动，退到空间插值。它的特点是把**运动检测阈值做在像素级**，且提供两个输出策略（输出单场/隔行场合并），因此质量比纯线性插值好、比 bwdif 略旧但依旧流行。与 `edeint` 组合是它的招牌用法：让一个**更聪明的插值器（nnedi3/eedi3）**负责"怎么插得漂亮"，Yadif 只负责"哪里该插、哪里该保留原场"——分工明确，动画线条去隔行的经典方案。

**`eedi2` / `eedi3` / `eedi3m` / `eedi3vk`（EEDI 家族，边缘导向）**：
方向性边缘插值器，尤其适合处理斜向边缘（漫画线条、纹理），通常**不单独用**，而是作为 deinterlacer 的"高质量补充帧"喂给 yadifmod / bwdif 的 `edeint`，或给 `eedi3` 做 2 倍垂直倍线。
- `eedi2.EEDI2(clip, field=...)`：老牌，参数含 mthresh/lthresh/vthresh/estr/dstr/maxd/nt/pp 等。
- `eedi3.eedi3(clip, field=, dh=, alpha=, beta=, ...)`：**本机 DLL 只支持 8-bit 输入**（`only 8 bits per sample input supported`）。`field` 0–3（dh=True 时须 0/1）；`alpha`/`beta` 须 0–1 且 **alpha+beta≤1**、`gamma≥0`。`dh=True` 时同时水平插值。
- `eedi3m`：eedi3 多线程改进版，支持 8–16 bit 与 32-bit float；参数 alpha/beta/gamma 约束同上，另有 `nrad`(0–3)/`mdis`(1–40)/`vcheck`(0–3)/`opt`(0–3)。
- `eedi3vk`：eedi3 的 Vulkan（GPU）版，可加 `device_id=`。

> **输入要求**：EEDI3 通常处理**隔行源的单一场**或作插值器输入逐行源做倍线。**注意 `eedi3`（非 m）只收 8-bit**，16bit/float 源请用 `eedi3m`。`field` 0–3；`dh=False` 时高度须偶。不同实现 field 对 tff/bff 定义略有差异，按各插件 README。
> **参数推荐**：作为 `yadifmod(edeint=…)` 的补充帧时，`eedi3(m)` 用默认 `alpha/beta/gamma` 即可（记得 alpha+beta≤1）；`dh=True` 表示把缺失的像素方向也插上（垂直倍线）。追求速度用 `eedi3m`（opt 拉高），N 卡/A 卡可用 `eedi3vk` 走 Vulkan。单用 EEDI3 会偏软，一般与运动补偿配合。

> **算法特点（EEDI = "Edge-Directed Interpolation"）**：它**不做运动估计**，而是假设"图像里的边缘是沿着某个方向的连续结构"。补缺失行时，先在邻域用 `alpha/beta` 检测边缘方向，再**沿着边缘方向（而不是竖直）插值**——斜向线条由此保持连续、不出现阶梯锯齿。这是它动画/漫画线条上远胜竖直线性插值的根本原因。局限：本质是"只信方向平滑"，**纹理太乱/高频密集**处方向检测不可靠会出波纹；单一插值也容易"偏软"（缺真实细节），所以常只作 `edeint` 而非主去隔行。`eedi3m/vk` 只是多线程/GPU 化执行。

**`nnedi3` / `nnedi3cl` / `znedi3`（神经网络边缘导向）**：
nnedi3 是"训练过的神经网络插值"，专做高质量逐行化/垂直倍线，动画线条边缘处理极佳，是**压制最常用的"补场 + 高质量放大"组件**。

| 函数 | 常用参数 |
|---|---|
| `nnedi3.nnedi3(clip, field=, dh=, nsize=, nns=, qual=, pscrn=, ...)` | 本机 DLL：`field` **0–3**（dh=True 时须 0/1）；`nsize` **0–6**（6=最大感受野默认，别当成"8×8"）；`nns` **0–4**（默认 1；4 最准最慢）；`qual` **1–2**；`pscrn` 0–1（预检器，越大越快略降质）；`etype` 0–1。8–16 bit 整数或 32-bit float |
| `nnedi3cl.NNEDI3CL(clip, field=, ...)` | OpenCL GPU 版，同参数加 `device=` |
| `znedi3.nnedi3(clip, field=, ...)` | nnedi3 的**优化 AVX2 重写**（同样式参数、更快更稳） |

```python
# nnedi3 垂直倍线到逐行（video 去隔行经典：先隔行源 nnedi3 倍线）
prog = core.znedi3.nnedi3(src, field=1, dh=False)
```

> **输入要求**：nnedi3 期望输入是**单一场/隔行**或用于垂直倍线的逐行；支持 8–16 bit 整数与 32-bit float。做去隔行常见链：`std.SeparateFields` → nnedi3 → `std.Weave`，或直接给隔行帧并设对 `field`。`field` 是本插件"对哪一场先处理"的选择（0–3，dh 时 0/1），与源 bff/tff 的对应按 README。
> **参数推荐**：
> - 质量优先：`nsize=6`、`nns=3~4`、`qual=2`；速度优先 `qual=1`、`nns=1~2`。`dh=False` 是纯垂直倍线（常见），`dh=True` 额外做水平插值（少用）。
> - 想倍线/放大 2 倍（例如作为去隔行+超分的"升场"），配合 `nnedi3` 后接 resize 或 AI。
> - znedi3 与 nnedi3 参数几乎一致，是更快更稳的 AVX2 实现，**无 GPU 时优先 znedi3**。

> **算法特点（nnedi3 = "neural network edge directed interpolation"）**：它是**用一个在大量图像上训练过的小神经网络**做插值的。对每个要补的像素，把周围像素喂给网络，网络输出"最可能是真实值的插值"。因为是数据驱动，它学到的不只是"边缘方向"，还包括真实世界中**哪些像素组合更常见**——所以在斜线、曲线、漫画线条上补出来比 EEDI 类更"自然可信"，尤其线条干净利落。`nns`（0–4）就是网络规模，越大参数量越多、越准越慢；`nsize` 是输入感受野。本质仍是**单帧插值**（不跨帧、不估运动），所以速度尚可、也不引入运动伪影。局限：网络在训练分布内很强，遇到训练外纹理仍会"幻想"，且对每帧独立处理（无时间一致性）——这也就是为什么严格来说它更适合"补场/放大"而非"时间滤波"。

### 7.2 IVTC（逆 telecine）

**`tivtc`（TFM / TDecimate）**：AviSynth TIVTC 的移植版，久经考验。
- `tivtc.TFM(clip, order=, field=, mode=, PP=, ...)`：场匹配还原 3:2 pulldown。`order`（**0=bff / 1=tff**，默认 -1 自动读帧属性）、`field`（默认 -1 同 order）、`mode`（默认 1，范围 0–7，**不是"0=自动"**）、`PP`（默认 6，0–7，配合 `clip2` 高质量去隔行结果）。`ovr` 可给逐场景覆盖文件。
- `tivtc.TDecimate(clip, mode=, cycle=, ...)`：删重帧把 29.97 抽回 23.976。

```python
# 标准 3:2 IVTC（order 依源场序；拿不准用 order=1 顶场先试，或先看源属性）
tf = core.tivtc.TFM(src, order=1, field=-1, mode=0)
ivtc = core.tivtc.TDecimate(tf, mode=1)
```

> **输入要求**：针对 29.97/25 telecine 混合源（DVD、部分 TV 抓取）。源须隔行或含 pulldown 场；`order` 须正确（**0=bff / 1=tff**）。`TFM` 输入为 telecine 后的帧序列。
> **参数推荐**：`order` 按源场序（0=bff、1=tff）；`mode` 有多个（0–7 是不同匹配/输出方式，默认 1）；逐场景异常时用 `ovr` 覆盖文件或 `mode=3` 看匹配帧；`PP` 想输出更干净逐行时配合 `clip2`（给 TDeintMod/nnedi3 的高质量逐行结果）。`TDecimate(mode=1)` 是标准 3:2 删帧。**拿不准 telecine 类型先 `display=True` 调试**。

> **算法特点（IVTC 两步的本质）**：
> - **TFM/VFM（场匹配）**：telecine 把 4 帧展开成 5 帧（每帧由前后场的交错组合）。场匹配就是为每个输出帧**尝试"顶场+本帧底场 / 底场+本帧顶场 / 顶+前后帧场"等几种组合**，选 combing（梳状）最轻的那个作为"真正属于同一时刻的场对"。它靠**检测梳状伪影（combed pixels）**来判断"这个组合是不是同一时刻"，`cthresh/mi` 管这个检测。
> - **TDecimate/VDecimate（删帧）**：场匹配完仍有多余的重复帧（5 帧里有一帧是纯重复），按周期删掉多余帧，把 29.97 变回 23.976。
> - 因此 IVTC 的本质是"**先正确配对场、再删冗余**"，两步都要对场序/周期有正确认知。混合内容（动画与真人、交错剪辑）场景切换处常需 `ovr` 逐段覆盖。局限：一旦某段不是标准 3:2（例如 30p 实拍真隔行），IVTC 会误删/留梳——需先判断源类型再决定该不该 IVTC。

**`vivtc`（VFM / VDecimate）**：VapourSynth 原生重写（更现代、更快，API4 友好），是目前 VS 下 IVTC 的**推荐选择**。

| 函数 | 说明 |
|---|---|
| `vivtc.VFM(clip, order=, field=, mode=, micmatch=, mchroma=, ...)` | 场匹配。`order`（**0=bff / 1=tff**）必填；`field` 0–3；`mode` 0–5；`cthresh` 判定 combing。**本机 DLL 硬性约束：输入须为 8-bit 的 YUV420P8/422P8/440P8/444P8 或 GRAY8**（blockx/blocky 须 4–512 的 2 的幂） |
| `vivtc.VDecimate(clip, cycle=, dupthresh=, scthresh=, ...)` | 删帧降帧率。`cycle`（默认 5）；输入 8–16 bit 整数 |

```python
v = core.vivtc.VFM(src, order=1)          # order=1 = 顶场优先(tff)；0 = bff
v = core.vivtc.VDecimate(v, cycle=5)
```

> **输入要求**：同 tivtc——telecine 源、`order` 正确（**0=bff / 1=tff**）。**VFM 只接受 8-bit 的 YUV420P8/YUV422P8/YUV440P8/YUV444P8/GRAY8**——若源是 10bit/更高位，先转成 8-bit 再做 IVTC（VDecimate 可处理 8–16bit，但 VFM 这步限 8bit）。源含硬 telecine 时配合 VDecimate。
> **参数推荐**：`VFM(order=…, mode=…)` 的 `mode` 0–5（选匹配偏好，默认 1）；`cthresh`/`mchroma` 影响 combing 判定，默认即可。`VDecimate(cycle=5)` 对应 3:2。新项目优先 vivtc（快、现代），老脚本/需 TFM 特殊 PP 时 tivtc。

**`tdm`（TDeintMod / IsCombed）**：老式智能去隔行 + combing 检测。TDeintMod 更像"仅对 combed 区域去隔行"，可用 `edeint` 提供高质量源；`tdm.IsCombed` 输出掩码判断某帧是否有梳状伪影（常用于"是否需要去隔行"的条件判断）。

> **输入要求**：隔行源；`TDeintMod` 的 `order` 同前须正确。`IsCombed` 单帧检测 combing，输出二值 clip（可用 `std.PlaneStats` 判定是否触发去隔行）。
> **参数推荐**：把高质量插值（nnedi3/eedi3）给 `edeint`，TDeintMod 只对 combed 区用它，静止区保留原场——省时保细节。`IsCombed` 阈值（`cthresh` 等）默认即可，用于脚本里做"是否需要去隔行"的自动判断（配 `std.FrameEval`）。

> **算法特点（TDeintMod）**：它是"**选择性去隔行**"——先用 `IsCombed` 类的梳状检测把画面分成"确实交错（combed）"和"其实已逐行"两部分，只在 combed 区域做去隔行、其余原样保留。这避免了对"本来就干净"的区域做无效插值带来的软化。搭配 `edeint` 时，它能用外部高质量插值只处理 combed 块。`IsCombed` 独立函数让"是否去隔行"变成可编程条件，适合混合源（一段隔行一段逐行）自动处理。局限：判断错误的区域会留梳或误插，阈值需小心。

### 7.3 抗锯齿 / 缩放辅助

**`sangnom`（SangNom）**：老式边缘抗锯齿/单场去隔行器（"VapourSynth Single Field Deinterlacer"）。现代常被 `eedi3`/`nnedi3` 抗锯齿方案替代，但处理简单锯齿仍有效。

> **输入要求**：输入须**逐行已完成去隔行**的画面（或单场源做倍线）；**高度须为偶数**；8–16 bit。参数（本机 DLL）：`order` **0–2**（**0=倍帧率输出且需帧属性 `_FieldBased`**、1/2=保留顶/底场）、`dh`（0/1，是否倍线）、`aa` **0–128**（默认 48，抗锯齿强度）。
> **参数推荐**：`aa` 默认 48 附近即可，过高会糊细节；要做"去锯齿不倍线"用 `order=1`/`2` 之一。锯齿明显（斜线阶梯）时用它，轻微情况用锐化/边缘处理即可。偏老，重锯齿动画可用 nnedi3+mask 方案替代。

> **算法特点（SangNom）**：经典抗锯齿思路——检测**斜边上的锯齿阶梯**，沿"本应连续的边缘方向"把阶梯上错位的像素重新内插对齐，让斜线恢复平滑。它不引入新信息，只是把已有像素沿边缘方向"捋顺"，因此对轻微/中等锯齿有效、速度快。局限：只对"斜边锯齿"有效，对真实模糊/低分辨率无解；方向误判会软化或引入波纹，所以 `aa` 别开太大。现代压制多用 nnedi3/eedi3 的边缘导向插值替代（更聪明），SangNom 偏老但仍是简单场景的省事选择。

**`resize.Bob`**：见 §1.2，快速粗略去隔行（场分离 + 倍线），质量一般但超快，预览/应急可用。

> **输入要求**：隔行源。仅应急/预览，正式压制请用 §7.1 的去隔行器。
> **参数推荐**：`tff=` 给对场序。想快速预览逐行效果用它最省事；追求质量别用它。

---

## 8. AI / 神经放大

压制里"AI 放大"的通用做法：把低分辨率源（尤其动画）用训练好的神经网络放大，再用轻度降噪/抗锯齿收尾。你机器上装的是这类里偏"开箱即用"的几款。

### 8.1 `w2xnvk`（Waifu2x-NCNN-Vulkan）

| 函数 | 参数（本机 DLL 硬性约束） |
|---|---|
| `w2xnvk.Waifu2x(clip, noise=, scale=, model=, tile_size=, gpu_id=, precision=, ...)` | `noise` **只能是 -1,0,1,2,3**（**-1=不去噪**；0/1/2/3 = 去噪档位，3 最强）；`scale` **只能 1 或 2**；`model` **只能 0,1,2**（**0=anime_style_art、1=photo、2=cunet**，非"4~9 多种"）；`precision` 16 或 32；`tile_size/tile_size_w/h` ≥32 且 4 的倍数；`tta` test-time augmentation |

**输入硬性约束（本机 DLL：`only constant RGB format and 32 bit float input supported`）**：只接受 **32-bit float RGB**——不能直接喂整数 YUV，须先 `fmtc.bitdepth(..., bits=32, flt=True)` 并转 RGB（如 `resize.Bicubic(format=vs.RGBS)`）。需可用的 Vulkan GPU。放大目标像素受显存限制，大图要 `tile_size` 分块。

```python
# 正确用法：先转 float RGB 再超分
src_rgb = core.resize.Bicubic(src_yuv, format=vs.RGBS)          # 或 fmtc 系列转 float RGB
up = core.w2xnvk.Waifu2x(src_rgb, noise=-1, scale=2, model=0)   # noise=-1 关闭去噪
# 若要送编码，再转回目标 YUV/位深
```

适合：动漫 / 线条放大 2x（model=0/2）；实拍用 model=1 但效果一般——真人超分建议 RealESRGAN 类（需另装 vsmlrt 等）。

> **输入要求**：如上，**必须 32-bit float RGB**；需 Vulkan GPU。要放大 4x 就链两次 scale=2（或视模型支持）。
> **参数推荐**：动漫放大用 `model=0`（或 cunet=2 细节略多）；源若已干净用 `noise=-1` 关闭内置去噪（`noise=0` 其实是"低档去噪"，不是关闭——要关就是 -1）；显存不足调小 `tile_size`；求稳开 `tta`（慢数倍）。

> **算法特点（Waifu2x = SRCNN 系超分）**：它用**卷积神经网络（CNN）**学习"低分辨率 → 高分辨率"的映射。训练目标是让网络从大量高清动漫图中学会"如何补出高频细节"。与插值核的根本差异：插值只是数学平滑，网络是**从数据学到的先验**——能把线条补得更利、把缺失高频"生成"出来。`noise` 档会在网络里先学去噪再放大（动漫扫描噪很匹配训练数据）。Waifu2x 是 2x 的轻量风格模型，GPU 友好、动漫效果好。局限：网络有"幻觉"，会偶尔强化噪声/产生假的纹理；对真人/实拍泛化差（训练几乎都是二次元）。现代已有一批更强超分（RealESRGAN、vsmlrt），Waifu2x 属"入门/轻量/动漫"档。

### 8.2 `anime4kcpp`（Anime4KCPP）

| 函数 | 参数 |
|---|---|
| `anime4kcpp.ACUpscale(clip, factor=, processor=, model=, device=)` | `factor` 缩放倍数；`processor`（CPU/GPU）；`model` 是**模型名子串**（如 `acnet`/`artcnn`/`fsrcnnx` 等，默认 acnet） |
| `anime4kcpp.ACInfoList` | 列出可用 model / processor 列表 |

Anime4KCPP 专注动漫/线条、速度快，可近实时。"A/B/C/D 系列"的提法来自 **bloc97 的 Anime4K 着色器项目**，不要与 Anime4KCPP 的 model 名混淆——本插件的 `model` 用具体模型名（先 `ACInfoList` 看支持哪些）。

```python
up = core.anime4kcpp.ACUpscale(src, factor=2.0)          # 默认模型即可起步
# up = core.anime4kcpp.ACUpscale(src, factor=2.0, model="artcnn")
```

> **输入要求**：本插件（Vulkan 版）只接受 **planar YUV 或 Gray**，**不收 RGB**（与 w2xnvk 相反）；8–16 bit 整数视版本而定，需可用的 Vulkan/GPU。自动回退 CPU 或走 GPU，对内存占用较友好。
> **参数推荐**：`factor=2.0` 常用；先跑默认模型看效果，想更多细节再 `ACInfoList` 里换模型试；`processor` 按可用后端。放大前建议轻降噪，否则线条噪声会被强化。

> **算法特点（Anime4KCPP）**：Anime4KCPP 是 Anime4K 思路的 C++ 落地，用**卷积神经网络**专做动画放大与线条重构，核心取向是"边缘保真、实时可用"。它的模型多为轻量 CNN（acnet/artcnn 等），与 w2xnvk 相比更适合近实时/实时管线；细节重建上限低于重型超分模型。与 Waifu2x 相同：训练几乎全动漫，实拍一般；速度取向也意味着细节上限有限。区别注意：真正的 "Anime4K A/B/C 系列着色器" 是 bloc97 的项目（主要给 mpv/VapourSynth 的另一套），Anime4KCPP 是其重编译变体之一，两者模型体系不完全一致——以你本机 `ACInfoList` 输出为准。

### 8.3 `akarin`（视频工具箱：DLISR / DLVFX）

akarin（作者 **AkarinVS**，repo: AkarinVS/vapoursynth-plugin）是一个多用途实验插件，其中 AI 放大相关：
- `akarin.DLISR(clip, scale=, device_id=)`：用 ONNX 加载模型做超分（DLISR 模型库），可接任意 ONNX 超分模型。
- `akarin.DLVFX(clip, op=, ...)`：视频增强（去噪/去模糊等），模型需在 `model_dir`。

akarin 里还有 `Cambi`（VMAF 指标实现）、`Expr`/`ExprTest`（极速逐像素表达式）、`PickFrames`、`Select`、`Text` 等工具函数。DLISR/DLVFX 都依赖额外模型文件，属"进阶自定义"用途。

> **输入要求**：DLISR/DLVFX 需外部 ONNX/模型文件，运行时从 `model_dir` 加载；需确认模型路径与插件能匹配的模型格式。Expr 系则吃标准逐像素输入。
> **参数推荐**：模型放好路径再调 `scale`（DLISR）/ `op`（DLVFX，指定增强算子）。DLISR 多用于超分特定来源；DLVFX 多算子（去噪/去模糊/超分）按需选。两者属于"要自己下模型+试参"的进阶插件，日常压制更常用 §8.1/8.2 的开箱即用款。

> **算法特点**：akarin 的 DLISR/DLVFX 本质是 **ONNX 模型推理桥**——把通用 ONNX 超分/增强模型（不只动漫，可换任意 .onnx）搬进 VS。DLISR 专注超分（scale），DLVFX 是"一个模型多 op"的封装。真正的"算法"取决于你加载的模型权重，插件本身只是执行框架 + tile 分块显存管理。因此它能接的模型面比 w2xnvk/anime4kcpp 更广，但也要求用户自己找模型、装 runtime、理解模型特性，门槛更高。akarin 其余 Expr 系是高性能 RPN 计算引擎（见 §10）。

---

## 9. 锐化 / 边缘 / 增强 / 其它滤镜

> 本章指"非降噪、非放缩"的中小工具。有些已在 §5（如 cas 锐化）提过，此处汇总剩余的。

### 9.1 锐化 / 细节增强

| 命名空间 | 函数 | 说明 |
|---|---|---|
| `cas` | `CAS(clip, sharpness=)` | AMD 对比度自适应锐化（§5 已述） |
| `warp` | `AWarpSharp2(clip, thresh=, blur=, type=, depth=[luma,...], chroma=)` | 高质量边缘锐化（线稿/动画线条利落）；**只 8–16 bit 整数非 RGB**，`depth` 数组 -128~127；配合 `tcanny`/边缘 mask 效果更佳。`ASobel`/`ABlur`/`AWarp` 是配套工具 |
| `msmoosh` | `MSharpen` / `MSmooth` | 老式 MSharpen（轻度锐化）/ MSmooth（轻度平滑）。MSharpen `threshold`/`strength` 0–100%；MSmooth `threshold` 0–100、`strength` 1–25 |

> **算法特点（锐化工具的共同原理）**：传统锐化（含 MSharpen）都是"**高通增强**"——把原图减去低通（模糊）得到高频细节层，再加回原图（`sharp = src + strength·(src − blur)`），边缘被加粗高亮。`AWarpSharp2` 属**自适应变形锐化（warp-based）**：它检测边缘方向后，把像素**沿边缘法线方向轻微位移重采样**，让边缘更"紧实干净"，比单纯加高频少光边、线条更利落，尤其适合线稿。`cas`（§5.5）用局部对比度调制，介于两者之间。三者都遵循"**锐化 = 增加边缘过渡对比**"，本质都不产生新细节，量过大会白边/脏噪。动画线条多用 AWarpSharp2（edge-aware）；实拍细节多用 CAS（克制）；MSharpen 偏老。

### 9.2 边缘 / 检测

| 命名空间 | 函数 | 说明 |
|---|---|---|
| `tcanny` | `TCanny(clip, sigma=, t_h=, t_l=, mode=, op=, ...)` | Canny 边缘检测，输出边缘 mask，常配 `std.MaskedMerge`。`op` 0–6 选算子、`mode` -1/0/1 决定输出二值或梯度；8–16int/32f 均可 |
| `warp` | `ASobel` | Sobel 边缘（亮度阈值 `thresh`） |
| `rgvs` | `RemoveGrain` / `Repair` / `Clense` | RemoveGrain 去颗粒保线条；`Repair`（用参考图修复）；`Clense` 系时间修复。压制里 RemoveGrain/Repair 常配 MaskedMerge 做边缘保护降噪 |
| `misc` | `Hysteresis` / `AverageFrames` / `SCDetect` | Hysteresis（滞回二值化，清边缘噪声）；AverageFrames（带权多帧平均）；SCDetect（场景检测输出帧属性） |
| `retinex` | `MSRCR` / `MSRCP` | Retinex 色彩/对比度增强（去雾感、提暗部），HDR/SDR 转制偶尔用 |

> **算法特点（检测/分析工具）**：
> - **tcanny（Canny 边缘）**：先用高斯平滑去噪，再算梯度幅值与方向，最后**双阈值滞后连接**（高于高阈值必留、介于其间且连到已留边缘的保留、低于低阈值丢弃），得到干净连贯的单像素边缘。比 Sobel 这类一阶算子更稳、边缘更连续，因此常被当作"边缘 mask"喂给 MaskedMerge。
> - **Sobel/边缘检测**：一阶微分核，找亮度梯度大的像素，快但对噪声敏感、边缘宽。
> - **rgvs（RemoveGrain/Repair）**：RemoveGrain 是一族**固定 mode 的空间滤波模板**，每个 mode 是一种去噪/保边缘策略（看官方对照表），速度快、可做边缘保护降噪；`Repair` 用第二个参考图的干净结构约束第一个图的改动量（典型用途：降噪强图和弱图之间按 mask 混合），是"限制过度处理"的巧思。
> - **retinex（MSRCR/MSRCP）**：把人眼感知的"照度×反射率"解耦，估计光照并压缩其影响来提升暗部/去雾，属感知增强。副作用是可能改变色调（MSRCR 带颜色恢复），所以整片慎用。
> - 这些函数多是"产出 mask/参考"给更聪明的主滤镜（MaskedMerge/Repair）用的中间件，单独用价值有限。

> **本章通用输入/参数提示**：除 warp/rgvs/msmoosh 等各插件硬性要求（见下）外均吃常见 8–16bit GRAY/YUV；`warp` 与 `tcanny` 需先有逐行画面；`rgvs` 的 RemoveGrain/Repair 常用于保护边缘的降噪链（与 `std.MaskedMerge` 配合）。
> **参数推荐（本机 DLL 已核实约束）**：
> - `warp.AWarpSharp2`：**只接受 8–16 bit 整数且非 RGB**；`depth` 是数组（Y/UV），**范围 -128~127**（默认约 16）；`thresh` 0–255（默认 128）、`blur`≥0、`type` 0/1、`chroma` 0/1、`cplace` 传 `'mpeg1'/'mpeg2'`。做高质量边缘锐化建议先给边缘 mask，强度由 `depth` 控制，避免过冲白边。
> - `warp.ASobel`：`thresh` 0–255，仅 8–16 int 非 RGB。
> - `tcanny`：**`op` 是算子选择 0–6**（不是"输出灰度/二值"开关）；决定二值 vs 灰度梯度的是 **`mode`（-1/0/1）**。`sigma`（默认 1.5）高斯半径、`t_h/t_l` 双阈值（须 `t_h>t_l`，默认 t_l=1.0/t_h=8.0）；8–16 bit 整数或 32-bit float 均可。要"灰度边缘强度"mask 用对应 mode，要二值线用另一 mode。
> - `rgvs.RemoveGrain/Repair`：**mode 有效 0–24**（8–16 bit int 输入）；`Repair(clip, repairclip, mode)` 第二参考图须同格式。参考官方 mode 表选去噪/保边缘策略。
> - `msmoosh`：MSharpen `threshold`/`strength` 均 **0–100%**；MSmooth `threshold` 0–100、`strength` 1–25。
> - `retinex`：MSRCR 用 `sigma`（尺度数组）/`lower_thr`/`upper_thr`/`restore`（**无 `lambda`**）；通常只在 HDR 转 SDR 或画面发闷时轻微使用，别整片加强。
> - `grain.Add`：加回颗粒用 `var`（默认 4.0，越大幅度越粗），先降噪后加噪回质感时调出均匀颗粒即可。

### 9.3 其它杂项

| 命名空间 | 函数 | 说明 |
|---|---|---|
| `flux` | `SmoothT` / `SmoothST` | 便宜的时间平滑（§5） |
| `bilateral` | `Bilateral` / `Gaussian` | 双边/高斯滤波（§5） |
| `ctmf` | `CTMF` | 常量时间中值滤波（去椒盐，保留边缘） |
| `grain` | `Add` | 给画面加模拟胶片颗粒（`var`/`hcorr`/`vcorr`），用于"降噪后找回质感"或统一颗粒 |
| `hist` | （见 §3.7） | 直方图 |

> **杂项输入/参数提示**：
> - `bilateral`（见 §5.8）：`sigmaS`（空间）/`sigmaR`（色度-值域）控制强度；去噪边缘感知，不糊边。
> - `bilateralgpu`：同 bilateral 的 GPU 版，`radius` 1–3、`sigma_spatial/color` 同义。
> - `ctmf`：常量时间中值滤波，`radius` 一般 1–3；专去椒盐/坏点，保留边缘，速度快。
> - `grain.Add`：`var`（方差）控亮度颗粒强度、`uvar` 控色度；`hcorr/vcorr` 控制颗粒纵横相关（调 0~0.5 更像真实胶片）。加到画面记得在**最后一步前**（字幕之后、位深转换前），避免编码时被二次量化抹掉。

> **算法特点（杂项）**：
> - **bilateral**：见 §5.8（双边滤波=空间+灰度双权重，保边平滑）。
> - **ctmf（Constant-Time Median Filter）**：用**常量时间算法**算中值（传统中值滤波每个像素要排序，它用直方图/统计技巧把每像素代价降到近 O(1)），故即使 `radius` 较大也很快。中值天生保边缘去脉冲噪，适合坏点/椒盐。
> - **misc.Hysteresis**：先低阈值选候选、再高阈值锚定强信号并**沿连通性传播**，把碎片边缘连成完整区域——常用于把弱边缘 mask 补全。
> - **grain.Add**：生成**时间变化的随机颗粒**并叠加；因为加的是时变噪声，能"骗过"压缩器在平坦区分配码率（去色带/降噪后画面太平会浪费码率，加回颗粒既保质感又能帮助编码）。`hcorr/vcorr` 控颗粒的空间相关性（0=纯白噪，越大越像胶片颗粒的各向异性）。

---

## 10. 质量评估 / 分析与杂项

### 10.1 质量度量

| 命名空间 | 函数 | 说明 |
|---|---|---|
| `vmaf` | `VMAF` / `Metric` / `CAMBI` | §3.8 已详述：VMAF 主指标，`Metric` 可出 psnr/ssim，CAMBI 出感知带宽。对比脚本前后或压片参数调优必备 |

### 10.2 高级脚本工具

| 命名空间 | 函数 | 说明 |
|---|---|---|
| `akarin` | `Expr` / `ExprTest` / `PropExpr` | **极速逐像素/逐帧表达式**：`Expr` 支持多 clip + 复杂 RPN，`PropExpr` 读帧属性做条件运算，`ExprTest` 可做浮点仿真调试。比 std.Expr 更底层的性能向工具 |
| `akarin` | `Select` / `Tmpl` / `PickFrames` / `Text` | `Select` 条件选 clip；`Tmpl` 模板文本注入帧属性；`PickFrames` 抽任意帧序列；`Text` 叠加 |
| `std` | `FrameEval` / `ModifyFrame` / `PlaneStats` | 逐帧回调 + 统计，配合条件逻辑 |

> **输入/参数提示**：`akarin.Expr` 语法与 std.Expr 不同（读它的文档），适合对性能极端敏感的 RPN 场景，普通逐像素用 `std.Expr` 即可。`PropExpr` 需 `dict` 传帧属性与表达式映射。`FrameEval`/`ModifyFrame` 的 Python 回调里不要做重活（会逐帧调用拖慢）。`PlaneStats` 输出的均值可存帧属性，供后面 `std.Expr`/FrameEval 做自适应阈值。

> **算法特点**：akarin 的 Expr 系列也是 **RPN 逐像素引擎**，比 std.Expr 更贴近底层（类似 AviSynth 的 ExTools 系）：一次能跨多个输入剪辑、支持读帧属性（`PropExpr`）、带浮点调试（`ExprTest`）。性能取向强、但语法较冷门。选型：图省心用 std.Expr；真要压榨性能/用帧属性做复杂条件再上 akarin。`PlaneStats`/`FrameEval` 则是"按帧做统计→写属性→回调决策"的控制流积木——许多自适应滤镜（如按平均亮度决定阈值）都靠这套搭起来。

### 10.3 命令行 / 配套（非滤镜，供参考）

- `vspipe`：把脚本渲成裸 YUV / 用 `-y` 出 Y4M。真正压片时用 `vspipe script.py - -c y4m | x264/x265 --demuxer y4m ...` 喂给编码器。
- Python 侧 `vapoursynth` 包：本机 Python 313，插件在 `site-packages/vapoursynth/plugins`。加载顺序见启动时的 deprecation 提示。

---

## 11. 典型处理链示例

> 流程经验顺序（尤其压制）：**取源 →（IVTC/去隔行）→ 去色带/去块 → 高位深 → 轻度降噪（动补偿优先）→ 缩放/超分 →（可选的锐化/加噪收尾）→ 字幕 → 转位深/输出**。下面给两种常见链。

### 11.1 动画 / Web 源的常规清洗链（含 IVTC + MVTools 降噪）

```python
import vapoursynth as vs
core = vs.core

# 1) 源：lsmas 解码（VFM 只吃 8bit，先不升位深；源若已是 10bit 先转 8bit 再做 IVTC）
src = core.lsmas.LWLibavSource(r"C:\v\anime.mkv", cache=1, threads=1)

# 2) 若是 telecine 源先 IVTC（假定源是 29.97i 3:2；24p 源跳过）。注意 VFM 限 8bit 输入
iv = core.vivtc.VFM(src, order=1)
iv = core.vivtc.VDecimate(iv, cycle=5)
iv16 = core.fmtc.bitdepth(iv, bits=16)                  # IVTC 完成后再升 16bit

# 3) 去色带（动画暗部常有色带；f3kdb 默认 range=15/grainy=64，这里压到不额外加噪）
deb = core.neo_f3kdb.Deband(iv16, range=16, y=24, cb=24, cr=24, grainy=0, grainc=0, sample_mode=2)

# 4) 轻度动补偿降噪（MVTools 双参考，16bit）
super = core.mv.Super(deb, pel=2, sharp=1)
bw = core.mv.Analyse(super, isb=True,  delta=1, search=3)
fw = core.mv.Analyse(super, isb=False, delta=1, search=3)
den = core.mv.Degrain1(deb, super, bw, fw, thsad=150)

# 5) 转回 8bit 输出
out = core.resize.Spline36(den, 1920, 1080, format=vs.YUV420P8)

out.set_output()
```

### 11.2 高清实拍 · MVUtensils 动补偿降噪 + BM3D 收尾

```python
import vapoursynth as vs
core = vs.core

src = core.lsmas.LWLibavSource(r"C:\v\film.mkv", cache=1, threads=1)
src = core.fmtc.bitdepth(src, bits=16)

# A) MVUtensils 双参考降噪（AnalyseMany 自动出 [bw1,fw1,bw2,fw2]）
super = core.mvu.Super(src, blksize=8, overlap=4, pel=2)
vecs = core.mvu.AnalyseMany(super, radius=2)
den = core.mvu.Degrain(src, super, vecs, thsad=[400, 400])

# B) BM3D CPU 收尾空间细节（轻 sigma）——注意 bm3dcpu 只吃 32-bit float
denf = core.fmtc.bitdepth(den, bits=32, flt=True)         # 16bit int → 32f
cleanf = core.bm3dcpu.BM3D(denf, sigma=[1.5, 1.0], block_step=8, bm_range=6, radius=0, chroma=True)
clean = core.fmtc.bitdepth(cleanf, bits=16)               # 回 16bit 再降 10bit 输出

out = core.fmtc.bitdepth(clean, bits=10)               # 10bit 输出
out.set_output()
```

### 11.3 脚本自检提醒

- 先 `out = core.text.ClipInfo(out)` 确认分辨率/格式/帧率正确再批量压。
- 高位深（16bit）处理末尾**务必**转回目标位深，否则编码器报错。
- 动补偿、BM3D、AI 放大三类都慢——先用 `core.std.BlankClip` 造短测试源调参，别直接全片试。
- IVTC/去隔行要判断源，拿不准先用 `vivtc.VFM` / `tivtc.TFM` 看 combing 程度再决定。
