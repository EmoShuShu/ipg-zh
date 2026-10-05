# 本地审校助手性能优化

基线：`7cbf197baf8e25048fb9c260041fb1b135faae3c`，分支仍为
`p4-5-full-review-workflow`。本次不迁移内容，不更新实际 OmegaT 工程或审校
记录，不进入后续阶段。`mtr-zh` 仅用于只读比较。

## 改动

- YAML 读取和最小标量定位使用已安装的 `CSafeLoader`；不可用时回退到
  `SafeLoader`。没有使用不安全构造器，也没有增加依赖。
- 内容 YAML 始终重新读取。schema 验证器按全部 schema 文件的实际字节内容
  复用，最多保留 32 组；主 schema 或引用 schema 的同大小、同修改时间编辑也
  会使缓存失效。每个待验证对象仍独立执行全部检查。
- 输入及预览校验去掉紧接着又被 `validate_release` 执行的重复 schema 遍历；
  manifest/source/output schema、结构、registry 和发布门槛均保留。
- 完整测试实时逐项输出；预览、回写、验证、术语、确定性构建和归档显示阶段。
  失败仍抛出异常，触发既有回退，不会登记为审校完成。
- 最小写回保留原始换行格式，新增中文、非 BMP 字符、CRLF 和错误旧值检查。

没有缓存译文、预览或审校结论，没有跳过任何原有测试，没有重用测试中的可变
全文工程。预览后 YAML、版本说明、target PO、TMX、映射或候选的变化检查保持
原样；连续两次完整构建及 output schema 验证保持原样。

## 测量与验证

同一组两项测试，在独立新进程中按相同顺序执行：

```powershell
.venv\Scripts\python.exe -m pytest -o addopts= -q --durations=5 -p no:cacheprovider tests/test_full_review_workflow.py::test_complete_batch_records_unchanged_file_and_only_builds_candidate tests/test_full_review_workflow.py::test_full_project_exports_exactly_eight_po_and_1007_unique_units
```

| 单次测量 | 优化前 | 优化后 |
|---|---:|---:|
| 两项合计 | 38.02s | 16.46s |
| 审校闭环用例 call | 33.52s | 14.44s |
| 创建全文工程用例 call | 3.83s | 1.47s |

两项合计减少约 57%。闭环用例的完整测试步骤使用既有测试桩，因此该时间
衡量的是准备、预览和闭环，不是维护者一次真实操作的总耗时。

最终完整测试命令：

```powershell
.venv\Scripts\python.exe -m pytest -o addopts= -v --durations=15 -p no:cacheprovider
```

结果：**158 passed in 179.56s（2 分 59 秒）**，无跳过项。保留全部原有
149 项，新增 9 项安全/缓存/实时输出测试。此前完整测试历史记录为
149 passed in 485.08s；不是本轮重新运行的全量前测，不作为严格重复性能基准。

定向验证曾发现 CRLF 被归一化的问题；修正后专项及最终完整测试均通过。
安全检查包括纯 Python/native 读取全部正式源的一致性、拒绝不安全标签、
重新读取内容变化、实例逐次验证、主/引用 schema 缓存失效、错误旧值禁止写回，
以及输出在等待子进程结束前已显示且测试非零退出仍阻塞。

## 内容和候选不变量

在临时目录完整构建两次，比较四个文件，再与实际既有候选比较：全部逐字节一致。
没有替换实际候选或实际审校记录。

- 官方结构：36 section、110 component、124 group、338 block。
- registry：1976 条；原有稳定 ID 不变，PDF 覆盖检查和 P2/P3 ID 回归通过。
- candidate 通过；release 失败：缺译 12、unreviewed 1007、授权署名未完成、
  `publishable: false`。没有把自动测试通过当成人工审校通过。
- `dist/` 不存在；没有远端、GitHub、Actions、PR 或 Release 操作。

候选哈希与基线相同：

| 文件 | SHA-256 |
|---|---|
| IPG.md | `1064bd82101241f206ac6bf00b37ec55e860ccc4150d622903e0118a7aff75f1` |
| rules.json | `a8e1934816cc3b9722ff6c6630b8bca4fd173ee10c5b376108a57afbe816d15e` |
| SHA256SUMS | `92108156d30df9d92bc0cd30efd45f43b7d7adb8e251095e2009a396d10b1e29` |
| build-report.json | `57fa2c134f0a1ecf5c4f8621b9eb01f05ae89432399aab7beaec2b57c3e68f49` |

哈希仍保存在外部报告，不进入 rules.json 自身。使用时关闭旧的审校助手窗口，
重新双击 `审校助手.cmd`；无需重建或替换 OmegaT 项目。
