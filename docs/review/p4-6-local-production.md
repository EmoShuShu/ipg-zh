# P4.6 本地生产链路审阅包

日期：2026-10-05。基线：`4a1460565eb5559d70524c224bf0abfd7e654284`。
工作分支：`codex/p4-6-local-production`。没有改写历史、添加远端或配置 GitHub。

## 结果与功能对照

日常维护现在有独立正式入口，不依赖 `ipg-pilot` 或重新迁移源文件。
真实资料仍不可发布，没有创建或修改真实 `dist/`。

调查先于实现，详见 [开始前功能对照表](p4-6-functional-comparison.md)。

| 分类 | 本轮结论 |
|---|---|
| 已等价、保持 | 结构化源、确定性 Markdown/JSON、版本说明、OmegaT 最小回写、共享词表和非阻塞审计 |
| 底层已有、接通正式入口 | source / output 验证、完整审校门槛、两次构建、中文收尾助手 |
| 本轮补齐 | current-release 指针、版本化迁移完成证据、release rehearsal、受保护的正式 dist 晋升 |
| 有意保留差异 | IPG 三版本轴、处罚/角色/附录结构、原生 schema、多段发布注解及 readingSegments、独立翻译批注/审校账本 |
| 尚未做、后续另批 | 官方版本发现、更新 diff/继承及完整快照维护入口；GitHub CI、远端、PR、Release |

## 命令与文件

正式命令（已安装并实测）：

```powershell
ipg-validate --profile candidate
ipg-validate --profile release
ipg-build --profile release
ipg-build --profile candidate --output outputs/new-candidate
ipg-build --profile release --rehearsal --output outputs/new-rehearsal
ipg-validate-output --input outputs/current-candidate/rules.json
```

审校助手保留 1—4，新增 5“检查正式发布条件（只读）”、6“条件满足后生成
最终文件（dist）”。不让维护者输入 manifest 路径，不代替人工审校。

新增文件：

- `src/ipg/current-release.txt`
- `ipg_pipeline/release.py`、`ipg_pipeline/production.py`
- `scripts/validate.py`、`scripts/build.py`、`scripts/validate_output.py`
- `schema/display-values.schema.json`、`schema/migration-completion.schema.json`
- `review/migration/ipg-2024-09-23__ann-aipg-legacy__zh-r0001.json`
- `tests/test_production.py`、`tests/fixtures/release-rehearsal/source.yaml`
- `README.zh-CN.md`、上述功能对照表和本审阅包

调整已有 pyproject、builder、validation、OmegaT 身份路径、full_review、助手和
历史 CLI 的 validate/build 兼容入口；补充既有隔离测试。README、OmegaT 说明及
架构 HTML 同步记录当前流程和边界。没有更改 source/output v1 的 IPG 节点形状。

迁移证据从已提交的 `docs/review/p4-migration-summary.json` 派生，并校验其 SHA-256
`3612474d080bb62c9a05b2d4baaab4b02fd4564e9b5558d535ba6f2200518dbe`、权威来源和覆盖计数。
没有重跑真实迁移，也没有用写死的成功结果代替证据。
该已提交摘要的工作字节与 Git 字节相同；以明确的 LF 属性保护其哈希，避免
Windows 自动换行转换在将来 checkout 时破坏新生产入口的证据契约。

## 成功路径证据

测试仓库明确使用 `ipg-test-only__ann-fixture__zh-test`；正文、版本说明、署名和
快照均标为合成演练，不伪装真实 IPG。快照字节仅用于本地构建的哈希契约测试，
不是官方 PDF 或解析器正确性的替代物。解析器已有真实黄金测试继续全部运行。

在隔离临时目录实测：release 验证成功，完整审校记录与授权齐全；两次内部构建
各自独立 schema 校验，通过后只提升 `IPG.md`、`rules.json` 两个文件。再执行
一次正式构建，四个构建文件的字节/哈希仍相同。版本说明同时进入 Markdown 和 JSON；
`rules.json` 不含自身哈希。临时演练结束即清理，没有进入真实 dist。

| 文件 | 第一次与第二次相同的 SHA-256 |
|---|---|
| IPG.md | `5f17c22b3a026eac3a32a659a010ca6f3a754d88f4e3bf647a297b259a561d4e` |
| rules.json | `72b91554884ea91aad51153016891db4c68035d2034d1b774a1ba23301e4b268` |
| SHA256SUMS | `32d1835d41145b34391d7d3a4e83461bb3d864d97249fbc3912c7737ef2fa72b` |
| build-report.json | `64d846f0e875c3b3297176bb754972a0af882ed430a1cb1832072d35dab9ce93` |

后两者不放进正式 dist。报告/哈希通过外部构建返回值或控制台提供；演练输出
额外保存 receipt。独立输出命令解析当前指针并核对 releaseId，但不重复要求
审校账本存在：schema 有效不是发布资格。

## 失败路径证据

每个基础/后期失败场景分别验证“尚无 dist”与“已有旧 dist”两种情况。
先证明未损坏夹具可以 release 成功，再注入故障，避免因错误夹具让拒绝测试虚假通过。

