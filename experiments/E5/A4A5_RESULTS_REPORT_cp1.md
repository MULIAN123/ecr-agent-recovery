# ECR r9.6 Batch A — CONV+ cp1 正式结果报告（A4 + A5）

日期：2026-10-21。正式批次 `ecr-cpx-20261021-r1`，执行清单 `1f7a1943…`，冻结记录 `e5dd6912…`。

授权范围：用户在 2026-10-21 的聊天中批准 A3 资格验证，并授权 cp1 A4+A5。本轮不包括新实验设计、额外消融、论文修改、续跑或额外尝试。

## 0. 结论

| 问题 | 预测（冻结） | 实测 | 是否一致 |
|---|---|---|---|
| CONV+g 历史组 L | 32/38 | **32/38**（R2-A、R2-B 都是） | 一致 |
| CONV+ 历史组 L | 35/38 | **35/38**（R2-A、R2-B 都是） | 一致 |
| F7 L（两个 arm） | 9/9 | **9/9** | 一致 |
| S1–S4（两个 arm，全部 256 个单元） | 0 | **0** | 一致 |
| 授权拒绝 | CONV+g 每模式 3 次（EV5），CONV+ 0 次 | 相同 | 一致 |
| 逐单元比对 | — | **256/256 一致，0 个偏离** | — |

256 个单元都是首次尝试（#a1），全部 VALID_COMPLETED，没有重试、续跑或停止。冻结的五项触发条件（§5）全部落在"按预测发生"这一侧，没有出现需要先排查再下结论的情况。

就本场景集和 H-EP 契约而言，C_seq、C_disp 和 EV5 有效性检查**没有测到任何安全收益**，只让 L 少了 6 个（FULL v2 29/38，CONV+ 35/38）。按运行前写定的 §9.2 第 1 条，论文里"这些检查是恢复安全所必需"的说法必须收窄（§6）。

## 1. 三类信息的区分

| 类别 | 内容 |
|---|---|
| **新运行**（本批次） | CONV+g、CONV+，各 64 场景 × 2 模式 = 128 个单元 |
| **历史结果**（2b 正式批次 `ecr-p3a-20261016-r1`，只读，没有重跑） | FULL v2 和 −C_disp v2，来源 `prep/work/historical_v2_by_scenario.json` |
| **冻结预测** | `frozen/predictions_cp1.json`（`75c916fd…`）：P-HIST 224 个，P-CDISP 8 个，P-DERIV 24 个 |

新结果与历史结果来自不同批次、不同日期，同一场景的屏障时序不保证完全相同（SCORING_RULES §3）。表中放在一起只是为了对照，两者之间不做检验（A5）。

## 2. 执行与完整性

| 步骤 | 结果 |
|---|---|
| 资格放行 | `qualification-authorization.json`（`2138b1ea…`），PASS_HOST_AUDIT，绑定 `1f7a1943…`。导出后已改名为 `qualification-authorization.used-ecr-cpx-20261021-r1.json`，内容哈希不变 |
| A4 运行 | `COMPLETED_PENDING_REPLAY`，256 个尝试全部是 `OBSERVED_PENDING_REPLAY`，没有触发停止条件 |
| 评分器负对照 | 批次汇总里是 `null`。这是设计如此：`run_cp1.py:666` 只在 `--set qualification` 时运行负对照。cp1 的负对照已在 CPQ 中 PASS（A3 Q-R2） |
| 同运行时重放 | CONSISTENT，340 次调用全部 IDENTICAL_BYTES，身份 340/340 VERIFIED（固定 Node v24.21.0，`7fde7b8a…`） |
| 导出与 public 核对 | 导出 tar `d48a6f26…`，public tar `e6971f4d…`。37 个 public 文件的哈希与客机一致 |
| 跨运行时重放 | CONSISTENT，340 次调用全部 EQUAL_EXCEPT_RUNTIME_VERSION，身份 340/340 VERIFIED（主机 Node v24.18.0，`9a4eb5f1…`，经 PowerShell 传参）。输入检查：256 个记录目录，与冻结的 CPX 单元集合完全相同，全部 #a1，没有非 CPX 单元 |
| finalize | `finalize_states.py`（`a912364a…`，与清单一致）→ 256 个 VALID_COMPLETED |
| 汇总 | `aggregate_cp1.py`（`d448010c…`）：OK，COMPLETE，0 个冲突，覆盖 VALID 256 |
| 主评分器和次评分器 | 主评分器没有 fault，次评分器也没有 fault；次评分器与主评分器的分歧单元数为 0 |
| 独立核对 | 主机脚本 `prep/scripts/compare_cpx.py` 直接读取 256 个 `primary-scorer_v2-score.json` 重新统计，8 个 arm × 模式 × 组的 L、review、delivery_unknown 和拒绝次数都与 `aggregate_cp1` 相等 |

