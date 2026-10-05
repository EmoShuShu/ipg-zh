# 本地审校助手与 OmegaT 阅读顺序修正

本轮以 `1cda4d8f7d07375063093534e5d588fb2f945342` 为基线，仍停留在
`p4-5-full-review-workflow`，未进入后续阶段。`mtr-zh` 仅作为只读行为参考。

## 三项修复

1. 启动器切换到自身所在目录，使用 CRLF、ASCII 启动诊断和 Python UTF-8 模式。
   从其他工作目录启动并选择退出的 Windows 回归测试通过；另已从项目目录外
   实际走通选项 1（继续既有工程），退出码 0，无错误。
2. PO 不再导出空 `msgid` 的 MIME 表头，PO filter 同时跳过 header。被注解分隔
   的正文使用独立双语阅读片段，不再出现整段英文对应两段中文的审校边界。
3. Markdown 与 PO 共用 `reading_events`，按 section/component/group/block 的
   anchor、position、order 导出正文和注解，而不是先全篇正文、再全篇注解。

## 数据边界与溯源

source schema v1 增加可选 `readingSegments` 和 `anchor.segmentId`，output
schema 继续直接引用 source 定义，不另建容易漂移的节点模型。官方父 block 保持
完整英文、汇总中文、原 ID 和原 PDF provenance；片段以 `sourceRange` 精确引用
父段英文（Unicode 字符索引、左闭右开），并保存已有 `legacyRawUnits`。段落类型
和列表项类型才允许阅读分段；列表续段不会重复项目编号。

按旧源证据分段 65 个官方 block，共 167 个阅读片段。注册新片段 ID 167 条，
registry 从 1809 增至 1976；原有 registry 条目全部保留。新身份由父 ID 及不可变
旧源对生成，不依赖当前数组位置。官方树仍为 36 section、110 component、
124 group、338 block，P3 冻结前缀 649 条及 P2 158 个 ID 保持不变。

分段只改变阅读和翻译单元边界，不改写中文。重复英文和轻微版本差异通过唯一的
两侧英文证据定位边界；2.1 延迟/复制触发的已知旧英文异常用显式、有源文哈希的
override。不能唯一判断的情况返回 finding，不按位置猜测。全部当前分段均成功
复现，未决边界为 0。167 个既有发布注解增加片段定位，注解文本与原有 order 不变。

第四章开头现在是四个连续单元：第一段正文、解释运动道德区分的注解、主审仲裁
那句正文、裁判/主审判定的注解。两个片段均使用官方英文和已迁移中文。

片段回写只修改选定片段的中文和父段落的派生汇总中文。英文、PDF provenance、
ID 和注解文本不变；预览完整列出被修改的翻译单元，并验证这两类允许的标量差异。
审校账本枚举与 PO 采用相同单元边界，发布门槛未降低。

## 已有工程保护

维护者保存并关闭 OmegaT 后，原工程完整保存在
`outputs/omegat-ipg-full.before-reading-layout/`；正式 source 和 review 的原始
本地备份在 `outputs/repair-backup-2026-10-05/`。正常工程路径保持
`outputs/omegat-ipg-full/omegat.project`，没有自动打开软件。

原 TMX 的 65 个未修改父段落转换为对应片段，保存的 TU 从 893 增至 995。
12 个原先缺译单元仍为空，target 仍为空，未模拟人工审校。其他单元的 TMX、
目标译文和批注保留。升级遇到父段落未回写修改、父段落批注、未知身份、不完整
target、异常旧账本或工程竞态时停止；原工程不被覆盖。备份中的旧记录供追溯，
新片段不会自动继承父单元的已审状态。

真实 OmegaT 的 TMX `lang` 属性和 `path` 身份属性现已纳入批注提取，保留既有
`xml:lang` 支持。翻译批注与发布注解、审校状态继续隔离。

当前 PO 数量为：display-values 15、front-matter 7、chapter-01 140、chapter-02
302、chapter-03 278、chapter-04 223、appendix-a 24、appendix-b 18，总计 1007。
其中发布注解仍为 516 个独立 block 单元，集中显示值仍为 15 个唯一单元。

## 验证与候选

完整测试命令：`.venv\Scripts\python.exe -m pytest -o addopts= -q`。
最终结果：**149 passed in 485.08s**，无跳过项。新增 18 项测试覆盖原生 CMD
启动、PO 表头、双语分段与注解交错、四级锚点及 order、列表续段、非法范围/锚点/
重复身份、歧义 finding、最小回写与汇总中文、真实 TMX 属性，以及工程升级的
保留和拒绝路径。完整回归发现的旧数量断言和重复 order 检查问题均已修正；最终
测试重新从新进程完整运行。官方 PDF 再次解析覆盖 1050/1050，未分类为 0。

candidate 通过；release 仍失败：缺译 12、unreviewed 1007、注解授权未完成、
`publishable: false`。没有新增已审记录。连续两次构建四个文件逐字节一致：

- IPG.md：`1064bd82101241f206ac6bf00b37ec55e860ccc4150d622903e0118a7aff75f1`
- rules.json：`a8e1934816cc3b9722ff6c6630b8bca4fd173ee10c5b376108a57afbe816d15e`
- SHA256SUMS：`92108156d30df9d92bc0cd30efd45f43b7d7adb8e251095e2009a396d10b1e29`
- build-report.json：`57fa2c134f0a1ecf5c4f8621b9eb01f05ae89432399aab7beaec2b57c3e68f49`

哈希只进入外部报告，未写入 rules.json 自身。候选仅在
`outputs/current-candidate/`。`dist/` 仍不存在，未配置远端或 GitHub。

本摘要是 P4.5 后的修正记录；原 P4/P4.5 审阅包保留为当时的历史证据，不把旧
905 单元统计改写成新的统计。当前使用说明与 README 已同步新边界。
