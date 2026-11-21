# P3-A 正式结果报告 r1（批次 ecr-p3a-20261016-r1，2026-10-16）

**修订说明（r1）**：原报告 `P3A_FORMAL_RESULTS_REPORT.md`（`460502ed…`）保留不改。本版只修正经只读复核发现的表述问题，冻结的分析工具和分析结果都没有改动。复核记录见 `work/p3a_20261016/step13_report_review.log`。

1. **X1 表格显示错误**：原表的臂名（如 `FULL|v1|-`）中含有竖线，在 Markdown 表格里被当成列分隔符，导致整张表列错位。本版改用 `FULL/v1`、`CONV`、`FULL/v2-R2A`、`FULL/v2-R2B`，其余各列不变。复核确认：原表 48 行的数据内容与 `summary.json` 逐行一致。
2. **数字错误**：原第 3 节写 FULL/v1"12 个安全机会一个都没有完成"。F7 只有 9 个安全机会（P0 档位没有安全机会），应为 0/9。
3. **补充 Q2 的适用范围**："没有观察到差异"只适用于 FULL 分类器在历史场景上的比较；消融分类器在历史场景上，v1 与 v2 之间有差异（见第 4 节）。
4. **补充 v2 在消融臂上的代价**：原报告只写了安全事件的减少，没有写人工审核代价的增加。

**数据来源**：本报告的所有数字都来自冻结分析工具 v3 的输出，路径为 `analysis_v3_result/summary.json`（SHA-256 `ff10a80d…`）。

**本轮没有做的事**：没有追加任何测试，没有修改代码，没有重跑实验，没有修改论文，没有冻结新对象，没有更新长期记忆，也没有关闭 VM。

## 1. 状态：数据有效 ≠ 安全性结论

| 环节 | 状态 | 说明 |
|---|---|---|
| 执行（用户 sudo） | 完成 | 588/588 个单元启动，全部是首次尝试（#a1），全部为"已观察、待回放"；没有失败，没有重试，没有续跑 |
| 同运行时回放（用户 sudo，VM，Node v24.21.0） | **PASS** | 724/724 条记录字节完全一致，身份全部核实 |
| 跨运行时回放（主机，Node v24.18.0） | **PASS**（经批准的恢复运行） | 724/724 条只差 Node 版本号；计划和键顺序全部相同；身份全部核实。第一次启动失败的经过见第 5 节 |
| 定案（冻结的 `finalize_states.py`） | **PASS** | 588/588 个单元为 VALID_COMPLETED |
| 汇总分析（冻结的分析工具 v3） | OK，COMPLETE | 首次尝试的覆盖率为 588/588 有效；没有输入冲突；没有重试；没有失败尝试中的安全观察 |

**数据有效**：588 个单元的证据完整，两种回放都一致，代码身份与冻结对象一致。

**安全性结论**：只适用于本批次既定的场景集（52 个历史场景加 12 个 F7 场景），也就是模拟环境、所测的故障模型、所测的实现版本，并且依赖以下前提：端点如实遵守声明的 P0–P3 契约（H-EP），A1–A3；R2-A 还依赖 A4（稳定键）。

按 A5 的决定：不计算置信区间，不做统计检验，**不外推**到总体失败率。

## 2. 各臂结果（只用首次尝试；分母均为有效首次尝试）

- 每个"分类器 × 执行器"臂：历史场景 52 个单元，F7 场景 12 个，合计 64 个；计划安全机会数为历史 38 个、F7 9 个、合计 47 个。
- CONV 只有 F7：12 个单元，9 个安全机会。
- 本批次没有无效单元，所以三种 L 口径（有效首次尝试中的比例、全部计划机会中的已确认比例、上下界）的数值相同，下表只列一次；"未确定的安全机会数"全部为 0。

表中：S1–S3 和"任一安全事件"按单元计；"S4 单元 / 次数"指有无效发送的单元数和无效发送的总次数；"闸门拒绝"一栏为"拒绝次数 / 被拒绝的单元数"，只适用于 v2。

