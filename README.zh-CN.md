# IPG 中文翻译项目

本项目维护《万智牌违规处理方针》的中文正文、译文注解，并生成供阅读和网站使用的 Markdown 与 JSON 文件。
日常操作以配套的 MTR 项目为基准；两项目可独立安装和运行。

## 常用文件

- [`dist/IPG.md`](dist/IPG.md)：生成的中文版 IPG，适合直接阅读；
- [`dist/rules.json`](dist/rules.json)：网站使用的 JSON 文件；
- [`src/ipg/current-release.txt`](src/ipg/current-release.txt)：当前正式版本的位置；
- [`src/ipg/version-notes.md`](src/ipg/version-notes.md)：所有版本共用的版本说明；
- [`snapshots/`](snapshots/)：官方文件、旧源与已提交的更新证据；
- [`docs/maintenance-conventions.md`](docs/maintenance-conventions.md)：与 MTR 共用的维护约定及保留差异。

获取最新 JSON：

```text
https://github.com/EmoShuShu/ipg-zh/releases/latest/download/rules.json
```

## 更新方式

两个项目均于北京时间每天 **06:00** 自动检查官方文件。GitHub 排队可能造成延迟。
在 [官方更新检查记录](https://github.com/EmoShuShu/ipg-zh/actions/workflows/official-update.yml)
查看当天是否出现绿色成功记录；也可选择 Run workflow 手动检查。

网页读取或识别失败、PDF 下载失败时最多尝试 3 次，间隔为 5 秒和 15 秒。
重试仅限取得官方资料，候选生成不会重复执行；全部失败仍会使任务报错。
每次取得的网页和错误记录都会留档，成功恢复也保留先前失败证据。

没有变化时不会创建更新分支或 PR。发现不同 PDF 时，系统会创建一个 Draft PR，保存：

- 官方 PDF、网页、解析结果和完整校验值；
- 与当前版本的英文差异、继承旧译文与注解后的候选；
- 身份映射和需要人工复核的问题。

同一官方文件已有 PR（包括已关闭的 PR）时，不重复创建。
Draft PR 中的不可变证据位于 `snapshots/updates/<日期>/<完整PDF哈希>/<上下文哈希>/`。
运行附件 `ipg-official-update-…` 另保留 30 天，包含诊断及全部隔离输出。

IPG 的候选涉及官方、注解和中文修订三部分版本。Draft PR 不自动切换当前版本；
先审阅候选和映射、按文末命令确认接入审校基线；完成翻译及注解复核后更新版本说明和 dist。
全部检查通过后，才将 PR 标记为可审阅并人工合并。正式产物的提交触发独立的 Release 工作流。

## 校对时编辑哪里

- 编辑 `src/ipg/releases/<完整版本标识>/` 中的 YAML；
- 编辑共用版本说明时修改 `src/ipg/version-notes.md`；
- 不直接编辑 `snapshots/` 或首版冻结记录，它们用于备份和审计；
- 不手工修改 `dist/rules.json` 或 `dist/IPG.md`，它们由构建脚本生成。

当前指针是唯一版本入口，程序不会通过目录排序猜测“最新版本”。
IPG 的原生 schema、稳定 ID、注解锚点和审校记录保持原样，不转换成 MTR 的格式。

## 本地使用

Python 3.12 或更高版本；两个项目的 GitHub 工作流均使用 Python 3.13。
首次使用时安装环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

运行全部检查：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

旧安装方式 `.[test]` 和旧命令 `ipg-review` 继续可用。

## OmegaT 审校工作流

### 日常使用：审校助手（推荐）

双击仓库根目录的 [`审校助手.cmd`](审校助手.cmd)，或运行：

```powershell
.\审校助手.cmd
```

菜单与 MTR 一致：

1. **准备或继续 OmegaT 审校**：首次创建全文项目；已有项目只检查，不覆盖译文、批注或术语表。
2. **完成审校并生成最终文档**：在 OmegaT 中选择“项目 → 创建已译文档”后，完成一次回写与构建。
3. **查看审校进度**：刷新报告，显示已审未改、已审有改、失效及未审数量。

输入 `q` 退出；选项完成后显示结果并等待按键。无需另外选择“生成最终文件”。
选择已审文件时，回车采用自动检测结果，`a` 选择全部，编号之间用逗号分隔；`q` 取消。
预览后用 `y` 或 `yes` 确认。旧的 `all`、`c` 和菜单编号 4/5/6 保留兼容，但不再显示为日常入口。

完成阶段会自动：

1. 检查源 PO、译文 PO、稳定 ID 和 OmegaT 批注，生成隔离预览；
2. 阻止未选择文件的临时修改混入本批；
3. 显示已审数量、实际修改和保留旧译数量，等待一次确认；
4. 最小差异回写，执行完整测试和非阻塞术语审计；
5. 归档批注与已审状态，再检查全文生成条件；
6. 全文条件满足时，自动生成 `dist/IPG.md` 和 `dist/rules.json`。

部分文件仍未审、缺译或失效时，本批审校进度和候选阅读文件会保存到
`outputs/current-candidate/`，并明确说明尚未满足的条件；旧 dist 保持不变。
正式构建失败时，恢复本批源、映射、审校记录和批注，保留旧输出。
无文字修改的文件仍可登记为“已审未改”。取消确认不改变正式源或审校记录。

`src/ipg/version-notes.md` 不进入 OmegaT；构建时自动放在最终 Markdown 前部，
并写入 JSON 的独立版本说明节点，不会遗漏。

### 项目与报告位置

- OmegaT 工程：`outputs/omegat-ipg-full/`，文件集合和单元数量由当前版本决定（目前 8 个 PO）；
- 回写预览：工程内 `import-preview.json`、`import-preview.md`；
- 审校进度：`outputs/review-status.json`、`outputs/review-status.md`；
- 术语审计：`outputs/terminology-audit.json`、`outputs/terminology-audit.md`；
- 共享词汇表：`terminology/ipg-glossary.txt`，OmegaT 直接读写这一份。

词汇表为 UTF-8、制表符分隔的英文术语、首选中文和说明三列。
术语问题属于参考信息，不阻塞回写、生成或审校登记。
批注与审校状态仍分别保存在 `review/translation-notes/`、`review/status/`，
不与发布注解混合。原文或中文后来变化时，原审校记录会失效，需要重新审阅。

### 排错和复现

日常使用助手即可；以下脚本与 MTR 的常用命名一致，各自 `--help` 说明原生参数：

```powershell
.\.venv\Scripts\python.exe scripts/review_workflow.py
.\.venv\Scripts\python.exe scripts/export_omegat.py
.\.venv\Scripts\python.exe scripts/review_status.py status
.\.venv\Scripts\python.exe scripts/audit_terminology.py
.\.venv\Scripts\python.exe scripts/create_snapshot.py
.\.venv\Scripts\python.exe scripts/check_official_update.py
```

只生成候选可用 `scripts/review_workflow.py --candidate-only`；
只读检查最终条件用 `--check-release`；从已经审完的源重新构建用 `--build-release`。
旧 `scripts/snapshot.py` 继续可用。

底层正式构建仍要求显式 `--profile release`，保留原生全部质量门槛：

```powershell
.\.venv\Scripts\python.exe scripts/validate.py --profile release
.\.venv\Scripts\python.exe scripts/build.py --profile release
.\.venv\Scripts\python.exe scripts/validate_output.py --input dist/rules.json
```

普通候选默认在 `outputs/candidate/`，正式演练在 `outputs/release-rehearsal/`；
不再使用含开发阶段编号的默认目录。已有旧输出不会被移动或删除。

## GitHub 自动检查与发布

[Validate IPG](.github/workflows/validate.yml) 在 push、PR、合并队列和手动运行时执行：
严格验证 → 完整测试 → 隔离构建 → 独立 JSON 校验 → 两次字节比较 → 对照已提交 dist。
失败日志、测试报告和演练产物在 Actions 中查看，验证附件保留 14 天。

[Publish IPG release](.github/workflows/release.yml) 在最终产物、当前指针、版本说明或
发布配置变更时运行。通过检查后发布 `IPG.md`、`rules.json`、`SHA256SUMS`。
既有 Release 不覆盖，草稿附件须下载核验后才公开。本地助手只生成文件，不上传。

发布注解的来源说明和 `publishable` 等既有信息仍按此前约定作为非阻塞提醒；
缺译、审校失效、结构及溯源错误仍会阻止最终构建。日常维护对齐不改变这些条件。

## 保留的原生差异与历史

IPG 的完整版本标识、`releases/`、批注库、ID registry、PDF 解析器和 JSON 格式
都承载实际数据含义，因此保留。与 MTR 的逐项映射见
[维护约定](docs/maintenance-conventions.md)。
早期 P0—P5 命令和首版冻结记录仅用于追溯，不是第二套日常入口。
旧说明存档见 [对齐前维护记录](docs/review/2026-11-06-before-maintenance-alignment.md)。

## 接入新的官方版本

在更新 Draft PR 分支上先阅读 `snapshots/updates/.../reports/review.md` 和 `update.json`，
然后运行（把 `<候选目录>` 替换为实际快照路径）：

```powershell
python scripts/promote_official_update.py --snapshot <候选目录>
python scripts/promote_official_update.py --snapshot <候选目录> --apply
python scripts/export_omegat.py --new-version
```

第一条只检查与预览；第二条在 `[y/N]` 确认后切换新审校基线；第三条确认后完整归档
旧 OmegaT 工程并创建新工程。若有身份、注解锚点或批注丢失问题，先修正更新 override，
重新生成候选，不能跳过。英文变化和新增内容仍需人工审校。

新旧 release 和账本并存，迁移证据记录继承关系。切换失败恢复当前指针、注册表和本次新增文件；
旧 dist 保持不变，等全文审校满足发布条件后才重新生成。
检查、准备和助手命令都可通过 `--project-root` 明确指定仓库。


菜单第 1 项现在也会识别旧版本工程，询问确认后完整归档并创建新工程，无需另找命令。
菜单第 3 项或 `python scripts/review_status.py` 无需预先创建工程；工程过期时仍显示
当前源与账本的进度，并单独提示工程需处理，不改写旧工程或账本。

`validate.py`、`build.py` 省略参数时默认正式校验/构建；未满足全文条件时停止并保留旧 dist。
`build.py --output-dir` 与原 `--output` 等价，仅用于候选或演练输出。
`validate_output.py` 默认检查 dist；显式相对输入路径也以 `--project-root` 为基准。
