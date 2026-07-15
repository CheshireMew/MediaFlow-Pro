# MediaFlow Pro

MediaFlow Pro 是面向 Windows 10/11 x64 的项目制视频创作工作站。V2 使用 PySide6/QML 构建桌面界面，Python 承载领域模型与工作流，MLT 统一生成实时预览和最终导出；所有内部通信都在桌面进程内完成，不启动本地网络服务或浏览器自动化运行时。

项目采用 GPLv3，下载能力以随运行环境提供的 yt-dlp 为准。

## 已实现的产品能力

- 可移动项目目录，`project.mfp` SQLite 文件是项目唯一数据来源。
- 主序列与任意数量的短视频序列，共享素材、字幕、翻译和高光候选。
- 多轨视频、音频和字幕时间线；支持移动、裁剪、分割、复制、普通删除、波纹删除、转场、变速、反向、画面变换和撤销/重做。
- MLT 驱动的同源预览与导出，C++ Qt Quick 插件负责帧纹理、音频主时钟、定位和掉帧报告。
- 自动代理、波形、素材指纹、离线检测和单个/批量重新定位。
- yt-dlp 下载、faster-whisper 转录、OpenAI 兼容接口翻译与高光分析。
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

QML 不直接访问数据库或启动外部程序。Python 类型是唯一合同来源，MLT 图是从 `project.mfp` 编译出的派生结果。

完整边界与线程模型见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 项目目录

```text
<ProjectName>/
  project.mfp
  downloads/
  generated/
  proxies/
  cache/
  exports/
```

外部导入素材保留绝对引用；下载、代理、波形、字幕、翻译和导出由项目目录管理。素材失踪时会保持为离线记录，重新定位需要指纹验证或用户明确确认。

## 开发运行环境

版本固定为：

- Python 3.12 x64
- PySide6 / Qt 6.11.1
- MLT 7.40
- yt-dlp 2026.3.17
- FFmpeg（GPL 构建）

依赖、模型和构建缓存默认位于 `D:\Tools\MediaFlow`。若没有 D 盘，首次启动会要求用户明确选择运行环境目录，不会静默占用 C 盘；也可以预先设置 `MEDIAFLOW_RUNTIME_DIR`、`MEDIAFLOW_MELT`、`MEDIAFLOW_NATIVE_QML`、`MEDIAFLOW_FFMPEG` 和 `MEDIAFLOW_FFPROBE`。

创建开发环境：

```powershell
py -3.12 -m venv D:\Tools\MediaFlow\.venv
D:\Tools\MediaFlow\.venv\Scripts\python.exe -m pip install -e ".[dev,build]"
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

## 测试与真实验收

```powershell
D:\Tools\MediaFlow\.venv\Scripts\python.exe -m pytest tests\v2
D:\Tools\MediaFlow\.venv\Scripts\python.exe scripts\verify_ui_matrix.py
D:\Tools\MediaFlow\.venv\Scripts\python.exe scripts\verify_performance.py
D:\Tools\MediaFlow\.venv\Scripts\python.exe scripts\verify_preview_performance.py
D:\Tools\MediaFlow\.venv\Scripts\python.exe scripts\verify_display_capabilities.py
D:\Tools\MediaFlow\.venv\Scripts\python.exe scripts\verify_real_user_chain.py
```

测试覆盖领域计算、SQLite 事务、QML 页面、原生预览、代理、下载、转录、翻译、高光、MLT 导出、HDR 元数据及预览/导出抽帧比对。需要网络、模型或 API 凭据的验收脚本会使用真实服务，不以消费端伪造数据代替生产链路。

## 许可与分发

MediaFlow Pro 源码以 [GNU GPL v3](LICENSE) 发布。随运行目录提供的 Qt、MLT、FFmpeg、yt-dlp、Python 包及其他组件仍适用各自许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

本仓库只维护可重复构建脚本和依赖清单；除非项目所有者明确要求，不生成便携包或安装器。
