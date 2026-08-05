# 参与 MediaFlow Pro

感谢你愿意改进 MediaFlow Pro。Issue、代码、测试、文档和可复现的问题样本都欢迎提交；中文和英文均可。

## 开始之前

1. 阅读 [README.md](README.md) 了解当前产品边界，阅读 [ARCHITECTURE.md](ARCHITECTURE.md) 了解分层、线程和持久化约束。
2. 阅读 [AGENTS.md](AGENTS.md)。它是本仓库当前的协作规则，包含共享合同、验证范围和跨平台验收要求。
3. 较大的功能、公共合同变化或跨层重构请先创建 [Issue](https://github.com/CheshireMew/MediaFlow-Pro/issues)，说明用户结果、影响范围和验收方式。

## 开发环境

项目使用 Python 3.12，Python 依赖由 `requirements.lock` 固定，Qt、MLT、FFmpeg 和 Chromium 运行时由 `runtime.lock.json` 固定。请先按 README 的[快速开始](README.md#快速开始)准备 `.env`、虚拟环境和原生插件。

不要把机器路径、下载缓存、媒体文件、运行时、模型、项目目录或测试产物提交到仓库。`.env.example` 是可公开的路径合同，本机值保存在被 Git 忽略的 `.env` 中。

## 修改原则

- 从根因修改唯一真源，并迁移全部生产者和消费者；不要留下长期兼容层、重复 helper 或双重运行路径。
- `project.mfp` 是项目状态的唯一真源。缓存、代理、波形、MLT 图和分析报告应保持为可重建的派生产物。
- `editable-media` v5 的 schema 与真实案例来自外部生产者。不要直接手改 `tests/fixtures/editable-media-v5*` 或 `mediaflow/resources/contracts/editable-media.v5.schema.json`，应使用仓库规定的同步脚本。
- 公开自动化能力以实际 `mediaflow-cli describe` 输出为准。修改操作时同时更新实现、参数 schema、`describe`、测试和正式调用点。
- 保留与本次任务无关的工作区修改。不要为了通过测试而 mock 掉正在验证的核心生产链。

## 验证

先运行直接覆盖改动的目标测试。只有公共合同、核心运行时、跨平台边界或目标测试暴露系统性影响时，才扩大回归范围。

```powershell
. .\scripts\load_environment.ps1
& $env:MEDIAFLOW_PYTHON -m pytest tests\v2\path\to\test_file.py
```

完整 Python 测试入口为：

```powershell
& $env:MEDIAFLOW_PYTHON -m pytest tests\v2
```

CI 的测试等级由 `scripts/ci/quality_plan.py` 决定。文档、许可证和纯元数据修改只运行维护级检查；桌面、媒体运行时、共享合同或原生插件变化需要相应的真实运行时与用户链验收。

## Pull Request

提交 PR 前请确认：

- 说明了用户可观察的变化和改动原因。
- 列出了实际运行的测试、脚本和未执行项。
- 新增或修改的公开入口、文档链接和许可证信息都可到达真实目标。
- 没有提交本机路径、凭据、下载内容、测试产物或无关工作区变化。
- 如果修改了共享 `editable-media` 或公开 CLI 边界，生产者、传输边界、消费者和最终结果已经一起验证。

发现问题但暂时无法修复时，请提交包含复现步骤、预期结果、实际结果、平台信息以及日志短编号的 [Issue](https://github.com/CheshireMew/MediaFlow-Pro/issues)。

---

## Contributing in English

Issues, code, tests, documentation, and reproducible samples are welcome in either Chinese or English. Before making a substantial change, read [README.md](README.md), [ARCHITECTURE.md](ARCHITECTURE.md), and the repository rules in [AGENTS.md](AGENTS.md).

- Set up the pinned Python and media runtimes through the README quick start. Fix the owning source of truth, migrate all producers and consumers, preserve unrelated working-tree changes, and run the tests that directly cover the user-visible result.
- For larger features, public-contract changes, or cross-layer refactors, open an [Issue](https://github.com/CheshireMew/MediaFlow-Pro/issues) first with the intended outcome, impact, and acceptance evidence.
- State what users can observe, why the change was necessary, which checks actually ran, and what remains unverified. Never commit credentials, machine-specific paths, downloaded runtimes, media, models, project data, caches, or test artifacts.
