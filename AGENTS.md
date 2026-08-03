# MediaFlow Pro 项目协作规则

## 项目定位

MediaFlow Pro 是通用的本地媒体编辑器。它正式消费 `visual-multimedia` 生产的 `editable-media` 网页包，也应允许其它生产者遵循同一公开合同接入。领域模型、项目状态、时间线、渲染与 CLI 不能依赖 visual-multimedia 的仓库布局、案例名称或一次性制作流程。

维护本仓库不等于启用任何 Skill。除非用户明确要求使用 Skill，否则按普通应用开发任务处理。

## 共享边界与唯一真源

- `$env:VISUAL_MULTIMEDIA_ROOT\schemas\editable-media.v5.schema.json` 是 `editable-media` v5 清单结构的唯一真源；生产者仓库位置由本机 `.env` 提供，不写死在项目规则中。
- `tests/fixtures/editable-media-v5*` 和 `mediaflow/resources/contracts/editable-media.v5.schema.json` 是从生产者同步得到的消费快照，不是独立设计入口，禁止直接手改。
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

## 同步与验证

涉及共享边界时，先从真实生产者同步：

```powershell
. .\scripts\load_environment.ps1
& $env:MEDIAFLOW_PYTHON scripts\sync_visual_multimedia_fixture.py $env:VISUAL_MULTIMEDIA_ROOT --destination tests\fixtures
```

同步完成后检查 diff，确认 schema、网页包和 `fixture-origin.json` 来自当前生产者；不得保留手写 fixture 与同步结果并行。

至少运行：

```powershell
& $env:MEDIAFLOW_PYTHON -m pytest tests\v2\domain\test_editable_media_v5_contract.py
```

并根据改动覆盖受影响的网页导入、项目仓储、CLI、桌面编辑、浏览器捕获、原生媒体合成、缓存、时间线、预览和导出测试。修改共享渲染或交互主链时还必须运行：

```powershell
& $env:MEDIAFLOW_PYTHON -m scripts.verify_real_user_chain
```

同时在 visual-multimedia 中运行 `node scripts/check-skill.mjs`。验收必须从它实际生产的 starter 或合同案例开始，让 MediaFlow 完成导入、保存、读取或修改、浏览器逐帧渲染、时间线消费和真实导出，并查看最终画面或成片。只有解析测试、手写 manifest、伪造缓存、mock 核心链路或“文件存在”检查不能证明兼容。

修改捕获性能、并行 worker、帧时钟、原生预览或合成时，继续运行对应的性能与画面对照脚本。若环境暂时无法完成真实链路，应说明未验证项和原因，不得把局部单元测试通过写成双方兼容完成。
