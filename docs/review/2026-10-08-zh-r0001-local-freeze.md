# zh-r0001 本地审校首版冻结

本次按维护者批准，只做人工审校成果归档与本地首版冻结。没有新翻译或审校判断，
没有切换官方基线、中文修订号或 OmegaT 版本，没有配置远端、CI、PR 或 GitHub Release。

## 冻结身份与提交

- 完整身份：`ipg-2024-09-23__ann-aipg-legacy__zh-r0001`。
- 官方轴：2024-09-23 英文 PDF，SHA-256 `056b1687bae4a6c5d7bc4fe4e15d66613e725197cc2a45e18084c2eaf1b5aae0`。
- 注解轴：`aipg-legacy-full`，既有 AIPG_2025.md 快照与来源哈希原样保留。
- 中文轴：`zh-r0001`，没有人为递增或重命名。
- 审校源提交：`d0b79f3150442b3529a901d762f0ebd5a5e4259f`，保存译文、账本、252 条翻译批注、术语表、版本说明，以及已有 CPV 勘误的登记历史/脚本/测试/说明。
- 冻结产物另行提交；本地标记为 `local-freeze/ipg-2024-09-23__ann-aipg-legacy__zh-r0001`，指向该冻结提交，不是 GitHub Release。

`review/freezes/<完整身份>/freeze.json` 保存三版本轴、源提交、输入/产物/备份哈希、
验证结果、测试结果和快照位置。旁边的 SHA256SUMS 校验两个 dist 文件及 freeze.json。
哈希在外部记录，不写入 rules.json 自身，也不参与规则内容构建。

## 复核结果

- 完整回归：**344 passed in 320.73s**；没有跳过或未处理失败。
- 1007 个单元：734 reviewed-unchanged、273 reviewed-modified；缺译、未审、stale、失锚、未决映射、重复消费及账本问题均为零。
- 252 条翻译批注没有 stale；OmegaT 没有未回写文件。
- 中文源标量更改共 310 处（含派生汇总）；英文改动仅为已确认的 CPV 注解重复句勘误。其余官方英文、结构字段和稳定 ID 无变化。
- release profile 隔离构建两次字节相同，输出独立 schema 校验通过，与现有 dist 完全一致，所以没有覆盖 dist。
- 从审校源提交导出纯 Git 归档，在临时目录用其自身代码独立构建；不依赖工作目录中的未提交内容、现有候选或 OmegaT。两个最终文件逐字节匹配 dist。
- dist 与冻结记录限定 LF 检出，避免 Windows 换行转换影响固定产物哈希；不格式化用户源文件或审校记录。

最终产物：

| 文件 | SHA-256 |
|---|---|
| dist/IPG.md | 2ad3f897dd4f9d5ac9dd85d5b2a9fc7efcad58cacfd01b2a238a951fc843371f |
| dist/rules.json | 1a0d1cd4bdf8660339e2fd57f201e68ffa764fc3c12e0bd238e3c7645c4749bf |

## 本地证据与恢复

沿用现有离线 `ipg-snapshot` 能力，保存 87 项官方/解析器/版本输入及审校资料，
快照上下文为 `6b79367a4cd43815e4f0cee4b165f3554fe29c131e75438f59aaf99dfa143566`。
核验通过，结构/英文差异为零、finding 为零。该结果是当前基线的离线快照，
**不是一次重新联网确认 WPN 最新版本的检查**。

`outputs/local-freeze/<完整身份>/` 另存：

- `source-commit.zip`：审校源提交的 Git 归档（不含被忽略的工作产物）。
- `omegat-project.zip`：34 个原工程文件的完整备份，压缩包成员哈希逐项匹配原文件；未重建、覆盖或清空工程。

上述本地 zip 及完整解析快照不纳入 Git；其位置、大小、SHA-256 记录在 freeze.json。
它们可能包含内部批注，不能默认作为公开发布附件。现有候选、备份与原始快照保持原样。
恢复时保留当前工作目录，在独立目录使用归档或本地冻结标记；不要强行重置未提交的新修订。
后续形成新冻结版时另行确认中文修订号以及审校/OmegaT 协调，不覆盖本次记录或强制移动本地标记。

## 发布边界

`publishable: false`、注解授权/署名待定仍原样保留，继续是本地构建的非阻塞提醒。
本次冻结不改变公开发布许可、不上传任何内容。是否创建 GitHub 仓库、CI 和 Release，
以及是否公开内部翻译批注和审校资料，留待维护者另行决定。

主要检查：完整 pytest、`validate_project(profile='release')`、现有生产入口的
release rehearsal、`snapshot_current` / `verify_snapshot`、纯 Git 归档独立双次构建。
