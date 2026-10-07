# IPG 中文项目：本地维护与生产链路

参照 `mtr-zh` 的维护流程和接口，但使用 IPG 原生模型，不照搬 MTR 的 PDF
解析器、日期型版本或 JSON 字段形状。

## 当前版本与事实源

- `src/ipg/current-release.txt`：完整 release ID，组合官方、注解与中文修订。
- `src/ipg/releases/<release-id>/manifest.yaml`：声明版本和范围，不选择 profile。
- 同目录 7 个 YAML：正文、译文和最终发布注解的维护事实源。
- `src/ipg/version-notes.md`：共用版本说明，同时进入 Markdown/JSON，不进入 OmegaT。
- `review/status/<revision>.json`：审校账本；生产命令只读，不自动审校或补记录。
- `review/translation-notes/<revision>.json`、`review/actions/<revision>.yaml`：独立批注与行动记录。
- `review/migration/<release-id>.json`：版本化迁移完成证据，引用既有 P4 精简摘要及哈希。
  不读取忽略目录中的 raw-unit 工作文件，不使用写死的成功覆盖计数。

指针只能填写一个完整 ID。程序核对目录、manifest 身份和安全路径，不通过排序
猜测“最新版本”。换版本后的 OmegaT 协调属于后续阶段；不一致的基线会被拒绝，
不会自动覆盖工程。历史阶段命令用于复现，不用于重新迁移已经审校的事实源。

## 四类产物

| 类别 | 验证与位置 | 含义 |
|---|---|---|
| candidate | `--profile candidate`，只在 `outputs/` | 可审阅，可报告缺译与审校问题，不能发布 |
| release rehearsal | `--profile release --rehearsal`，只在 `outputs/` | 全部正式门槛的演练，不更新 dist、不发布 |
| 正式 dist | `--profile release`，仅 `dist/IPG.md` / `dist/rules.json` | 通过全部本地门槛的读者/网站文件，不手改 |
| GitHub Release | 本阶段不做 | 远端标签、附件及公开发布，不能由本地 dist 推断 |

初次迁移有 12 项缺译；当前缺译数和审校状态以检查报告为准，不要求修订后仍
保留 12 项空译。注解授权/署名与 `publishable: false` 仍是独立发布门槛，
程序不会代替维护者解决这些内容问题。

## 审校助手

双击 `审校助手.cmd`。选项 1—3 保留 OmegaT 准备、回写候选和进度查看，4 退出。
新增 5 **检查正式发布条件**（只读，中文列出原因）和 6 **条件满足后生成最终
文件**（再次验证、临时两次构建、独立校验、目录事务晋升）。无需填写 manifest
路径，不改审校账本，不创建 GitHub Release。

条件不满足时旧 dist 不变，候选流程仍可用。修订开头的版本说明请编辑
`src/ipg/version-notes.md`，不要改不可变旧源或输出。
详见 [OmegaT 使用说明](docs/omegat-review-guide.md)。

选项 2 的历史迁移测试从不可变 PDF/旧源重建独立基线，不再把旧中文的内容哈希
或初始缺译数当成当前中文必须遵守的条件。正常修订和缺译补齐仍须通过结构、
稳定 ID、PDF 溯源、预览基线、最小改动及候选输出验证。

新候选在隔离目录完成两次构建、批注归档和最终审校检查后，才整体替换
`outputs/current-candidate/`。晋升前的检查失败或 Ctrl+C 时恢复本批源、工程映射、账本、
批注和报告，不删除此前候选，也不触碰 OmegaT 的 source/target/TMX。
若目录替换及自动恢复同时失败，旧候选保留在
`outputs/.current-candidate-previous/`，程序会报告位置并阻止再次覆盖；应先
人工恢复，不直接删除备份。详见 [修复记录](docs/review/2026-10-08-review-writeback-repair.md)。

## 安装与命令

Python 3.12+。已有环境重新安装 editable 项目即可注册新命令：

```powershell
python -m pip install -e ".[test]"
# 无 pip、由 uv 管理的现有环境：
uv pip install --python .venv/Scripts/python.exe -e ".[test]"
```

```powershell
.venv\Scripts\ipg-validate.exe --profile candidate
.venv\Scripts\ipg-validate.exe --profile release
.venv\Scripts\ipg-build.exe --profile release
.venv\Scripts\ipg-validate-output.exe

.venv\Scripts\python.exe scripts/validate.py --profile release
.venv\Scripts\python.exe scripts/build.py --profile release
.venv\Scripts\python.exe scripts/validate_output.py --input outputs/current-candidate/rules.json
```

维护者不必输入路径；开发者可用 `--project-root` 指向隔离仓库。validate/build
必须显式选择 profile；退出码 0 为成功，1 为失败。候选/演练：

