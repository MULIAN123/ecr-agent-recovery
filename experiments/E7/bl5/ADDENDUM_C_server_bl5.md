# Addendum C：把 bl4 实验迁到学院服务器运行（bl5）

日期：2026-11-13。依据：用户在对话中的指示：“我们不排查错误了，你直接把所有需要用到的环境等等打包下来，我用我学院的服务器跑”。
本文件补充已冻结的 bl3 协议（`protocol_review_bl3.md`，freeze_record_bl3.json）和 bl4 补充协议（`protocol_amendment_bl4_DRAFT.md`，freeze_record_bl4.json）。

## C1 原因

原笔记本宿主（i9-13980HX）上已有三次批次因 CPython 解释器内存损坏，或者内核 Oops/WARNING 而作废：bl1 r1、bl3、bl4（G-K FAIL）。根因没有查明，但症状指向宿主硬件。用户决定换到学院服务器的硬件上运行。已作废的批次保留作证据，不作为结果。

## C2 不变的部分

研究问题、全部 1512 个 VX 单元和 9 个 VQ 单元、场景表 `private/cells_bl3.json`（SHA-256 cc13210b…）、6 个 arm 的规则、端点契约、两个评分器、只用第一次尝试、不自动重试、G-K（内核 12 个模式加上 stderr 中的 `Fatal Python error` 或 `Assertion failed`）、G-D（1512 个唯一单元、11 个字段加发送标签、执行清单一致）、两遍分别在两次冷启动中运行，以及汇总和报告规则，都与 bl3 协议和 bl4 补充协议相同。

## C3 改变的部分

1. **硬件和 hypervisor**：改在学院服务器上运行。首选方式是把原客机磁盘镜像（`vm/`，Ubuntu 24.04、内核 6.8.0-146、Python 3.12.3、固定版本 Node v24.21.0、服务账户和隔离目录都和以前一样）放到服务器的 hypervisor 里运行。服务器的 CPU 型号、内存是否 ECC、hypervisor 名称和版本、宿主操作系统，都要记录进报告。
2. **命名空间**：镜像里还留着已作废的 `async-sim-bl4b`，所以 runner 改用新的命名空间 bl5b/bl5c。`candidate_bl5` 与冻结的 `candidate_bl4` 相比，只在 `run_bl2b.py` 和 `run_bl2c.py` 中各有 3 处 bl4b→bl5b、bl4c→bl5c 的替换，其余 79 个文件逐字节相同（`candidate_bl5_manifest.json`）。
3. **执行清单**：在新客机上重新生成，写入新的 VM UUID。内核、Python 和 Node 的哈希必须等于 `6.8.0-146-generic`、`3.12.3`、`7fde7b8afa198da66257f42ee2001d874c7355631e6d1579a5fb5ef1f246df4c`。只要有一项不同，就停止并报告，不得修改清单工具或 runner 来绕过。
4. **批次 ID**：资格验证用 `ecr-bl5q-srv-b1`（boot 1）；正式第一遍用 `ecr-bl5x-srv-b1`（同在 boot 1，资格验证通过后再跑）；正式第二遍用 `ecr-bl5x-srv-c1`（boot 2，冷启动）。
5. **执行者和授权**：服务器上的 agent 负责运行。只有当资格验证的全部检查都通过时（第 C4 节），它才可以按模板写 `qualification-authorization.json`，并且只授权两遍正式运行。
6. **独立复核**：结果交回后，Claude Code 会用导出副本独立复核：跨运行时重放、定稿、资格审计、G-K、G-D 和汇总。复核通过之后，结果才能使用。

## C4 资格验证通过的条件（全部满足才能写授权文件）

1. 9 个 VQ 单元在定稿后全部是 VALID_COMPLETED；负对照 PASS。
2. 同运行时重放和跨运行时重放都 CONSISTENT。跨运行时重放要用另一个 Node 运行时（任一与 v24.21.0 不同的 Node 24.x）。如果服务器上没有可用的第二个 Node，就停在这里，把导出交回给 Claude Code 完成。
3. 运行 `host_tools/audit_qualification_bl3_candidate.py`，结果为 PASS。
4. boot 1 截至这一刻的 G-K 为 PASS（用 `host_tools/gate_gk_bl3_candidate.py` 判定）。

## C5 停止规则

出现以下任一情况就停止并报告，不重试、不续跑、不删除任何东西：
- G-K 有任何命中；
- 批次状态是 STOPPED_INVESTIGATE；
- guard 拒绝运行；
- 哈希或身份对不上；
- 某个单元不是 OBSERVED_PENDING_REPLAY；
- G-D 有任何差异。

已经产生的证据全部保留，一起交回。