| 臂 | 分组 | 有效 / 计划 | S1 | S2 | S3 | S4 单元 / 次数 | 任一安全事件 | L（完成 / 安全机会） | review | delivery_unknown | 闸门拒绝 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| FULL / v1 | 历史 | 52/52 | 0 | 0 | 0 | 0 / 0 | 0 | 29/38 | 16 | 7 | 不适用 |
| FULL / v1 | F7 | 12/12 | 4 | 4 | 0 | 12 / 12 | 12 | 0/9 | 0 | 0 | 不适用 |
| FULL / v1 | 合计 | 64/64 | 4 | 4 | 0 | 12 / 12 | 12 | 29/47 | 16 | 7 | 不适用 |
| FULL / v2-R2A | 历史 | 52/52 | 0 | 0 | 0 | 0 / 0 | 0 | 29/38 | 16 | 7 | 0 / 0 |
| FULL / v2-R2A | F7 | 12/12 | 0 | 0 | 0 | 0 / 0 | 0 | 9/9 | 0 | 3 | 0 / 0 |
| FULL / v2-R2A | 合计 | 64/64 | 0 | 0 | 0 | 0 / 0 | 0 | 38/47 | 16 | 10 | 0 / 0 |
| FULL / v2-R2B | 历史 | 52/52 | 0 | 0 | 0 | 0 / 0 | 0 | 29/38 | 16 | 7 | 0 / 0 |
| FULL / v2-R2B | F7 | 12/12 | 0 | 0 | 0 | 0 / 0 | 0 | 9/9 | 0 | 3 | 0 / 0 |
| FULL / v2-R2B | 合计 | 64/64 | 0 | 0 | 0 | 0 / 0 | 0 | 38/47 | 16 | 10 | 0 / 0 |
| −C_disp / v1 | 历史 | 52/52 | 2 | 2 | 0 | 4 / 4 | 4 | 29/38 | 12 | 7 | 不适用 |
| −C_disp / v1 | F7 | 12/12 | 4 | 4 | 0 | 12 / 12 | 12 | 0/9 | 0 | 0 | 不适用 |
| −C_disp / v1 | 合计 | 64/64 | 6 | 6 | 0 | 16 / 16 | 16 | 29/47 | 12 | 7 | 不适用 |
| −C_disp / v2-R2A | 历史 | 52/52 | 0 | 0 | 0 | 0 / 0 | 0 | 29/38 | 15 | 8 | 3 / 3 |
| −C_disp / v2-R2A | F7 | 12/12 | 0 | 0 | 0 | 0 / 0 | 0 | 9/9 | 0 | 3 | 0 / 0 |
| −C_disp / v2-R2A | 合计 | 64/64 | 0 | 0 | 0 | 0 / 0 | 0 | 38/47 | 15 | 11 | 3 / 3 |
| −C_disp / v2-R2B | 历史 | 52/52 | 0 | 0 | 0 | 0 / 0 | 0 | 29/38 | 15 | 8 | 3 / 3 |
| −C_disp / v2-R2B | F7 | 12/12 | 0 | 0 | 0 | 0 / 0 | 0 | 9/9 | 0 | 3 | 0 / 0 |
| −C_disp / v2-R2B | 合计 | 64/64 | 0 | 0 | 0 | 0 / 0 | 0 | 38/47 | 15 | 11 | 3 / 3 |
| −U / v1 | 历史 | 52/52 | 10 | 10 | 0 | 28 / 28 | 28 | 8/38 | 16 | 0 | 不适用 |
| −U / v1 | F7 | 12/12 | 4 | 4 | 0 | 12 / 12 | 12 | 0/9 | 0 | 0 | 不适用 |
| −U / v1 | 合计 | 64/64 | 14 | 14 | 0 | 40 / 40 | 40 | 8/47 | 16 | 0 | 不适用 |
| −U / v2-R2A | 历史 | 52/52 | 0 | 0 | 0 | 0 / 0 | 0 | 29/38 | 16 | 7 | 28 / 28 |
| −U / v2-R2A | F7 | 12/12 | 0 | 0 | 0 | 0 / 0 | 0 | 9/9 | 0 | 3 | 0 / 0 |
| −U / v2-R2A | 合计 | 64/64 | 0 | 0 | 0 | 0 / 0 | 0 | 38/47 | 16 | 10 | 28 / 28 |
| −U / v2-R2B | 历史 | 52/52 | 0 | 0 | 0 | 0 / 0 | 0 | 29/38 | 16 | 7 | 28 / 28 |
| −U / v2-R2B | F7 | 12/12 | 0 | 0 | 0 | 0 / 0 | 0 | 9/9 | 0 | 3 | 0 / 0 |
| −U / v2-R2B | 合计 | 64/64 | 0 | 0 | 0 | 0 / 0 | 0 | 38/47 | 16 | 10 | 28 / 28 |
| CONV | F7（即合计） | 12/12 | 2 | 2 | 0 | 3 / 3 | 3 | 9/9 | 0 | 0 | 不适用 |