输出验证入口同样解析当前指针并核对 releaseId；独立 schema 检查不重复要求
源的审校账本通过，不把“输出结构有效”当作“允许发布”。

```powershell
ipg-build --profile candidate --output outputs/my-candidate
ipg-build --profile release --rehearsal --output outputs/my-release-rehearsal
```

选择新的独立目录，程序不清空已有证据。rehearsal 不放宽门槛，真实当前资料
也会拒绝；使用相同的最终文件字节，以路径和 receipt 区分用途。测试夹具在
release ID、正文、版本说明和快照中明确标识“合成测试”，不是 IPG 发布。

## 正式构建与回退

从当前指针读取 source，核对 schema、registry、快照哈希、PDF 溯源版本、迁移
证据、完整审校账本、缺译、许可署名、全文范围和 publishable。通过后在同一
磁盘临时目录构建两次，各自独立读取验证 rules.json，比较 Markdown、JSON、
SHA256SUMS 和 build-report 四文件。输入期间变化会拒绝更新。

dist 只放两个最终文件。哈希和报告通过外部返回报告/命令输出提供；候选和
演练目录还保存 SHA256SUMS、build-report、receipt。rules.json 不包含自身
哈希；术语问题或审计程序错误只进报告，不阻塞构建。

晋升是应用级目录事务，不是逐个文件覆盖：构建锁串行化写入者，旧目录改名为
恢复备份，再以一次重命名放入准备完整的新目录；晋升异常回退。Windows 不能
直接 rename-exchange 非空目录，已有 dist 在两次重命名间有极短的“路径暂
不存在”窗口，但没有半新半旧文件；首次创建为单次原子目录晋升。外部并发
读者应重试，不能把它误称为无窗口的内核原子交换。

崩溃或回退文件占用时，`.ipg-build-*-previous-dist` 保留完整旧文件；锁和
恢复备份阻止后续覆盖。请先核对并恢复，不直接删除备份。成功后的清理错误会
记录保留备份，不把已更新的完整目录误报为校验失败。若需要对外读取时绝对
无窗口，必须另行设计读者支持的版本目录/指针，不偷偷把 dist 改成链接。

## 测试与边界

```powershell
.venv\Scripts\python.exe -m pytest -o addopts= -v
```

成功路径使用 `tests/fixtures/release-rehearsal/source.yaml` 和隔离临时仓库。
不填真实缺译、不改真实许可或账本。没有远端、GitHub、PR、Release 或自动
发布。官方更新 diff/继承与不可变证据已在 P5 接通；人工修订、当前版本提升、
OmegaT 新版本协调及 CI/发布仍须另行批准。

## 官方更新与快照（P5）

三个动作不同：**检查**是取得官方资料并比较；**快照**是固化可核验的证据；
**候选更新**是提出新版本源和身份映射。它们都不表示人工批准，更不等于正式
dist 或 GitHub Release。当前版本仍只由 `current-release.txt` 决定。

```powershell
.venv\Scripts\ipg-check-official-update.exe
.venv\Scripts\ipg-snapshot.exe
.venv\Scripts\ipg-snapshot.exe --verify <快照目录>

.venv\Scripts\python.exe scripts/check_official_update.py
.venv\Scripts\python.exe scripts/snapshot.py --verify <快照目录>
```

`check-official-update` 访问 `https://wpn.wizards.com/en/rules-documents`，而
`snapshot` 无参数时完全离线，使用当前指针的已有官方 PDF 生成加强证据。
`snapshot --verify` 只核验已有 P5 快照。退出码 0 为检查/核验成功，1 为停止，
不把 `no-update` 视为错误。网络超时、页面异常或未知 PDF 版式都会停止。

### 发现与版本判定

WPN 首屏可能只渲染最新 10 项，IPG 不一定可见。程序按已核验的 Nuxt 引用池
读取规则页的完整 `downloadableDocuments.entries`，精确检查 IPG 标题、英文
语言、文档类型、更新时间和关联 CTA；可见卡片存在时必须与完整集合一致。
没有完整集合时仅接受唯一明确的可见 IPG 卡片；结构变化、重复条目、无效引用、
无效日期或两个来源不一致均拒绝，不全文搜字符串、猜分页或回退到旧 PDF 地址。

只接受 HTTPS 的 `wpn.wizards.com` 指定规则页及 `media.wizards.com` PDF；每个
重定向在跟随前验证，最多 5 次。网页上限 5 MiB、PDF 上限 30 MiB；拒绝压缩
响应、截断、非法地址和非 `%PDF-` 文件，计算完整文件 SHA-256。

