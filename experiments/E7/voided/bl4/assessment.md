# bl4 接手判定（Claude Code，2026-11-13 14:00 UTC 前后）

依据：Codex 交接文件 `<EXPERIMENTS>/ecr_bl2_codex_20261113/HANDOFF_CC_20261113_bl4_STOPPED.md`。
本次只做了只读检查：没有重试、续跑、删除或覆盖任何东西。sudo 由用户在自己的终端标签页里输入。

## 1. 检查结果

| 项 | 结果 |
|---|---|
| VM | 运行中，boot `9d787d44-…`（就是 bl4 那次启动），runner 已退出，主机密钥一致 |
| 本次启动的内核日志（冻结 G-K 的 12 个模式） | 全部 0 命中 |
| 资格批次 `ecr-bl4q-codex-b1` | 9 个单元 OBSERVED_PENDING_REPLAY，stderr 没有命中 |
| 正式批次 `ecr-bl4x-codex-b1` | 409 个 OBSERVED_PENDING_REPLAY，1 个 INTEGRITY_UNKNOWN，状态 STOPPED_INVESTIGATE；还有 1102 个单元没有运行 |
| 第 410 个单元 `VX:H_F2_B2_LATE_P0:FULL:v2:R2B#a1` | 恢复进程 `agent-recover` 以 -6（SIGABRT）退出。stderr 内容是 `type_traverse: Assertion failed: type_traverse() called on non-heap type 'InvalidBase64CharactersDefect'` 和 `Fatal Python error: _PyObject_AssertFailed`，崩溃发生在 GC 阶段，调用栈落在 `apport_python_hook.py` 的 `apport_excepthook` 里。final.json 没有写出，恢复期没有发送，ledger 里的 effect_count 是 1（来自迟到的原始请求，P0 下这是预期行为） |
| stderr 全量扫描 | 只有上面这一个文件命中 |

只读副本：`<WORKSPACE>/cc_bl4_takeover_20261113/copy/bl4b-batches.tar`（db22722e…）。客机上的原件在 `~/cc-bl4-readonly-20261113/`。

## 2. 按冻结协议判定

G-K 规定：只要任一 attempt 的 stderr 里出现 `Fatal Python error` 或 `Assertion failed`，这次启动的所有批次就作废。
**结论：G-K FAIL。`ecr-bl4q-codex-b1` 和 `ecr-bl4x-codex-b1` 都作废，保留作证据，不作为结果。** 第二遍不应在这次启动里运行。

## 3. 新的观察：三次崩溃的特征完全相同

| 批次 | 单元 | 进程 | 断言里的类型 | 调用栈 |
|---|---|---|---|---|
| bl1 `ecr-blx-20261113-r1` | 第 57 个 | agent-send（B1 之前） | `Set` | GC，在 `apport_python_hook` 里 |
| bl3（Codex） | 第 402 个 | agent-send（B1 之前） | 见 diagnosis_bl3 | GC，type_traverse 非 heap type |
| bl4 `ecr-bl4x-codex-b1` | 第 410 个 | agent-recover | `InvalidBase64CharactersDefect` | GC，在 `apport_python_hook` 里 |

三次都是同一个断言，都发生在 GC 阶段，而且至少 bl1 和 bl4 都落在 Ubuntu 的 apport 异常钩子里。这说明两点：
1. 崩溃之前，agent 进程里**已经抛出了一个未捕获的异常**。apport 钩子在处理这个异常时又崩溃了，所以原始异常的类型和内容都丢了。
2. 这次内核日志是干净的，所以“宿主硬件随机出错”不再是唯一解释，也可能是 Ubuntu 的 Python 3.12.3 加 apport 钩子这个组合里存在可复现的软件问题。不过，前面那次内核 Oops 和 Intel 诊断的结论还摆在那里，两种可能都不能排除。目前根因仍然未知。

## 4. 可选的下一步（都需要你决定）

1. **先做诊断，不产生任何实验数据（建议）。** 只在客机上一个临时副本里跑，不需要 sudo，也不碰任何批次目录：
   - 只读查看 apport 钩子是怎么装进来的（sitecustomize）；
   - 在临时副本里用同一个解释器反复运行 agent 的导入路径和异常路径，看能不能复现；
   - 关掉 apport 钩子，打开 faulthandler，重跑同类单元的 agent 进程，目的是抓到被掩盖的原始异常。
2. **修改协议后重跑。** 如果诊断确认是 apport 钩子的问题，可以在 runner 启动 agent 时关闭这个钩子（例如 `-S` 或设置环境变量）。这属于代码和环境的改动，需要写 addendum、重新冻结，然后从资格验证开始重做两遍。论文里也要披露。
3. **换一台机器**，重新做 A1–A3。

在你决定之前，我不会做任何会改变状态的操作。VM 目前保持开机，方便诊断；如果不需要，可以软关机。
