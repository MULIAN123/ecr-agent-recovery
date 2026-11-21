# Addendum D：ECR-G v3（隔离后由端点裁决）与第二留出集（bl6）

日期：2026-11-14。依据：用户在对话中说“我认为先改进再跑一轮实验……记得冻结我们这次的实验结果”。
前置：bl5 结果已冻结（`outputs/20261114/ECR_bl5_结果冻结/FREEZE_RECORD_bl5_results.json`，SHA-256 `91528c77…`），本补充协议不改动其中任何数据。
继承：bl3 协议、bl4 补充协议、Addendum C 的全部规则继续有效，包括只用第一次尝试、不自动重试、G-K、G-D、两遍分别冷启动、停止规则。本文只写新增和改动的部分。

## D1 动机（来自已冻结的 bl5 结果）

bl5 的 96 个复合留出场景，每个都是本地故障加一条外来 attempt 的 acceptance 记录。在这些场景上，ECR-G v2 没有任何 S1–S4 事件，但完成数是 0/72：分类器判为 CORRUPT，授权门的验证器也判无效，于是全部转 review，即使端点提供查询或幂等能力也没有使用。

## D2 改进：ECR-G v3

v3 只在一个分支上与 v2 不同：分类器返回 `CORRUPT_OR_UNREADABLE_EVIDENCE` 时，**隔离**本地 journal（不再作为任何依据），改由端点裁决。启用条件三条，缺一条就照旧走 v2 的 review：

- **Q1**：授权记录本身没有异常（未 frozen）；
- **Q2**：没有 EV2 版本冲突。被作废或版本不一致的任务永远不补发、不完成；
- **Q3**：R2B 模式下，AttemptCommitted 里的操作键存在，并等于配置键。

启用后：
- **P1/P3**：先做隔离 fence。结果是 COMMITTED，就标记完成（不发送）；是 DEFINITELY_NOT_COMMITTED，就用 fence 给出的代号发一次 FENCED_NEGATIVE；不确定则标为 unknown。
- **P2**：用原始首发键做一次 IDEMPOTENT 重发。
- **P0**：review，与 v2 相同。

授权只读取配置键、声明的契约和授权记录（fence、grant、consumed、端点结果），**不读取 journal 中的 outcome**；隔离状态下永远不授予 PRE_DISPATCH。
代码：`src/agent_v3.py`、`src/authority_v3.py`，单元测试 `tests/test_bl6_v3.py`（10/10 通过）。冻结的 v2、authority、evidence、分类器和规划器都没有改动；在非 CORRUPT 路径上，v3 执行的是 v2 原样的逻辑体。

**设计是在看过 bl5 的 heldout 结果之后做的**，所以那 96 个场景（bl6 中改名为 heldout1）对 v3 来说只是开发证据，不能作为验证证据。

## D3 第二留出集 heldout2（104 个场景，以前任何 arm 都没有运行过）

- **96 个**：`C2_FOREIGN_FAILURE_AFTER_<X>`，即本地故障 X（弱确认、乱序回调、丢失 dispatch 加失败、dispatch 后本地失败）之后，再写入一条外来 attempt 的 **definite_failure** 记录（bl5 写入的是 acceptance）。× 6 个 barrier（B2_DROP、B2_ARRIVE、B2_LATE、B3_LATE、B4、B5）× P0–P3。
- **8 个**：`C2_SUPERSEDED_WITH_FOREIGN_{ACCEPT,FAILURE}`，即任务版本在 dispatch 前被作废（F5，B1），再加一条外来记录。× P0–P3。这一组专门检验 Q2：正确的行为是不发送、不完成。
- **truth 规则**：直接使用基础场景的 truth 行。`C2_FOREIGN_FAILURE_*` 用对应 barrier/profile 的行；作废组合用 F5 的行，即不允许发送、最大效应 0、没有安全机会。
- 在冻结之前，**没有在本地对 heldout2 运行过任何 arm**（本地测试台只跑了开发组和 heldout1）。

## D4 Arm、单元、分组

- **8 个 arm**：bl5 的 6 个（ECR-G R2A、ECR-G R2B、CONV、INTENT-LOG、RIFL-C、CA-LOG），加上 ECR-G3 R2A 和 ECR-G3 R2B。
- **356 个场景**：hist 52、F7 12、extended_dev 92、heldout1 96、heldout2 104。每遍 356 × 8 = **2848** 个 VX 单元。
- **10 个 VQ 资格单元**：都不在 heldout2 中。其中两个使用只在资格验证里出现的 C2 场景（B1），用来检查新 overlay 的接线。
- 单元表：`private/cells_bl6.json`。

## D5 冻结预测（`predictions_bl6.json`）

- **P-HIST-bl5（1512 个）**：bl5 的 6 个 arm 在 252 个 bl5 场景上，预测值就是冻结的 bl5 run-1 结果。这一组是复现检验。
- **P-HIST-v2（216 个）**：v3 单元中，bl5 里 v2 首次分类不是 CORRUPT 的，预测值等于 v2 的结果。
- **P-HARNESS-v3（288 个）**：v3 单元中，CORRUPT 的，预测值来自本地测试台。测试台用真实发送路径、冻结的 endpoint.Service、真实的分类器和规划器（host Node），并且已经逐单元复现了 504/504 个 bl5 v2 结果。
- **无预测（832 个）**：heldout2 的全部单元，所有 arm，都是盲测。

## D6 事先写下的假设（只描述 heldout2；无论结果如何都完整报告）

- **H1**：ECR-G3 在 heldout2 上没有 S1–S4 事件。
- **H2**：ECR-G3 在 heldout2 上完成的安全机会数高于 ECR-G v2。
- **H3**：在 8 个作废版本组合上，ECR-G3 不发送、不完成，也就是 Q2 生效。
- 这三条都可能不成立；不成立也是结果。各基线的数字照实报告。

## D7 运行身份

- 代码包 `candidate_bl6`（由 candidate_bl5 派生；文件清单见 `candidate_bl6_manifest.json`）。
- runner：`run_bl6b.py`（第一遍）和 `run_bl6c.py`（第二遍）。与 bl5 runner 的差异只有：命名空间 bl6b/bl6c、新增执行器 v3、v3 使用 v2 的配置、清单名和单元表名，以及新增的 overlay 分发函数 `apply_overlay_bl6`，它只处理 `C2_*`，其余 overlay 交给冻结函数处理。
- 执行清单工具：`make_execution_manifest_bl6.py`；执行清单名：`execution-manifest-bl6.json`。
- 批次 ID：资格验证 `ecr-bl6q-srv-b1`（boot 1）；第一遍 `ecr-bl6x-srv-b1`（boot 1）；第二遍 `ecr-bl6x-srv-c1`（boot 2，冷启动）。
- 环境：与 bl5 相同的服务器和方式（Docker 中的 QEMU 加 KVM）。**使用从原始 VMDK（SHA-256 `07360d64…`）新克隆的磁盘**，不复用跑过 bl5 的磁盘。
- 主机工具：`aggregate_bl6.py`、`audit_qualification_bl6.py`、`gate_gd_bl6.py`、`gate_gk_bl6.py`，均由 bl5 工具做精确替换派生，指标规则不变；合成数据自检通过。

## D8 报告规则（补充）

- 主结论只以 heldout2 为准。heldout1 和开发组只作为背景，并注明 v3 是在看过 heldout1 之后设计的。
- v2 和 v3 同批运行，必须分别报告；不能只报 v3。
- 安全性和完成率必须同时报告。