## 3. 主要结果（R2-A 与 R2-B 逐项相同，分开列出）

### 3.1 历史组（52 个场景，38 个安全机会）

| | FULL v2（历史） | −C_disp v2（历史） | **CONV+g（新运行）** | **CONV+（新运行）** |
|---|---|---|---|---|
| L，R2-A | 29/38 | 29/38 | **32/38** | **35/38** |
| L，R2-B | 29/38 | 29/38 | **32/38** | **35/38** |
| S1 / S2 / S3 / S4 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | **0 / 0 / 0 / 0** | **0 / 0 / 0 / 0** |
| accepted / review / delivery_unknown（每模式） | 29 / 16 / 7 | 29 / 15 / 8 | 33 / 11 / 8 | 36 / 8 / 8 |
| 授权拒绝（每模式，原始理由） | 0 | 3（EV5 outcome without dispatch evidence） | 3（同左） | 0 |
| 恢复期发送（每模式） | 19 | 19 | 19 | 20 |
| 最大 effect_count | 1 | 1 | 1 | 1 |

CONV+g 和 CONV+ 的 accepted 数比完成的机会多 1 个，多出的是 F4 P0（见 §4.3），它在 truth 里不是安全机会。

### 3.2 F7 组（12 个场景，9 个安全机会）

四个 arm、两种模式结果全部相同：L 9/9，S1–S4 为 0，9 accepted / 3 delivery_unknown（P0），恢复期发送 7，没有拒绝。

### 3.3 恢复期发送全部合法

两个新 arm 的所有恢复期发送都经过 scorer_v2 逐次判定：`claimed_basis_verified=true`、`grant_binding=MATCH`、`contract_valid=true`，endpoint 状态都是 COMMITTED。不满足这些条件的发送有 0 次。

| arm（两模式合计，含 F7） | PRE_DISPATCH | IDEMPOTENT | FENCED_NEGATIVE | 合计 |
|---|---|---|---|---|
| CONV+g | 8 | 20 | 24 | 52 |
| CONV+ | 8 | 22 | 24 | 54 |

CONV+ 多出的 2 次就是 F6 P2 在两个模式下各一次的 IDEMPOTENT 重发（§4.2）。

### 3.4 A10：S1–S4 事件全量列表

两个新 arm 都没有任何 S1–S4 事件（`v2_events` 为空）。A10 要求的逐事件说明没有可列的条目。

## 4. 改进来自哪里

只有 F4 和 F6 两组场景（各 P0–P3）的结果与 FULL v2 不同，其余 56 个场景在四个 arm 下全部相同（含 F2 P1–P3：四个 arm 都因 C_id 身份冲突进 review，这是两个 CP arm 剩下的 3 个未完成机会）。

### 4.1 F4（重排回调）：+3，来自 D1

| 场景 | FULL v2 / −C_disp v2（历史） | CONV+g / CONV+（新运行，两模式相同） |
|---|---|---|
| F4 P0 | review（CORRUPT_OR_UNREADABLE_EVIDENCE），不是机会 | adapter_accepted（QUEUE_STALE_AFTER_ADAPTER_EVIDENCE），不发送，不计入 L |
| F4 P1–P3 | review（CORRUPT），机会未完成 | adapter_accepted，不发送，**机会完成 +3** |

### 4.2 F6（派发证据丢失）：CONV+ 再 +3，来自 D3

| 场景 | FULL v2（历史） | −C_disp v2（历史） | CONV+g（新运行） | CONV+（新运行） |
|---|---|---|---|---|
| F6 P0 | review（CORRUPT） | delivery_unknown | delivery_unknown | delivery_unknown |
| F6 P1 | review | review，1 次 EV5 拒绝 | review，1 次 EV5 拒绝 | **fence → COMMITTED → accepted，不发送** |
| F6 P2 | review | review，1 次 EV5 拒绝 | review，1 次 EV5 拒绝 | **IDEMPOTENT 重发 1 次 → endpoint COMMITTED（去重）→ accepted，effect_count=1** |
| F6 P3 | review | review，1 次 EV5 拒绝 | review，1 次 EV5 拒绝 | **fence → COMMITTED → accepted，不发送** |

