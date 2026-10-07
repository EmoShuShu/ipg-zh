# 本地最终阅读文件：两项行政检查改为提醒

用户于 2026-10-08 明确批准：不再以注解许可/署名待定或 publishable=false 阻止
本地 dist 构建。该决策取代 P4.6/P5 及早期审阅记录中相应的本地硬门槛约定。
它不等于确认许可、允许公开发布或提升官方更新候选。

## 实现契约

- 共用 `validate_release` 把 annotation-license-pending、manifest-not-publishable
  放入 `advisoryFindings` / `advisoryFindingCounts`；结构和 readiness 问题仍阻塞。
  生产命令、包装脚本、审校助手和候选检查采用同一规则，不增加跳过检查开关。
- 仍需显式 `--profile release`。成功的 meaning 改为 `local-build-ready`。
  完整正文/注解范围、缺译、未决映射、重复消费、失锚和延期注解、完整唯一且
  哈希匹配的审校记录、schema、PDF/迁移证据、registry 等门槛均不变。
- 程序不把 manifest 的 false 或 pending 改成 true/complete，不编造署名。
  JSON 保存原有字段原值，source/output v1 节点形状不变，仅补充字段描述。
  非候选 Markdown 的首行注释标识为 LOCAL BUILD，不充当公开发布批准。
- 菜单 5 显示本地最终文件生成条件，并单独列出非阻塞提醒；菜单 6 使用既有
  临时两次构建、独立 schema 验证、字节比较及整目录替换，只生成本地文件。
- 候选及 rehearsal 的目录隔离、输入变化检测、写锁及旧文件恢复不变；
  P5 官方更新仍不切换指针、不自动提升候选、不升级 OmegaT，也不发布。

## 数据保护和使用

本轮不执行真实回写或构建，不改译文、注解、版本说明、账本、许可记录、
当前指针、现有 OmegaT、候选、dist 或任何快照。用户正在修改的 OmegaT
使用说明也不覆盖；最新规则见 README.zh-CN.md 和架构记录第 23 节。

修订与回写完成后，可直接通过助手选项 5 检查，选项 6 生成最终阅读文件。
提醒仍显示，但无需额外填写许可资料或手动修改 publishable 才能本地构建。
公开分发是独立决策，不能仅以本地构建成功作为许可结论。

## 验证记录

修改前相关基线测试：85 项通过，64.15 秒。

测试使用隔离合成工程：许可待定、署名空白、publishable=false（各自及组合）
均允许本地构建，提醒保留且 manifest/ledger 不改；新建及更新既有 dist 均验证
两次构建、独立输出 schema 和仅包含两个最终文件。

缺译、缺少/未审/stale/哈希错位/孤立/重复审校记录、非全文范围、来源损坏及
输出/确定性/输入变化/目录晋升失败仍拒绝，并保留原 dist 字节；这些失败测试
同时保持许可待定及 publishable=false，证明没有借提醒化放宽技术门槛。

调整后的相关测试：101 项通过，53.46 秒。初次验证发现一项测试仍断言旧候选
提示用语，已按“本地条件与公开许可分离”同步更新，随后全部通过。

真实只读检查：release 有效，meaning=local-build-ready，结构与 readiness 均无
finding，缺译、未审、stale 等门槛计数为零；两项行政提醒各 1 条。脚本包装入口
退出码 0；实际 CMD 助手选择 5 后正常显示新菜单、通过状态与非阻塞提醒。

将当前真实资料复制到临时仓库（不调用真实工程构建），release 构建成功；两次
字节一致，独立输出 schema 通过，仅生成两个最终文件。以下哈希仅属于临时演练：

- `IPG.md`：`c4a9cc4a8e8c99f7491bb1a920453153385ceb119c772c6306ece6808aeedea4`
- `rules.json`：`308dc6a9e5d2b7d9277013c0b8ed212ec90bc1b2c562651097f38fa14f2b2f79`

完整测试：329 项全部通过，657.78 秒，无跳过。共用验证器、生产命令、官方更新、
解析、稳定身份、迁移、OmegaT 回写、审校与确定性等原有回归仍执行。
本轮开始记录的 494 个既有文件哈希一致，无缺失或新增，用户原有改动不纳入提交。
真实 dist 仍不存在，且本轮未修改既有候选。真实 dist 由维护者重新打开助手、明确选择菜单 6
时生成，不由本次测试代为创建。需重新打开，因为已运行的 Python 菜单不会自动
重新加载修改后的程序。

复核命令：

```powershell
.venv\Scripts\python.exe -m pytest -o addopts= -q tests/test_production.py tests/test_validation_build.py tests/test_full_migration.py
.venv\Scripts\python.exe -m pytest -o addopts= -q
.venv\Scripts\python.exe scripts/validate.py --profile release
```
