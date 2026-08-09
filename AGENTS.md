# MediaFlow Pro 项目协作规则

## 项目定位

MediaFlow Pro 是通用的本地媒体编辑器。它正式消费 `visual-multimedia` 生产的 `editable-media` 网页包，也应允许其它生产者遵循同一公开合同接入。领域模型、项目状态、时间线、渲染与 CLI 不能依赖 visual-multimedia 的仓库布局、案例名称或一次性制作流程。

维护本仓库不等于启用任何 Skill。除非用户明确要求使用 Skill，否则按普通应用开发任务处理。

## 共享边界与唯一真源

- `$env:VISUAL_MULTIMEDIA_ROOT\schemas\editable-media.v6.schema.json` 是 `editable-media` v6 清单结构的唯一真源；生产者仓库位置由本机 `.env` 提供，不写死在项目规则中。
- `tests/fixtures/editable-media-v6*`、`mediaflow/resources/contracts/editable-media.v6.schema.json` 和标准 runtime 是从生产者同步得到的消费快照，不是独立设计入口，禁止直接手改。
- `scripts/sync_visual_multimedia_fixture.py` 是更新 schema、starter、真实案例和来源哈希的唯一同步入口。
- `window.editableMedia` 是网页结构化状态入口，`window.__hf.duration/seek(seconds)` 是确定性逐帧时间入口。导入、编辑、预览、缓存和导出只能共同消费这套边界。
- MediaFlow 的项目模型、`WebClipState`、时间线、渲染缓存和 Editor API 由本仓库负责；公开自动化能力以实际 `mediaflow-cli describe` 输出为唯一真源。
- visual-multimedia 只能通过公开 CLI 和通用网页包与本项目协作，不能直接读取或修改 `project.mfp`、内部数据库、缓存目录或私有 Python 类型。

## 何时必须联动 visual-multimedia

修改以下任一边界前，必须同时检查 `$env:VISUAL_MULTIMEDIA_ROOT`：

- editable-media schema 副本、解析模型、校验规则、默认值继承、场景、变体、图层或素材槽；
- 网页包导入、发布、换版、稳定 ID 迁移、`WebClipState` 或网页片段公开编辑操作；
- `window.editableMedia`、`window.__hf`、随机定位、浏览器捕获、透明画面和原生媒体合成；
- `browser`、`native-underlay`、`native-audio` 管线、素材哈希、代理、缓存键或时间线编译方式；
- `mediaflow-cli describe`、`web.*`、timeline、preview 或 export 能力中被 Skill 正式调用的操作和参数；
- 会改变网页包在桌面端导入、编辑、预览、保存、重开、导出或最终画面表现的项目迁移；
- visual-multimedia 的正式 profile 所依赖的帧范围、字幕、片段状态、渲染或交付行为。

与共享网页包和公开 CLI 无关的下载、转录、普通原生素材编辑等内部修改，不要求为了形式修改 visual-multimedia。先确认变化是否越过共同合同。

## 联动修改规则

共享边界变化必须按根因一次性完成两边迁移，不允许在 MediaFlow 内用宽松解析、字段猜测、默认补值、旧类型分支或渲染回退掩盖生产者与消费者不一致。

1. 先确认变化属于生产者合同、MediaFlow 公开能力还是纯内部实现。
2. 生产者合同需要变化时，先在 visual-multimedia 更新唯一 schema、运行时、starter、真实案例、校验器和所有生产调用点。
3. 只通过同步脚本更新本仓库的 schema 与 fixture，再更新解析、项目状态、编辑操作、渲染、时间线、缓存和导出消费者。
4. MediaFlow 公开 CLI 发生变化时，同时更新 `describe`、操作实现、参数 schema、桌面端共同状态边界、测试，以及 visual-multimedia 的调用说明和正式调用点。
5. 完成功能迁移后移除旧字段、旧类型、旧 helper、旧迁移外的运行分支、旧回退和旧导出。若必须升级协议，协调新版本并在同一任务中迁移两边，不把旧协议保留成长期兼容层。

MediaFlow 新增能力时优先扩展公开、通用的编辑器合同。visual-multimedia 的某个 profile 若有局部限制，应留在该 profile 及其调用计划中，不能变成所有网页包的全局规则。

## 跨平台验收范围