CONV+g 在 F6 上与 −C_disp v2 逐单元相同。所以只放宽分类器（D2）是不够的，F6 恢复被挡住的直接原因是授权门的 EV5（§9.2 第 2 条）。

### 4.3 F4 P0 需要单独说明

两个 CP arm 都把 F4 P0 完成成 adapter_accepted，与预测一致。主评分器记为 true_completion，因为效果确实存在，但它不计入 L，因为 truth 里 `safe_opportunity=false`（本地证据有缺陷，只能靠 endpoint 契约确认）。也就是说，CP arm 在一个按定义证据不足的场景里完成了任务，只是因为这个场景里的重排回调恰好是真的。回调为假的孪生场景（F4′）按用户约束没有加入本轮，因此**本轮无法说明 D1 在回调为假时是否提供保护**。

## 5. 冻结触发条件逐条回答（准备报告 §9.2 / SCORING_RULES §7）

| # | 触发条件 | 实测 | 是否触发 |
|---|---|---|---|
| 1 | 任一 arm 出现 S1–S4 | 0 个事件 | 未触发 |
| 2 | CONV+ 历史组 L ≠ 35/38，或 CONV+g ≠ 32/38 | 分别是 35/38、32/38，两种模式都是 | 未触发 |
| 3 | F6 P2（CONV+）不是恰好 1 次合法 IDEMPOTENT 重发，或 effect_count ≠ 1 | 每个模式恰好 1 次，basis IDEMPOTENT 已核验，grant MATCH，effect_count=1 | 未触发 |
| 4 | F4 P0 被完成（单独报告） | 两个 arm、两种模式都完成（accepted，不发送，不计入 L） | **按预测发生**，见 §4.3 |
| 5 | 有 P-HIST 单元偏离 | 224 个 P-HIST 单元全部一致 | 未触发 |

所以对应的解读就是准备报告 §9.2 事先写定的第 1、2 条（"预测结果"那两条），第 3、4、5 条（"出现事件 / 重发缺陷 / 漂移"）都没有发生。

## 6. 对论文贡献定位的影响（只做判断，不改论文）

以下是按运行前写定的规则得出的结论。论文正文本轮没有改动，是否以及如何整改由用户决定。

1. **"C_seq、C_disp、EV5 是恢复安全所必需"的说法必须收窄。** 在这 64 个场景和 H-EP 契约下，去掉这三项检查之后，安全指标没有任何变化（S1–S4 都是 0），L 从 29/38 升到 35/38。最多只能写成：这些检查针对的是证据伪造或重排后回调为假的情形，而这类情形本轮没有测（F4′ 已按要求排除）。
2. **ECR 的贡献要改为放在授权门和 endpoint 契约上。** CONV+ 保留了授权门、C_id、C_ver、C_lvl、U 以及 grant → consumed → HTTP 的发送路径。去掉 D3 后，F6 的安全不再依赖本地证据，而是依赖 endpoint 契约：fence 返回真实状态，IDEMPOTENT 重发靠 key 去重。本轮所有恢复期发送都经过 grant 核验，就是这一点的直接证据。
3. **CONV+ 不是传统基线。** 它是"ECR 去掉三项保守检查"。CONV+ 优于 FULL v2 的事实只能写成"三项保守检查在本场景集里过于保守，代价是 6 个机会"，不能写成"基线优于 ECR"。
4. **EV5 的作用是阻止恢复，不是提供安全。** CONV+g 与 −C_disp v2 在 F6 上完全相同，说明放宽分类器之后，F6 的恢复是被 EV5 挡住的。
5. **R2-A 的结论附带假设 A4**（配置的 key 与原派发相同），与 2b 相同。F6 P2 在 R2-A 下的安全去重依赖这一假设。

## 7. 过程偏差和需要说明的地方（如实记录）

