# 10 · 调研方法：版本、源码、探针与验收分层

**结论：任何结论都要同时回答“哪个上游版本、哪个产品提交、哪个运行时根、什么
输入、跑了什么”。某次网络或工具不可用只是当次执行限制，不能永久写成技术事实。**

## 证据分层

| 层 | 用途 | 不能证明什么 |
|---|---|---|
| 固定 tag 官方文档/源码 | 证明指定 R73/R79 的 API、常量和实现边界 | 本项目已正确调用或候选二进制与 tag 相同 |
| 当前项目源码 | 证明这个提交实际构造的 job、脚本、协议和命令 | 二进制可加载、插件可用、帧像素正确 |
| 运行时身份探针 | 证明实际 binding/core/VSPipe/plugin/version/hash | 完整产品场景或冻结包已通过 |
| 定向测试 | 证明一个明确合同或回归 | 未覆盖的真实媒体、线程时序或部署形态 |
| 真实 worker/VSPipe/编码 | 证明给定输入和环境的端到端行为 | 所有媒体、机器或未来版本都成立 |
| 历史报告/第三方经验 | 提供线索、风险和复现实验方向 | 当前状态或项目合同 |

不要用“运行时实测优先于一切”替代分析：若探针与固定 tag 文档冲突，先核对实际
二进制身份、探针输入和 API 层，再解释差异。mock 测试与真实媒体测试也必须分开
报告；真实媒体用例被 skip 时，绿色结果不能冒充端到端证据。

## 固定版本而不是滚动网页

R73 历史基线引用（不能作为当前运行时证明）：

```text
https://github.com/vapoursynth/vapoursynth/blob/R73/...
```

当前 R79 的上游语义应核对固定版本来源，并结合本项目源码及运行时证据：

```text
https://github.com/vapoursynth/vapoursynth/blob/R79/...
```

滚动的 `vapoursynth.com/doc`、master/main、论坛和插件数据库可用于发现线索，但必须
再落到 tag、release artifact、候选文件 hash 或可复现探针。若某 tag 页面暂时无法
访问，记录“本次未取得”即可；不要把抓取方式、超时或某个工具缺席写成永久限制。

## 当前项目的最小复核序列

### 1. 先固定源码与文件入口

```powershell
git rev-parse HEAD
git status --short
rg --files resources/vapoursynth core/vs_runtime tests
```

核对默认脚本、固定 runner、portable helper、runtime config、worker process 与实际
测试文件存在，再引用命令。不要沿用已删除模块或旧测试名。

### 2. 固定运行时身份

```powershell
tools\media\runtime\Lib\site-packages\vapoursynth\vspipe.exe --version
Get-Content tools\media\runtime\Lib\site-packages\vapoursynth-79.dist-info\METADATA
uv run python media_distribution.py verify-tree --app-dir . --manifest resources/packaging/media-tools-r79-v1.json
```

切换源码不会部署被 Git 忽略的媒体文件；先确认命令所在目录有完整 R79 包。
升级时还要保存 core/binding/VSPipe、portable 文件、插件 DLL、CPU 变体/manifest
和 helper 的 SHA-256，并证明 worker 与 VSPipe 都从该根启动。

### 3. 先跑纯合同，再跑真实媒体

```powershell
uv run python -m unittest -v `
  tests.test_vs_job_contract `
  tests.test_default_vpy_pipeline `
  tests.test_vs_output_contract `
  tests.test_vspipe_render_request

uv run python -m unittest -v `
  tests.test_preview_worker_real_acceptance `
  tests.test_worker_vspipe_parity `
  tests.test_media_encode_integration
```

命令结束后必须记录 tests、failures、errors、skips、exit code。真实测试还要记录素材
hash、工具版本与是否实际产生/回读编码结果。

### 4. 为高风险版本差异写最小探针

- Range：固定 YUV 值与冲突 frame props，分别测试 `_Range`、`_ColorRange`、
  参数兜底、输出类型和编码回读。
- plugin：逐目录加载，枚举 `core.plugins()`、callable、`plugin_path` 和 log warning；
  不能只看 `hasattr`。
- frame：包含 stride padding、奇数 RGB 视口、一像素窗口、Future error、close 后
  生命周期与 cancel ACK。
- core 资源：捕获原生基线，分别测试 0、非零配置、用户脚本污染、退休与 restart。

探针代码、输入和原始输出应随报告保留；只抄结论会让下一次升级无法复查。

## 性能数据的最低口径

必须说明计时起止点。当前 F4 的数据是“鼠标输入→QLabel Paint 开始分派”，不是
绘制结束、物理呈现、VS 帧完成或解码 FPS；P50/P95 只描述当次有限样本。事件发生
阶段和请求提交 cohort 要分开，IPC submit/complete 不应改名为底层解码次数。

## 当前未闭合项

- R79 已有 Range、布局、插件相关实现与定向证据，临时冻结媒体树也已校验。
  完整冻结版真实预览/导出和目标目录部署仍需完成，详见 [08](08-version-upgrade-notes.md)。
- `max_cache_size` 不是 RSS 硬上限；需用进程级内存观测验证真实压力。
- native 插件直接写 OS fd1 可能污染 Y4M；Python `sys.stdout` 重定向不覆盖它。
- `.lwi` 的损坏恢复和跨进程并发需要独立压力测试，不能靠论坛意见闭合。

## 相关

- [INDEX](INDEX.md) — 当前合同与问题路由
- [08 版本升级](08-version-upgrade-notes.md) — U 阶段门禁
- [12 现场风险](12-field-hazards.md) — 尚未闭合的运行时陷阱
