<p align="center">
  <img src="mediaflow/resources/branding/mediaflow-mark.svg" width="112" alt="MediaFlow Pro logo">
</p>

# MediaFlow Pro

<!-- readme-header:start -->

<p align="center">
  <a href="./README.md">中文</a> · <strong>English</strong> · <a href="./README.ja.md">日本語</a> | <a href="./ARCHITECTURE.md">Docs</a> | <a href="./CONTRIBUTING.md">Contributing</a> | <a href="https://github.com/CheshireMew/MediaFlow-Pro/issues">Issues</a>
</p>

<p align="center">
  <a href="https://x.com/0xCheshire" title="X"><img src="https://img.shields.io/badge/X-%400xCheshire-000000?logo=x&amp;logoColor=white" alt="X: @0xCheshire"></a>
  <a href="https://t.me/CheshireBTC" title="Telegram"><img src="https://img.shields.io/badge/Telegram-CheshireBTC-26A5E4?logo=telegram&amp;logoColor=white" alt="Telegram: CheshireBTC"></a>
  <a href="https://blog.blacknico.com/" title="Blog"><img src="https://img.shields.io/badge/Blog-blog.blacknico.com-2E7D32?logo=rss&amp;logoColor=white" alt="Blog: blog.blacknico.com"></a>
  <a href="https://blacknico.com/" title="Homepage"><img src="https://img.shields.io/badge/Home-blacknico.com-1F6FEB?logo=googlechrome&amp;logoColor=white" alt="Homepage: blacknico.com"></a>
</p>

<p align="center">
  <a href="https://github.com/CheshireMew/MediaFlow-Pro/stargazers"><img src="https://img.shields.io/github/stars/CheshireMew/MediaFlow-Pro?style=flat" alt="GitHub Stars"></a>
  <a href="https://github.com/CheshireMew/MediaFlow-Pro/forks"><img src="https://img.shields.io/github/forks/CheshireMew/MediaFlow-Pro?style=flat" alt="GitHub Forks"></a>
  <a href="https://github.com/CheshireMew/MediaFlow-Pro/blob/main/LICENSE"><img src="https://img.shields.io/github/license/CheshireMew/MediaFlow-Pro?style=flat" alt="Repository License"></a>
</p>

<!-- readme-header:end -->

MediaFlow Pro is a project-based local video creation workstation. Give it video, audio, images, subtitles, media links, or an `editable-media` v6 web package, and it keeps asset management, transcription, editing, multitrack timelines, real-time preview, mixing, quality checks, and final export inside one portable project.

`project.mfp` is the single source of truth for project state. Preview and export are compiled from the same timeline, so the editor and the final render do not follow separate implementations.

