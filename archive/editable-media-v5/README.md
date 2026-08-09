# editable-media v5 消费快照

这里保存 MediaFlow Pro 切换到 editable-media v6 前的 schema、标准运行时和同步测试包。它们只用于人工追溯与项目升级研究，不参与资源加载、正常导入、测试 fixture 同步、预览或导出。

活动合同以 visual-multimedia 的 `schemas/editable-media.v6.schema.json` 为唯一真源，并通过 `scripts/sync_visual_multimedia_fixture.py` 单向同步。

迁移前关键快照的 SHA-256：

- `mediaflow/resources/contracts/editable-media.v5.schema.json`：`bc89a16c48347482688c1edd7e3ac39994012fcfb73a0e9dcf363f1afbd1c4c5`
- `mediaflow/resources/contracts/editable-media-runtime.v5.js`：`460ad7a3b16738659dccbcc6cfb325470472a43d8ef16666c92a858a8798403a`
- `tests/fixtures/editable-media-v5/editable-media.json`：`e86f6f821114704eb780b2ce3eed106d26205503ce1b367dcd2737ecdb6ae914`
