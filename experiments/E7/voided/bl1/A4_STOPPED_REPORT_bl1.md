# bl1 A4 正式运行：停在 STOPPED_INVESTIGATE

日期：2026-11-13。批次 `ecr-blx-20261113-r1` 于 09:27 UTC 开始，09:34 停止。环境是 VMware 25.0.0 build 24995812，执行清单 3581e11f…，授权文件 119287f0…。
**结论：批次没有跑完，不能作为实验结果。VM 已软关机，没有续跑。**

## 1. 发生了什么

- 运行了 57 个单元（共 128 个）：56 个是 OBSERVED_PENDING_REPLAY，第 57 个 `BLX:B6_P0:INTENT_LOG:BL:-` 是 INTEGRITY_UNKNOWN。runner 按规则停在了 STOPPED_INVESTIGATE。
- 第 57 个单元的直接原因：agent 的发送进程在第一个 barrier（B1）之前，CPython 解释器自身断言失败并中止：
  `type_traverse() called on non-heap type 'Set'`，`Fatal Python error: _PyObject_AssertFailed`，当时正在 GC 中。
  这是进程内存被破坏的典型表现，与我们的代码无关：出错的是冻结的 v1 发送路径，Python 和 re 模块都是系统自带的。进程死在 B1 之前，所以没有派发，也没有外部效应。runner 因 B1 超时把它记为 InfraError；规划器记录不完整，状态定为 INTEGRITY_UNKNOWN。
- 同运行时重放（56 个单元，调用数 0）结果为 CONSISTENT；导出 tar 的 SHA 是 9d5cca23…。

## 2. 更严重的发现：A2 重跑时内核已经 Oops

- 客机内核在 **09:20:20 UTC**（A2 重跑做 G3 测试时，同样是 25.0.0 下）出现 `BUG: kernel NULL pointer dereference` 和 `Oops: 0002`，位置在 `irqentry_exit_to_user_mode`，进程是 python3。
- A2 重跑报告写的是“内核日志 0 trap、0 WARNING”。**这是我的检查漏了**：我只统计了 `cut here`、`trap int3` 和 `segfault`，没有统计 `BUG:` 和 `Oops`。A2 重跑报告已加上更正。

## 3. 综合判断

| 现象 | 26.0.1 | 25.0.0 |
|---|---|---|
| Node int3 trap | 3 次 | 0 次 |
| route T 的 Node SIGSEGV | 有 | 有（1/72） |
| 内核 WARNING 或 Oops | WARNING：filemap_add_folio | **Oops：NULL 指针解引用** |
| 用户态解释器内存损坏 | — | **CPython 断言失败（A4 第 57 个单元）** |

两个 hypervisor 版本下都出现了与软件无关的随机内存或 CPU 错误。10-17 和 10-21 在同一台宿主上的启动都很干净，所以问题更可能来自**宿主硬件这段时间的变化**。i9-13980HX 属于 13 代 HX，有已知的随时间退化导致不稳定的问题（Vmin shift）。另外 10-09 有过一次已更正的 CPU 奇偶校验错误，11-09 有过一次意外断电。以上只是推断，没有得到证实。

## 4. 已跑完的 56 个单元（只作诊断，不是结果）

这 56 个单元的主评分与冻结预测逐字段比对：**56/56 一致，0 偏离**。不过它们没有经过跨运行时重放和定稿，宿主也已经证实不稳定，按 R0 不能当作结果使用。这个比对只说明一点：已跑完的单元里，没有看到明显被损坏的结果。

## 5. 证据位置

- 主机：`<WORKSPACE>/blx_20261113/`，含导出 tar 和解包内容、运行日志、重放日志。
- 本包的 `a4_logs/`：授权文件、客机内核日志摘录 `guest_kernel_after_A4.log`。
- 客机：`/var/lib/ecr-followup-r1/async-sim-bl1/ecr-blx-20261113-r1/`、`~/ecr-bl1-20261113-claude-02/`。授权文件还没有改名为 used，客机关机前也没有动它。

## 6. 需要你决定

按协议，STOPPED_INVESTIGATE 之后不能自动续跑。续跑需要你批准一个 continuation 授权，而且第 57 个单元的首次尝试会永远记为 NO_VALID_RESULT（R0）。

1. **先查宿主硬件（建议）。** 更新主板 BIOS 和 Intel 微码（Intel 针对 13/14 代不稳定问题发布了 0x12B 及之后的版本），运行 Windows 内存诊断或 Intel Processor Diagnostic Tool。硬件确认稳定后，作废这次批次，用新批次 ID 从头重跑 A3 和 A4，并披露这次中止。
2. **换一台机器跑。** 这个包可以整体迁移，但要重新做 A1–A3，执行清单里的 VM UUID 也要换成新机器的。
3. **在当前宿主上续跑剩下的 71 个单元。** 不建议：内核 Oops 和解释器内存损坏说明，这台机器上的运行结果无法保证完整性。
