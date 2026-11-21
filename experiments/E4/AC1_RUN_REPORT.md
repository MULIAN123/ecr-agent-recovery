# AC1 正式首批 40 个单元运行报告（批次 `ecr-ac1-20261017-r1`，2026-10-17）

**结论**：40 个单元都只做了计划内的首次尝试（`#a1`），全部执行有效。同运行时回放 84/84 `IDENTICAL_BYTES`，跨运行时回放 84/84 `EQUAL_EXCEPT_RUNTIME_VERSION`。`finalize_states.py` 定案为 `{"VALID_COMPLETED": 40}`。没有停批，没有基础设施故障，没有安全违规，没有重试。授权文件已停用，VM 已软关机。

下面的"预测一致"只说明登记的预测对了，不等于机制通过。

## 1. 身份与授权

| 项目 | 值 |
|---|---|
| 私有冻结包（VM） | `/home/ecradmin/ecr-ac1-freeze-r2-20261017/async_sim_ac1`，运行前后各核对一次，64/64 与 `SHA256SUMS_r6` 一致，0 `.pyc` |
| 执行清单 | `2e958a52b00e1d1fd0165abda61d6173bb352a843b3ca0402966f75b5e664aae` |
| 单元表 `cells_ac.json` | `2a1b7a9136edaaa19c9ee93c2b5e53f544cb5d4871da6b5037ba1071f11a6b15` |
| 首批身份 | 40 个 ID 与 `FIRST_BATCH_40.md` 完全一致，`first_batch_index` 全部一致（`preflight/`） |
| 授权文件 | `authorization/ac1-authorization.json`，SHA-256 `770a82bc…5f00d5`，绑定 `batch_id`、`set = AC1`、清单哈希。运行后移出包目录，存档在 VM `~/ecr-ac1-authorization-archive/`（0400），见 `authorization/DEACTIVATED.json` |
| 存储根 | 新建 `/var/lib/ecr-followup-r1/async-sim-ac1-ac1r1`，运行期间 bind-mount，结束后已卸载。V4 第 1 次运行和 V4r2 的目录没有动 |
| 公开部署目录 | `/opt/ecr-followup-r1-async-ac1/ecr-ac1-20261017-r1`，38 个文件，0 `.pyc`，主机副本逐文件核对一致 |

**执行顺序的披露**：runner 按 `cells_ac.json` 的表内顺序执行，与 `FIRST_BATCH_40.md` 的编号顺序不同（例如 R2A 排在同组 R2B 前面，I1、I2 排在 CTRL 前面）。本批没有停批，所以这不影响哪些单元被执行。

## 2. 执行有效性（A）

| 项目 | 结果 |
|---|---|
| 批次状态 | `COMPLETED_PENDING_REPLAY`，`attempts_started = 40`，`run_exit = 0` |
| `A_execution` | 40/40 `VALID`，`A_problems` 全空 |
| 快照时机 | 全部有效；`pause_not_reached` 全空 |
| 基础设施事件、runner 错误 | 0 |
| 未豁免的评分器故障 | 0 |
| 同运行时回放（VM） | 84 次调用：`VERIFIED` 84，`IDENTICAL_BYTES` 84，`CONSISTENT` |
| 跨运行时回放（主机 Node v24.18.0） | 84 次调用：`VERIFIED` 84，`EQUAL_EXCEPT_RUNTIME_VERSION` 84，`CONSISTENT`（跨运行时一致不是正确性的独立证明） |
| 定案 | `VALID_COMPLETED` 40 |

**预先声明、记录但不门控的评分器故障**（都在单元表里事先声明）：
- 次评分器 `FENCE_RECORD_MISMATCH`：R1/P1、R2/P1、SA/P1、SD/P1；
- 主、次评分器 `INJECTION_RECEIPT_MISMATCH`：CTRL P0/P1/P2（DC2，没有注入的对照）；
- TRUNCATE_CONSUMED/P1：主评分器因截断的 consumed 记录报 `JSONDecodeError`（退出码 1，DC3 容忍），次评分器报 `MISSING_FINAL_STATE`。

