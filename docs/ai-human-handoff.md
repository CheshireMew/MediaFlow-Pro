# AI 制作、人工微调与再次交接

MediaFlow Pro 的常见协作方式不是让人和 AI 同时抢同一条时间线，而是让同一个原生工程在不同阶段顺序交接：AI 先完成可用版本，用户在桌面端继续微调，之后 AI 能读到这些改动并从当前工程继续工作。

## 产品无关时间线进入原生工程

外部制作方可以用 `media-timeline` v1 表达素材、轨道、片段、定格、声音、语义标记和多种字幕样式。它不依赖 MediaFlow Pro。需要桌面编辑时，先新建一个空工程，再通过公开操作完成检查与导入：

1. `timeline.portable.inspect` 校验协议、相对素材路径、SHA-256、时间范围、轨道兼容、重叠和字幕引用。
2. `timeline.portable.import` 把真实素材、定格、画面变换、声音、标记和字幕样式写入原生时间线。
3. 重新调用 `timeline.get` 和 `project.inspect`，确认桌面端将读取同一份内容。

导入只允许写入空序列，避免覆盖已有人工工作。成功后，项目目录中的 `project.mfp` 成为唯一编辑真源；原 portable timeline 只是可追溯的迁移输入，不能和原生工程长期双向修改。

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
