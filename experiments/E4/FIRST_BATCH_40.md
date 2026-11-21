# 首批 40 个单元清单（ac1，按方案 r2 第 8 节）

**来源**：
- 单元 ID 由 `async_sim_ac1/tools/build_ac_cells.py` 生成；首批顺序固定在该脚本的 `first` 列表里。
- 测试 T-C 核对这个列表与方案一致。

**说明**：
- **"预期"一栏全部是登记的预测，不是结果。** 观察与预测一致，只说明预测对了，不等于机制通过。
- B1a 和 B1b 的结果都继续标为预测。
- B1b 是篡改记录的压力测试，不在首批里，**不能**用来证明在正常故障模型下 A4 必不可少。

**判据（每个单元都一样）**：
- **A 执行有效**：收据通过 `acreceipts.validate_kill_receipt`；快照通过 `validate_snapshot_timing`；暂停点已到达；没有预期外的评分器故障。
- **B 安全**：S1–S4 各自判定，可以是 SATISFIED、VIOLATED 或 UNKNOWN。
- **C 恢复完成**：COMPLETED、NOT_COMPLETED、NO_OPPORTUNITY 或 UNKNOWN。

三者分开记录。

**表中的缩写**：
- 发送阶段崩溃点：
  - F1 = `before_grant`
  - F2 = `after_grant`
  - F3 = `after_consumed`
  - F4 = `after_dbc`
- 恢复阶段崩溃点：
  - R1 = `after_fence_post`
  - R2 = `after_fence_record`
  - R3 = `after_grant`
  - R4 = `after_consumed`
  - R5 = `after_request`
  - R6 = `after_response`
  - R7 = `after_result_log`
- "→ 正常" 表示最后一次恢复不暂停。

