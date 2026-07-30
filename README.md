# MediaFlow Pro

MediaFlow Pro 是面向 Windows 10/11 x64 的项目制视频创作工作站。V2 使用 PySide6/QML 构建桌面界面，Python 承载领域模型与工作流，MLT 统一生成实时预览和最终导出；所有内部控制通信都在桌面进程内完成，不启动常驻 API 服务。解析抖音、快手直链时，下载任务会按需使用本机 Chrome 或 Edge 的无界面 Playwright 会话。

项目采用 GPLv3，下载能力以随运行环境提供的 yt-dlp 为准。

## 已实现的产品能力

- 可移动项目目录，`project.mfp` SQLite 文件是项目唯一数据来源。
- 主序列与任意数量的短视频序列，共享素材、字幕、翻译和高光候选。
- 多轨视频、音频和字幕时间线；轨道随素材的拖放位置按需创建，片段可在同类轨道间移动，带音频的视频可解除视音频绑定后分别编辑；支持可见的多选模式、复合片段、成组移动、裁剪、分割、复制、普通删除、跨轨波纹删除、显式添加转场、变速、反向、画面变换，以及时间线与字幕文档共用的撤销/重做历史。拖动片段不会通过重叠自动创建转场。
- 时间线可一键分析最终合成画面的首尾黑屏，并以启用字幕中的首句、末句对白为准设置序列入点和出点；正常画面、音乐或环境声本身不会被视为对白。入出点只限定预览与导出范围，不移动或缩短素材，可继续拖动调整、清除或撤销。
- 预览画布支持滚轮缩放、中键平移、直接拖动画面、缩放和旋转，并提供定位、倍速、音量、静音和全屏控制；长片段波形只读取并绘制当前视口覆盖的部分。
- MLT 驱动的同源预览与导出，C++ Qt Quick 插件负责帧纹理、音频主时钟、定位和掉帧报告。
- 自动代理、波形、素材指纹、离线检测和单个/批量重新定位。
- 统一的转写工作区包含自动字幕、字幕编辑、翻译和术语表；转录前可确认实际源音频时长并选择 Whisper 模型、设备、语言和长音频并行度。任务只提取时间轴真实使用的源区间，内置 faster-whisper 与 Faster-Whisper XXL 都会对超过 15 分钟的音频按静音位置分块，并在资源允许时并行识别。桌面端不加载或展示全量词卡；词级时间作为项目数据保留，供 AI 通过 CLI 生成、预检和应用转录剪辑计划。
- 可编辑网页素材与视觉资源转场中心；网页包和普通视频、图片、音频通过同一素材入口导入，并通过真实浏览器渲染进入时间线，转场悬停通过临时 MLT 图预览。
- 场景切点检测、自动构图与主体跟踪会写入时间线标记或画面关键帧；素材搜索同时检索文件元数据、关联转写内容和中英文概念。
- 导出后对实际成片执行画面、冻结、静音、True Peak、时长和安全区检查，保留报告、证明帧与导出历史；命名版本保存完整 SQLite 快照并可恢复。支持向 Final Cut Pro / DaVinci Resolve 导出 FCPXML。
- yt-dlp 下载；首页可先读取视频标题、分辨率、帧率与可用画质，再按视频信息创建并打开项目，项目界面显示真实下载进度。YouTube 合集使用扁平分析并保留失效条目位置，X/Twitter 同时识别当前推文和引用推文中的视频，B 站支持合集/分 P，抖音和快手提供浏览器监听回退；faster-whisper 转录；OpenAI 兼容接口翻译与高光分析。
- SDR BT.709 与 HDR10 BT.2020/PQ 工程，支持 H.264、HEVC、AV1、ProRes 和独立字幕导出。
- 多音频总线、内置效果链、ducking、LUFS 和 True Peak 测量。
- 中文、英文、日文界面，键盘操作、高 DPI 和持久化面板布局。

## V2 结构

```text
mediaflow/
  domain/             项目、素材、序列、轨道、片段、字幕、音频与导出模型
  application/        编辑命令、任务编排、事件总线和工作流
  infrastructure/     SQLite、yt-dlp、FFmpeg、ASR、LLM、MLT
  desktop/
    controllers/      QML 可调用控制器
    models/           QAbstractListModel
    qml/              页面、组件和设计系统
    native/           最小 MLT Qt Quick C++ 插件
  resources/          字体和 Qt 翻译文件
tests/v2/             V2 单元、QML 与真实媒体集成测试
scripts/              原生构建和验收脚本
```

QML 不直接访问数据库或启动外部程序。桌面控制器和无头 CLI 都通过同一个 `EditorApplication` / `EditorProject` 应用接口执行真实编辑与任务流程；Python 类型是唯一合同来源，MLT 图是从 `project.mfp` 编译出的派生结果。

完整边界与线程模型见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 项目目录

```text
MediaFlow Pro/
  WorkSpace/
  Project/
    <ProjectName>/
      project.mfp
      generated/
      proxies/
      cache/
      exports/
```