网页 `updated` 是 `pageUpdatedDate`，不是政策有效日期。政策有效日期必须来自
PDF 唯一的 `Effective ...` 标题，并与全文解析交叉验证。文件名不参与判定。
相同有效日期与哈希是 `no-update`；新日期新哈希是 `new-official-version`；相同
日期不同哈希是 `same-day-repack`。PDF 日期回退直接拒绝，不自动回退当前版本。

### 不可变证据与位置

所有新增文件仅在 `outputs/official-update/`，不扩写原有 `snapshots/official/`。
P5 快照路径：

```text
outputs/official-update/snapshots/official/<有效日期>/<完整 PDF 哈希>/
  imports/<注解来源完整哈希>/<中文修订>/<完整上下文哈希>/
    official/IPG_EN.pdf、rules-page.html（联网检查时）
    parsed/official.json
    inputs/base/…、inputs/update-overrides.json
    implementation/ipg_pipeline/…
    proposed/id-registry.yaml、mapping-overrides.yaml
    reports/update.json、review.md、pdf-coverage.json、reconciliation.json
    reports/unresolved-annotations.json、deleted-review-records.json
    candidate/…（仅发现 PDF 变化时）
    snapshot.json、SHA256SUMS
```

`snapshot.json` 保存三版本轴、来源/下载时间、实现及依赖版本、所有基础输入和
registry/override 哈希；`SHA256SUMS` 包括 snapshot.json，但不对自身求哈希。
上下文由基础版本/资料、目标官方版本、实现和 override 决定。复用同一上下文时
保留首次下载时间，逐项验证而不覆盖；基础译文、账本、实现或人工映射改变会得到
另一个快照。相同日期不同 PDF 哈希永远分开。哈希清单检测损坏，不是数字签名。

为保证审阅包可独立核验，P5 隔离包包含 PDF 和实现/输入副本；它不是再次导入旧
中文，也不提交重复大型工作文件。删除本地 outputs 会丢失这些未提交证据，请在
需要时自行保存完整目录；旧基线快照、源、迁移证据及 Git 历史仍保持不变。

### 保守协调与继承

解析只给 extraction ID；独立 reconciliation 使用既有 registry、英文证据和
IPG 父节点/角色上下文。编号、标题或数组位置不是身份。唯一匹配才继承 ID；
插入新节点分配未使用编号，删除保留永久退役及历史。移动和重排做结构 diff。
无法唯一确认身份时产生 finding 和隔离的临时拟议身份，旧候选 ID 不盲目退役。

英文修改默认不自动猜身份：报告保存旧中文建议，新节点中文保持空白。人工明确
确认身份后可通过 `--overrides <文件.json>` 重新生成另一快照。该文件符合
`schema/update-overrides.schema.json`，绑定基础 release ID、目标日期/哈希以及
全部旧/新节点的语义哈希；支持 continue、rename、renumber、move、split、merge。
哈希改变或重复消费即拒绝。拆并默认不保留旧 ID、不批量复制旧译；如需连续性，
必须明确选择 `preserveId` 与 `preserveExtractionId`。旧迁移 override 仅归档，
不会当作新 PDF 的身份决策自动套用。合成示例见 `tests/test_official_update.py`。

身份及英文未变：继承中文和仍有效的审校状态。英文变：旧译只作待修订候选，
审校 stale；新单元缺译且 unreviewed；删除单元退出正文和新 ledger，旧审校记录
单独归档。上下文改变也可令关联注解/译文 stale，不能把保留译文当作已审。

发布注解保留四级 anchor、position、order、appliesTo 和多段内容；锚点/片段/
引用丢失时整个注解进入 unresolved-annotations 报告，不丢弃、不改挂。英文段落
改变后旧 readingSegments 不再套用，其旧译/划分进入 finding。翻译批注独立继承
到新修订 notes store，源文或译文任一哈希不符都会 stale，删除单元的批注保留
并报告；批注不混入内容或审校账本。

候选的 `publishable` 永远为 false。其 `migrationLineage` 只说明旧源的历史覆盖，
不是“新英文已经迁移、翻译或审完”的证明；全部更新未决同时记录为 candidate
readiness 问题。提升前必须人工解决 finding、建立新版本来源/继承证据、核验版本
轴和全部发布门槛，再另行批准源/指针与 OmegaT 协调。P5 不提供 `--apply`。

与 MTR 一致：正式入口与 scripts 包装、安全发现、哈希快照、结构 diff、保守
翻译继承。刻意不同：IPG 原生 parser、三版本轴、持久 ID 生命周期、处罚/附录
交叉引用、三套审校数据隔离，以及只提出候选、不自动写入当前 release 或指针。
P5 审阅包交付后停止，不开始翻译或配置远端发布。