| # | 单元 ID | 发送阶段 | 恢复序列 | 注入 | 预期 B（S1–S4） | 预期 C | 接触 |
|---|---|---|---|---|---|---|---|
| 1 | AC:F1:R2B:P0 | 在 F1 暂停后被杀 | → 正常 | — | 全部满足 | COMPLETED | 新 |
| 2 | AC:F1:R2B:P1 | F1 | → 正常 | — | 全部满足 | COMPLETED | 新 |
| 3 | AC:F1:R2B:P2 | F1 | → 正常 | — | 全部满足 | COMPLETED | 新 |
| 4 | AC:F2:R2B:P0 | F2 | → 正常 | — | 全部满足 | COMPLETED | 开发期已接触 |
| 5 | AC:F2:R2B:P1 | F2 | → 正常 | — | 全部满足 | COMPLETED | 开发期已接触 |
| 6 | AC:F2:R2B:P2 | F2 | → 正常 | — | 全部满足 | COMPLETED | 开发期已接触 |
| 7 | AC:F3:R2B:P0 | F3 | → 正常 | — | 全部满足 | 按 5.1 条件判定：成立时 NOT_COMPLETED，证据不足时 UNKNOWN | 开发期已接触 |
| 8 | AC:F3:R2B:P1 | F3 | → 正常 | — | 全部满足 | NOT_COMPLETED（转 review） | 开发期已接触 |
| 9 | AC:F3:R2B:P2 | F3 | → 正常 | — | 全部满足 | COMPLETED | 开发期已接触 |
| 10 | AC:F4:R2B:P0 | F4 | → 正常 | — | 全部满足 | NO_OPPORTUNITY | 开发期已接触 |
| 11 | AC:F4:R2B:P1 | F4 | → 正常 | — | 全部满足 | COMPLETED | 开发期已接触 |
| 12 | AC:F4:R2B:P2 | F4 | → 正常 | — | 全部满足 | COMPLETED | 开发期已接触 |
| 13 | AC:F2:R2A:P1 | F2 | → 正常 | — | 全部满足 | COMPLETED | 开发期已接触 |
| 14 | AC:F3:R2A:P1 | F3 | → 正常 | — | 全部满足 | NOT_COMPLETED | 开发期已接触 |
| 15 | AC:F4:R2A:P1 | F4 | → 正常 | — | 全部满足 | COMPLETED | 开发期已接触 |
| 16 | AC:R1:R2B:P1 | B2_DROP 处被杀 | R1 → 正常 | — | 全部满足 | COMPLETED | 新 |
| 17 | AC:R2:R2B:P1 | B2_DROP | R2 → 正常 | — | 全部满足 | COMPLETED | 新 |
| 18 | AC:R3:R2B:P1 | B2_DROP | R3 → 正常 | — | 全部满足 | COMPLETED | 开发期已接触 |
| 19 | AC:R3:R2B:P2 | B2_DROP | R3 → 正常 | — | 全部满足 | COMPLETED | 开发期已接触 |
| 20 | AC:R4:R2B:P1 | B2_DROP | R4 → 正常 | — | 全部满足 | COMPLETED | 开发期已接触 |
| 21 | AC:R4:R2B:P2 | B2_DROP | R4 → 正常 | — | 全部满足 | COMPLETED | 开发期已接触 |
| 22 | AC:R5:R2B:P1 | B2_DROP | R5 → 正常 | — | 全部满足 | COMPLETED | 开发期已接触 |
| 23 | AC:R5:R2B:P2 | B2_DROP | R5 → 正常 | — | 全部满足 | COMPLETED | 开发期已接触 |
| 24 | AC:R6:R2B:P1 | B2_DROP | R6 → 正常 | — | 全部满足 | COMPLETED | 新 |
| 25 | AC:R6:R2B:P2 | B2_DROP | R6 → 正常 | — | 全部满足 | COMPLETED | 新 |
| 26 | AC:R7:R2B:P1 | B2_DROP | R7 → 正常 | — | 全部满足 | NOT_COMPLETED（对账缺口） | 新 |
| 27 | AC:R7:R2B:P2 | B2_DROP | R7 → 正常 | — | 全部满足 | NOT_COMPLETED | 新 |
| 28 | AC:R4:R2A:P1 | B2_DROP | R4 → 正常 | — | 全部满足 | COMPLETED | 开发期已接触 |
| 29 | AC:SA:R2B:P1 | B2_DROP | R1 → R1 → 正常 | — | 全部满足 | COMPLETED | 新 |
| 30 | AC:SB:R2B:P1 | B2_DROP | R4 → R5 → 正常 | — | 全部满足 | COMPLETED | 新 |
| 31 | AC:SB:R2B:P2 | B2_DROP | R4 → R5 → 正常 | — | 全部满足 | COMPLETED | 新 |
| 32 | AC:SC:R2B:P2 | F3 | R3 → 正常 | — | 全部满足 | COMPLETED | 新 |
| 33 | AC:SD:R2B:P1 | B2_DROP | R2 → R7 → 正常 | — | 全部满足 | NOT_COMPLETED | 新 |
| 34 | AC:CTRL:R2B:P0 | 正常完成，没有注入 | → 正常 | — | 全部满足 | COMPLETED | 新 |
| 35 | AC:CTRL:R2B:P1 | 正常完成 | → 正常 | — | 全部满足 | COMPLETED | 新 |
| 36 | AC:I1:R2B:P1 | B2_DROP | → 正常（恢复时配置换成另一个键） | — | 全部满足 | NOT_COMPLETED（冻结后转 review） | 新 |
| 37 | AC:I2:R2B:P1 | B2_DROP | → 正常 | F5（版本被取代） | 全部满足 | NO_OPPORTUNITY | 新 |
| 38 | AC:B1A:R2A:P1 | B4 处被杀 | → 正常（恢复时配置换成另一个键） | — | 全部满足 | NOT_COMPLETED（EV7 判异常后转 review） | 新 |
| 39 | AC:TRUNCATE_CONSUMED:R2B:P1 | B2_DROP | → 正常 | 截断 consumed 记录 | 全部满足（按账本和端点日志判） | NOT_COMPLETED；标签：异常退出 | 新 |
| 40 | AC:CTRL:R2B:P2 | 正常完成 | → 正常 | — | 全部满足 | COMPLETED | 新 |

**接触情况说明**：
- "开发期已接触"指第二阶段的开发测试已经运行过同样的条件（当时用的是 `os._exit(137)` 钩子，不冻结）。这些单元**不是留出测试**。
- 标"新"的单元也只是首次执行，与已接触的条件共用模拟器和代码，**不能称为独立的留出验证**。
