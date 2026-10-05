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

真实资料仍有 12 项缺译、注解授权/署名待定与 `publishable: false`。
程序不会代替维护者解决这些内容问题。

## 审校助手

双击 `审校助手.cmd`。选项 1—3 保留 OmegaT 准备、回写候选和进度查看，4 退出。
新增 5 **检查正式发布条件**（只读，中文列出原因）和 6 **条件满足后生成最终
文件**（再次验证、临时两次构建、独立校验、目录事务晋升）。无需填写 manifest
路径，不改审校账本，不创建 GitHub Release。

条件不满足时旧 dist 不变，候选流程仍可用。修订开头的版本说明请编辑
`src/ipg/version-notes.md`，不要改不可变旧源或输出。
详见 [OmegaT 使用说明](docs/omegat-review-guide.md)。

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
发布。后续人工修订、官方更新 diff/继承、长期快照与 CI/发布须另行批准。