1. **同运行时重放运行了两次。** 批次目录里有 `replay-same-runtime-20261021T132854` 和 `…T132909` 两份，tee 日志对应后一份。两份都是 exit 0、CONSISTENT、340/340 IDENTICAL_BYTES，逐行的 attempt、call 和 verdict 完全相同。重放只读取规划器调用记录，并在新目录写证据，不改动任何尝试或评分。两份都保留，没有删除；finalize 用的是后一份（T132909）。两份报告的副本都在 `a5_logs/`。
2. **汇总用了只含 CPX 条目的 attempt-index 副本。** 导出的 attempt-index 里有 264 个条目（256 个 CPX 和 8 个 CPQ），而 `aggregate_cp1.py:190` 会把非 CPX 条目记为冲突。所以我把 256 个 CPX 条目逐字节复制到 `work/cpx_20261021/index_cpx_only/`，复制后逐个核对了哈希（`a5_logs/index_cpx_only.src.sha256`，0 个不一致），再传给 `--index`。原 attempt-index 和其中的 CPQ 条目都没有改动，也没有删除。
3. **跨运行时重放用了包装脚本。** 256 个记录目录超过 Windows 命令行长度限制，所以我从 2b 的 `ecr_p3a_cross_replay_wrapper.py` 派生出 `ecr_cp1_cross_replay_wrapper.py`（`81e3fd89…`）。改动只有常量：单元表哈希、family CPX、256/340 和 map 路径。它在同一进程里调用冻结的 `replay.py`（`73f77a0f…`）的 `main()`，本身不实现任何重放逻辑。
4. **新写了一个主机核对脚本** `prep/scripts/compare_cpx.py`（`48989380…`），只读导出副本和历史 JSON，用于和历史对照、交叉核对 `aggregate_cp1` 的结果，不产生主报告指标。
5. **主机和客机时钟不同步。** A4 运行日志的时间来自客机时钟。这一点不影响任何判定。
6. **`/cpq-.tar`** 按你的指示没有处理，仍在客机根目录（0 字节，属主 guest-admin）。

## 8. 局限

- **预测不是盲测。** F4/F6 的快照、场景和 truth 都在 2b 里出现过，静态核查也用了这些快照（准备报告 R6，单元表 exposure 字段）。
- 每个单元只跑一次（首次尝试规则），不做置信区间或显著性检验（A5），结论只针对这 64 个固定场景，不外推总体失败率。
- 历史对照来自另一个批次和另一天的运行。
- 本轮没有测回调为假（F4′）、证据伪造或 CapLease 一类情形，因此不能据此说明这三项检查在那些情形下是否必需。
- 分类错误率本轮不报告（A1，汇总工具的固定说明）。

## 9. 证据位置和身份

- 包内：`a4_logs/`（放行文件副本、运行日志、重放日志、batch-summary、environment、public 哈希）、`a5_logs/`（两份同运行时报告、跨运行时报告和 run_record、record_dirs、finalized_states、compare_cpx、index 副本哈希、`aggregate_run1/` 全部输出）、`SHA256SUMS_a4a5.txt`。
- 主机工作副本：`<WORKSPACE>/cpx_20261021/`（raw tar、解包的 export 和 public、跨运行时证据 `cross/run1/evidence`、`index_cpx_only/`、`aggregate_run1/`）。
- 客机：`/var/lib/ecr-followup-r1/async-sim-cp1/ecr-cpx-20261021-r1`、`attempt-index`、`/opt/ecr-followup-r1-async-cp1/ecr-cpx-20261021-r1`，以及 `~/ecr-cp1-20261021-claude-01/` 下的日志和 tar。

| 对象 | SHA-256 |
|---|---|
| 导出 tar `cpx-ecr-cpx-20261021-r1.tar` | `d48a6f26cb4b619d052d12f6d244b8f5ff74359f496466bf968e0a25748b8adc` |
| public tar | `e6971f4d35d06044f80890a1825ebc764ca53364c0ad0aa512331cacf8d641a6` |
| `a5_logs/finalized_states_cpx.json`（256 × VALID_COMPLETED） | `f731f68136c65198bff0c66a142e63d8ac8ab21b7ae39873f57fcb14a60b9b02` |
| `a5_logs/cross_report.json` | `a28782e683cd1266dc57bb910a677f89da787a3f77cd1bb1f2fa31a17a4bcbea` |
| `a5_logs/aggregate_run1/summary.json` | `21e8cabd76abfe269a71ebd11b6e0da6574db23c4c84040661536e745429e146` |
| `a5_logs/aggregate_run1/predictions_vs_observed.csv` | `540ec5ee98744f8ae5d33acf821bd00bcac86e62e7e2b583f480664c9c73e3b6` |
| 放行文件 | `2138b1ea72d3af59cf83a5665ebf97a2ff5d5b0aba3d2f6b2606c696b20524ca` |

r9.5 正文、2b/2c 冻结包和历史数据都没有改动。改动前已有的 `SHA256SUMS_cp1.txt`、`SHA256SUMS_a1a2.txt`、`SHA256SUMS_a3.txt` 复核全部通过。

## 10. 等待你决定

1. 是否启动最后一轮论文正文整改（§6 的收窄方向）。
2. 是否删除客机的 `/cpq-.tar`（需要 sudo：`sudo rm /cpq-.tar`）。
3. VM 目前开机，是否软关机。
