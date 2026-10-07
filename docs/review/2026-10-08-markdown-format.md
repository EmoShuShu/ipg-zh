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
