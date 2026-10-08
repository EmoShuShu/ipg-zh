# 本地阅读文档格式对齐（2026-10-08）

## 问题与参照

实际旧 `dist/IPG.md` 只渲染中文正文/注解，缺少目录；标题采用不同层级和
“中文 (英文)”排列，重复增加“AIPG 注解”标签，还暴露完整内部 release ID。
这些与已验证的 `mtr-zh` 阅读体验不一致，并非译文或注解锚点需要重新迁移。

只读参照 `D:/mtr-zh/mtr_pipeline/core.py` 的 `_quote_markdown`、
`_markdown_for_rendered`、`_markdown_anchor`、`_markdown_toc`、
`_chapter_heading`、`_section_heading`、`_build_markdown` 及实际 `dist/MTR.md`。
参照 HEAD：`ccab99f4a87eba58935309b2c23557ff7123392f`。没有修改 MTR，
没有运行其构建，也没有引入跨目录运行时依赖。

## 采用的格式与保留的差异

| 项目 | 修正后行为 |
|---|---|
| 开头 | 原样读取用户版本说明；随后自动生成目录及中英总标题 |
| 目录 | 使用与 MTR 相同的 Unicode/标点处理锚点规则；36 个章节项，加版本说明/目录两项 |
| 章节 | 一级章/前言/附录，二级小节；英文在前、中文在后；编号使用 IPG |
| 正文 | 每个 block 或持久化 reading segment 先英文、后中文 |
| 发布注解 | 按原有 anchor/position/order 插入；整个注解的英文、中文置于同一引用框；保留全部多段/列表及持久化 marker，无额外标签 |
| 缺译候选 | 保留英文；中文位置明确标记缺译，不复制英文伪造译文 |
| IPG 组件/处罚 | IPG 特有组件用三级中英标题，基础处罚使用集中中英显示值；不套用 MTR 模型 |
| 附录 A | 保留逐行呈现，但补全每行英文及相应英文处罚；暂不另造表格布局 |
| 附录 B | 读取持久化 date，以二级日期标题显示五组；17 条更动，每条中英一对 |
| JSON | 完全保留 IPG 原生 output-v1；不是 MTR 的 chapter/contents/extras 形状 |
| 三版本轴 | 完整 ID、版本及范围继续保存在 JSON；不额外在读者正文展示内部 ID |

没有修改中文、官方英文、reading divisions、registry、迁移结论、审校账本、
翻译批注或 manifest。也没有重建 OmegaT 或既有候选。注解许可/署名及
publishable=false 继续只作本地构建的非阻塞提醒，不改变此前用户决策。

## 证据

新增 `tests/test_markdown_format.py`（11 项），检查目录链接/层级、Unicode 锚点、
全部正文与注解中英内容的实际阅读顺序、多段/多行/列表引用、缺译标记、附录
日期和处罚及 JSON/源结构不变。既有 P3 和版本说明测试同步采用新格式断言，
仍保留原有数量、日期顺序和结构检查。另修正既有注解列表的 Z/Y 等字母标记被
渲染成 1 的问题，仅使用现有 marker，不改事实源或进行内容判断。

- 修正前相关基线：40 passed。
- 格式、版本说明、完整解析相关测试：43 passed。
- 全套回归：337 passed，679.41 秒；这是补加三项 marker 测试前已收集的完整套件。
- 最终 marker 补正后，格式/版本说明/完整解析/正式构建相关回归：117 passed，41.51 秒，包含三项新增 marker 测试；当前共收集 340 项。没有未处理失败。
- 最终正式 profile 隔离演练：`outputs/dist-format-preview-20261008-final/`（较早的预览另存，未覆盖）。
- 两次构建逐字节相同；两次 `rules.json` 独立输出校验均通过。
- Markdown SHA-256：`34e70a05973845f0a3dfbf40aba45f16015f9241dff6f2b9ccdc28dc880654b1`。
- JSON SHA-256：`308dc6a9e5d2b7d9277013c0b8ed212ec90bc1b2c562651097f38fa14f2b2f79`。
- JSON 与用户已有 dist 逐字节相同，文件不包含自身哈希。
- 当前 release 检查通过：缺译、未审、stale、失锚、未决映射、重复消费及账本问题全部为零；两项许可/发布声明提醒原样保留。

已将旧 dist 原样备份至 `outputs/dist-format-backup-20261008/`，两文件备份哈希
与原文件一致。随后通过正式 release 构建入口事务更新 `dist/IPG.md` 和
`dist/rules.json`；正式两次构建/独立验证再次通过，结果与最终隔离预览一致。
正式 dist 仍只有这两个文件，不保存构建报告或自身哈希。

保护校验覆盖 496 个既有文件：只有明确请求修复的 `dist/IPG.md` 哈希改变，其余
495 个原样保留（含 JSON、所有用户修订、审校资料、旧源、快照、现有候选及
OmegaT 工程）。MTR 工作树仍干净。用户已有/新生成 dist 和用户未提交修订
不纳入代码提交。现有 `outputs/current-candidate` 需要时用助手选项 2 重新生成。

主要检查命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_version_notes.py tests/test_reading_layout.py tests/test_validation_build.py
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m pytest tests/test_markdown_format.py tests/test_version_notes.py tests/test_full_parser.py tests/test_production.py
.venv/Scripts/python.exe scripts/validate_output.py
```

代码提交主题：`Align IPG reader Markdown with reference MTR format`；以本地 Git
记录定位提交，不把提交自身哈希写进会参与该提交的文件。

## 后续补正：附录 A 表格

按用户指定的 `D:/MTG_Tools/AIPG/创作者资源/AIPG_2025.md` 附录 A 阅读格式，
将前述“逐行呈现”改为“违规／Infraction／处罚”三列表格。保留三个分类行和
6/9/8 条违规，共 23 条。参照文件只用于确定展示形式；所有名称、章节分类、
引用和处罚仍读取当前结构化源，不重新导入旧译。

只改共用 Markdown 渲染器；正文/注解、schema、registry、稳定 ID、处罚 code
和 JSON 模型不变。对单元格竖线/换行作转义，行上的 before/after 注解仍在
原位置显示，必要时中断并续接表格，不移到整张表之后。新增四项回归覆盖这些
行为，并同步调整两项旧断言。

- 修正前格式测试：11 passed。
- 最终格式/完整解析/版本说明/生产链路相关回归：121 passed，21.13 秒。
- candidate/release 门槛及契约测试：23 passed，16.96 秒。本轮共 144 项相关回归通过。
- 隔离演练：`outputs/appendix-a-table-preview-20261008/`；两次输出字节相同，独立 schema 校验通过。
- Markdown SHA-256：`2ad3f897dd4f9d5ac9dd85d5b2a9fc7efcad58cacfd01b2a238a951fc843371f`。
- JSON SHA-256：`1a0d1cd4bdf8660339e2fd57f201e68ffa764fc3c12e0bd238e3c7645c4749bf`。

JSON 与上一轮 dist 的差异仅为 versionNotes：用户在本轮开始前已经于源中增加
一段精解鸣谢，本次正常构建带入该已有修改，源文件原样保留。sections 和
publicationAnnotations 与旧 dist 完全相同。

旧 dist 已原样备份至 `outputs/appendix-a-table-backup-20261008/`；正式构建
再次通过双次字节比较及独立 schema 验证后事务更新 dist。未重建旧候选或
OmegaT，未修改/提交用户源文件和审校资料。补正提交主题：
`Render Appendix A as the legacy three-column table`。
