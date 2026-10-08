# CPV 英文录入重复勘误

- 日期：2026-10-06T21:23:52+08:00
- 单元：`annotation-block:ipg-ann-3-7-60602d07adb5-g01-b02`
- 原因：维护者确认是录入时重复，不是注解作者的原文错误。
- 修改：仅删除相邻重复的第一句中的一次；中文、稳定 ID、锚点和旧源定位保留。
- 旧英文 SHA-256：`8336bca2c2ce27804c73c1f049cd13cbdfdd8df3483a1d6f41bfc921c261f33a`
- 新英文 SHA-256：`0bea8abd84edec78a75a34aeee51374c04f1fc5b1fca9092d8f2a3b2df0fb09a`
- 登记表保留原始 allocated 历史及迁移 evidence，追加 updated 勘误历史。
- 同步：第三章 YAML、source/target PO、工程 TMX 与三份导出 TMX、映射基线和审校账本。
- 本单元原已审状态因英文变化而失效，现为 stale；需重新人工核对。其他审校状态保留。
- 原始 AIPG、旧源快照、历史迁移证据、备份及官方版本身份均保留。
- 完整本地备份：`outputs/cpv-duplicate-backup-2026-10-06T212352_0800`。
- 校验：OmegaT 基线与八份 target PO 通过；所有 target 中文和 TMX 批注保留；candidate 验证通过，连续两次构建逐字节一致；审校进度已刷新。

这是一次特定的本地录入勘误。复现脚本为 `scripts/correct_cpv_duplicate.py`，只接受指定单元的原始英文哈希，并拒绝重复执行。

## 修改范围与验证结果

受版本控制的内容修改只有第三章 YAML 中一个英文标量、登记表中一条新增历史，
以及审校账本中该单元的 sourceHash 和 status 两行。与修复前备份逐项比较，
1007 个单元的中文、其他 1006 个单元的审校记录及词汇表均保持不变。

OmegaT 工程内实际更新七个文件：

- `source/chapter-03.po`
- `target/chapter-03.po`
- `omegat/full-review.mapping.json`
- `omegat/project_save.tmx`
- `omegat-ipg-full-level1.tmx`
- `omegat-ipg-full-level2.tmx`
- `omegat-ipg-full-omegat.tmx`

工程配置、光标位置、其他 PO、备份 TMX 和其他项目文件均未改动。工程保存的
176 条批注全部保留。`outputs/current-candidate/` 的五个文件及
`outputs/review-status.md`、`outputs/review-status.json` 已重新生成。

修复前完整回归为 312 passed。新增的两个回归场景验证保留未回写译文与批注、
拒绝重复执行，以及构建失败后恢复原文件。修复后完整回归为
**314 passed in 130.77s**；candidate 校验通过，两次候选构建逐字节一致。

后续打开 `outputs/omegat-ipg-full/omegat.project` 继续审校。这一个英文更正单元
需重新人工核对，当前进度为已审未改 1006、stale 1。
