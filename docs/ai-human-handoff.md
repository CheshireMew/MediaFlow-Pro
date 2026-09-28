# AI 制作、人工微调与再次交接

MediaFlow Pro 的常见协作方式不是让人和 AI 同时抢同一条时间线，而是让同一个原生工程在不同阶段顺序交接：AI 先完成可用版本，用户在桌面端继续微调，之后 AI 能读到这些改动并从当前工程继续工作。

## 产品无关时间线进入原生工程

外部制作方可以用 `media-timeline` v1 表达素材、轨道、片段、定格、声音、语义标记和多种字幕样式。它不依赖 MediaFlow Pro。需要桌面编辑时，先新建一个空工程，再通过公开操作完成检查与导入：

1. `timeline.portable.inspect` 校验协议、相对素材路径、SHA-256、时间范围、轨道兼容、重叠和字幕引用。
2. `timeline.portable.import` 把真实素材、定格、画面变换、声音、标记和字幕样式写入原生时间线。
3. 重新调用 `timeline.get` 和 `project.inspect`，确认桌面端将读取同一份内容。

导入只允许写入空序列，避免覆盖已有人工工作。成功后，项目目录中的 `project.mfp` 成为唯一编辑真源；原 portable timeline 只是可追溯的迁移输入，不能和原生工程长期双向修改。

当 Agent 已经准备好完整时间线时，优先提交一份 `mediaflow-production-bundle` v1 并调用 `mediaflow-cli produce --request <文件>`。它先完成 portable timeline、字幕轨、指定原声 source id、零帧封面和已有语句剪辑表的检查；疑似错误重录默认只返回人工复核候选并停止，不自动删除。首轮由 `project.name` 与 `project.directory_name` 创建工程；下一轮把完成结果中的绝对工程路径写入 `project.existing_path`，继续同一原生工程。续做入口只接受与该主序列原始导入哈希一致的 portable timeline，后续实际修改仍通过原生时间线操作完成，因此不会覆盖人工编辑。多场景或长视频可把覆盖完整时间线的连续帧区间写入 `output.build_units`，让同一入口直接采用分段缓存构建；没有构建单元时才做普通整片导出。完成结果的 `output.build` 会明确列出各画面单元、连续音频母版和最终装配是本轮生成还是从缓存复用。

如果没有可靠的语句剪辑表，`speech_review.auto_transcribe=true` 会让软件在导入后转写 `dialogue_track_id` 指向的保留原声轨，并运行公开只读操作 `speech.review.inspect`。该操作给出相似重录、孤立口水字以及开头、中间和结尾的无语音区候选。无语音区只能说明那里可能是吸气、口腔音、底噪或等待，仍需实际试听；所有删除都必须先走 `transcript.edit.preview`，再由人或 Agent 明确提交 `transcript.edit.apply`。自动候选存在时，工程与转写已经保存，但版本和渲染不会开始。候选清理完成后，`produce` 才继续命名版本、导出等待、质量报告和交接检查。完成结果中的 `timeline.required_audio_provenance` 会把指定 portable 原声映射到导入后的外部素材和当前启用片段，并与 `export_matches_current_revision` 一起证明本次导出仍消费这条原生时间线。精确输入结构分别由 `mediaflow-cli describe --operation production.bundle.inspect`、`speech.review.inspect` 和 `transcript.sequence.transcribe` 提供。

## 异步交接

AI 交付给用户前：

1. 使用 `project.version.create` 建立有意义的命名版本。
2. 使用 `project.handoff.inspect` 检查素材是否离线、当前内容修订、最后导出是否来自当前修订，以及工程能否继续编辑。
3. 把工程路径、版本、当前修订和交付文件一起告诉用户。

用户在桌面端调整后，AI 下一次接手时：

1. 从原命名版本或上次事件游标调用 `project.changes.list`。
2. 区分人工修改了哪些操作和写入范围，不用比较两个扁平 MP4 猜测变化。
3. 再调用 `project.handoff.inspect`，确认素材、修订和导出状态。
4. 需要继续修改时，使用当前 `base_revision` 写回同一个工程；多项相关编辑通过 CLI 的 `batch --request` 原子提交。

桌面端、CLI 和可选的 stdio MCP 都连接同一个 Editor Service。MCP 只是为支持它的宿主提供另一种传输方式，不增加第二套项目状态，也不是异步交接的必需组件。

## 可观察结果

一次完整交接必须同时成立：

- 桌面端能打开工程并继续编辑，而不是只能导入最终 MP4。
- `timeline.get` 能读到导入后的原生素材、轨道、定格、标记和字幕样式。
- 人工编辑作为带 `actor` 的持久项目事件存在，`project.changes.list` 能在下一轮返回。
- `project.handoff.inspect` 能指出离线素材、未导出的当前修订或其它阻断。
- 最终视频从当前 `project.mfp` 修订导出，不能在工程外另有一条更“新”的 FFmpeg 时间线。

具体操作名先以当前 `mediaflow-cli describe --summary` 为准；请求和结果结构再以选中操作的 `mediaflow-cli describe --operation <名称>` 为准。
