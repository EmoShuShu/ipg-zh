# P5 官方更新、不可变快照与保守继承审阅

日期：2026-10-05。基础：P4.6 `c887eddc164bac6e5932134f38d913c1ba9f015f`。
工作分支：`codex/p5-official-update`。本阶段只提出候选，不提升、不翻译、不发布。

## 入口与实现

新增命令 `ipg-check-official-update`、`ipg-snapshot`；包装为
`scripts/check_official_update.py`、`scripts/snapshot.py`。

| 文件 | 变更 |
|---|---|
| `ipg_pipeline/official_update.py` | 官方发现、安全下载、版本判断及隔离候选协调 |
| `ipg_pipeline/snapshot.py` | 三轴/上下文快照、哈希清单、不可变复用及独立核验 |
| `ipg_pipeline/official_inheritance.py` | 原生结构 diff、译文/状态/注解/批注的分离继承 |
| `ipg_pipeline/full_parser.py` | 保留冻结 P3 契约；显式更新模式接受已核验的新 PDF 日期/哈希和动态页区 |
| `ipg_pipeline/reconcile.py` | 独立更新 reconciliation；上下文/证据/override 决定 ID，非数组位置 |
| `schema/update-overrides.schema.json` | 版本绑定、全部参与节点哈希、操作和严格未知字段策略 |
| `tests/test_official_update.py` | 86 项本地更新/快照/继承/安全回归 |
| `pyproject.toml` | 注册两个正式入口；无新依赖 |
| README、中文说明、架构计划第 22 节 | 使用说明、MTR 一致/差异及人工停止点 |

不依赖 `D:\mtr-zh` 运行。参照其入口与快照控制流程，但不用其 parser、日期单轴
或检查后自动更新当前源/指针的行为。source/output v1 节点形状保持不变。

## 实际官方检查

- 规则页：<https://wpn.wizards.com/en/rules-documents>
- 精确条目：`Magic Infraction Procedure Guide`（英文 downloadableDocument）。
- 网页 `updated`：**2024-09-24**；PDF 唯一 `Effective`：**2024-09-23**。
- PDF：<https://media.wizards.com/ContentResources/WPN/MTG_IPG_2024Sep23_EN.pdf>
- 下载时间：**2026-10-05T12:56:12+00:00**；本次 PDF 无重定向。
- 完整 PDF SHA-256：
  `056b1687bae4a6c5d7bc4fe4e15d66613e725197cc2a45e18084c2eaf1b5aae0`。
- 结果：**no-update**。与当前 2024-09-23 基线日期/字节一致，没有发现新官方版本。

首轮可见卡片解析严格停止：WPN 首屏只有最新 10 项，未包含 IPG。随后只读诊断
确认完整文档集合位于 Nuxt 结构化引用池，增加并通过 14 项页面/日期回归，再用
保存的实际官方页面完成同一次检查并下载 PDF。未把“未显示 IPG”当作无更新，
未猜旧地址或文件名；页面更新时间不冒充 PDF 有效日期。确定性复跑使用缓存，
没有再次下载 PDF。实际官方检查只有一个完成的 PDF 观察；非持续监控。

### 结构 diff 与继承计算

| 项目 | 实际结果 |
|---|---:|
| section / component / group / block | 36 / 110 / 124 / 338 |
| 新增 / 删除 / 修改 / 英文变化 | 0 / 0 / 0 / 0 |
| 改号 / 移动 / 重排 | 0 / 0 / 0 |
| 保留官方 ID / 新 ID / 退役官方 ID | 608 / 0 / 0 |
| registry 条目 | 1976，拟议 registry 与当前内容相同 |
| 非空中文翻译单元 / 缺译单元 | 995 / 12 |
| 发布注解继承 / 失锚未决 | 330 / 0 |
| 审校 reviewed-unchanged / reviewed-modified / unreviewed / stale | 1007 / 0 / 0 / 0 |
| 翻译批注继承 / stale | 0 / 0 |
| PDF 覆盖 / 未分类 | 1050 / 0（31 页） |
| 更新未决 finding / 人工 update override | 0 / 0 |

上述是协调器的只读继承计算，**没有写回当前源或审校资料**。中文计数包含当前
OmegaT 的正文/片段、发布注解、标题及受控显示值，不是官方段落数。审校状态来自
用户现有账本；不代表 12 项缺译已经完成，也不是程序新增审校判断。

实际 candidate 验证通过；当前 release 仍因 12 项缺译、注解授权/署名未完成及
`publishable: false` 拒绝。P5 不解决这些既有门槛。

## 快照与确定性证据

相对仓库根目录的快照位置（为便于阅读分行，实际路径连续）：

```text
outputs/official-update/snapshots/official/2024-09-23/
056b1687bae4a6c5d7bc4fe4e15d66613e725197cc2a45e18084c2eaf1b5aae0/imports/
acce82ca7224d5d741609807a2d95697337e56110354efec370938dea51b75fb/zh-r0001/
647b3c3d4a766d5791e470b977e89d729db07efdccd06f181142e73d7c75c7f8/
```