[Quick start](#quick-start) · [Capabilities](#what-you-can-do) · [editable-media](#editable-media-web-packages) · [CLI and MCP](#cli-and-mcp-automation) · [Architecture](ARCHITECTURE.md)

![MediaFlow Pro desktop workspace in Chinese](docs/images/mediaflow-workspace-zh-cn.png)

<p align="center"><sub>Real Qt/QML acceptance screenshot: assets, preview, inspector, and a multitrack timeline share one desktop workspace.</sub></p>

> [!IMPORTANT]
> This repository ships source code, pinned dependencies, and reproducible build entry points. It does not ship a prebuilt installer. Windows 10/11 x64 covers full local desktop, native preview, and export acceptance. Ubuntu 24.04 x64 and macOS 14+ on Apple Silicon have source builds, runtime contracts, CI builds, and native-preview smoke tests, but have not yet been claimed as fully validated release targets on physical machines.

## What MediaFlow Pro is for

| What you need to do | What MediaFlow Pro delivers |
| --- | --- |
| Turn footage, images, audio, and subtitles into a project you can keep revising | Portable projects, a multitrack timeline, and one preview/export path |
| Put structured web animation and regular media on the same timeline | `editable-media` v6 import, unified field editing, keyframes, package replacement, true-time filmstrips, and recoverable deterministic browser rendering |
| Edit, translate, or find highlights from a transcript | A transcription workspace and previewable, undoable CLI automation |
| Verify that a final video meets delivery requirements | Reports for black frames, freezes, silence, loudness, duration, safe areas, and reference-video comparison |
| Download online media and continue editing it locally | yt-dlp downloads, source inspection, project creation, and real progress reporting |

If you only need to deterministically render finished HTML animation to video and do not need a nonlinear editing project, [HyperFrames](https://github.com/heygen-com/hyperframes) is the more direct tool. MediaFlow Pro is for workflows that combine footage, multiple tracks, subtitles, sound, and repeated revisions.

## Quick start

### 1. Prepare the environment

You need Python 3.12, a C++20 toolchain for the target platform, and enough disk space for Qt, MLT, FFmpeg, Chromium, caches, and media files. [`runtime.lock.json`](runtime.lock.json) pins runtime versions and SHA-256 checksums; [`requirements.lock`](requirements.lock) pins Python dependencies.

Copy [`.env.example`](.env.example) and configure these three machine-level roots:

| Variable | Purpose |
| --- | --- |
| `MEDIAFLOW_DEV_ROOT` | Python environment, SDKs, runtimes, builds, and caches |
| `MEDIAFLOW_PROJECT_ROOT` | Default root for new projects |
| `MEDIAFLOW_MEDIA_ROOT` | Application-level root for downloaded, imported, and transcribed media |

Windows PowerShell:

```powershell
Copy-Item .env.example .env
# Edit .env before continuing
. .\scripts\load_environment.ps1

py -3.12 -m venv (Join-Path $env:MEDIAFLOW_DEV_ROOT ".venv")
& $env:MEDIAFLOW_PYTHON -m pip install --require-hashes -r requirements.lock
& $env:MEDIAFLOW_PYTHON -m pip install --no-deps --no-build-isolation -e .

& $env:MEDIAFLOW_PYTHON scripts\prepare_runtime.py --runtime-root $env:MEDIAFLOW_RUNTIME_DIR
& $env:MEDIAFLOW_PYTHON scripts\prepare_ci_qt.py --qt-root (Join-Path $env:MEDIAFLOW_RUNTIME_DIR "qt")
& $env:MEDIAFLOW_PYTHON scripts\build_native.py --runtime-root $env:MEDIAFLOW_RUNTIME_DIR
```

<details>
<summary>Ubuntu / macOS commands</summary>

```bash
cp .env.example .env
# Edit .env before continuing
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

### 2. Launch the application

Windows:

```powershell
.\scripts\launch.ps1
```

Ubuntu / macOS:

```bash
"$MEDIAFLOW_PYTHON" -m mediaflow.desktop.app
```

On all three platforms, pass a project directory as the first argument to open it directly.

### 3. Complete your first edit

1. Create an empty project from the home page, or open the built-in sample to explore the full interface.
2. Import local media, subtitles, or a web package, or paste a media link to start a download.
3. Drag assets onto the timeline and edit clips, subtitles, audio, and picture settings.
4. Check the final composition in the program monitor, then use Export to produce the video and its quality report.

![MediaFlow Pro home page in Chinese](docs/images/mediaflow-home-zh-cn.png)

<p align="center"><sub>The home page creates projects, starts link downloads, and opens existing or sample projects.</sub></p>

## What you can do

| Workspace | Implemented capabilities |
| --- | --- |
| Projects and assets | Portable project directories, asset folders, proxies, waveforms, fingerprints, offline detection, relinking, and version snapshots |
| Timeline | Multiple sequences and video/audio/subtitle tracks; trim, split, copy, ripple delete, transitions, source replacement, speed, reverse, compound clips, effect chains, and unified undo/redo |
| Preview and picture | Source and program monitors, canvas transforms, a native audio clock, HDR/SDR projects, and MLT-backed preview |
| Text and subtitles | faster-whisper / Faster-Whisper XXL transcription, subtitle editing, translation, terminology, and text-based editing from real word timestamps |
| Audio | Multiple buses, effect chains, ducking, LUFS, and True Peak measurement |
| Analysis and delivery | Scene cuts, subject tracking, black-frame/freeze/silence checks, frame-by-frame reference comparison, H.264/HEVC/AV1/ProRes, separate subtitles, and FCPXML |
| Interface and workspaces | Chinese, English, and Japanese UI; high DPI and keyboard support; persistent standard, media, and vertical layouts |

Download and optional runtime availability depends on the current source site and local environment. Remote authenticated pages are outside the `editable-media` import boundary: a web package must be local, verifiable, and deterministically seekable.

## `editable-media` web packages

MediaFlow Pro formally consumes generic local `editable-media` v6 packages. It does not depend on a producer repository layout or sample name. DOM, React, and other front-end technologies are only ways to produce the package; after import they all become ordinary web assets without creating a second project state or export pipeline.

- `window.editableMedia` exposes structured text, style, variants, scenes, layers, parameters, and asset slots.
- `window.__hf.duration`, asynchronous `window.__hf.seek(seconds)`, registered renderers, and frame tasks provide the only deterministic frame-time and readiness boundary.
- A package explicitly declares whether media is browser-rendered, a native video underlay, or native audio. MediaFlow Pro does not infer this from file extensions.
- The source package is never written back. Clip state, replacement history, and project references live in `project.mfp`; published project copies are immutable.
- Browser imagery, native video, and native audio enter one cache and FFmpeg encoding pipeline consumed by preview, timeline, and export.

Standard v4/v5 web assets in older projects migrate directly to v6 in one transactional project upgrade. Old packages move to project-local `archive/web` for manual inspection and no longer participate in a second runtime path. A third-party runtime that cannot be proven safe to convert stops the upgrade and must be republished.

## CLI and MCP automation

`mediaflow-cli` is a structured client for the resident Editor Service. The first call starts the service on demand; later invocations only send requests. The CLI does not open `project.mfp` directly or bypass the project write lock.

First inspect the operations, parameters, and runtime requirements exposed by the current machine:

```powershell
mediaflow-cli describe
```

Then send `mediaflow-editor` v4 JSON through a file or standard input:

```powershell
mediaflow-cli execute --request request.json
Get-Content request.json -Raw | mediaflow-cli execute --request -
```

Write requests use a stable `request_id`, the latest read `base_revision`, an `actor`, and a `client_id`. Identical retries reuse a durable receipt; stale writes on disjoint paths may rebase, while conflicting writes fail explicitly instead of silently overwriting data.

Export, transcription, web-field and keyframe editing, package replacement, project handoff, and diagnostics screens can preview and copy the same executable request without starting a task or changing the project revision. `diagnostics.bundle.create` is a persistent task that produces a size-bounded diagnostic ZIP while excluding raw media and credentials.

MCP-capable hosts can configure `mediaflow-mcp` as a stdio server. It shares the same Editor Service with the desktop and CLI and contains no second editing implementation. The live output of `mediaflow-cli describe` remains the source of truth for operations and parameters.

## Project and architecture boundaries

```text
<MEDIAFLOW_PROJECT_ROOT>/
  <ProjectName>/
    project.mfp
    generated/
    proxies/
    cache/
    exports/
```

- `project.mfp` is the single source of truth for the project model, timeline, subtitles, web clip state, and versions.
- MLT graphs, web-render caches, proxies, waveforms, and analysis reports are rebuildable derived artifacts.
- QML does not access the database or launch external processes directly. Desktop, CLI, and MCP use the same `EditorApplication` / `EditorProject` boundary.
- Each signed-in user has one on-demand local Editor Service, and only that service process may hold project write locks.
- `.env.example` is the public machine-path contract; source code does not guess runtimes from drive letters or system installations.

See [ARCHITECTURE.md](ARCHITECTURE.md) for layers, threading, persistence boundaries, and the service protocol.

## Development and verification

Load the local environment and run the tests that directly cover your change first. After they pass, use the one local quality entry point to perform the final change-scoped verification:

```powershell
. .\scripts\load_environment.ps1
& $env:MEDIAFLOW_PYTHON -m pytest tests\v2\path\to\test_file.py
.\scripts\run_quality.ps1
```

Do not run the unbounded `pytest tests/v2` suite directly. The local entry point and CI both consume [`scripts/ci/quality_plan.py`](scripts/ci/quality_plan.py), with cross-platform source builds separated from project interchange. Documentation-only changes do not trigger unrelated desktop or end-to-end runs. Use `.\scripts\run_quality.ps1 --dry-run` to preview the exact commands.

Desktop logs live at `logs/mediaflow.log` under the runtime directory, rotate at 5 MiB, and keep five backups. The short code at the end of an error dialog is written to the log unchanged; include it when reporting an issue through [GitHub Issues](https://github.com/CheshireMew/MediaFlow-Pro/issues).

## License and distribution

MediaFlow Pro source is released under the [GNU GPL v3](LICENSE). Qt, MLT, FFmpeg, yt-dlp, Python packages, and other third-party components keep their respective licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

This repository maintains source, build scripts, and dependency manifests. It does not produce a portable bundle or installer unless the project owner explicitly starts a release plan.

## Star History

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/CheshireMew/MediaFlow-Pro/star-history/star-history-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/CheshireMew/MediaFlow-Pro/star-history/star-history.svg">
  <img alt="MediaFlow Pro GitHub Star History" src="https://raw.githubusercontent.com/CheshireMew/MediaFlow-Pro/star-history/star-history.svg">
</picture>
