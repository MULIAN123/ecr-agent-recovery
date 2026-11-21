# ECR r9.6 Batch A — 评分规则、资格验证与停止条件（cp1，执行前冻结）

日期：2026-10-21。状态：冻结候选，等待用户授权。本文件与 `ARM_DEFINITIONS_cp1.md`、`predictions_cp1.json`、`UNIT_MANIFEST_cp1.csv` 一起登记在 `FREEZE_RECORD_cp1.json` 里。

## 1. 评分器（不改）

- 主评分器：2b 冻结的 `src/scorer_v2.py`（`846d612b…`）。S1 重复效果、S2 越权效果、S3 虚假完成、S4 无效重发都从 truth、endpoint 日志和持久化记录判定，不看执行器写的标签。
- 次评分器：2b 冻结的 `src/scorer.py`（`e4cbb567…`），只做一致性检查（A8），不进主结论，也不替代主评分器。
- 两个评分器都以 ecr-scorer 身份运行。runner 的调用方式和 2b 相同（`run_cp1.py` 与 `run_r2.py` 的差异只有路径名、清单名、单元表名和执行器键，由 `tests/test_cp1_static.py` 的 T6 检查）。

## 2. 汇总（与 2b 规则相同）

工具：`analysis_cp1/aggregate_cp1.py` v1，由 2b 冻结工具 `analysis_r2/aggregate_p3a.py` v3（`2ad62ae2…`）派生。改动只有 C1–C4，写在文件头里：

- C1：family 改为 CPX，共 256 个单元，没有 X1。
- C2：新增分类器名 `CP_SEQ_DISP`。
- C3：`v2cp` 按 v2 执行器统计。
- C4：可选的预测对照表 `predictions_vs_observed.csv`，不改动任何指标。

沿用 2b 的规则：

- **R0**：主报告只用每个单元的第一次尝试。获批重试的结果单列在 `retries.csv`，不替代第一次尝试。
- **A2**：授权拒绝按原始理由字符串计数，不另外分组。
- **A3**：有 NOT_STARTED 单元时，报告标记为 PARTIAL。全计划上的上下界是假设界，不是观测事件。
- **A4**：恢复率 L 按 arm × 模式 × 组计算。历史组 38 个机会，F7 组 9 个，合计 47。三种口径都报告：有效首次尝试上的比例、计划机会上的比例、上下界。
- **A5**：不做置信区间或显著性检验，结论只针对固定场景集，不外推总体失败率。
- **A8**：见 §1。
- **A10**：v2 / v2cp 两个 arm 的每一个 S1–S4 事件都全量列出，一个不排除，不写"v2 安全"这类概括。
- R2-A 和 R2-B 分开报告，两者之间不做检验。

## 3. 对照与表述

- 历史对照：2b 的 FULL v2 和 −C_disp v2（各 128 单元），只读，来自 `prep/work/historical_v2_by_scenario.json`，表中标明"历史结果"。
- 新结果：CONV+g、CONV+（各 128 单元），表中标明"新运行"。
- 新结果与历史对照放在同一张表里比较时，要注明两者来自不同批次、不同运行日期，同一场景的屏障实现（特别是 B2_LATE、B3_LATE 的到达时序）不保证完全相同。
- 主要报告量：每个 arm × 模式的 L（历史组 /38，F7 /9）、S1–S4、review / delivery_unknown 单元数、授权拒绝次数与原始理由、恢复期发送次数、首次分类分布。

## 4. 预测（冻结，不随结果改）

`predictions_cp1.json` 给出全部 256 个单元的逐单元预测，每条注明来源：

- **P-HIST**（224 个）：= 同场景、同模式的 FULL v2 历史结果。
- **P-CDISP**（8 个，即 CONV+g 的 F6）：= −C_disp v2 历史结果。
- **P-DERIV**（24 个，即 F4 两个 arm 和 CONV+ 的 F6）：由源码路径和静态核查推导。

偏离预测的单元逐个列出，作为结果报告。不因为偏离去改预测、指标、场景或 truth，也不以"符合预测"为理由删除或重跑任何单元。

## 5. 资格验证（CPQ，8 个单元，不是实验结果）

