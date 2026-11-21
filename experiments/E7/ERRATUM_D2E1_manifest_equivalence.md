# Addendum D2 勘误 E1：执行清单“相同”改为“等价”

日期：2026-11-14。性质：**修正 D2 中写错的一条判定**。代码包、场景表、预测、runner、评分器、冻结工具都不变。

## 问题

D2 第 5 条要求新盘的 `execution-manifest-bl6.json` 与 b1 的 SHA “完全相同”，理由是“生成工具是确定性的”。这个理由是错的。我们只核对了 `make_execution_manifest_bl6.py`，没有核对它要收录的 `arms_js/strip-manifest.json`。后者由冻结工具 `tools/strip_ts.mjs` 生成，其中有一个运行时间戳字段 `utc`（第 39 行，`new Date().toISOString()`）。每次部署这个字段都不同，所以 strip 清单的哈希不同，执行清单的 SHA 也必然不同。bl5 没有遇到这个问题，因为它的两遍都在同一块磁盘、同一次部署上运行。

服务器 agent 在 boot 3 部署后发现了这一点并按规定停下（`BL6_C2_BLOCKER_manifest.md`）：b1 是 `6f27a54e…`，新盘是 `9d237b11…`。93 个文件中有 92 个哈希相同，strip 清单的 91 个字段中只有 `utc` 不同，三个 JS 输出的哈希相同。

## 修正

1. **身份判定改为“等价”**。由新工具 `host_tools/check_manifest_equiv_bl6.py` 判定，下列三条全部满足才算等价：
   - 除 `files` 外，所有顶层字段完全相同（VM UUID、Python、内核、Node 哈希、planner 路线、public 文件列表、候选包清单 SHA 和文件数）；
   - `files` 的 93 个键相同，除 `arms_js/strip-manifest.json` 外，每个哈希都相同；
   - 两份 strip 清单分别与各自执行清单里登记的哈希一致，并且去掉顶层 `utc` 字段后完全相同。
2. **G-D 改用 `host_tools/gate_gd_bl6_r1.py`**。它由 `prep/make_gate_gd_r1.py` 对冻结的 r0（SHA `71d241db…`）做精确替换生成。只改了执行清单的检查：每一遍内部只能有一个清单 SHA；两遍的 SHA 要么相同，要么正好是一份 `EQUIVALENT` 报告中的 m1/m2 这一对。11 个评分字段、发送标签、单元集合、“有效首次尝试”和 VALID_COMPLETED 规则都不变。r0 保留不动。
3. **c2 的授权文件**：`manifest_sha256` 填新盘的清单 SHA（runner 要求它与本次部署一致）。`basis` 里要写上 b1 的清单 SHA 和等价报告的 SHA（模板 `guest_tools/qualification-authorization-c2.TEMPLATE_r3.json`）。
4. **不允许的做法**：不能把 b1 的 strip 清单拷到新盘来凑出相同的 SHA，因为那样的记录与实际运行时间不符。也不能重新运行 strip 或重新生成执行清单。新盘上已有的 `9d237b11…` 就是 c2 的清单。

## 自测

`prep/selftest_r3.log`：在合成数据上做了 11 项检查，全部通过。等价判定有 6 项：只有 utc 不同时判为等价；其他文件、strip 的其他字段、strip 哈希登记、顶层字段，任一不同都判为不等价。G-D r1 有 5 项：两个 SHA 加 EQUIVALENT 报告时 PASS；没有报告或报告为不等价时 FAIL；SHA 相同时 PASS，与 r0 行为一致；评分字段不同时 FAIL。

## 论文披露

Threats 一节要写明：第二遍使用了另一次部署，两份执行清单只在 strip 清单的生成时间戳上不同，按本勘误判定为等价。