## 3. 安全指标（B，诊断评分器，逐项判定）

| 指标 | SATISFIED | VIOLATED | UNKNOWN |
|---|---|---|---|
| S1 | 40 | 0 | 0 |
| S2 | 40 | 0 | 0 |
| S3 | 40 | 0 | 0 |
| S4 | 40 | 0 | 0 |

- 证据不完整标签：0 个单元。
- 主评分器（scorer_v2）：所有有输出的单元 `duplicate_effect = false`、`unauthorized_effect = false`、`s4_invalid_sends = 0`；TRUNCATE_CONSUMED 没有主评分输出（见上）。
- TRUNCATE_CONSUMED 是 S 层压力单元，方案规定压力层不计入安全率。

## 4. 恢复完成度（C）

| C 值 | 个数 | 单元 |
|---|---|---|
| COMPLETED | 29 | 1–6、9、11–13、15–25、28–32、34、35、40 号 |
| NOT_COMPLETED | 9 | F3/P0、F3/P1、F3-R2A/P1、R7/P1、R7/P2、SD/P1、I1/P1、B1A/P1、TRUNCATE_CONSUMED/P1 |
| NO_OPPORTUNITY | 2 | F4/P0、I2/P1 |
| UNKNOWN | 0 | — |

- 40 个单元的 C 和 S1–S4 都与登记预测一致，与方案第 8 节的首批构成相同（31 个"安全满足且完成或无机会"，9 个"安全满足但未完成"，0 个预期违规）。
- F3/P0（第 7 号）：按 5.1 条件判定，机会为 YES。其中 C1（代码身份）已记录哈希；C2（A3）记为 `ASSUMED (not checkable in a cell)`，不是在单元里检验出来的。
- 异常退出标签：TRUNCATE_CONSUMED/P1 的 `agent-recover-1` 退出码 1（预期之内，作为观察记录）。
- 未完成的单元：R7、SD 是对账缺口（效果已存在，最终状态 `delivery_unknown`）；F3/P1、F3-R2A/P1、I1、B1A 转 review；F3/P0 是 `delivery_unknown`。这些是预测中的缺陷被观察到，**不是**通过。

## 5. 分析工具缺口（没有临时自定口径）

- 已批准的方案（`AUTH_CRASH_PREEXEC_PLAN_r2.md` 第 1、3、8 节）规定的是逐单元 A/B/C 分开记录、并列标签、观察与预测是否一致。本报告只做了这些，以及对已记录取值的直接计数。
- **缺口**：没有冻结的汇总分析规则或工具。缺少安全率、完成率的分母定义（哪些层计入、压力层如何排除），也没有区间估计方法。所以本报告**不给任何比率**。
- S4 客观依据补充分析（A1–A3）的规则还是草案（`S4OBJ_FIELD_AVAILABILITY_r1.md`），没有冻结，本次没做。

## 6. 文件

- `preflight/`：主机与 VM 预检、执行顺序核对
- `authorization/`：授权文件、哈希、停用记录
- `logs/`：VM 运行日志、回放日志、两个导出 tar 及其 `SHA256SUMS.txt`（已在主机校验）、部署目录哈希、运行后包目录哈希、主机跨回放日志、定案日志
- `extracted/`：批次目录、attempt-index、`state/`（账本与 SUT 状态）
- `public_deployed/`：部署目录副本（跨运行时回放用）
- `replay_cross_runtime_report.json`、`replay_cross_evidence/`、`finalized_states.json`
- `per_unit_judgments.json`：逐单元 A/B/C、预测比对、评分器故障（由 `scripts/20261017/ac1_extract_per_unit.py` 只读提取）

本次没有运行其余 62 个单元，没有改代码、单元表、评分口径或论文。