补充说明：

- **S4 诊断项**（客观上存在合法依据、但声称的依据不成立的发送次数；这是诊断项，不是 S4）：FULL/v1 为 6，−C_disp/v1 为 8，−U/v1 为 20；其余各臂为 0。
- **闸门拒绝的原因**：按 A2 的决定，以原始字符串计数，见 `summary.json` 中 `arm_metrics` 的 `gate_denial_reasons_raw` 字段。
- **首次分类与 F7 期望**：按 A1 的决定，本轮不报告分类错误率。F7 期望是由代码推导出的预测，不属于 A1 的期望集合；三个 FULL 臂中满足它的单元数为：v1 12/12，v2-R2A 12/12，v2-R2B 12/12。这只是描述性统计，不是正确性指标。

## 3. X1（48 个单元，全部列出，不挑选）

X1 = F7 场景 × {FULL/v1、CONV、FULL/v2-R2A、FULL/v2-R2B}。表格由 `summary.json` 自动生成，没有手工改动。

| 臂 | 场景 | 档位 | 状态 | 效果数 | S1 | S2 | S3 | S4 无效发送 | 最终状态 | 安全机会 | 安全完成 | 首次分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| FULL/v1 | F7_B2_DROP_P0 | P0 | VALID | 1 | False | False | False | 1 | adapter_accepted | False | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v1 | F7_B2_DROP_P1 | P1 | VALID | 1 | False | False | False | 1 | adapter_accepted | True | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v1 | F7_B2_DROP_P2 | P2 | VALID | 1 | False | False | False | 1 | adapter_accepted | True | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v1 | F7_B2_DROP_P3 | P3 | VALID | 1 | False | False | False | 1 | adapter_accepted | True | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v1 | F7_B3_LATE_P0 | P0 | VALID | 2 | True | True | False | 1 | adapter_accepted | False | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v1 | F7_B3_LATE_P1 | P1 | VALID | 2 | True | True | False | 1 | adapter_accepted | True | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v1 | F7_B3_LATE_P2 | P2 | VALID | 1 | False | False | False | 1 | adapter_accepted | True | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v1 | F7_B3_LATE_P3 | P3 | VALID | 1 | False | False | False | 1 | adapter_accepted | True | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v1 | F7_B4_P0 | P0 | VALID | 2 | True | True | False | 1 | adapter_accepted | False | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v1 | F7_B4_P1 | P1 | VALID | 2 | True | True | False | 1 | adapter_accepted | True | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v1 | F7_B4_P2 | P2 | VALID | 1 | False | False | False | 1 | adapter_accepted | True | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v1 | F7_B4_P3 | P3 | VALID | 1 | False | False | False | 1 | adapter_accepted | True | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| CONV | F7_B2_DROP_P0 | P0 | VALID | 1 | False | False | False | 1 | adapter_accepted | False | False | None |
| CONV | F7_B2_DROP_P1 | P1 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | None |
| CONV | F7_B2_DROP_P2 | P2 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | None |
| CONV | F7_B2_DROP_P3 | P3 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | None |
| CONV | F7_B3_LATE_P0 | P0 | VALID | 2 | True | True | False | 1 | adapter_accepted | False | False | None |
| CONV | F7_B3_LATE_P1 | P1 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | None |
| CONV | F7_B3_LATE_P2 | P2 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | None |
| CONV | F7_B3_LATE_P3 | P3 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | None |
| CONV | F7_B4_P0 | P0 | VALID | 2 | True | True | False | 1 | adapter_accepted | False | False | None |
| CONV | F7_B4_P1 | P1 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | None |
| CONV | F7_B4_P2 | P2 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | None |
| CONV | F7_B4_P3 | P3 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | None |
| FULL/v2-R2A | F7_B2_DROP_P0 | P0 | VALID | 0 | False | False | False | 0 | delivery_unknown | False | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2A | F7_B2_DROP_P1 | P1 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2A | F7_B2_DROP_P2 | P2 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2A | F7_B2_DROP_P3 | P3 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2A | F7_B3_LATE_P0 | P0 | VALID | 1 | False | False | False | 0 | delivery_unknown | False | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2A | F7_B3_LATE_P1 | P1 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2A | F7_B3_LATE_P2 | P2 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2A | F7_B3_LATE_P3 | P3 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2A | F7_B4_P0 | P0 | VALID | 1 | False | False | False | 0 | delivery_unknown | False | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2A | F7_B4_P1 | P1 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2A | F7_B4_P2 | P2 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2A | F7_B4_P3 | P3 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2B | F7_B2_DROP_P0 | P0 | VALID | 0 | False | False | False | 0 | delivery_unknown | False | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2B | F7_B2_DROP_P1 | P1 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2B | F7_B2_DROP_P2 | P2 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2B | F7_B2_DROP_P3 | P3 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2B | F7_B3_LATE_P0 | P0 | VALID | 1 | False | False | False | 0 | delivery_unknown | False | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2B | F7_B3_LATE_P1 | P1 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2B | F7_B3_LATE_P2 | P2 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2B | F7_B3_LATE_P3 | P3 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2B | F7_B4_P0 | P0 | VALID | 1 | False | False | False | 0 | delivery_unknown | False | False | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2B | F7_B4_P1 | P1 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2B | F7_B4_P2 | P2 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |
| FULL/v2-R2B | F7_B4_P3 | P3 | VALID | 1 | False | False | False | 0 | adapter_accepted | True | True | CONFIRMED_NO_ACCEPTANCE_EVIDENCE |