主要审阅文件：`reports/update.json`、`reports/review.md`、
`reports/reconciliation.json`、`reports/pdf-coverage.json`、`snapshot.json`。
无新 PDF，所以不生成新 candidate preview，不重建现有候选或 OmegaT。

两次相同上下文运行：**89 个被列入哈希清单的文件逐字节一致**，首次下载时间
保持不变；SHA256SUMS 本身也保持不变。独立 `ipg-snapshot --verify` 通过。

| 文件 | SHA-256 |
|---|---|
| `parsed/official.json` | `7c3c2c887c67a11caf0e46103d471349fffbf36ba1bf16b1335b645bbe9de29e` |
| `reports/update.json` | `bb32ceec4fca44af0402ddafabe5949ce568496d5a5eaa3f88bcca60d13d51a7` |
| `proposed/id-registry.yaml` | `fca86db05a9df1f0850e92f74f671954c36521128e75ff47f9da21d86287fead` |
| `snapshot.json` | `2e1d195060e6f3ba52aa25f8365bb5d5b6924eded51a0d95946e159622d6fa9a` |
| `SHA256SUMS` | `e6afbd7f482e87775af9c0f0a48b7db451d2e046108c01959b6a457819bb914c` |

文件哈希映射规范 JSON 的摘要：
`170cfb064efe055789a18ba51beca135b8a9bafa39aab1900b357d9fa5577bef`。
快照全部在忽略目录，未提交大型 PDF/解析/工作文件或用户账本副本。

## 测试与失败保护

P5 专门测试：**86 passed，39.09 秒**。
完整命令：`.venv\Scripts\python.exe -m pytest -o addopts= -v --durations=10 -p no:cacheprovider`。
完整结果：**312 passed，263.88 秒（4 分 23 秒）**，无失败或跳过。

合成 PDF 下载夹具明确标注 TEST ONLY，解析器显式注入；不能伪装成真实政策。
真实 PDF 的 parser/reconciliation 另有只读回归。主要证据：

| 类别 | 通过的检查 |
|---|---|
| 发现/下载 | 精确条目；首屏外完整集合；重复/缺失/非法引用/未知结构；可见与集合冲突；网页与 PDF 日期分离；非法 URL/跳转、长度、压缩、截断、超大和非 PDF |
| 版本 | 无更新、新日期新 PDF、同日换包、日期回退拒绝；不同基线/不同包隔离 |
| 原生身份 | 插入、删除、重排、改名、改号、移动；拆并默认不保留及显式连续性；歧义 finding、不按位置继承；退役不复用；坏 override 哈希/重复消费拒绝 |
| parser | 新日期/哈希、附录页区移动；新增第 24 个违规必须得到 TOC/正文/附录 A 实际引用闭环，删除附录行即失败 |
| 继承 | 未变中文/有效审校；英文修改保留旧译候选且 stale；新单元缺译/unreviewed；无 override 不猜修改身份；四级发布注解保留位置/order、失锚完整隔离；源或译文改变令批注 stale |
| 快照/保护 | 修改、删除、额外文件、坏 manifest/清单检测且不覆盖；同上下文逐字节复用；保留首次下载时间；失败不改变 source/指针/ledger/OmegaT/dist；外部输入变化拒绝且不还原外部编辑 |

测试成功路径只在隔离临时仓库中生成新版本候选。真实当前源、原始 PDF、
`AIPG_2025.md`、迁移结论、pointer、registry 与候选内容均未重建。
用户 `review/status/zh-r0001.json` 始终保持未提交，未暂存/提交，字节哈希：
`6da1987fe1e08be7398ce6922cffbcc42d8b948d9d122a59143c0e777cc1d4c6`。

开始时记录 220 个可读取的既有资料文件；217 个保持原字节，3 个 OmegaT 运行时
文件（project_stats.json、project_stats.txt、last_entry.properties）由正在运行的
OmegaT 改变。它们未被本流程写入或恢复；PO、TMX、候选阅读文档等保持不变。
另一个 omegat.project 被 OmegaT 占用、无法做字节核验，不宣称已核验。
正式 `dist` 仍不存在；未配置 Git 远端，MTR 工作树未修改。

## 提交与停止点

实现提交：`1312ea65c063ab05cea37e9bb57d384b1eefc49a`
（P5: add isolated official updates and immutable native IPG snapshots）。
文档与审阅证据另作收尾提交，基础历史不改写；最终 HEAD 由交付消息及 Git 日志
给出，不写自引用哈希。两次提交均不包含用户账本或 outputs。

本次无新官方版本，更新未决为空。仍待后续人工解决的是原有缺译、完整审校、
注解许可/署名及 publishable 决策。未来真正更新时，提升前还需明确身份 override、
失锚处置、新版本来源/继承证据及源/指针与 OmegaT 的协调授权。
本阶段不提供自动提升，不进入人工翻译、OmegaT 升级、当前 release 切换或远端发布。
