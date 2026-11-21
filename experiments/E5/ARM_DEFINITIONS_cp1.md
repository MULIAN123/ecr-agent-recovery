# ECR r9.6 Batch A — CONV+ / CONV+g arm 定义（cp1，执行前冻结）

日期：2026-10-21。状态：冻结候选，等待用户授权。本文件的 SHA-256 记在 `FREEZE_RECORD_cp1.json` 里。正式运行开始后，本文件、`predictions_cp1.json`、`SCORING_RULES_cp1.md` 和 `UNIT_MANIFEST_cp1.csv` 都不能再改。确实需要修改时，另出新版本，并把旧版本和改动原因一起保留。

## 1. 基线

两个 arm 都以 2b 冻结包 `async_sim_r2`（候选清单 v4，`freeze_candidates_code_manifest_v4.json`，SHA-256 `6496b502…`）里的 **FULL 分类器 + EBRQ-v2 执行器** 为起点。下表 7 个文件的哈希与 2b 冻结值一致：

| 文件 | SHA-256（前 8 位） |
|---|---|
| `arms/FULL/delivery-reconciliation.ts` | `a293da95` |
| `arms/FULL/delivery-reconciliation-execution.ts` | `908a26a9` |
| `src/agent_v2.py` | `34a07df1` |
| `src/agent_v1.py` | `e9e24a94` |
| `src/authority.py` | `666a3383` |
| `src/evidence.py` | `e5fbdf3d` |
| `src/endpoint.py` / `scorer_v2.py` / `scorer.py` / `protocol.py` | `d5d3b5e1` / `846d612b` / `e4cbb567` / `ca67293c` |

## 2. 三处删除（全部改动）

**D1**：分类器 C_seq（`arms/CP_SEQ_DISP/delivery-reconciliation.ts` 第 40 行）

```diff
-    if (typeof type !== "string" || eventRank[type] === undefined || !Number.isSafeInteger(sequence) || sequence <= previousSequence) return "invalid_event_or_sequence";
+    if (typeof type !== "string" || eventRank[type] === undefined || !Number.isSafeInteger(sequence)) return "invalid_event_or_sequence";
```

**D2**：分类器 C_disp（同一文件第 50 行，改成空行，和 2b 消融 arm `A_OUTCOME_BOUNDARY` 的写法一样）

```diff
-  if (seen.has("AdapterOutcomeObserved") && !seen.has("DispatchBoundaryCrossed")) return "outcome_without_dispatch";
+
```

**D3**：验证器 EV5 有效性检查（`src_cp/evidence.py`，原第 201–202 行）

```diff
-    if outcome is not None and dbc is None:
-        r.fail("EV5 outcome without dispatch evidence")
```

除这三处外，下面这些都没有改：

- `CP_SEQ_DISP/delivery-reconciliation-execution.ts` 与 FULL 逐字节相同。
- `src_cp/` 下除 `evidence.py` 外，其余文件与 `src/` 逐字节相同。
- C_rank（`eventRank[type] < previousRank`）、C_id、C_ver、C_lvl、U 和 EV1–EV4、EV6、EV7 都保留，验证器自己的 EV3 序号检查也保留。
- 授权门（`authority.py`）的 grant → consumed → DBC 顺序、三种基础（PRE_DISPATCH / FENCED_NEGATIVE / IDEMPOTENT）的条件、R2-A/R2-B 的 key 绑定、endpoint 契约、overlay、truth 和评分器都没有改。

`CP_SEQ_DISP` 的分类器只比 `A_OUTCOME_BOUNDARY` 多一处 D1。这一点由 `tests/test_cp1_static.py` 的 T1 检查。

## 3. Arm

| 标签 | classifier_arm | executor | 运行的代码 | 含义 |
|---|---|---|---|---|
| **CONV+g** | `CP_SEQ_DISP` | `v2` | `src/agent_v2.py`（冻结源码，验证器原样） | 去掉分类器 D1、D2，保留原授权门 |
| **CONV+** | `CP_SEQ_DISP` | `v2cp` | `src_cp/agent_v2.py`（只有 `evidence.py` 去掉 D3） | CONV+g 再去掉验证器 D3 |

执行器 `v2cp` 的接法：`runner/run_cp1.py` 里 `AGENT["v2cp"] = "src_cp/agent_v2.py"`。agent 用 `python -s -E <script>` 启动，所以 `sys.path[0]` 是脚本所在的目录 `src_cp/`，`import evidence` 和 `from evidence import …`（authority.py）都会解析到 `src_cp/evidence.py`。`src_cp/` 和 `src/` 一起作为 public 文件部署，执行清单里列出全部哈希。这条接线由资格验证的行为标记确认，见 `SCORING_RULES_cp1.md` §5 的 Q-W1/Q-W2。

不做的事（按用户指示）：不加 F4′ 孪生场景，不加 CapLease 近似执行器，不去掉 C_rank，恢复决策不读 truth 或 ledger（执行器的 ledger 读取探针沿用 2b 的 `ledger-access-*.json`，必须是 DENIED）。

## 4. 单元

- 场景：2b 冻结单元表 `cells_p3.json`（`c2441997…`）里 FULL/v2 的 64 个 P3-A 场景（52 个历史场景 + 12 个 F7）。场景、truth、endpoint 契约和 group 按原样复制，`build_cp1_cells.py` 会校验源表的哈希。
- 正式单元（family `CPX`）：64 场景 × {R2-A, R2-B} × {CONV+g, CONV+} = **256**，全部是新运行。单元名和标识都是新的（`CPX-0001…0256`），不复用 2b 的任何标识。
- 资格验证单元（family `CPQ`，不是实验结果）：8 个，见 `SCORING_RULES_cp1.md` §5。
- 历史对照（不重跑）：2b 的 FULL v2（128 单元）和 −C_disp v2（128 单元），取第一次尝试、主评分器 scorer_v2 的结果，来自本地导出 `work/p3a_20261016/copy/export/…/attempts/`，与 `analysis_v3_result/summary.json` 逐组核对一致（`prep/work/historical_v2_by_scenario.json`）。