**v2 的安全事件与反例**（依据 A10）：

- 所有 v2 臂（FULL、−C_disp、−U × R2A、R2B，共 384 个单元）中，S1–S4 事件数为 **0**，`v2_events` 为空，因此**没有任何反例**。
- 这只说明在所测的 384 个单元中没有观察到事件，**不等于**一般意义上的"v2 安全"。

**X1 中的观察**：

- **FULL/v1**：12 个单元全部出现无效发送（S4），即对本地失败记录直接重发，与审计中指出的 E1 缺陷一致；其中 4 个单元（B3_LATE 和 B4 的 P0、P1 档位）产生了重复效果。F7 的 **9** 个安全机会（P0 档位没有安全机会）一个都没有完成（L 为 0/9），因为 L 要求没有无效发送。
- **CONV**：3 个单元有无效发送（都在 P0 档位），其中 2 个产生了重复效果（B3_LATE_P0、B4_P0）。
- **FULL/v2-R2A 和 FULL/v2-R2B**：都没有安全事件，各 9 个安全机会全部完成；3 个 P0 单元（没有安全机会）最终为 delivery_unknown。

## 4. 次要评分器与研究问题

**次要评分器（历史 scorer）**：只作一致性检查。不一致的单元数为 **0**。v1 臂的次要评分结果原样附在 `analysis_v3_result/appendix_secondary_v1.csv`，不用于主结论。

**研究问题**（结论都只限于本批次的场景集和第 1 节所列的前提）：