当前阶段没有在用户持有的真实 Linux 或 macOS 机器上执行完整桌面发布验收的计划，也不把缺少这两类实机验收视为跨平台改造未完成或任务阻塞。Linux 与 macOS 支持默认通过平台无关的领域与应用测试、运行时合同、构建脚本检查、目标平台 CI 源码构建、原生预览冒烟和跨平台交接导出来验收；Windows 本机继续覆盖可实际执行的完整桌面、原生预览和导出链路。

对外说明必须区分“源码、运行时合同和 CI 已支持目标平台”与“已经在目标平台实机完成完整验收”，不得把未执行的 Linux/macOS 实机测试写成已经执行，也不得仅因没有这两类实机环境而反复要求补测或阻塞当前任务。若用户以后明确启动 Linux/macOS 实机发布或验收计划，再按当时指定的机器、系统版本和发布范围补充对应验证。

## 同步与验证

CI 的测试等级由 `scripts/ci/quality_plan.py` 唯一决定，workflow 只能消费它输出的 `maintenance`、`core`、`full` 和 portable 标记，不得再用另一套路径清单猜测范围。README、许可证、说明文字和仓库自有文档图片只运行文档、图片、许可证链接和测试分片合同检查；领域、应用、自动化与服务代码先运行 Windows 预检和两个无重叠的轻量核心分片，轻量分片不得准备 Qt SDK、MLT/FFmpeg 运行时、Chromium 或原生插件。`scripts/ci/test_resources.py` 只维护 `lightweight` 与 `runtime` 这一条资源边界，不要求给现有测试机械添加细分类 marker；只有经过审查、确实会启动桌面、媒体运行时、浏览器或外部进程的节点才进入四个运行时分片。全部分片按 `scripts/ci/test_timings.windows.json` 中最近一次 Windows 实测的慢节点耗时平衡，新节点使用保守默认权重。基础设施、桌面、QML、原生插件、运行时锁、fixture、共享合同、依赖与 quality workflow 变化必须运行 Windows 完整交互与离线验收。三平台源码构建只在平台运行时边界变化、手动完整运行和每日完整回归中执行；Linux 与 macOS 的原生预览冒烟必须放在更昂贵的 portable 合同和交接链之前。

完整 Python 环境按操作系统、架构、精确 Python 版本和 `requirements.lock` 摘要缓存。预检负责优先填充 Windows 缓存，后续任务只能刷新当前仓库的 editable 安装并运行 `pip check`，不能在每个分片重复解析和安装完整依赖；运行时、Qt SDK 和原生构建产物继续使用各自独立、可审计的缓存键。

预检失败后先运行对应的最小合同或测试文件，不反复启动完整链；产品、配置和验证器冻结后才把完整链作为最终确认。每个 pytest 长任务保留 `--durations` 数据，QtWebEngine 场景使用独立 Python 进程，失败任务上传已登记测试根中的诊断产物。同一分支的新提交自动取消旧 workflow，不能让已被新证据取代的运行继续占用资源或参与完成结论。

涉及共享边界时，先从真实生产者同步：

```powershell
. .\scripts\load_environment.ps1
& $env:MEDIAFLOW_PYTHON scripts\sync_visual_multimedia_fixture.py $env:VISUAL_MULTIMEDIA_ROOT --destination tests\fixtures
```

同步完成后检查 diff，确认 schema、网页包和 `fixture-origin.json` 来自当前生产者；不得保留手写 fixture 与同步结果并行。

至少运行：

```powershell
& $env:MEDIAFLOW_PYTHON -m pytest tests\v2\domain\test_editable_media_v6_contract.py
```

并根据改动覆盖受影响的网页导入、项目仓储、CLI、桌面编辑、浏览器捕获、原生媒体合成、缓存、时间线、预览和导出测试。修改共享渲染或交互主链时还必须运行：

```powershell
& $env:MEDIAFLOW_PYTHON -m scripts.verify_real_user_chain
```

同时在 visual-multimedia 中运行 `node scripts/check-skill.mjs`。验收必须从它实际生产的 starter 或合同案例开始，让 MediaFlow 完成导入、保存、读取或修改、浏览器逐帧渲染、时间线消费和真实导出，并查看最终画面或成片。只有解析测试、手写 manifest、伪造缓存、mock 核心链路或“文件存在”检查不能证明兼容。

修改捕获性能、并行 worker、帧时钟、原生预览或合成时，继续运行对应的性能与画面对照脚本。若环境暂时无法完成真实链路，应说明未验证项和原因，不得把局部单元测试通过写成双方兼容完成。
