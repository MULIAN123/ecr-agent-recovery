# bl1 A1+A2 预检报告：已中止

日期：2026-11-13（客机时间 08:56–09:07 UTC）。授权范围：A1+A2。没有运行任何资格验证或正式实验单元，也没有执行任何 sudo 操作。
**结论：A1 通过；A2 在 G3 测试阶段触发停止条件，已中止。VM 已软关机。**

## 1. 通过的部分

| 项目 | 结果 | 日志 |
|---|---|---|
| 宿主资源 | 空闲内存 6.3 GB，C 盘剩余 58 GB，D 盘剩余 168 GB | — |
| 客机身份 | 内核 6.8.0-146-generic、Python 3.12.3、Node v24.21.0（哈希 7fde7b8a…，root 755）、ecr-sut 999/987、ecr-adapter 996/986、ecr-scorer 995/985+988、ext4，以及隔离目录的属主和权限，都与冻结记录一致 | step01 |
| 目标目录 | `async-sim-bl1`、`/opt/ecr-followup-r1-async-bl1`、`~/ecr-bl1-*` 都不存在。`sut-state/bl1-*`、`ledger/bl1-*` 没有权限列出，runner 建目录时用的是独占创建，所以如果已经存在会直接失败 | step01 |
| 部署 | `~/ecr-bl1-20261113-claude-01/async_sim_bl1`，guest-admin，0700，祖先目录都不可写。tar 的 SHA 是 7616b56a…。61/61 个文件与候选清单 7c2e5b8b… 一致，没有多余文件，没有 pycache | step02、03 |
| G1 生成 JS | 用固定版本 Node 生成 FULL 的 JS，状态 COMPLETED。三个输出文件和 2b 冻结的 JS 逐字节相同（915809da… / 3ba1f9a0… / 1239d4d8…） | step04 |
| 部分 G3 测试 | test_bl1_static 6/6；test_scorer_v2 10/10；test_authority 16/16；test_single_entry 4/4 | step05、05d |

## 2. 停止原因

| 现象 | 时间（UTC） | 日志 |
|---|---|---|
| 固定版本 Node 崩溃：`trap int3 … in node`，共 3 次 | 09:05:33、09:06:38、09:06:44 | step06 |
| test_phase3_tools 里，strip_ts 子进程收到 SIGSEGV | 第 2 次运行 | step05c |
| TS 与 JS 路线的等价性测试 NOT_EQUIVALENT，3 次运行都失败 | 第 1–3 次运行 | step05、05c、05d |
| test_classifier_unchanged 报错（这个测试会调用 Node） | 09:06 | step05d |
| **内核 WARNING**：python3 写 ext4 时触发 `mm/filemap.c:978 filemap_add_folio`，属于页缓存一致性检查 | 09:06:10 | step06 |

和以前的客机启动比较：10-21 的两次启动（cp1 资格验证和正式运行都在这两次里完成）中，Node 崩溃和内核 WARNING 都是 **0** 次。本次启动分别是 3 次和 1 次。

还有一点：两次启动的虚拟 BIOS 版本不同。10-21 是 `VMW201.00V.24866131 (2025-07-21)`，今天是 `VMW201.00V.25600138 (2026-07-15)`。宿主上 VMware Workstation 现在装在 `<HOST-PATH> Files\VMware\`（vmware.exe 的日期是 2026-08-17），而记忆里记录的路径是 `Program Files (x86)`。也就是说，**冻结之后 hypervisor 换了版本**。这个环境不是 2b 和 cp1 运行时的那个环境。

宿主侧只做了只读检查：最近 30 天没有 WHEA 硬件错误记录；2026-11-09 有一次意外断电或重启（事件 41/6008）。宿主 CPU 是 i9-13980HX，此前（2026-10-09）记录过一次已更正的 CPU 奇偶校验错误。

**判断**：页缓存 WARNING 加上 Node 的随机崩溃，说明客机里的计算和写盘结果不能确定可靠。原因可能是新版本 VMware，也可能是宿主硬件，目前分不清。按我们的规则（不靠重跑来求通过，完整性存疑就停），不能进入 A3。

BL 单元本身不调用 Node，但内核 WARNING 发生在 python3 的写盘路径上，所以这个问题同样会影响 BL 单元。

## 3. 对准备材料的更正

- 预执行清单 A1-4 写的是“root，0700，由你执行 sudo”。这是错的，cp1 的报告里已经更正过：runner 要求包目录属主是执行 sudo 的用户（guest-admin），权限 0700，部署不需要 sudo。这次是按 runner 的要求部署的。
- 那 4 个依赖 fixtures 的测试，用 `python3 -I` 启动时找不到同目录的 `fixtures` 模块（`-I` 不把脚本所在目录加进路径）。改用 `python3 -B -E -s` 后可以运行。这是调用方式的问题，与代码无关。
- test_phase3_tools 这次跑出 30 个测试，cp1 当时是 29 个。原因没有查清，等环境恢复后再核对。

## 4. 客机和主机上留下的东西

- 客机：`~/ecr-bl1-20261113-claude-01/`，含部署的包、arms_js、tar、g3_work、g3_rerun。没有任何 root 目录，也没有 sut-state 和 ledger 目录。
- 主机：`a1a2_logs/`，含 step01–06b 的日志和传输用的 tar。
- 包和冻结材料都没有改动。

## 5. 需要你决定的事

继续之前，环境要先恢复到可信状态。可选做法：

1. **查 VMware 版本变化**：确认是谁、在什么时候升级或重装了 VMware Workstation。如果能回到 10-21 那个版本，再开机重跑 A2，看崩溃和 WARNING 是否消失。
2. **查宿主硬件**：Windows 内存诊断，以及确认 BIOS 和 Intel 微码是否为最新。13 代 HX 处理器有已知的不稳定问题，相关检查和更新只能由你来做，我不能改系统设置。
3. **接受环境变化，重新资格验证**：如果你认定新 VMware 版本可以用，就要把它当作新环境。先重跑一次 A2，必须完全没有崩溃和 WARNING；再把 hypervisor 版本的变化写进执行清单的说明里，并在论文里披露。

在环境问题澄清之前，我不建议开 A3。