| 问题 | 结果 | 依据 |
|---|---|---|
| Q1：在 F7 上，v2 能否消除"本地失败记录直接授权重发"的缺陷 | **在所测场景中得到支持** | FULL/v1 在 F7 上：12/12 单元出现 S4，4 个重复；FULL/v2-R2A、R2B：S1–S4 全部为 0，L 为 9/9。R2-A 与 R2-B 分开报告，两者结果相同，没有做两者之间的比较 |
| Q2：在 52 个历史场景上，v2 的安全性和恢复能力相对 v1 有没有变化 | **FULL 分类器在历史场景上，所测范围内没有观察到差异** | 依据：FULL 的历史分组（52 个单元）中，v1、v2-R2A、v2-R2B 的 S1–S4 都是 0，L 都是 29/38，review 都是 16，delivery_unknown 都是 7。**范围限制**：这一结论只适用于 FULL 分类器和上述指标。F7 分组中 FULL 是有差异的（见 Q1）；消融分类器在历史分组中也有差异（见 Q3） |
| Q3：闸门对分类错误能起多大作用 | **部分得到支持；其余部分无法回答** | **得到支持的部分**：两个有缺陷的分类器配 v1 时，出现了外部安全事件（−C_disp：16 个单元，其中 6 个重复；−U：40 个单元，其中 14 个重复）；配 v2 时，所有 S1–S4 都为 0，闸门拒绝分别为 3 个单元和 28 个单元；−U 的 L 从 8/47 变为 38/47。**无法回答的部分**：按 A1 的决定，本轮不报告分类错误率，所以无法给出"闸门拦下了多少比例的分类错误"。**同时观察到的代价**（历史分组）：−C_disp 配 v2 时，review 从 12 增至 15，delivery_unknown 从 7 增至 8，L 不变（29/38）；−U 配 v2 时，L 从 8/38 升至 29/38，delivery_unknown 从 0 增至 7。F7 分组中，两种消融分类器配 v2 时，delivery_unknown 都从 0 增至 3（都是没有安全机会的 P0 单元）|
| Q4：P3-C 持久化窗口 | **没有回答**（本轮不执行） | — |
| Q5：DSH | **没有回答**（暂不加入） | — |

**不支持的说法**：

- 不能据此说 v2 在任意系统或任意故障下都安全；
- 不能据此说明真实端点的行为（H-EP 是前提假设）；
- 不能据此给出总体失败率。

## 5. 调用故障、恢复记录与身份

**本批次中的调用故障与恢复**：

1. **跨运行时回放第一次启动失败**（2026-10-16 12:01:29 UTC）：
   - 原因：588 个目录拼成的命令行超过了 Windows 的长度上限，系统拒绝启动 `python.exe`；
   - 进程**没有启动**，0 条记录被执行，没有生成证据，也没有生成报告；
   - 日志中打印的 `exit=0` 是 PowerShell 留下的旧 `$LASTEXITCODE`，**不是结果**；日志里已注明；
   - 记录见 `logs/step07_cross_replay.log`。
2. **经用户批准的恢复运行** `P3A-cross-recovery-r1`：
   - 方式：用放在冻结包之外的包装脚本 `ecr_p3a_cross_replay_wrapper.py`（SHA-256 `0e7975fe…`），在同一个进程里调用冻结的 `replay.main()`，走与命令行完全相同的代码路径；
   - 输入核对：588 个目录正好对应冻结单元表中的 588 个 P3A 单元，全部是首次尝试，没有重复，没有非 P3A 单元，共 724 条记录；
   - 进程已启动，真实退出码 0；
   - 记录见 `run_record.json`、`logs/step08_cross_recovery_r1.log`。
3. **分析输入的准备**：
   - 导出的全局尝试索引中共 608 条，其中有 20 条是资格验证（P3-Q）的；
   - 把本批次的 588 条原样复制到新目录，作为分析输入；被排除的正好是那 20 条 P3-Q 记录；
   - 记录见 `analysis_input_index_p3a.list.json`、`logs/step10_analysis_input_prep.log`。
4. **授权文件**：
   - 运行前用独占方式创建（`362a2b98…`）；导出之后改名停用为 `qualification-authorization.used-ecr-p3a-20261016-r1.json`，内容不变；
   - 需要说明：运行器只检查 `status` 和 `manifest_sha256` 两个字段，批次 ID 只是记录，不被强制执行。

