# P4.5 全文 OmegaT 审校闭环审阅摘要

P4.5 从 `6f6eff0912ab9f1bbefd9c1b300c582664358cf0` 开始，范围严格限于全文
OmegaT 审校闭环。没有进入 P5，没有修改正式 source 的译文或 AIPG 发布注解，
没有写入 `dist/`，也没有添加远端、GitHub、Actions、PR 或 Release。

## 实际项目

- 本地项目：`outputs/omegat-ipg-full/omegat.project`
- 一个 OmegaT 项目、8 个 PO、905 个唯一稳定单元
- `display-values.po`：15
- `front-matter.po`：7
- `chapter-01.po`：109
- `chapter-02.po`：273
- `chapter-03.po`：252
- `chapter-04.po`：207
- `appendix-a.po`：24
- `appendix-b.po`：18
- 发布注解 block：516 个独立单元
- 附录 B 空 `msgstr`：12
- `target/`：空；没有模拟人工审校或生成伪造的已译文档

项目使用 EN-US、ZH-CN、英文与简体中文 tokenizer、段落级分段和 PO filter。
过滤器保留空格及文件上下文。词汇表直接指向仓库唯一可写的
`terminology/ipg-glossary.txt`，项目内没有副本。

## 安全闭环

根目录的 `审校助手.cmd` 只启动 Python 工作流。中文菜单提供项目准备、批次完成和
进度查看。再次准备会验证既有 8 个 PO、905 个 ID、英文哈希、正式 YAML、账本和
批注库，不会覆盖 source、target、TMX、批注或词汇表；不完整或不兼容状态故障
封闭。

回写预览要求完整且精确的 8 个 target PO，自动识别修改文件，并阻止未选择文件
混入修改。临时候选只允许选定单元的 `zh` 标量变化。确认后会重新校验 mapping、
正式 YAML、全部 target PO、TMX 和临时候选哈希；正式写回使用最小标量替换。必要
后置检查失败时会回退本批 YAML 和 mapping，不提前登记审校状态。

候选只写入 `outputs/current-candidate/`。两次构建逐字节一致，哈希为：

- `IPG.md`：`cbb812826a73c7f417d46961b692774b0a259284b76ee996356b71aaf0603400`
- `rules.json`：`d7f052c742cdcaf3dde53347c85f7b72fb5d36515f809474f9eebf27c2efe79c`
- `SHA256SUMS`：`dfae8b9fee72a3471804689eb38ef82e9fa345de1d342f5be0b0a57e94c89c0c`
- `build-report.json`：`96c7dad507c99567e2d717b479ded5ec35ff62c5e385a9fc670a7e3fa2585b3e`

## 批注、账本和进度

真实 OmegaT `<note>` 可通过稳定身份或唯一 source/target 对归档；重复文本歧义、
孤立批注、冲突批注和预览后 TMX 变化都会停止。批注只进入
`review/translation-notes/zh-r0001.json`，不会进入内容、发布注解、审校账本或
构建产物。

P2 的 128 单元演示账本、两条演示批注及 action 已移入 pilot 测试夹具。当前真实
全文状态为：

- 总单元：905
- `unreviewed`：905
- `reviewed-unchanged`：0
- `reviewed-modified`：0
- `stale`：0
- 当前翻译批注：0
- 12 项初始缺译中尚余：12

术语审计输出 Markdown/JSON，始终为非阻塞参考；审计程序自身出错也会形成明确
报告，不会阻止已经通过必要检查的写回。

## 验证结果

- 完整测试：124 项通过（P4.5 前基线为 97 项）
- candidate：通过
- release：按预期失败
- release 阻塞：`unreviewed=905`、`missing-translation=12`、注解授权待定、
  `publishable: false`
- 连续两次候选构建：逐字节一致
- 正式 `dist/`：不存在
- Git 远端：未配置

新增覆盖包括精确 PO/单元边界、重复导出、空缺译文、首次/再次准备、所有 ID 与
源文故障、未选修改、无文字变化审校、最小 YAML 回写、预览竞态、真实 `<note>`、
批注歧义/冲突/孤立、目标变化 stale、四种账本状态、P2 状态隔离、非阻塞术语
审计、后置失败回退、candidate-only 输出和 release 门槛。

## 提交序列

1. `71a6a62` `feat(review): prepare safe full OmegaT project`
2. `cd495e4` `feat(review): add isolated full writeback preview`
3. `31c6f6d` `feat(review): archive OmegaT notes and track full review state`
4. `4c7fa71` `feat(review): add Chinese full-document review assistant`
5. 本审阅摘要、最终回归结果与边界证据由随后的 P4.5 收尾提交保存。

P4.5 到此停止。下一步应由维护者真实打开 OmegaT 并开始人工审校；不得据此自动
进入 P5。
