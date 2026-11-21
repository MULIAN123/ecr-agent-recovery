# Addendum D 勘误 E1：资格审计工具（audit_qualification_bl6.py → r1）

日期：2026-11-14。性质：**工具缺陷修正**。协议、场景、代码包、预测和判定规则都不变。

## 问题

`host_tools/audit_qualification_bl6.py`（r0，SHA 见冻结记录 D）是由 bl3 版审计工具派生的。派生时只改了资格单元数和留出组名，漏掉了三处：

1. **清单文件名**：r0 仍然检查 `execution-manifest-bl3.json`，而 bl6 的清单叫 `execution-manifest-bl6.json`。结果报出 `public manifest differs` 和 `unexpected public files: ['execution-manifest-bl6.json']`，导致资格审计 FAIL。服务器 agent 在资格验证之后报告了这个问题，并且没有写授权、没有运行正式批次，处理正确。
2. **模式接线检查**：r0 只对 `v2` 执行器做这项检查，没有覆盖 `v3`。
3. **路径检查**：r0 的路径检查是为 bl3 资格场景写的，没有检查 bl6 资格单元原本要验证的 v3 隔离路径，也没有检查 C2 overlay 的接线。

## 修正（r1）

新文件 `host_tools/audit_qualification_bl6_r1.py`，由 `prep_r1/make_audit_r1.py` 对 r0 做精确替换和插入生成。r0 文件保留不动。

- **E1**：清单名改为 `execution-manifest-bl6.json`（2 处）。
- **E2**：模式接线检查覆盖 `v2` 和 `v3`。
- **E3**：按冻结的资格清单（`tools/build_bl6_cells.py` 中的 QUAL 注释）和 Addendum D 第 D2 节，检查 6 个 v3 资格单元的路径，以及两个仅用于资格验证的 C2 场景的 overlay 回执。这些期望已经与冻结预测 `predictions_bl6.json` 背后的本地测试台结果逐项核对，完全一致。

**时间说明**：r1 写于资格批次 `ecr-bl6q-srv-b1` 运行之后，但 Claude Code 编写时**没有看过这个批次的任何输出**；期望只来自冻结设计和冻结预测。

## 执行要求

1. 在**同一个 boot 1** 中，用 r1 对已有的资格批次重新审计。不重跑任何单元，也不改动任何证据。
2. r0 的审计输出要保留，r1 的输出另存为新文件（例如 `qualification_audit_r1.json`）。两者都交回。
3. 只有 r1 审计 PASS、且其他资格检查仍然全部 PASS 时，才能写授权文件。授权文件的 `qualification_audit_sha256` 填 r1 输出的哈希，并在 `basis` 里注明 `"audit_tool": "audit_qualification_bl6_r1.py (Erratum D1)"`。
4. 如果 r1 报出任何问题，就停下来，把输出交回，不要写授权。
