# ipg-zh

This repository currently contains the approved work through P5: the complete
2024-09-23 official English IPG structure, conservatively migrated legacy
Chinese text, the bilingual AIPG publication annotations found in
`AIPG_2025.md`, and a full-document OmegaT review workflow. It is a review
candidate, not a publishable release.

## P5 官方更新与不可变快照

```powershell
.venv\Scripts\ipg-check-official-update.exe
.venv\Scripts\ipg-snapshot.exe
.venv\Scripts\ipg-snapshot.exe --verify <快照目录>
```

提供 `scripts/check_official_update.py` / `scripts/snapshot.py`。更新检查读取 WPN
规则页完整文档集合，精确匹配英文 IPG；网页更新时间与 PDF 的 Effective 日期
分别记录，以 PDF 有效日期和完整 SHA-256 判断更新。同日换包不会被当作未更新。

所有新证据都在忽略目录 `outputs/official-update/`。快照保存权威 PDF、完整解析
与溯源、实现与输入哈希、registry/override、结构差异及三版本轴。已有快照仅验证
并复用；新版本仅生成不可发布的隔离候选，不修改当前指针、源、账本、OmegaT 或
dist。歧义、拆并和失锚需要人工确认，不能自动套用位置或丢弃注解。
详见 [中文维护说明](README.zh-CN.md#官方更新与快照p5) 和
[P5 审阅记录](docs/review/p5-official-update.md)。P5 不提供自动提升或发布入口。

## P4.6 本地生产入口

详见 [中文说明](README.zh-CN.md) 和 [MTR/IPG 功能对照](docs/review/p4-6-functional-comparison.md)。
`src/ipg/current-release.txt` 指向完整 release ID；日常维护不依赖 pilot 或阶段命令。

```powershell
.venv\Scripts\ipg-validate.exe --profile candidate
.venv\Scripts\ipg-validate.exe --profile release
.venv\Scripts\ipg-build.exe --profile release
.venv\Scripts\ipg-validate-output.exe
```

同时提供 `scripts/validate.py`、`scripts/build.py`、`scripts/validate_output.py`。
正式构建只有 release 门槛、临时两次构建字节比较、独立输出 schema 检查均通过后，
才事务更新仅含 `IPG.md` 与 `rules.json` 的 dist。候选/演练只在 outputs，
GitHub Release 不在本阶段。当前真实资料仍被拦截，不会生成正式 dist。

## 全文 OmegaT 审校

普通维护者请双击仓库根目录的 `审校助手.cmd`。中文菜单可以安全准备或继续唯一的
全文 OmegaT 项目、完成一批审校并生成候选阅读文档，以及查看 1007 个翻译单元的
进度。选项 5 检查发布条件，选项 6 在条件满足后生成本地正式文件，无需填写
manifest 路径。详细步骤见 `docs/omegat-review-guide.md`。

OmegaT 项目位于忽略目录 `outputs/omegat-ipg-full/`，包含 8 个 PO。项目直接使用
`terminology/ipg-glossary.txt`，不会复制另一份词汇表。初次迁移有 12 项缺译；当前
缺译数以检查报告为准，允许人工补齐，但不得复制英文或用机器译文填充。候选
阅读产物只写入 `outputs/current-candidate/`；正式
`dist/` 只有正式 release 检查通过后才生成；当前不会生成。

正文与发布注解按阅读位置交错导出。被注解分隔的官方段落以稳定的双语阅读片段
进入 OmegaT，同时保留完整官方 block 和 PDF 溯源。详见使用说明中的分段与备份规则。

选项 2 的历史迁移测试使用独立基线，不要求当前中文保持旧样或永久缺译。
本批任一检查失败会恢复源、映射及审校资料，并保留此前候选；新候选只有完整
成功后才替换。详见 [回写修复记录](docs/review/2026-10-08-review-writeback-repair.md)。

本地修订“版本说明”时，请直接编辑共用的 `src/ipg/version-notes.md`。这与
`mtr-zh` 的维护方式一致：它不进入 OmegaT，构建时会位于 IPG 内容前部，同时以
独立的 `ipg-version-notes` 节点写入 `rules.json`。后续通过审校助手完成一批审校并
生成候选文档时，会读取最新内容。编辑该文件不会自动改变 manifest 中的官方日期、
注解版本或中文修订号，也不会解决注解授权门槛。

The authoritative English source is the immutable WPN PDF snapshot. The legacy
`AIPG_2025.md` file is a migration input only and remains byte-for-byte
unchanged. Candidate artifacts are visibly marked and must not be published.

## P4 full legacy migration

P4 rebuilds the seven-file candidate source directly from the immutable PDF
and legacy snapshots, the stable-ID registry, and reviewed mapping overrides.
It does not use an ignored P3 output as an input.

```powershell
ipg-p4 migrate
ipg-p4 validate
ipg-p4 build
ipg-p4 review
# or run the four steps in order
ipg-p4 all
```

The committed source is under
`src/ipg/releases/ipg-2024-09-23__ann-aipg-legacy__zh-r0001/`. Generated raw
ledgers, parser diagnostics, reports, and the visibly marked preview are kept
under ignored `outputs/p4/`. Formal `dist/` remains untouched.

Candidate validation succeeds with all 4,085 legacy raw units uniquely
disposed and no unresolved mappings, duplicate consumption, orphan annotation,
or deferred annotation. The original P4 migration has 12 Appendix B
entries with no legacy Chinese; that is a historical baseline, not a permanent
constraint on subsequent manual translations. Release validation remains gated:
publication-annotation licensing/attribution is pending, and the manifest is
`publishable: false`.

P4.5 creates the ignored local project only when the review assistant prepares
it. Translation notes and review state remain isolated under their versioned
`review/` paths. A newly created ledger starts unreviewed; current status must be
read from the versioned ledger, not inferred from this README. Older P2
demonstration records live only in pilot fixtures.

## Historical P0-P2 pilot workflow

```powershell
ipg-pilot parse
ipg-pilot reconcile
ipg-pilot migrate
ipg-pilot omegat-export
ipg-pilot omegat-preview
ipg-pilot omegat-apply --expected-change-count 1
ipg-pilot translation-notes
ipg-pilot review-status
ipg-pilot terms
ipg-pilot validate --profile candidate
ipg-pilot build --profile candidate
ipg-pilot p2-pack
```

These commands reproduce the historical 2.5 vertical slice. `parse` emits
temporary extraction IDs. `reconcile` is a separate identity
step using the prior registry, English evidence, structural context and
explicit overrides before any stable ID reaches source YAML. Generated parser,
migration, report and OmegaT work files live under ignored `outputs/`. The
committed candidate reading artifact lives under `pilot/output/`; `dist/` is
reserved for a future build that has passed `--profile release`.

The three editorial data sets have separate stores and lifecycles:

- published AIPG annotations are part of release YAML and rendered output;
- translator rationale lives in `review/translation-notes/<revision>.json`;
- review state lives in `review/status/<revision>.json`, derived from explicit
  actions in `review/actions/<revision>.yaml`.

Publication annotations may contain multiple bilingual paragraph or list
blocks. Each inner block is a separate OmegaT unit. Repeated component labels
and penalty names are exported once per controlled display code.

## Validation and tests

The validation profile is selected only on the command line:

```powershell
ipg-validate --profile candidate
ipg-validate --profile release
```

The manifest never selects a validation profile. Any future publishing entry
point must invoke `--profile release` explicitly. Candidate validation reports
missing translations, unresolved mappings and review problems without blocking
structurally valid work. Release validation additionally requires exactly one
current review record per OmegaT unit, no unreviewed or stale records, no orphan
records, and matching source and target hashes. Annotation licensing and
attribution remain an independent release gate.

Historical `ipg-pilot validate/build` are compatibility aliases to the current
production commands, not a second dist writer. Other phase commands reproduce
past investigation/migration and are not daily maintenance entry points.

Run all local regression tests with:

```powershell
python -m pytest -q
```

## P3 full official-English parser

P3 uses only the pinned official PDF. It writes the complete reconciled source,
coverage reports, and a visibly non-publishable candidate under ignored
`outputs/p3/`; it does not migrate additional legacy Chinese or annotations.

```powershell
ipg-p3 parse
ipg-p3 reconcile
ipg-p3 build
ipg-p3 review
# or run the four steps in order
ipg-p3 all
```

The committed compact review is `docs/review/p3-full-official-parser.md`. P3 is
kept as the frozen official-structure baseline used by P4.
