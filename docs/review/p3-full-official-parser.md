# P3 完整官方英文解析审阅摘要

本阶段只完成 2024-09-23 官方英文 IPG 的完整解析、结构验证、稳定 ID 协调，以及 source/output schema v1 冻结。新增范围没有迁移旧中文或 AIPG 注解，没有生成完整 OmegaT 工程，`dist/`、GitHub、远端、Actions、PR 和 Release 均未触碰。

## 权威输入与输出边界

- 唯一官方结构来源：`MTG_IPG_2024Sep23_EN.pdf`
- PDF SHA-256：`056b1687bae4a6c5d7bc4fe4e15d66613e725197cc2a45e18084c2eaf1b5aae0`
- 页数：31；提取文本行：1050；已分类：1050；未分类：0
- P3 manifest：官方正文为 `full-document`，发布注解仍为仅含 2.5 的 `pilot`
- `publishable: false`；86 个延期注解组和 338 个 raw unit 继续作为 release 阻塞项
- 完整解析 JSON、覆盖账本、候选 YAML、候选 Markdown/JSON 和调试资料均位于被忽略的 `outputs/p3/`
- 可提交内容仅含解析/验证代码、schema、registry、自动测试、本摘要和精简黄金摘要

## 完整结构

| 项目 | 数量 |
|---|---:|
| section | 36 |
| └ front-matter / chapter / policy / infraction / appendix | 2 / 4 / 5 / 23 / 2 |
| component | 110 |
| group | 124 |
| block | 338 |
| └ paragraph / list-item / appendix-row / change-entry | 183 / 115 / 23 / 17 |

结构树按顺序包含 Introduction、Framework of this Document、第 1 章及 1.1—1.5、第 2 章及 2.1—2.6、第 3 章及 3.1—3.9、第 4 章及 4.1—4.8、附录 A、附录 B。机器可复核的完整树位于 `outputs/p3/reports/p3-review-summary.json`。

组件角色出现次数：body 10、definition 23、examples 23、philosophy 21、additional-remedy 16、upgrade 9、downgrade 2、penalty-definition 4、appendix-table 1、change-log 1。角色由实际标题和内容决定，不要求每个违规小节具有同一套组件。

## 23 个违规及基础处罚

| 编号 | 违规 | 基础处罚 |
|---|---|---|
| 2.1 | Missed Trigger | No Penalty |
| 2.2 | Looking at Extra Cards | Warning |
| 2.3 | Hidden Card Error | Warning |
| 2.4 | Mulligan Procedure Error | Warning |
| 2.5 | Game Rule Violation | Warning |
| 2.6 | Failure to Maintain Game State | Warning |
| 3.1 | Tardiness | Game Loss |
| 3.2 | Outside Assistance | Match Loss |
| 3.3 | Slow Play | Warning |
| 3.4 | Decklist Problem | Game Loss |
| 3.5 | Deck Problem | Warning |
| 3.6 | Limited Procedure Violation | Warning |
| 3.7 | Communication Policy Violation | Warning |
| 3.8 | Marked Cards | Warning |
| 3.9 | Insufficient Shuffling | Warning |
| 4.1 | Minor | Warning |
| 4.2 | Major | Match Loss |
| 4.3 | Improperly Determining a Winner | Match Loss |
| 4.4 | Bribery and Wagering | Match Loss |
| 4.5 | Aggressive Behavior | Disqualification |
| 4.6 | Theft of Tournament Material | Disqualification |
| 4.7 | Stalling | Disqualification |
| 4.8 | Cheating | Disqualification |

每个违规小节恰好从正文标题取得一个基础处罚。附录 A 共 23 行，每行均引用现有违规 ID，未知引用为 0，正文/附录处罚不一致为 0。

附录 B 解析为 5 个日期组、17 个条目：2024-09-23（5）、2024-04-15（6）、2024-02-02（1）、2023-11-13（2）、2023-09-04（3）。可识别的规则引用全部解析并验证存在。

## PDF 覆盖与故障封闭

1050 行的分类包括：正式 block 文本 802、TOC 条目 36、section 标题 36、component 标题 83、处罚定义标题 4、附录 A 行 23、附录 B 条目原始行 22、附录 B 日期 5、页码 31，以及其余文档元数据和表头。页码、页脚及目录点线不进入正文。

每个正式英文 block 都带 PDF SHA-256、页码、bbox 和 extraction unit。解析器会拒绝：PDF 哈希变化、TOC/正文任一方向缺失、TOC 与正文同时漏掉预期节、重复小节、未知角色标题、未分类正文、错误处罚、附录未知引用或处罚不一致。

## 稳定 ID 协调

PDF 解析只产生临时 extraction identity；独立 reconciliation 阶段才分配稳定 ID。协调使用 registry、规范化英文证据和结构上下文，不使用当前数组位置作为最终身份。

- P2 既有范围节点 ID：158
- 完整文档中保持不变：158
- 缺失：0
- 歧义或人工 override：0
- registry 条目：649；新增节点均有分配历史和证据，退役 ID 仍禁止复用

既有第 2 章 ID 的完整集合包含在回归测试中；前置章节的加入没有改变第 2 章的 section/component/group/block ID。

## Schema v1 冻结

- 新增 `ipg-manifest.schema.json`，固定版本组合、机器可验证 scope、`publishable`、构建 schema 和文档清单。
- `ipg-source.schema.json` 固定 front-matter/policy/infraction/appendix、classification、upgrade/downgrade/penalty-definition、附录引用、日期组和规则引用。
- `ipg-output.schema.json` 固定 `ipg-output-v1` 顶层字段，并将 scope 和 `publishable` 带入输出，防止候选范围在消费端丢失。
- P3 新增范围的 `zh` 保持空字符串；不会以英文填充中文。仅保留 P2 已有第 2 章、附录小样译文及 2.5 发布注解。

## 验证与确定性

- 自动测试：74 项通过。
- candidate：通过，报告 272 个缺译、缺少完整 review ledger 和注解许可待完成；这些均完整报告但不阻塞候选。
- release：按预期失败；除上述项目外，还明确因 `publishable: false` 和 86/338 个延期注解材料失败。
- 两次完整解析/协调结果一致；两次候选构建逐字节一致。
- 候选哈希：`IPG.md e3459019cde322e00be81926f153a25f48c3e712a0715f49abc9dd1383b12a39`；`rules.json c1a0b651fa105f745855ab29e4a560f288c03889688ff7accf9c71b25a1bba98`。
- `rules.json` 不包含自身哈希；哈希只存在外部 build report 和 `SHA256SUMS`。

## 提交摘要

- `b564280`：P2 收尾、非发布 pilot scope 与 release gate。
- `f382df9`：完整官方解析器和 schema v1。
- `9928e50`：完整结构 reconciliation 与 P3 本地流水线。
- `913b354`：完整解析、结构、失败模式与确定性测试。
- `bdf4c73`：完整文档稳定 ID registry 分配及额外故障封闭检查。

## 进入 P4 前仍需决定

没有新的技术选择需要在 P4 前补决。唯一阻塞项是用户明确批准进入 P4；注解许可和署名仍是正式发布前门槛，但按已批准方案不阻塞下一阶段的本地迁移工作。