| 场景 | 预期及已覆盖检查 |
|---|---|
| 缺译、空白译文 | 正式构建拒绝，旧 dist 逐字节不变/不存在的 dist 不创建 |
| ledger 缺失/格式损坏、记录缺失/重复/孤立、unreviewed/stale、原/译哈希错误、版本不匹配 | 正式构建拒绝，不自动补审校记录 |
| 授权或非空署名未完成、publishable=false、注解范围非全文 | 正式构建拒绝，候选仍可审阅 |
| source schema、registry、PDF 哈希、迁移证据损坏 | 源检查 fail closed |
| 生成的输出 schema 损坏、第二次构建不一致、构建期间输入变化 | 提升前拒绝；验证具体错误原因 |
| candidate 请求 dist、outputs 重定向到 dist、复用已有候选目录 | 拒绝，不清空已有输出 |
| 晋升 I/O 失败 | 自动恢复旧文件；没有半新半旧组合 |
| 晋升及恢复都失败 | 旧文件完整保存在恢复备份，明确报错；不被临时目录清理吞掉 |
| 第二个构建者遇到写锁 | 拒绝，不删除别人的锁、不更改旧 dist |
| 术语 finding、缺失/损坏词表或审计错误 | 仅生成报告，release 构建仍成功 |
| 指针越界/不存在/多行、切换合法 release | 非法指针拒绝，合法新指针生效，不套用旧硬编码目录 |

**Windows 原子性边界：**整目录事务保证不逐文件混合更新；首次 dist 创建为一次
目录重命名。已有非空 dist 的备份与晋升之间可能短暂缺少路径，不能宣称无间隙的
内核原子交换。写锁约束合作写入者，异常回滚；崩溃后保留恢复目录并阻止后续覆盖。
严格无间隙的并发读取需要另行批准版本目录/读者指针方案。

## 真实当前资料的只读检查

candidate 成功；release / 正式 build 返回失败（退出码 1），阻塞项：

- `missing-translation`：12，仍是附录 B 既有缺译。
- `annotation-license-pending`：1，授权/署名待定。
- `manifest-not-publishable`：1，保持 `publishable: false`。

读取用户当前账本时，1007 条为 reviewed-unchanged，unreviewed/stale/缺失/孤立
记录为零；这是其现状，不是本轮新作的审校判断。即使账本已审也不能绕过缺译、
署名和 publishable 门槛。

正式结构继续为 36 section、110 component、124 group、338 block；330 个发布
注解、516 个内部 block，1007 翻译单元，registry 1976 条。源、registry、原始
材料和既有迁移结论没有改写。PDF 1050/1050 覆盖、P2 身份继承等既有回归继续运行。

用户未提交账本 SHA-256 前后完全相同：
`6da1987fe1e08be7398ce6922cffbcc42d8b948d9d122a59143c0e777cc1d4c6`。
不覆盖、还原、格式化或提交它。

现有 `outputs/current-candidate` 四文件逐字节保持原样；本轮只读校验其 JSON。
OmegaT 工程未重建或清空，source/target/映射/其他可读项目文件保持原样。
运行中的 OmegaT 自行更新了 `omegat/project_stats.json`、`project_stats.txt` 和
`last_entry.properties`，未还原它们；`omegat.project` 被占用无法读取哈希，本轮
未写入该文件。参照 `D:\mtr-zh` 只读、工作树干净。

## 测试与 Git 交付

全量命令：

```powershell
.venv\Scripts\python.exe -m pytest -o addopts= -v --durations=10 -p no:cacheprovider
```

第一轮全量 225 passed / 0 failed / 0 skipped，225.61 秒。
最后补充输出入口指针核对测试后再次全量复跑：**226 passed / 0 failed /
0 skipped，232.68 秒**。运行真实三个 console 入口与独立输出检查也符合预期。

新增 68 个生产测试覆盖成功/失败/保护/指针/入口。旧的 158 个测试不删减。
命令已通过实际 console exe 和仓库外 scripts 包装入口检查。
实现提交：`e0122b3b6d422560c0135693c962ed090395b9d5`，
`feat: align P4.6 local production entries and guarded dist build`。
本审阅包以独立的 `docs: record P4.6 verification and production boundaries` 收尾提交
保存；最终 HEAD 以 `git log -2 --oneline` 为准（文档不自引用自身提交哈希）。
用户账本始终不进入暂存区或提交；收尾后工作树只保留这项用户改动。

## 停止点

仅完成本地生产链路。后续仍需人工补齐 12 项译文、确认注解许可/署名、实际
完成并保持审校记录、最后由维护者确认 publishable。日常官方更新与 GitHub
CI/PR/Release 能力留待单独授权，不在本轮开展。
跨平台 checkout 还须单独审查历史原始文本的 Git 换行策略：本地 Windows 的旧源
CRLF 与记录哈希一致，但历史 Git blob 为 LF；本轮不改这些原始材料或重写历史。
