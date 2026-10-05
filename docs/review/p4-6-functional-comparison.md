# P4.6 开始前功能对照（2026-10-05）

调查基线：IPG `4a1460565eb5559d70524c224bf0abfd7e654284`。
参照项目 `D:\mtr-zh` 只读；查看其 pyproject、scripts、core、build、validate、
output_validate、review_workflow、snapshot、official_update 和中文使用说明。

| 分类 | 能力 | MTR 当前行为 | IPG 开始前状态 / P4.6 处理 |
|---|---|---|---|
| 已等价 | YAML 事实源、稳定翻译身份、嵌套 schema | 正文/注解结构化维护 | IPG 原生模型与 source/output v1 已实现；继续复用 |
| 已等价 | Markdown/JSON、确定性、共享版本说明 | 生成 MTR.md / rules.json，包含 version-notes | builder 可生成 IPG.md / rules.json；共享 version-notes 同时进入两者 |
| 已等价 | OmegaT 预览、最小回写、共享术语表 | 同项目多 PO、人工选择已审文件、hash 防覆盖 | 完整 8 PO / 1007 单元闭环已有；不重建实际工程 |
| 已等价 | 四种审校状态、非阻塞术语审计 | 哈希关联状态、术语报告 | IPG 独立 ledger 和批注库已有；不代替人工审校 |
| 底层已有、缺入口 | 验证 source / rules.json | mtr-validate / mtr-validate-output + scripts | validate_release / validate_schema 分散于阶段代码；补 ipg-validate / ipg-validate-output |
| 底层已有、缺入口 | 两次构建、候选与发布门槛 | mtr-build + scripts | builder/full_review 有能力，但生产依赖 pilot 名称且缺成功发布晋升；补 ipg-build |
| 尚未实现 | 当前版本指针 | current-version 日期指针，助手解析 manifest | 增加 current-release 完整身份指针；生产和助手不依赖硬编码目录 |
| 尚未实现 | 正式 dist 事务、release rehearsal | 本地生成 dist 中两个阅读文件 | 新增临时构建、独立校验、字节比较、目录事务与回退；真实当前资料仍拒绝 |
| 尚未实现 | 中文正式收尾菜单 | 审校完成后构建最终文件 | 新增检查发布条件、满足后生成最终文件；保留 candidate 流程 |
| 有意不同 | 版本与输出模型 | 日期型版本；intro/main/appendices 等 MTR 形状 | IPG 官方/注解/中文三轴完整 releaseId；section/component/group/block 与原生 JSON |
| 有意不同 | 违规及附录语义 | MTR 章/节与自己的 PDF 结构 | IPG 基础处罚、组件角色、附录 A 引用和附录 B 日期组，不套用 MTR 解析器 |
| 有意不同 | 发布注解、翻译批注与状态 | MTR 部分批注存于 ledger note | IPG 三套数据独立；多段注解、四级 anchor、order、readingSegments 保持不变 |
| 本阶段不做 | 官方更新发现、diff、翻译继承 | 官方更新入口、快照、自动 PR 等 | IPG 已有冻结 PDF 快照和 reconciliation；完整日常更新入口仍待 P5 |
| 本阶段不做 | GitHub CI / Release | 已有托管、CI 和发布流程 | 未配置；P4.6 仅本地生产链路，不添加远端或发布自动化 |

正式能力与参照项目对齐不意味着放宽 IPG 发布门槛。真实資料的 12 项缺译、
授权/署名及 publishable=false 均保留；成功路径仅使用明确标识的合成测试仓库。
账本用户改动基线 SHA-256：
`6da1987fe1e08be7398ce6922cffbcc42d8b948d9d122a59143c0e777cc1d4c6`。
它不纳入本阶段提交。旧内容、快照、迁移结论、实际 OmegaT 和既有候选不重建。
