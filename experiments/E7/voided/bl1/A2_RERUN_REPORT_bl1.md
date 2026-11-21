# bl1 A2 重跑报告（VMware 降级后）

> **更正（2026-11-13 09:37 UTC 补记）**：本报告第 1 节写的“内核 WARNING 0、trap 0”是错的。客机内核在 09:20:20 UTC（G3 测试期间）出现了 `BUG: kernel NULL pointer dereference` / `Oops: 0002`。当时我只统计了 `cut here`、`trap int3` 和 `segfault`，漏掉了 `BUG:` 和 `Oops`。因此“环境已恢复、A2 通过”这个结论不成立，详见 `A4_STOPPED_REPORT_bl1.md`。下面的正文保留原样，不做改动。

日期：2026-11-13（客机时间 09:19–09:22 UTC）。
授权范围：A1+A2，以及按你的指示把 VMware 退回原版本后重跑。
没有运行任何资格验证或正式实验单元，也没有执行任何 sudo 操作。日志在 `a2_rerun_logs/` 下。第一次 A1/A2（26.0.1 版本下）的日志和报告原样保留。

**结论：A2 通过，可以进入 A3。** VM 现在开着，IP 是 <IP>。

## 1. 环境已恢复

| 项 | 10-21（cp1） | 今天第一次（已中止） | 今天重跑 |
|---|---|---|---|
| VMware Workstation | 25.0.0 build 24995812 | 26.0.1 build 25688693 | **25.0.0 build 24995812**（你于今天降级） |
| 虚拟 BIOS | VMW201.00V.24866131 | VMW201.00V.25600138 | **VMW201.00V.24866131** |
| VMX uuid.bios | 564d5ba1-… | 同左 | 同左 |
| 内核：Node trap 和 segfault 次数 | 0 | 3 | **0** |
| 内核：WARNING 次数（cut here） | 0 | 1（filemap_add_folio） | **0** |
| 内核：EXT4 错误次数 | — | — | **0** |

宿主机的 IP 是新租到的 .128，主机密钥与冻结的 VM 密钥 SHA256:JJjxKl… 一致。

## 2. 检查结果

| 项 | 结果 | 日志 |
|---|---|---|
| 客机身份 | 内核、Python、Node 哈希 7fde7b8a…、服务账户、ext4、隔离权限，全部与冻结记录一致 | step01 |
| 目标目录 | bl1 的批次目录、public 目录和 `~/ecr-bl1-20261113-claude-02` 都不存在 | step01 |
| 部署 | 部署到新目录 `~/ecr-bl1-20261113-claude-02`，使用同一个 tar（7616b56a…）。61/61 个文件与候选清单 7c2e5b8b… 一致，权限 0700，祖先目录都不可写，没有 pycache | step02 |
| G1 | FULL 的 JS 与 2b 冻结的版本逐字节相同 | step02 |
| G3 | test_bl1_static 6/6、test_scorer_v2 10/10、test_authority 16/16、test_single_entry 4/4 都通过。另外两项见第 3 节 | step03、04 |
| G2 执行清单 | `execution-manifest-bl1.json`，SHA **3581e11f…**，route J。共 65 个文件：候选清单 61 个，加 G1 的 4 个输出。清单与客机磁盘一致，主机侧交叉核对 61/61 也一致。public 文件 35 个，其中 src_bl 11 个（含 agent_bl.py）。包里没有 r2 清单，没有 pycache | step06、07 |

## 3. 仍然失败的两项：都是 route T 的 Node 不稳定，与 VMware 无关

- **test_phase3_tools**：跑了 3 次，每次都只有等价性测试一项失败。最后一次的明细如下：
  - route T 实时剥离 TypeScript，72 次调用里有 1 次以 exit 139（SIGSEGV）退出，具体是 A_UNKNOWN_PRESERVATION 在 S4_weak_ack:R2B 这个输入上；
  - route J 使用预先剥离好的 JS，144/144 次调用全部成功，72 组重复调用的输出逐字节相同。
- **test_classifier_unchanged**：这个测试也走 TS 路线，前两次报错，第三次通过。
- 降级前后，这两项的表现一样。降级后内核日志里也没有任何 trap、WARNING 或 EXT4 错误。所以它们不是 26.0.1 带来的问题。
- 这是 phase 3 已经记录过的问题：固定版本 Node 在 TS 剥离路径上偶尔崩溃，第一次资格验证时出现过 ERR_INTERNAL_ASSERTION。正式运行改用 route J 就是因为这个。cp1 当时那几次运行恰好全部通过。

**对 bl1 的影响**：正式运行用的是 route J。而 BL 单元根本不调用规划器，也就不会启动 Node（runner 只会对 BL 的 recorder 环境记录空的调用集）。所以这个问题不影响 BL 的结果。保留下来的风险是：runner 本身依赖的 Python、写盘和 endpoint 路径如果出问题，会表现为 INFRA_FAILED 或 INTEGRITY_UNKNOWN，按冻结的规则单独报告。

## 4. 第一次 A2 的结论需要更正

第一次报告把 Node 崩溃和内核 WARNING 一起算作环境问题。现在可以分开说：
- **内核 WARNING 和 int3 trap**：只在 26.0.1 下出现，降级后消失。
- **route T 的 SIGSEGV 和等价性失败**：两个版本下都有，属于已知的 Node 问题。

## 5. 下一步：A3 资格验证（需要你授权）

1. 由你在 VM 的 SSH 会话里执行：
   `cd ~/ecr-bl1-20261113-claude-02/async_sim_bl1 && sudo python3 -I runner/run_bl1.py run --set qualification --batch-id ecr-blq-20261113-r1`
   这一步跑 7 个 BLQ 单元，runner 的 guard 会在启动时重新读取 product_uuid。
2. 我检查 7 个单元的状态和负对照结果，然后做同运行时重放；导出后在主机上做跨运行时重放。
3. 把 7 个 BLQ 的结果与对应 BLX 单元的预测逐项对照，写资格验证报告给你审阅。你批准之后，再写 qualification-authorization.json。
