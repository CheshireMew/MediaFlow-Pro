<p align="center">
  <img src="mediaflow/resources/branding/mediaflow-mark.svg" width="112" alt="MediaFlow Pro 标志">
</p>

# MediaFlow Pro

<!-- readme-header:start -->

<p align="center">
  <strong>中文</strong> · <a href="./README.en.md">English</a> · <a href="./README.ja.md">日本語</a> | <a href="./ARCHITECTURE.md">文档</a> | <a href="./CONTRIBUTING.md">贡献</a> | <a href="https://github.com/CheshireMew/MediaFlow-Pro/issues">反馈</a>
</p>

<p align="center">
  <a href="https://x.com/0xCheshire" title="X"><img src="https://img.shields.io/badge/X-%400xCheshire-000000?logo=x&amp;logoColor=white" alt="X：@0xCheshire"></a>
  <a href="https://t.me/CheshireBTC" title="Telegram"><img src="https://img.shields.io/badge/Telegram-CheshireBTC-26A5E4?logo=telegram&amp;logoColor=white" alt="Telegram：CheshireBTC"></a>
  <a href="https://blog.blacknico.com/" title="Blog"><img src="https://img.shields.io/badge/Blog-blog.blacknico.com-2E7D32?logo=rss&amp;logoColor=white" alt="博客：blog.blacknico.com"></a>
  <a href="https://blacknico.com/" title="Homepage"><img src="https://img.shields.io/badge/Home-blacknico.com-1F6FEB?logo=googlechrome&amp;logoColor=white" alt="个人主页：blacknico.com"></a>
</p>

<p align="center">
  <a href="https://github.com/CheshireMew/MediaFlow-Pro/stargazers"><img src="https://img.shields.io/github/stars/CheshireMew/MediaFlow-Pro?style=flat" alt="GitHub Stars"></a>
  <a href="https://github.com/CheshireMew/MediaFlow-Pro/forks"><img src="https://img.shields.io/github/forks/CheshireMew/MediaFlow-Pro?style=flat" alt="GitHub Forks"></a>
  <a href="https://github.com/CheshireMew/MediaFlow-Pro/blob/main/LICENSE"><img src="https://img.shields.io/github/license/CheshireMew/MediaFlow-Pro?style=flat" alt="Repository License"></a>
</p>

<!-- readme-header:end -->

MediaFlow Pro 是一套以项目为中心的本地视频创作工作站。把视频、音频、图片、字幕、媒体链接或 `editable-media` v6 网页包交给它，可以在同一个可移动工程里完成素材管理、转写、剪辑、多轨时间线、实时预览、混音、质量检查和最终导出。

`project.mfp` 是工程状态的唯一数据来源；预览与导出都从同一条时间线编译，避免“编辑器里看到的”和“最后导出的”走两套逻辑。