**更早的、属于资格验证的记录**都保留不动：第 2 次资格验证中 Q3 跨运行时的首次调用失败，以及获批的恢复运行，见 `QUAL2_STATUS_20261015*.md`。

**冻结身份**（冻结记录 `FREEZE_RECORD_v4_analysis_v3.json`，`2356e5e1…`）：

| 对象 | SHA-256 |
|---|---|
| 候选清单 v4 | `6496b502131237e0553c599a195f2902fe508bf1c3de81415d02e293ce9b40d8` |
| 执行清单 | `5034c58c5ec354a2ef83f62a56de449440bab72fd3f678ac53ae882df7d44133` |
| JS 的 strip 清单 | `853c843bf1b79ff3548a3519b567a0bd48c1998a8d15428dac58ff3d1a7434bb` |
| 单元表 | `c244199721f4982bbda5aacf2b09e837e19a6b1395ab62a6dcdbc7025aa190b6` |
| Node | `7fde7b8afa198da66257f42ee2001d874c7355631e6d1579a5fb5ef1f246df4c` |
| 分析工具清单 v3 | `3a44f73425363410e6c7b3221876dbb2e97401e3a00f6af52f0f1eb6aefe15fe` |

运行前在 VM 上核对过一次，运行之后在主机上又核对过一次：使用的 `replay.py`、`finalize_states.py` 和分析工具，都与冻结版本一致。

**结果与证据的路径**：

- **本报告目录**：`D:\agent_workspace\claude_workspace\claude_robot\outputs\20261016\ECR_P3A_正式运行_ecr-p3a-20261016-r1\`
  - `analysis_v3_result\`（`summary.json` 及各个 CSV）
  - `finalized_states.json`（`b5761b1f…`）
  - `cross_report.json`（`8b10ee50…`）
  - `same_runtime_report.json`（`40f131b7…`）
  - `run_record.json`、`record_dirs.json`、`batch-summary.json`
  - 运行日志和回放日志
  - `logs\`
  - `SHA256SUMS.txt`（目录内 30 个文件的哈希）
- **主机工作目录**：`D:\agent_workspace\claude_workspace\claude_robot\work\p3a_20261016\`
  - `copy\`：导出包 `44115bd7…`（165,150,720 字节）、解开后的 67,974 个文件、部署目录的复制件
  - `cross_recovery_r1\evidence\`：2,172 个回放证据文件
  - `analysis_input_index_p3a\`：分析用的 588 条索引
- **VM 上的原件**（权限保持原样）：
  - `/var/lib/ecr-followup-r1/async-sim-r2/ecr-p3a-20261016-r1/`
  - `/var/lib/ecr-followup-r1/async-sim-r2/attempt-index/`
  - `/var/lib/ecr-followup-r1/ledger/r2-ecr-p3a-20261016-r1/`
  - `/var/lib/ecr-followup-r1/sut-state/r2-ecr-p3a-20261016-r1/`
  - `/opt/ecr-followup-r1-async-r2/ecr-p3a-20261016-r1/`
  - `/home/ecradmin/ecr-r2-qualification-20261015-claude-02/` 下的导出包、运行日志、回放日志，以及已停用的授权文件

## 6. 耗时、VM 状态、尚未完成的事项

- **实际耗时**：从第一个尝试开始到批次汇总写出，共 472.8 秒（7.88 分钟），平均每单元 0.80 秒。估算是 7.8 分钟（区间 6.9–8.8 分钟），实际落在区间内。
- **存储**：导出包为 165.2 MB，略高于估算的未压缩 tar 上限（约 164 MB），超出约 0.7%。
- **VM**：**运行中**，按要求没有关闭，等你决定。主机内存使用率约 87%，虚拟机占用约 1 GB。
- **尚未完成**：
  - 是否关机；
  - 是否以及如何把本结果写入论文（论文 v10 没有改动，也没有生成 v11）；
  - Q3 中"分类错误率"的部分（A1 决定本轮不报告）；
  - P3-C、D4–D6、−C_lvl、DSH；
  - 是否把本结果目录作为新的冻结对象。
- 本报告到此为止，不自动执行任何后续步骤。