应用目录是媒体和项目默认位置的唯一根目录。下载的视频与原始字幕默认进入应用级 `WorkSpace`，项目进入 `Project/<ProjectName>`，两者不会互相嵌套。合集按标题在 `WorkSpace` 中建立子目录，条目使用稳定序号、标题和媒体 ID 命名；项目通过绝对路径引用这些媒体。项目生成的字幕、代理、波形、分析结果和导出仍由各自的项目目录管理。素材失踪时会保持为离线记录，重新定位需要指纹验证或用户明确确认。

## 开发运行环境

版本固定为：

- Python 3.12 x64
- PySide6 / Qt 6.11.1
- MLT 7.40
- yt-dlp 2026.3.17
- Playwright 1.61.0（复用本机 Chrome/Edge，不下载单独浏览器）
- OpenCV 5.0.0（场景与主体运动分析）
- FFmpeg（GPL 构建）

依赖、模型和构建缓存默认位于 `D:\Tools\MediaFlow`。若没有 D 盘，首次启动会要求用户明确选择运行环境目录，不会静默占用 C 盘；也可以预先设置 `MEDIAFLOW_RUNTIME_DIR`、`MEDIAFLOW_MELT`、`MEDIAFLOW_NATIVE_QML`、`MEDIAFLOW_FFMPEG` 和 `MEDIAFLOW_FFPROBE`。

创建开发环境：

```powershell
py -3.12 -m venv D:\Tools\MediaFlow\.venv
$env:PIP_CACHE_DIR = "D:\Tools\MediaFlow\pip-cache"
D:\Tools\MediaFlow\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
D:\Tools\MediaFlow\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
```

`pyproject.toml` 只声明直接依赖，`requirements.lock` 是开发、测试和构建环境实际安装版本的唯一清单。修改依赖后，使用锁文件中固定的 `pip-tools` 重新生成并审查完整依赖图：

```powershell
$env:PIP_CACHE_DIR = "D:\Tools\MediaFlow\pip-cache"
D:\Tools\MediaFlow\.venv\Scripts\python.exe -m piptools compile pyproject.toml --extra dev --extra build --generate-hashes --resolver backtracking --allow-unsafe --strip-extras --output-file requirements.lock
```

原生预览插件要求 MSVC 2022、完整 Qt 6.11.1 SDK 以及 CMake/Ninja。默认路径可通过脚本参数覆盖：

```powershell
.\scripts\prepare_mlt_preview.ps1
.\scripts\build_native.ps1
```

启动应用：

```powershell
D:\Tools\MediaFlow\.venv\Scripts\python.exe -m mediaflow.desktop.app
```

也可以把项目目录作为首个参数直接打开。

## 无头 CLI

`mediaflow-cli` 是桌面界面之外的结构化自动化入口，复用同一个 Editor API，不另起服务，也不复制任务实现。它使用版本化 JSON 合同，命令始终向标准输出写 JSON，失败时返回非零退出码和稳定错误码。调用方应先读取 `describe`，再按返回的能力和参数 schema 选择操作。

```powershell
mediaflow-cli describe
Get-Content request.json -Raw | mediaflow-cli execute --request -
```

自动化程序也可以通过文件或标准输入发送统一请求：

```json
{
  "protocol": "mediaflow-cli",
  "version": 1,
  "operation": "task.start",
  "project": "D:\\Projects\\Demo",
  "arguments": {
    "task_command": {
      "command_type": "generate_waveform",
      "asset_id": "素材 ID"
    },
    "input_asset_ids": ["素材 ID"]
  }
}
```

```powershell
mediaflow-cli execute --request request.json
Get-Content request.json -Raw | mediaflow-cli execute --request -
```

AI 文字剪辑使用 `transcript.get`、`transcript.edit.preview`、`transcript.edit.apply` 三步合同。AI 必须先读取带项目修订号的源转录，再提交包含删除理由和词或字幕段 ID 的计划；应用命令只接受原样返回的预检计划，并在修改前自动创建命名恢复版本。只有识别器提供的真实词级时间可以按词删除，估算词时间只能通过完整字幕段删除。预检发现锁定轨道可能失去同步时，应用方必须在审阅警告后显式传入 `accept_warnings: true`。`project.version.list` 和 `project.version.restore` 可用于检查和恢复自动保存的版本。

当前自动化边界到 CLI 为止，不需要常驻 MCP 服务。如果以后确实需要让支持 MCP 的外部客户端调用，只应增加转接层，Editor API 仍是唯一实现入口。

### 可编辑网页素材