[快速开始](#快速开始) · [产品能力](#你可以用它做什么) · [editable-media](#editable-media-网页包) · [CLI 与 MCP](#cli-与-mcp-自动化) · [架构说明](ARCHITECTURE.md)

![MediaFlow Pro 中文桌面工作区](docs/images/mediaflow-workspace-zh-cn.png)

<p align="center"><sub>真实 Qt/QML 桌面验收截图：素材、预览、检查器和多轨时间线位于同一个工作区。</sub></p>

> [!IMPORTANT]
> 当前仓库交付源码、固定依赖和可重复构建入口，不提供预打包安装器。Windows 10/11 x64 覆盖完整桌面、原生预览和导出验收；Ubuntu 24.04 x64 与 macOS 14+ Apple Silicon 已具备源码构建、运行时合同、CI 构建和原生预览冒烟，但尚未宣称完成对应平台的完整实机发布验收。

## 适合哪些工作

| 你要完成的事情 | MediaFlow Pro 提供的结果 |
| --- | --- |
| 把原片、图片、音频和字幕剪成可反复修改的视频 | 可移动项目、多轨时间线、同源预览与导出 |
| 把结构化网页动画和普通素材放进同一条时间线 | `editable-media` v6 导入、统一字段编辑、关键帧、换版、真实时间胶片条与可恢复的浏览器逐帧渲染 |
| 根据转录文字做剪辑、翻译或高光处理 | 转录工作区，以及可预检、可撤销的 CLI 自动化操作 |
| 检查成片是否符合交付要求 | 黑帧、冻结、静音、响度、时长、安全区和参考视频对照报告 |
| 下载网络媒体后继续编辑 | yt-dlp 下载、站点信息读取、项目创建与真实进度展示 |

如果你只需要把完整 HTML 动画确定性渲染成视频，而不需要非线性编辑工程，[HyperFrames](https://github.com/heygen-com/hyperframes) 会更直接。MediaFlow Pro 面向需要原片、多轨、字幕、声音和后续修改的制作链。

## 快速开始

### 1. 准备环境

需要 Python 3.12、当前平台的 C++20 工具链，以及足够容纳 Qt、MLT、FFmpeg、Chromium、缓存和媒体文件的磁盘空间。运行时版本和 SHA-256 由 [`runtime.lock.json`](runtime.lock.json) 固定，Python 依赖由 [`requirements.lock`](requirements.lock) 固定。

先复制 [`.env.example`](.env.example) 并填写三个机器级目录：

| 变量 | 用途 |
| --- | --- |
| `MEDIAFLOW_DEV_ROOT` | Python 环境、SDK、运行时、构建和缓存 |
| `MEDIAFLOW_PROJECT_ROOT` | 新项目的默认保存根目录 |
| `MEDIAFLOW_MEDIA_ROOT` | 下载、导入和转写源媒体的应用级根目录 |

Windows PowerShell：

```powershell
Copy-Item .env.example .env
# 编辑 .env 后继续
. .\scripts\load_environment.ps1

py -3.12 -m venv (Join-Path $env:MEDIAFLOW_DEV_ROOT ".venv")
& $env:MEDIAFLOW_PYTHON -m pip install --require-hashes -r requirements.lock
& $env:MEDIAFLOW_PYTHON -m pip install --no-deps --no-build-isolation -e .

& $env:MEDIAFLOW_PYTHON scripts\prepare_runtime.py --runtime-root $env:MEDIAFLOW_RUNTIME_DIR
& $env:MEDIAFLOW_PYTHON scripts\prepare_ci_qt.py --qt-root (Join-Path $env:MEDIAFLOW_RUNTIME_DIR "qt")
& $env:MEDIAFLOW_PYTHON scripts\build_native.py --runtime-root $env:MEDIAFLOW_RUNTIME_DIR
```

<details>
<summary>Ubuntu / macOS 命令</summary>

```bash
cp .env.example .env
# 编辑 .env 后继续
set -a
. ./.env
set +a

export MEDIAFLOW_RUNTIME_DIR="${MEDIAFLOW_RUNTIME_DIR:-$MEDIAFLOW_DEV_ROOT/runtime}"
export MEDIAFLOW_PYTHON="${MEDIAFLOW_PYTHON:-$MEDIAFLOW_DEV_ROOT/.venv/bin/python}"

python3.12 -m venv "$MEDIAFLOW_DEV_ROOT/.venv"
"$MEDIAFLOW_PYTHON" -m pip install --require-hashes -r requirements.lock
"$MEDIAFLOW_PYTHON" -m pip install --no-deps --no-build-isolation -e .

"$MEDIAFLOW_PYTHON" scripts/prepare_runtime.py --runtime-root "$MEDIAFLOW_RUNTIME_DIR"
"$MEDIAFLOW_PYTHON" scripts/prepare_ci_qt.py --qt-root "$MEDIAFLOW_RUNTIME_DIR/qt"
"$MEDIAFLOW_PYTHON" scripts/build_native.py --runtime-root "$MEDIAFLOW_RUNTIME_DIR"
```

</details>

### 2. 启动应用

Windows：

```powershell
.\scripts\launch.ps1
```

Ubuntu / macOS：

```bash
"$MEDIAFLOW_PYTHON" -m mediaflow.desktop.app
```

三平台都可以把项目目录作为首个参数传入，直接打开已有项目。

### 3. 完成第一次编辑

1. 在首页新建空白项目，或打开内置示例了解完整界面。
2. 导入本地媒体、字幕、网页包，或粘贴媒体链接开始下载。
3. 把素材拖入时间线，完成裁剪、字幕、混音和画面调整。
4. 在节目监视器检查最终合成结果，再从“导出”生成成片和质量报告。

![MediaFlow Pro 中文首页](docs/images/mediaflow-home-zh-cn.png)

<p align="center"><sub>首页可以新建项目、粘贴链接开始下载，或打开和体验已有项目。</sub></p>

## 你可以用它做什么

| 工作区 | 已实现能力 |
| --- | --- |
| 项目与素材 | 可移动项目目录、素材文件夹、代理、波形、指纹、离线检测、重新定位和版本快照 |
| 时间线 | 多序列、多轨视频/音频/字幕、裁剪、分割、复制、波纹删除、转场、换源、变速、反向、复合片段、效果链和统一撤销/重做 |
| 预览与画面 | 源监视器、节目监视器、画布变换、原生音频时钟、HDR/SDR 工程和 MLT 同源预览 |
| 文字与字幕 | faster-whisper / Faster-Whisper XXL 转录、字幕编辑、翻译、术语表和基于真实词级时间的文字剪辑 |
| 音频 | 多总线、效果链、ducking、LUFS 与 True Peak 测量 |
| 分析与交付 | 场景切点、主体跟踪、黑帧/冻结/静音检查、参考视频逐帧对照、H.264/HEVC/AV1/ProRes、独立字幕和 FCPXML |
| 界面与工作区 | 中文、英文、日文界面，高 DPI、键盘操作，以及标准、媒体、竖屏三套可持久化布局 |

下载与按需运行组件的实际可用性取决于当前站点和本机环境。远程登录态网页不属于 `editable-media` 导入边界；网页包必须是本地、可验证、可确定性定位的目录。

## `editable-media` 网页包

MediaFlow Pro 正式消费通用的 `editable-media` v6 本地网页包，不依赖某个生产者的仓库布局或案例名称。DOM、React 或其它前端技术都只是成品包的生产方式；导入后统一成为普通 Web 素材，不会形成第二套项目状态或导出管线。

- `window.editableMedia` 提供文字、样式、变体、场景、图层、参数和素材槽等结构化状态。
- `window.__hf.duration`、异步 `window.__hf.seek(seconds)`、renderer 注册和 frame task 共同提供唯一的确定性逐帧时间与准备边界。
- 网页包明确声明素材由浏览器渲染，还是作为原生视频底层或原生音频进入合成；MediaFlow Pro 不根据扩展名猜测。
- 原始网页包不会被回写。片段状态、换版记录和项目引用保存在 `project.mfp`，发布后的项目内网页目录保持不可变。
- 浏览器画面、原生视频和原生音频会进入同一个缓存与 FFmpeg 编码管道，供预览、时间线和导出共同消费。

历史项目中的标准 v4/v5 网页素材会在事务性项目升级中直接迁移到 v6；旧包移入项目内的 `archive/web` 供人工追溯，不再保留第二套运行分支。无法证明可安全转换的第三方 runtime 会明确中止升级并要求重新发布。

## CLI 与 MCP 自动化

`mediaflow-cli` 是常驻 Editor Service 的结构化客户端。第一次调用会按需启动服务，后续命令只发送请求；CLI 不直接打开 `project.mfp`，也不绕过项目写锁。

先读取当前机器实际公开的操作、参数和能力要求：

```powershell
mediaflow-cli describe
```

再通过文件或标准输入发送 `mediaflow-editor` v4 JSON 请求：

```powershell
mediaflow-cli execute --request request.json
Get-Content request.json -Raw | mediaflow-cli execute --request -
```

写请求使用稳定的 `request_id`、最近一次读取取得的 `base_revision`、`actor` 和 `client_id`。相同重试会复用持久化回执；不相交的过期写入可以重放，冲突写入会明确失败，不会静默覆盖。

导出、转录、网页字段与关键帧、网页换版、项目交接和诊断界面可以直接预览并复制同一份可执行请求，不会因为复制而启动任务或增加项目修订。`diagnostics.bundle.create` 会作为持久任务生成有大小上限且排除原始媒体与凭据的诊断 ZIP。

支持 MCP 的宿主可以把 `mediaflow-mcp` 配置为 stdio server。它与桌面端和 CLI 共用同一个 Editor Service，不包含第二套编辑实现。具体工具和参数仍以 `mediaflow-cli describe` 的实时输出为准。

## 项目与架构边界

```text
<MEDIAFLOW_PROJECT_ROOT>/
  <ProjectName>/
    project.mfp
    generated/
    proxies/
    cache/
    exports/
```

- `project.mfp` 是项目模型、时间线状态、字幕、网页片段状态和版本信息的唯一真源。
- MLT 图、网页渲染缓存、代理、波形和分析报告都是可重建的派生结果。
- QML 不直接访问数据库或启动外部程序；桌面端、CLI 和 MCP 都调用同一个 `EditorApplication` / `EditorProject` 边界。
- 每个登录用户只有一个按需启动的本机 Editor Service，只有服务进程可以取得项目写锁。
- `.env.example` 是机器路径变量的公开合同，源码不会根据盘符或系统安装目录猜测运行时。

领域分层、线程模型、持久化边界和服务协议见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 开发与验证

修改前先加载本机环境，并运行直接覆盖改动的目标测试。目标测试通过后，使用唯一的本地质量入口按改动范围完成最终验证：

```powershell
. .\scripts\load_environment.ps1
& $env:MEDIAFLOW_PYTHON -m pytest tests\v2\path\to\test_file.py
.\scripts\run_quality.ps1
```

不要直接运行没有资源边界的整库 `pytest tests/v2`。本地入口和 CI 都由 [`scripts/ci/quality_plan.py`](scripts/ci/quality_plan.py) 统一决定范围，并把跨平台源码构建与项目交接分开；文档修改不会触发无关的桌面端或端到端验收。使用 `.\scripts\run_quality.ps1 --dry-run` 可以预览实际命令。

桌面日志保存在运行目录的 `logs/mediaflow.log`，达到 5 MiB 后轮转并保留 5 个备份。操作失败弹窗末尾的短编号会原样写入日志，反馈问题时请一并提供该编号。问题反馈使用 [GitHub Issues](https://github.com/CheshireMew/MediaFlow-Pro/issues)。

## 许可与分发

MediaFlow Pro 源码以 [GNU GPL v3](LICENSE) 发布。Qt、MLT、FFmpeg、yt-dlp、Python 包及其它第三方组件仍适用各自许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

本仓库只维护源码、构建脚本和依赖清单。除非项目所有者明确启动发布计划，否则不会生成便携包或安装器。

## Star History

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/CheshireMew/MediaFlow-Pro/star-history/star-history-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/CheshireMew/MediaFlow-Pro/star-history/star-history.svg">
  <img alt="MediaFlow Pro GitHub Star History" src="https://raw.githubusercontent.com/CheshireMew/MediaFlow-Pro/star-history/star-history.svg">
</picture>
