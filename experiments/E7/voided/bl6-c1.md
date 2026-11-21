# BL6 第二遍中断报告：客机 inode 耗尽

日期：2026-11-14（UTC）　执行位置：学院服务器 QEMU/KVM 客机（Ubuntu 24.04，boot 2）

## 结论

**第二遍在 2621/2848 处因客机 inode 耗尽（ENOSPC）中断**，重放、导出、内核日志抓取全部失败。
按协议这是停止条件，已立即停止：**没有重试、没有续跑、没有删除任何证据、没有换批次 ID**。
第一遍与资格验证的数据完整无损；第二遍的现场已从宿主机离线（只读）完整导出。

## 现象与根因

- 中断时间：2026-11-14 11:21:49 UTC（中国时间 19:21），第二遍从 09:44:24 UTC 开始。
- 报错：`OSError: [Errno 28] No space left on device`，发生在写
  `/var/lib/ecr-followup-r1/async-sim-bl6c/ecr-bl6x-srv-c1/attempts/VX__NEW_REORDERED_CALLBACK_B4_P3__INTENT_LOG__BL2__---a1/attempt-result.json.tmp-*`；
  随后 replay 建目录、`tar` 导出、`capture_kernel.sh` 都因同一错误失败。
- **不是字节空间问题**：客机 `df -h` 仍有 6.3G 空闲（19G 用 11G）；
  真正耗尽的是 **inode**：`df -i` = 1,212,388 / 1,212,416 已用，**只剩 28 个 inode**。
- 原因：bl6 每遍 2848 个单元（bl5 是 1512），8 个 arm、356 个场景；每个 attempt 会生成大量小文件
  （attempt 记录、replay、journal、events 等）。第一遍 2848 个 attempt 已用掉大半 inode，
  第二遍到第 2621 个 attempt 时耗尽。

## 协议状态

- 第二遍没有 `COMPLETED_PENDING_REPLAY`，没有 replay、没有导出、没有内核日志；
- 日志含 Traceback，`bad=2`；
- 按 Addendum D / runbook 第 0 节规则：**停止，不重试、不续跑、不删除、不换批次 ID**；
- 批次 `ecr-bl6x-srv-c1` 已使用，不能重跑。

## 已保全的数据

| 内容 | 位置（服务器） | 状态 |
|---|---|---|
| 资格验证（10/10，r1 审计 PASS） | `~/ecr-bl6-server/work/q/` | 完整 |
| 第一遍正式运行（2848/2848，重放 CONSISTENT，G-K 0 命中） | `~/ecr-bl6-server/work/b1/`（含 x-b1.tar 308M、内核日志、日志） | 完整 |
| 第二遍中断现场（私有批次 + attempt-index） | `~/ecr-bl6-server/work/c1_fail/evidence/async-sim-bl6c.tar`（330M） | 已离线导出 |
| 客机日志与导出目录（含 x-b1.tar） | `.../ecradmin-logs-exports.tar`（311M） | 已离线导出 |
| 第二遍 public 目录 | `.../public-bl6c.tar`（490K） | 已离线导出 |
| 第二遍运行日志 | `~/ecr-bl6-server/work/c1_fail/x-c1-run.log` | 完整（2621 行 + Traceback） |

客机已正常关机，磁盘原样保留（未删除、未修改）。

## 关键身份值

- 资格批次 `ecr-bl6q-srv-b1`；r1 审计工具 `audit_qualification_bl6_r1.py (Erratum D1)`，PASS；
- 执行清单 `execution-manifest-bl6.json` SHA `6f27a54e…`；授权文件在客机 pkg（含 r1 审计哈希）；
- boot 1 = `3059c176…`（第一遍完整），boot 2 = `6ef06c75…`（第二遍中断）；
- 第一遍重放：CONSISTENT，1568 次 `IDENTICAL_BYTES`；内核 0 命中。

## 需要 Claude Code 决定

1. **这是环境容量问题（inode），不是方法或数据问题**；但按协议第二遍已作废，bl6 目前无法完成两遍。
2. 可选的处理方向（由 Claude Code 定，我们不自作主张）：
   - 换一块 inode 更多的干净磁盘（例如更大的 LV / 指定 `mkfs.ext4 -N`，或把 `async-sim` 放在单独文件系统上），并**新增第二遍批次 ID**（需要协议修订，因为 `ecr-bl6x-srv-c1` 已用）；
   - 或重新评估本轮的可行性，改为在宿主侧限制单个 attempt 的文件数/清理中间文件（会改变协议，需明确授权）。
3. 在此之前我们保持停止：不重试、不删除、不修改。第一遍和资格的完整证据都已存档，可随时交回。

## 复现提示

如果 Claude Code 需要核对，离线证据包里的 `async-sim-bl6c.tar` 解包后含 `attempt-index/` 与
`ecr-bl6x-srv-c1/attempts/`（最后一项是那个写失败的 `attempt-result.json.tmp-*`），
`ecradmin-logs-exports.tar` 含 `ecr-bl6-srv-logs/`（x-c1-run.log 末尾是两次 Traceback）
和 `ecr-bl6-srv-exports/`（第一遍的 x-b1.tar 等）。