MediaFlow Pro 可导入 `editable-media` v4 本地网页包。网页包用 `window.editableMedia` 暴露结构化编辑状态，并用 `window.__hf.duration/seek(seconds)` 暴露唯一的确定性逐帧边界。v4 的每个媒体源必须明确声明由浏览器渲染、由 FFmpeg 作为原生音频解码，或由 FFmpeg 作为原生视频底层解码；MediaFlow 不再根据扩展名或 DOM 用法猜测处理方式。进程级 `WebCaptureEngine` 复用本机 Chromium，按素材长度、画布大小、逻辑处理器和可用内存启用有界并行页面，通过 Chrome 的快速无损截图接口取得透明 PNG，再严格按帧序交给同一个 FFmpeg 输入管道。原生视频在该管道内完成缩放、裁切或留边并与浏览器透明画面合成，原生音频也在同一次编码中完成定位、循环、增益和混音，最终形成同时供预览、MLT 时间线和交接导出读取的单一 MKV 缓存。

验证器和生产捕获共用同一条 `await seek → 强制布局与样式刷新` 边界，不再为每帧固定等待一次浏览器动画帧。首次捕获会用帧 0 锚点和前后跳转验证随机访问；实验性的 `drawElementImage` 必须在每个 worker 上分别通过真实截图的原图 PSNR、模糊结构 PSNR、平均误差、局部最大误差、Alpha 和稳定阶段耗时联合对照，所有 worker 一致通过后才会接管。一次性 Canvas 初始化不参与稳定捕获计时，避免某个 worker 因冷启动误判后拖累整次任务。高速后端若在正式捕获中失效，当前 FFmpeg 尝试会被整体废弃并从帧 0 改用截图路径重跑，不会留下混合后端缓存。纯浏览器画面的 610 帧重复基准中，四个 worker 的快速后端输出与单 worker 输出始终 610 帧逐帧一致，耗时为 14.89–16.74 秒、吞吐为 36.45–40.98 fps。MediaFlow 保留自己的项目状态、缓存键、原子输出和 MLT 合成边界，不引入 HyperFrames 依赖或第二套网页动画时钟。

网页包保存组件结构与复杂动画，`project.mfp` 的 `WebClipState` 保存每个片段的文字、样式、比例布局、关键帧、主题、数据快照和字段锁。桌面界面与 CLI 读写同一项目状态，网页素材换版按稳定图层 ID 迁移，原网页目录会保留而不会被回写。导入扫描会同时计算全包身份和每个媒体文件的 SHA-256、字节数及服务 MIME 类型；复制到项目时最多并行处理四个文件，并在写入过程中复核字节数和 SHA-256，不会在落盘后再次完整读取大型视频。发布目录提交后不可变，换版只会创建新的发布目录；渲染直接使用发布时保存的内容哈希，显式素材审计才重新读取全包。带原生音频的网页素材会建立绑定视音频剪辑，不会为 HTML 入口错误生成波形。缓存键包含浏览器状态、原生媒体时间线、素材内容、帧范围和音频规格；只有视频流、可选 FLAC 音频流、帧目标、边缘内容指纹和原子 manifest 全部通过 FFprobe 与内容校验时才会命中。可通过 `MEDIAFLOW_WEB_WORKERS=1..8` 限制捕获进程数；默认最多使用 4 个，并按帧数、分辨率、逻辑处理器和实时可用内存自动收缩，诊断会记录实际限制来源及 seek、捕获、队列等待和帧耗时分位数。

相关能力可从 `mediaflow-cli describe` 读取，包括 `web.import`、`web.clip.keyframe.*`、`web.clip.theme.update`、`web.clip.layout.select`、`web.clip.data.*`、`web.clip.diff`、`web.batch.create`、`web.asset.rebind` 和 `web.clip.export`。远程网址、登录态网页和任意 DOM/CSS 可视化开发不属于该边界。

## 测试与真实验收

```powershell
D:\Tools\MediaFlow\.venv\Scripts\python.exe -m pytest tests\v2
D:\Tools\MediaFlow\.venv\Scripts\python.exe -m scripts.verify_ui_matrix
D:\Tools\MediaFlow\.venv\Scripts\python.exe -m scripts.verify_performance
D:\Tools\MediaFlow\.venv\Scripts\python.exe -m scripts.verify_preview_performance
D:\Tools\MediaFlow\.venv\Scripts\python.exe -m scripts.verify_web_render_performance
D:\Tools\MediaFlow\.venv\Scripts\python.exe -m scripts.verify_display_capabilities
D:\Tools\MediaFlow\.venv\Scripts\python.exe -m scripts.verify_real_user_chain
```

测试覆盖领域计算、SQLite 事务、QML 页面、原生预览、代理、下载、转录、翻译、高光、MLT 导出、HDR 元数据及预览/导出抽帧比对。需要网络、模型或 API 凭据的验收脚本会使用真实服务，不以消费端伪造数据代替生产链路。

## 许可与分发

MediaFlow Pro 源码以 [GNU GPL v3](LICENSE) 发布。随运行目录提供的 Qt、MLT、FFmpeg、yt-dlp、Python 包及其他组件仍适用各自许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

本仓库只维护可重复构建脚本和依赖清单；除非项目所有者明确要求，不生成便携包或安装器。
