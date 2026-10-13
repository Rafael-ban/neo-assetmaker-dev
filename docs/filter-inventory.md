# 当前滤镜与插件清单

源码基线：`25b6e4127f2dd5257aaf3bb67d97cd68a4b11525`，2026-10-13。本工作分支已追加 Lanczos / 可选 RIFE 60 fps；此表来自实际脚本和分发清单，不代表用户本机已安装或每个插件已执行测试。

## 默认实际执行的处理

| 组件/函数 | 当前用途 | 上游 GitHub |
|---|---|---|
| `lsmas.LWLibavSource` | 视频输入与索引 | [L-SMASH-Works](https://github.com/HomeOfAviSynthPlusEvolution/L-SMASH-Works) |
| `imwri.Read` | 图片输入 | [vs-imwri](https://github.com/vapoursynth/vs-imwri) |
| `resize.Lanczos` | 输出缩放、色彩/位深转换；适应窗口预览 | [VapourSynth](https://github.com/vapoursynth/vapoursynth) / [zimg](https://github.com/sekrit-twc/zimg) |
| `resize.Point` | 最近邻像素放大预览 | [VapourSynth](https://github.com/vapoursynth/vapoursynth) |
| `std.CropAbs`、`Transpose`、`FlipHorizontal`、`FlipVertical`、`Turn180` | 按编辑参数裁剪和旋转 | [VapourSynth](https://github.com/vapoursynth/vapoursynth) |
| `std.AssumeFPS`、`Loop` | 图片生成指定帧率/帧数的循环片段 | [VapourSynth](https://github.com/vapoursynth/vapoursynth) |
| `std.SetFrameProps`、`AddBorders` | 颜色属性与必要的编码尺寸补边 | [VapourSynth](https://github.com/vapoursynth/vapoursynth) |

`FINAL_RESIZER = core.resize.Lanczos`，`FINAL_RESIZE_OPTIONS = {"dither_type": "none"}`。 `process_source()` 直接返回输入；DFTTest 与 neo_f3kdb 的代码在说明字符串中，当前未执行。默认链不启用降噪、去色带、锐化、去隔行、插帧、HDR tone mapping 或 AI 放大。可选 RIFE 默认关闭，启用后仅将低于 60 fps 的视频插到 60 fps，详见 [配置步骤](vapoursynth-kb/17-filter-configuration.md#可选-rife-插帧到-60-fps)。

`config/vs_runtime.json` 的 `native_plugin_dirs` 和 `python_module_dirs` 均为空；当前 Windows 用户覆盖或项目自定义脚本可以改变实际配置，本清单不推定那些本机内容。

## 已配置分发的扩展包

`resources/packaging/vs-plugins.json` 共 64 个包。允许自动加载表示运行时可注册该 DLL 的函数，不表示它被默认脚本调用；设为手动的 GPU 插件需另行配置运行依赖和插件目录。无命名空间的是运行库/权重/模型依赖。

| 包 | 锁定版本 | 命名空间 | 加载策略 | 上游 GitHub |
|---|---|---|---|---|
| `akarin` | v0.96 | `akarin` | 允许自动加载 | [AkarinVS/vapoursynth-plugin](https://github.com/AkarinVS/vapoursynth-plugin) |
| `anime4kcpp` | v3.2.0 | `anime4kcpp` | 允许自动加载 | [TianZerL/Anime4KCPP](https://github.com/TianZerL/Anime4KCPP) |
| `bilateral` | r3 | `bilateral` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-Bilateral](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-Bilateral) |
| `bilateralgpu` | r14 | `bilateralgpu` | 手动启用 | [WolframRhodium/VapourSynth-BilateralGPU](https://github.com/WolframRhodium/VapourSynth-BilateralGPU) |
| `bm3d` | r8 | `bm3d` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-BM3D](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-BM3D) |
| `bm3dcpu` | R2.15 | `bm3dcpu` | 允许自动加载 | [WolframRhodium/VapourSynth-BM3DCUDA](https://github.com/WolframRhodium/VapourSynth-BM3DCUDA) |
| `bm3dcuda` | R2.15 | `bm3dcuda` | 手动启用 | [WolframRhodium/VapourSynth-BM3DCUDA](https://github.com/WolframRhodium/VapourSynth-BM3DCUDA) |
| `bm3dcuda_rtc` | R2.15 | `bm3dcuda_rtc` | 手动启用 | [WolframRhodium/VapourSynth-BM3DCUDA](https://github.com/WolframRhodium/VapourSynth-BM3DCUDA) |
| `bwdif` | r4.1 | `bwdif` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-Bwdif](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-Bwdif) |
| `cas` | r2 | `cas` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-CAS](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-CAS) |
| `ctmf` | r5 | `ctmf` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-CTMF](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-CTMF) |
| `d2v` | v1.3 | `d2v` | 允许自动加载 | [dwbuiten/d2vsource](https://github.com/dwbuiten/d2vsource) |
| `dctf` | r2 | `dctf` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-DCTFilter](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-DCTFilter) |
| `deblock` | r7.1 | `deblock` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-Deblock](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-Deblock) |
| `descale` | r11 | `descale` | 允许自动加载 | [Jaded-Encoding-Thaumaturgy/vapoursynth-descale](https://github.com/Jaded-Encoding-Thaumaturgy/vapoursynth-descale) |
| `dfttest` | r7 | `dfttest` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-DFTTest](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-DFTTest) |
| `dfttest2_cuda` | v10 | `dfttest2_cuda`, `dfttest2_nvrtc` | 手动启用 | [AmusementClub/vs-dfttest2](https://github.com/AmusementClub/vs-dfttest2) |
| `dfttest2_cuda_runtime` | v10 | 无（依赖包） | 由使用它的插件依赖 | [AmusementClub/vs-dfttest2](https://github.com/AmusementClub/vs-dfttest2) |
| `dpid` | r6 | `dpid` | 允许自动加载 | [WolframRhodium/VapourSynth-dpid](https://github.com/WolframRhodium/VapourSynth-dpid) |
| `eedi2` | r7 | `eedi2` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-EEDI2](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-EEDI2) |
| `eedi3` | R1 | `eedi3` | 允许自动加载 | [vapoursynth/vs-eedi3-obsolete](https://github.com/vapoursynth/vs-eedi3-obsolete) |
| `eedi3m` | r8 | `eedi3m` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-EEDI3](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-EEDI3) |
| `eedi3vk` | R1.2 | `eedi3vk` | 手动启用 | [Sunflower-Dolls/Vapoursynth-EEDI3VK](https://github.com/Sunflower-Dolls/Vapoursynth-EEDI3VK) |
| `ffms2` | 5.0 | `ffms2` | 允许自动加载 | [FFMS/ffms2](https://github.com/FFMS/ffms2) |
| `fft3dfilter` | R2 | `fft3dfilter` | 允许自动加载 | [myrsloik/VapourSynth-FFT3DFilter](https://github.com/myrsloik/VapourSynth-FFT3DFilter) |
| `fftw3_library` | 3.3.10 | 无（依赖包） | 由使用它的插件依赖 | [Vapoursynth-Plugins-Gitify/fftw3](https://github.com/Vapoursynth-Plugins-Gitify/fftw3) |
| `flux` | v2 | `flux` | 允许自动加载 | [dubhatervapoursynth/vapoursynth-fluxsmooth](https://github.com/dubhatervapoursynth/vapoursynth-fluxsmooth) |
| `fmtc` | r30 | `fmtc` | 允许自动加载 | [EleonoreMizo/fmtconv](https://github.com/EleonoreMizo/fmtconv) |
| `grain` | r10 | `grain` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-AddGrain](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-AddGrain) |
| `hist` | v2 | `hist` | 允许自动加载 | [dubhatervapoursynth/vapoursynth-histogram](https://github.com/dubhatervapoursynth/vapoursynth-histogram) |
| `hqdn3d` | r2.0.1 | `hqdn3d` | 允许自动加载 | [theChaosCoder/vapoursynth-hqdn3d](https://github.com/theChaosCoder/vapoursynth-hqdn3d) |
| `knlm` | v1.1.1 | `knlm` | 手动启用 | [Khanattila/KNLMeansCL](https://github.com/Khanattila/KNLMeansCL) |
| `median` | v4 | `median` | 允许自动加载 | [dubhatervapoursynth/vapoursynth-median](https://github.com/dubhatervapoursynth/vapoursynth-median) |
| `misc` | R2 | `misc` | 允许自动加载 | [vapoursynth/vs-miscfilters-obsolete](https://github.com/vapoursynth/vs-miscfilters-obsolete) |
| `mpls` | r5 | `mpls` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-ReadMpls](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-ReadMpls) |
| `msmoosh` | v1.1 | `msmoosh` | 允许自动加载 | [dubhatervapoursynth/vapoursynth-msmoosh](https://github.com/dubhatervapoursynth/vapoursynth-msmoosh) |
| `mv` | v24 | `mv` | 允许自动加载 | [dubhatervapoursynth/vapoursynth-mvtools](https://github.com/dubhatervapoursynth/vapoursynth-mvtools) |
| `mvutensils` | 9 | `mvu` | 允许自动加载 | [myrsloik/mvutensils](https://github.com/myrsloik/mvutensils) |
| `neo_f3kdb` | r10 | `neo_f3kdb` | 允许自动加载 | [HomeOfAviSynthPlusEvolution/neo_f3kdb](https://github.com/HomeOfAviSynthPlusEvolution/neo_f3kdb) |
| `nnedi3` | v12 | `nnedi3` | 允许自动加载 | [dubhatervapoursynth/vapoursynth-nnedi3](https://github.com/dubhatervapoursynth/vapoursynth-nnedi3) |
| `nnedi3_weights` | r1 | 无（依赖包） | 由使用它的插件依赖 | [sekrit-twc/znedi3](https://github.com/sekrit-twc/znedi3) |
| `nnedi3cl` | r8 | `nnedi3cl` | 手动启用 | [HomeOfVapourSynthEvolution/VapourSynth-NNEDI3CL](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-NNEDI3CL) |
| `retinex` | r4 | `retinex` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-Retinex](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-Retinex) |
| `rife` | r9_mod_v33 | `rife` | 手动启用 / 依赖 | [styler00dollar/VapourSynth-RIFE-ncnn-Vulkan](https://github.com/styler00dollar/VapourSynth-RIFE-ncnn-Vulkan) |
| `rife_license` | r9_mod_v33 | 无（依赖/许可） | 手动启用 / 依赖 | [styler00dollar/VapourSynth-RIFE-ncnn-Vulkan](https://github.com/styler00dollar/VapourSynth-RIFE-ncnn-Vulkan) |
| `rife_model_bin` | r9_mod_v33 / rife-v4.6_ensembleFalse | 无（依赖/许可） | 手动启用 / 依赖 | [styler00dollar/VapourSynth-RIFE-ncnn-Vulkan](https://github.com/styler00dollar/VapourSynth-RIFE-ncnn-Vulkan) |
| `rife_model_param` | r9_mod_v33 / rife-v4.6_ensembleFalse | 无（依赖/许可） | 手动启用 / 依赖 | [styler00dollar/VapourSynth-RIFE-ncnn-Vulkan](https://github.com/styler00dollar/VapourSynth-RIFE-ncnn-Vulkan) |
| `rgvs` | R1 | `rgvs` | 允许自动加载 | [vapoursynth/vs-removegrain](https://github.com/vapoursynth/vs-removegrain) |
| `sangnom` | r42 | `sangnom` | 允许自动加载 | [dubhatervapoursynth/vapoursynth-sangnom](https://github.com/dubhatervapoursynth/vapoursynth-sangnom) |
| `sub` | R5 | `sub` | 允许自动加载 | [vapoursynth/subtext](https://github.com/vapoursynth/subtext) |
| `tcanny` | r14 | `tcanny` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-TCanny](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-TCanny) |
| `tdm` | r10 | `tdm` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-TDeintMod](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-TDeintMod) |
| `tivtc` | v2 | `tivtc` | 允许自动加载 | [dubhatervapoursynth/vapoursynth-tivtc](https://github.com/dubhatervapoursynth/vapoursynth-tivtc) |
| `tonemap` | R2 | `tonemap` | 允许自动加载 | [ifb/vapoursynth-tonemap](https://github.com/ifb/vapoursynth-tonemap) |
| `ttmpsm` | r4.1 | `ttmpsm` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-TTempSmooth](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-TTempSmooth) |
| `vivtc` | R1 | `vivtc` | 允许自动加载 | [vapoursynth/vivtc](https://github.com/vapoursynth/vivtc) |
| `vmaf` | r10 | `vmaf` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-VMAF](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-VMAF) |
| `vsfm` | r5.2.6 | `vsfm` | 允许自动加载 | [sorayuki/VSFilterMod](https://github.com/sorayuki/VSFilterMod) |
| `w2xnvk` | r5 | `w2xnvk` | 手动启用 | [Nlzy/vapoursynth-waifu2x-ncnn-vulkan](https://github.com/Nlzy/vapoursynth-waifu2x-ncnn-vulkan) |
| `w2xnvk_models` | r0.1 | 无（依赖包） | 由使用它的插件依赖 | [Nlzy/vapoursynth-waifu2x-ncnn-vulkan](https://github.com/Nlzy/vapoursynth-waifu2x-ncnn-vulkan) |
| `warp` | v4 | `warp` | 允许自动加载 | [dubhatervapoursynth/vapoursynth-awarpsharp2](https://github.com/dubhatervapoursynth/vapoursynth-awarpsharp2) |
| `xyvsf` | r3 | `xyvsf` | 允许自动加载 | [HomeOfVapourSynthEvolution/xy-VSFilter](https://github.com/HomeOfVapourSynthEvolution/xy-VSFilter) |
| `yadifmod` | r10 | `yadifmod` | 允许自动加载 | [HomeOfVapourSynthEvolution/VapourSynth-Yadifmod](https://github.com/HomeOfVapourSynthEvolution/VapourSynth-Yadifmod) |
| `znedi3` | r3.3 | `znedi3` | 允许自动加载 | [sekrit-twc/znedi3](https://github.com/sekrit-twc/znedi3) |

基础包内置的命名空间：`std`、`resize`、`text`、`avs`、`lsmas`、`imwri`。`text` 与 `avs` 不在当前默认画面处理链调用。

来源：[`default_pipeline.vpy`](../resources/vapoursynth/default_pipeline.vpy)、[`display.py`](../resources/vapoursynth/python/assetmaker_vs/display.py)、[`vs_runtime.json`](../config/vs_runtime.json)、[`vs-plugins.json`](../resources/packaging/vs-plugins.json)。LSMASHSource/IMWRI 上游地址另核对了 [官方 VSRepo](https://github.com/vapoursynth/vsrepo) 的包元数据。mvutensils 的上游地址来自项目已有滤镜文档；其实际安装字节来自清单指定的 PyPI wheel。
