# P4 全文旧源与 AIPG 发布注解迁移审阅摘要

P4 已把 `AIPG_2025.md` 中可由现有证据唯一确定的旧中文正文和双语 AIPG
发布注解迁入 P3 冻结的完整官方结构。迁移保持旧中文措辞、术语和标点；没有重译、
润色或以英文填充中文。本阶段没有进入 P5，没有生成完整 OmegaT 工程，没有修改
`dist/`，也没有配置远端、GitHub、Actions、PR 或 Release。

## 权威输入与输出边界

- 官方结构和英文仅来自 2024-09-23 PDF，SHA-256 为
  `056b1687bae4a6c5d7bc4fe4e15d66613e725197cc2a45e18084c2eaf1b5aae0`。
- 旧译和发布注解仅来自不可变 `AIPG_2025.md` 快照，SHA-256 为
  `acce82ca7224d5d741609807a2d95697337e56110354efec370938dea51b75fb`。
- source 已按阅读顺序拆为 `front-matter.yaml`、`chapter-01.yaml`、
  `chapter-02.yaml`、`chapter-03.yaml`、`chapter-04.yaml`、`appendix-a.yaml`、
  `appendix-b.yaml`；manifest 同时把官方正文和发布注解标为 `full-document`，
  延期注解组和 raw unit 均为 0。
- `publishable: false`。候选预览和完整工作报告仅位于被忽略的 `outputs/p4/`。

## 正文迁移结果

| 范围 | 官方 block | 已映射 | 真实缺译 | 未决 |
|---|---:|---:|---:|---:|
| 前置章节 | 5 | 5 | 0 | 0 |
| 第 1 章 | 30 | 30 | 0 | 0 |
| 第 2 章 | 84 | 84 | 0 | 0 |
| 第 3 章 | 102 | 102 | 0 | 0 |
| 第 4 章 | 77 | 77 | 0 | 0 |
| 附录 A | 23 | 23 | 0 | 0 |
| 附录 B | 17 | 5 | 12 | 0 |
| **合计** | **338** | **326** | **12** | **0** |

附录 B 的结果严格对应旧源：2024-09-23 的 5 条保留旧中文；2024-04-15
的 6 条和 2024-02-02 的 1 条仅有旧英文证据；2023-11-13 的 2 条及
2023-09-04 的 3 条在旧源中不存在。后 12 条保持空 `zh`，每项 finding
均保存官方文本、搜索范围以及有无旧英文证据。

标题按结构身份迁移；`1.0 General` 等旧标题差异和 `Theft of Tournament
Materials` 的单复数差异不会改变稳定 ID。所有 326 个非空中文官方 block
均保存参与迁移的全部 legacy raw unit；所有 338 个官方 block 仍保存 PDF
SHA-256、页码、bbox 和 extraction unit。

## 发布注解迁移结果

| 范围 | 注解 | 内部 block |
|---|---:|---:|
| 第 1 章 | 61 | 73 |
| 第 2 章 | 98 | 182 |
| 第 3 章 | 108 | 140 |
| 第 4 章 | 63 | 121 |
| **合计** | **330** | **516** |

330 个注解包含 352 个持久化 group 和 516 个双语 block；段落、列表、原始
次序和 source raw unit 均保留。孤立注解为 0，同一锚点按持久化 `order`
渲染。P2 第 2.5 节的 12 个注解和 14 个内部 block 已通过规范化内容哈希验证，
其 ID、锚点、position、order、正文和 raw provenance 均未变化。

2.1 的长注解因缺少部分引用标记，且两个英文段落合并对应一个旧中文段落，使用
`p4-ann-2-1-simple-backup-guide` 显式记录 29 个 raw unit、14 个双语 block
边界、列表标记、锚点和顺序。3246/3248 行则通过
`p4-body-4-2-upgrade-context` 明确处置为同一官方正文 block 的旧版本上下文，
没有生成发布注解。P2 已有的 `pilot-body-2-1-delayed-copy` 正文区间 override
继续生效并列入 applied override 审计。

## 4,085 个 raw unit 的唯一处置

- 已处置：4085/4085；重复消费：0；未决：0；延期：0。
- 正文英文/中文证据：400/400；标题：36；附录 A 行：23；附录 B 中文：5；
  附录 B 仅英文证据：7；旧版官方上下文：2。
- 发布注解英文/中文 raw unit：519/516。
- 版式空行：1286；引用分隔行：701；其他带理由忽略：188；与当前 PDF
  不同的旧官方措辞：2。
- 第 1—70 行全部作为版本说明或目录材料处理，没有进入正式正文或发布注解。

完整逐行账本、block 映射、finding 及分节统计在 `outputs/p4/reports/`，未纳入
版本管理。精简的机器可读摘要为 `docs/review/p4-migration-summary.json`。

## 稳定 ID 与冻结结构

- P3 结构保持 36 sections、110 components、124 groups、338 official blocks；
  PDF 文本覆盖保持 1050/1050，未分类为 0。
- P3 的 649 条 registry 前缀规范化 SHA-256 为
  `72a71c332a0988bc6361c771e981580583e7989e2a0e90a9fbb19ceecb698ffb`；
  流水线和测试会在其变化时故障封闭。
- P2 的 158 个官方节点 ID 全部保留。P4 仅为新增 annotation、annotation group
  和 annotation block 追加 1160 条 registry 记录，最终共 1809 条。
- 冻结协调阶段从 registry 英文证据、结构上下文和显式 override 恢复全部 608
  个官方节点 ID，不按数组位置继承，也不依赖 `outputs/p3/`。

## 验证、测试与确定性

- 97 项自动测试全部通过。
- candidate 结构验证通过；它完整报告 12 个缺译、缺少全文审校账本和注解许可/
  署名待解决，但这些是 candidate 的非阻塞 readiness finding。
- release 按预期失败：`missing-translation: 12`、`missing-review-ledger: 1`、
  `annotation-license-pending: 1`、`manifest-not-publishable: 1`。
- 两次全文候选构建逐字节一致：
  - `IPG.md`：`cbb812826a73c7f417d46961b692774b0a259284b76ee996356b71aaf0603400`
  - `rules.json`：`d7f052c742cdcaf3dde53347c85f7b72fb5d36515f809474f9eebf27c2efe79c`
- `rules.json` 不含自身哈希；哈希只写入外部 `SHA256SUMS` 和 build report。

## 提交摘要

- `8596c58`：全文旧源迁移核心、冻结 registry 协调和显式 overrides。
- `30144d0`：七份全文 source、P4 本地流水线、候选验证与构建。
- `b9592aa`：迁移 provenance、稳定 ID、失败模式和 release gate 回归测试。
- README 与本审阅包由最终 P4 文档提交保存；最终 HEAD 以交付报告为准。

## 人工判断与进入 P5 前门槛

本轮没有未决映射或不可靠锚点，因此不生成
`p4-human-judgment-required.md`。进入 P5 仍需用户另行批准；正式发布前还必须
补齐 12 项真实缺译、完成全文 OmegaT/审校账本，并解决 AIPG 注解许可和署名。