| # | 场景 | arm | 模式 | 覆盖的路径 |
|---|---|---|---|---|
| 1 | B1_P0 | CONV+g | R2A | 干净的首次派发前崩溃，PRE_DISPATCH |
| 2 | B2_LATE_P3 | CONV+ | R2B | v2cp 执行器，fence 路径 |
| 3 | F4_REORDERED_CALLBACK_P1 | CONV+g | R2B | CP_SEQ_DISP 分类器（D1） |
| 4 | F6_LOST_DISPATCH_FAILURE_P1 | CONV+g | R2A | 原授权门的 EV5 拒绝 |
| 5 | F6_LOST_DISPATCH_FAILURE_P3 | CONV+ | R2B | D3 已去掉，fence 放行 |
| 6 | F6_LOST_DISPATCH_FAILURE_P2 | CONV+ | R2A | D3 已去掉，IDEMPOTENT 放行 |
| 7 | F7_B4_P0 | CONV+ | R2B | F7 / P0 |
| 8 | B5_P2 | CONV+g | R2B | 已有接受证据 |

通过条件（全部满足）：

- **Q-R1**：8 个尝试都是 OBSERVED_PENDING_REPLAY；runner 完整性检查通过（屏障回执、部署哈希、身份、规划器调用记录 COMPLETE）。
- **Q-R2**：runner 内置的评分器负对照为 PASS（`runner/controls.py`）。
- **Q-R3**：同运行时重放 CONSISTENT。跨运行时重放（主机 Node v24.18.0）CONSISTENT。
- **Q-W1**（接线标记，CONV+g 的验证器没有改）：#4 的主评分恰好有 1 次授权拒绝，理由列表恰好是 `["EV5 outcome without dispatch evidence"]`。
- **Q-W2**（接线标记，CONV+ 实际加载了 `src_cp/evidence.py`）：#5 没有任何含 `EV5 outcome without dispatch evidence` 的拒绝，`store/authority/fences/` 里至少有一条 fence 记录。
- **Q-W3**（接线标记，部署的是 CP_SEQ_DISP 分类器）：#3 的首次分类是 `QUEUE_STALE_AFTER_ADAPTER_EVIDENCE`。

Q-W1 到 Q-W3 检查的只是"arm 是否按定义部署"。它们引用的分类结果和拒绝理由，已经在离线静态核查 S2/S3 里由冻结代码直接给出。如果不满足，说明部署的代码不是定义的 arm，此时停止，不做正式运行，先排查。

资格验证单元的其他结果（终态、发送次数等）如果与预测不同，不算失败，但要全部报告给用户，由用户决定是否继续。

## 6. 正式运行与停止条件

- 正式运行只有一个批次：`run_cp1.py run --set CPX`，256 个单元。runner 要求 `qualification-authorization.json` 的状态为 PASS_HOST_AUDIT，并且绑定当前执行清单的哈希。
- 停止条件（runner 内置，与 2b 相同）：任何一个尝试不是 OBSERVED_PENDING_REPLAY（INFRA_FAILED 或 INTEGRITY_UNKNOWN），批次就停下，状态记为 STOPPED_INVESTIGATE，已完成的尝试保留。之后续跑需要用户另行授权，并提供 continuation 文件。每个单元最多 2 次尝试，attempt 编号全局唯一，不删除、不覆盖。
- 不设基于结果的停止条件。出现 S1–S4、意外发送、意外拒绝或虚假完成时，批次照常跑完，所有事件按 A10 全量报告。
- 运行后的步骤（与 2b 相同）：同运行时重放 → 导出到主机 → 跨运行时重放（含 POSIX 路径的参数用 PowerShell 传，不用 Git Bash）→ `finalize_states.py` → `aggregate_cp1.py`。只有 VALID_COMPLETED 状态的尝试进入主报告。

## 7. 哪些结果需要重新评估论文主张（事先写定）

见报告 `ECR_R96_BATCH_A_CONVPLUS_PREP_REPORT.md` §9。这里只列触发条件：

1. CONV+ 或 CONV+g 出现任何 S1–S4 事件。
2. CONV+ 的历史组 L 不等于 35/38，或 CONV+g 不等于 32/38。
3. F6 P2（CONV+）没有发生恰好 1 次合法的 IDEMPOTENT 重发，或 effect_count ≠ 1。
4. F4 P0 被完成。预测本身就是完成；这项单独报告，因为 P0 在 truth 里不算安全机会。
5. 有 P-HIST 单元偏离（说明 CP 改动影响了预测范围以外的场景，或者屏障实现出现漂移）。
